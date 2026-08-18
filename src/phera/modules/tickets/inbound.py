"""Create or append a support ticket from a normalized inbound channel message."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import ChannelAccount, Contact, Message, OwnershipProfile, Ticket, Workspace
from phera.db.mutate import FieldChange, MutateRequest, mutate
from phera.modules.tickets.activity import touch_ticket_activity
from phera.modules.tickets.inbox_events import publish_inbox_event
from phera.modules.tickets.reuse_policy import (
    ASSIGNEE_QUEUE,
    OPEN_STATUSES,
    EffectiveReusePolicy,
    load_reuse_policy,
    support_agent_ids,
)
from phera.modules.tickets.short_id import insert_ticket_with_short_id

SHORT_ID_IN_SUBJECT = re.compile(r"\[#(\d{6}-\d{4,6})\]")

logger = logging.getLogger(__name__)


def normalize_channel_address(value: str | None) -> str:
    """Strip separators so Exotel E.164 payloads match stored channel addresses."""
    if not value:
        return ""
    return re.sub(r"[^\d+]", "", value.strip())


def _address_matches(account: ChannelAccount, address_hint: str) -> bool:
    if not account.address:
        return False
    hint = address_hint.lower().replace(" ", "")
    stored = account.address.lower().replace(" ", "")
    if hint and hint in stored:
        return True
    phone_hint = normalize_channel_address(address_hint).lower()
    phone_stored = normalize_channel_address(account.address).lower()
    return bool(phone_hint) and phone_hint in phone_stored


async def resolve_channel_account(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    kind: str,
    adapter_type: str,
    address_hint: str | None,
) -> ChannelAccount | None:
    q = await session.execute(
        select(ChannelAccount).where(
            ChannelAccount.workspace_id == workspace_id,
            ChannelAccount.kind == kind,
            ChannelAccount.adapter_type == adapter_type,
            ChannelAccount.is_active.is_(True),
        )
    )
    return _pick_channel_account(
        list(q.scalars().all()), kind=kind, adapter_type=adapter_type, address_hint=address_hint
    )


async def resolve_channel_account_global(
    session: AsyncSession,
    *,
    kind: str,
    adapter_type: str,
    address_hint: str,
) -> ChannelAccount | None:
    """Resolve an active channel by address across workspaces. Requires an address
    hint — never falls back to "the only voice channel" globally."""
    q = await session.execute(
        select(ChannelAccount).where(
            ChannelAccount.kind == kind,
            ChannelAccount.adapter_type == adapter_type,
            ChannelAccount.is_active.is_(True),
        )
    )
    accounts = [account for account in q.scalars().all() if _address_matches(account, address_hint)]
    if len(accounts) == 1:
        return accounts[0]
    if len(accounts) > 1:
        logger.warning(
            "Ambiguous global channel_account match kind=%s adapter_type=%s hint=%r — "
            "%d matches — rejecting",
            kind,
            adapter_type,
            address_hint,
            len(accounts),
        )
    return None


def _pick_channel_account(
    accounts: list[ChannelAccount],
    *,
    kind: str,
    adapter_type: str,
    address_hint: str | None,
) -> ChannelAccount | None:
    if not accounts:
        return None
    if address_hint:
        for account in accounts:
            if _address_matches(account, address_hint):
                return account
    if len(accounts) == 1:
        return accounts[0]
    # 0 or 2+ candidates with no address match: routing to an arbitrary account risks
    # placing one customer's message on a different mailbox/number. Reject rather than guess.
    logger.warning(
        "Ambiguous channel_account match kind=%s adapter_type=%s hint=%r — "
        "%d active accounts, no unambiguous match — rejecting",
        kind,
        adapter_type,
        address_hint,
        len(accounts),
    )
    return None


async def _find_or_create_contact(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    inbound: dict,
) -> Contact:
    email = inbound.get("contact_email")
    phone = inbound.get("contact_phone")
    contact = None
    if email:
        q = await session.execute(
            select(Contact).where(
                Contact.workspace_id == workspace_id,
                Contact.primary_email == email,
                Contact.is_deleted.is_(False),
            )
        )
        contact = q.scalar_one_or_none()
    if not contact and phone:
        q = await session.execute(
            select(Contact).where(
                Contact.workspace_id == workspace_id,
                Contact.primary_phone == phone,
                Contact.is_deleted.is_(False),
            )
        )
        contact = q.scalar_one_or_none()
    if contact:
        if inbound.get("contact_name") and not contact.name:
            contact.name = inbound["contact_name"]
        if email and not contact.primary_email:
            contact.primary_email = email
        if phone and not contact.primary_phone:
            contact.primary_phone = phone
        return contact

    contact = Contact(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=inbound.get("contact_name"),
        primary_email=email,
        primary_phone=phone,
        source=inbound.get("adapter_type"),
    )
    session.add(contact)
    await session.flush()
    return contact


async def _reuse_or_create_ticket(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    contact: Contact,
    channel: ChannelAccount,
    inbound: dict,
    policy: EffectiveReusePolicy,
) -> tuple[Ticket, bool]:
    thread = inbound.get("thread_keys") or {}
    in_reply_to = thread.get("in_reply_to")
    if in_reply_to:
        q = await session.execute(select(Message).where(Message.provider_message_id == in_reply_to))
        prior = q.scalar_one_or_none()
        if prior and prior.ticket_id:
            ticket = await session.get(Ticket, prior.ticket_id)
            if ticket and policy.allows_status(ticket.status):
                return ticket, False

    subject = inbound.get("subject") or ""
    match = SHORT_ID_IN_SUBJECT.search(subject)
    if match:
        q = await session.execute(
            select(Ticket).where(
                Ticket.workspace_id == workspace_id,
                Ticket.short_id == match.group(1),
            )
        )
        ticket = q.scalar_one_or_none()
        if ticket and policy.allows_status(ticket.status):
            return ticket, False

    cutoff = datetime.now(UTC) - timedelta(seconds=policy.window_seconds)
    statuses = policy.reusable_statuses()
    q = await session.execute(
        select(Ticket)
        .where(
            Ticket.workspace_id == workspace_id,
            Ticket.contact_id == contact.id,
            Ticket.channel_account_id == channel.id,
            Ticket.status.in_(statuses),
            or_(
                Ticket.last_activity_at >= cutoff,
                Ticket.resolved_at >= cutoff,
                Ticket.closed_at >= cutoff,
                Ticket.updated_at >= cutoff,
            ),
        )
        .order_by(
            case((Ticket.status.in_(OPEN_STATUSES), 0), else_=1),
            Ticket.last_activity_at.desc(),
        )
        .limit(1)
    )
    existing = q.scalar_one_or_none()
    if existing:
        return existing, False

    now = inbound.get("occurred_at") or datetime.now(UTC)
    subject_text = inbound.get("subject")
    if not subject_text:
        preview = (inbound.get("body") or "").strip()[:80]
        who = (
            inbound.get("contact_name")
            or inbound.get("contact_phone")
            or inbound.get("contact_email")
        )
        if preview:
            subject_text = preview
        elif channel.kind == "voice":
            subject_text = f"Call from {who}"
        elif channel.kind == "messaging":
            subject_text = f"WhatsApp from {who}"
        else:
            subject_text = f"Email from {who}"

    ticket = Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        contact_id=contact.id,
        channel_account_id=channel.id,
        subject=subject_text,
        status="open",
        last_activity_at=now,
        status_entered_at=now,
    )
    await insert_ticket_with_short_id(session, ticket)
    return ticket, True


async def _apply_reopen_assignee(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    ticket: Ticket,
    policy: EffectiveReusePolicy,
) -> None:
    if policy.on_reopen_assignee == ASSIGNEE_QUEUE:
        ticket.assignee_user_id = None
        return
    if not ticket.assignee_user_id:
        return
    profile = await session.get(OwnershipProfile, workspace_id)
    agents = support_agent_ids(dict(profile.flags or {}) if profile else {})
    if agents and ticket.assignee_user_id not in agents:
        ticket.assignee_user_id = None


async def resolve_call_ticket(
    session: AsyncSession,
    workspace: Workspace,
    *,
    adapter_type: str,
    from_number: str,
    to_number: str | None,
) -> tuple[ChannelAccount, Contact, Ticket, bool]:
    """Resolve the channel/contact/ticket for an inbound call, without committing —
    the caller (voice_hooks.py) still needs to decide the target agent and create the
    `Call` row in the same transaction. Mirrors `ingest_inbound_message`'s resolution
    steps but produces no `Message` row (a call is not a message)."""
    channel = await resolve_channel_account(
        session, workspace.id, kind="voice", adapter_type=adapter_type, address_hint=to_number
    )
    if not channel:
        raise ValueError(f"No active {adapter_type} voice channel account")

    inbound = {"contact_phone": from_number, "adapter_type": adapter_type}
    contact = await _find_or_create_contact(session, workspace.id, inbound)
    policy = await load_reuse_policy(session, workspace.id, channel.kind)
    ticket, created = await _reuse_or_create_ticket(
        session, workspace.id, contact, channel, inbound, policy
    )
    if ticket.status in ("resolved", "closed"):
        ticket.status = "open"
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.status_entered_at = datetime.now(UTC)
        await _apply_reopen_assignee(session, workspace.id, ticket, policy)

    touch_ticket_activity(ticket)
    return channel, contact, ticket, created


async def ingest_inbound_message(
    session: AsyncSession,
    workspace: Workspace,
    inbound: dict,
) -> dict:
    channel = await resolve_channel_account(
        session,
        workspace.id,
        kind=inbound["channel_kind"],
        adapter_type=inbound["adapter_type"],
        address_hint=inbound.get("address_hint"),
    )
    if not channel:
        raise ValueError(
            f"No active {inbound['adapter_type']} {inbound['channel_kind']} channel account"
        )

    provider_id = inbound.get("provider_message_id")
    if provider_id:
        q = await session.execute(select(Message).where(Message.provider_message_id == provider_id))
        existing = q.scalar_one_or_none()
        if existing:
            return {
                "duplicate": True,
                "message_id": str(existing.id),
                "ticket_id": str(existing.ticket_id) if existing.ticket_id else None,
                "contact_id": str(existing.contact_id) if existing.contact_id else None,
            }

    contact = await _find_or_create_contact(session, workspace.id, inbound)
    policy = await load_reuse_policy(session, workspace.id, channel.kind)
    ticket, created = await _reuse_or_create_ticket(
        session, workspace.id, contact, channel, inbound, policy
    )
    if ticket.status in ("resolved", "closed"):
        ticket.status = "open"
        ticket.resolved_at = None
        ticket.closed_at = None
        ticket.status_entered_at = datetime.now(UTC)
        await _apply_reopen_assignee(session, workspace.id, ticket, policy)

    now = inbound.get("occurred_at") or datetime.now(UTC)
    message = Message(
        id=uuid.uuid4(),
        channel_account_id=channel.id,
        ticket_id=ticket.id,
        contact_id=contact.id,
        direction="inbound",
        provider_message_id=provider_id,
        thread_keys=inbound.get("thread_keys") or {},
        body=inbound.get("body"),
        raw=inbound.get("raw") or {},
        occurred_at=now,
    )
    session.add(message)
    touch_ticket_activity(ticket, now)
    await session.flush()

    actor = Actor(actor_type="connector", id=inbound.get("adapter_type"))
    await mutate(
        session,
        MutateRequest(
            entity=message,
            entity_type="message",
            action="received",
            changes=[FieldChange("body", None, inbound.get("body"))],
            actor=actor,
            workspace_id=workspace.id,
            outbox_event_type="message.received",
            idempotency_key=f"message.received:{message.id}",
            outbox_payload={
                "message_id": str(message.id),
                "ticket_id": str(ticket.id),
                "contact_id": str(contact.id),
                "channel_kind": channel.kind,
            },
            timeline=True,
            timeline_type="message",
            timeline_body=inbound.get("body"),
            contact_id=contact.id,
            ticket_id=ticket.id,
        ),
    )
    await commit_and_notify(session)
    result = {
        "duplicate": False,
        "created_ticket": created,
        "message_id": str(message.id),
        "ticket_id": str(ticket.id),
        "contact_id": str(contact.id),
        "channel_kind": channel.kind,
    }
    publish_inbox_event(
        {
            "type": "message.received",
            "workspace_id": str(workspace.id),
            "ticket_id": result["ticket_id"],
            "created_ticket": created,
            "contact_id": result["contact_id"],
            "channel_kind": channel.kind,
            "assignee_user_id": ticket.assignee_user_id,
        }
    )
    return result
