from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import (
    ConversationItemOut,
    MessageCreate,
    MessageOut,
    TicketAssign,
    TicketCreate,
    TicketDetailOut,
    TicketOut,
    TicketUpdate,
)
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import (
    Call,
    ChannelAccount,
    Contact,
    Interaction,
    Message,
    Ticket,
    Transcript,
    Workspace,
)
from phera.db.mutate import FieldChange, MutateRequest, mutate
from phera.modules.connectors.gallabox import GallaboxMessagingProvider
from phera.modules.connectors.google_group import GoogleGroupEmailProvider
from phera.modules.tickets.activity import touch_ticket_activity
from phera.modules.tickets.enrichment import channel_for_ticket, ticket_detail_dict
from phera.modules.tickets.inbox_events import publish_inbox_event

router = APIRouter(tags=["tickets"])


@router.get("/tickets", response_model=list[TicketOut])
async def list_tickets(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(select(Ticket).where(Ticket.workspace_id == workspace.id))
    return q.scalars().all()


@router.get("/tickets/{ticket_id}", response_model=TicketDetailOut)
async def get_ticket(
    ticket_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket or ticket.workspace_id != workspace.id:
        raise HTTPException(404, "Ticket not found")
    return await ticket_detail_dict(session, ticket)


@router.post("/tickets", response_model=TicketOut, status_code=201)
async def create_ticket(
    body: TicketCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    now = datetime.now(UTC)
    ticket = Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        status_entered_at=now,
        last_activity_at=now,
        **body.model_dump(),
    )
    session.add(ticket)
    await session.flush()
    await mutate(
        session,
        MutateRequest(
            entity=ticket,
            entity_type="ticket",
            action="created",
            changes=[FieldChange("status", None, ticket.status)],
            actor=actor,
            outbox_event_type="ticket.created",
            idempotency_key=f"ticket.created:{ticket.id}",
            outbox_payload={"ticket_id": str(ticket.id), "contact_id": str(ticket.contact_id)},
            contact_id=ticket.contact_id,
            ticket_id=ticket.id,
        ),
    )
    await commit_and_notify(session)
    publish_inbox_event(
        {
            "type": "ticket.created",
            "ticket_id": str(ticket.id),
            "created_ticket": True,
            "assignee_user_id": ticket.assignee_user_id,
            "contact_id": str(ticket.contact_id),
        }
    )
    await session.refresh(ticket)
    return ticket


@router.patch("/tickets/{ticket_id}", response_model=TicketDetailOut)
async def update_ticket(
    ticket_id: uuid.UUID,
    body: TicketUpdate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket or ticket.workspace_id != workspace.id:
        raise HTTPException(404, "Ticket not found")

    changes: list[FieldChange] = []
    now = datetime.now(UTC)
    if body.status is not None and body.status != ticket.status:
        changes.append(FieldChange("status", ticket.status, body.status))
        ticket.status = body.status
        ticket.status_entered_at = now
        if body.status in ("resolved", "closed"):
            ticket.resolved_at = ticket.resolved_at or now
        if body.status == "closed":
            ticket.closed_at = now
    if body.priority is not None and body.priority != ticket.priority:
        changes.append(FieldChange("priority", ticket.priority, body.priority))
        ticket.priority = body.priority
    if body.subject is not None and body.subject != ticket.subject:
        changes.append(FieldChange("subject", ticket.subject, body.subject))
        ticket.subject = body.subject

    if changes:
        await session.flush()
        await mutate(
            session,
            MutateRequest(
                entity=ticket,
                entity_type="ticket",
                action="updated",
                changes=changes,
                actor=actor,
                outbox_event_type="ticket.updated",
                idempotency_key=f"ticket.updated:{ticket.id}:{int(now.timestamp())}",
                outbox_payload={"ticket_id": str(ticket.id), "contact_id": str(ticket.contact_id)},
                contact_id=ticket.contact_id,
                ticket_id=ticket.id,
            ),
        )
    await commit_and_notify(session)
    await session.refresh(ticket)
    return await ticket_detail_dict(session, ticket)


@router.patch("/tickets/{ticket_id}/assign", response_model=TicketOut)
async def assign_ticket(
    ticket_id: uuid.UUID,
    body: TicketAssign,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket or ticket.workspace_id != workspace.id:
        raise HTTPException(404, "Ticket not found")
    old = ticket.assignee_user_id
    ticket.assignee_user_id = body.assignee_user_id
    if not ticket.first_assigned_at:
        ticket.first_assigned_at = datetime.now(UTC)
    await session.flush()
    await mutate(
        session,
        MutateRequest(
            entity=ticket,
            entity_type="ticket",
            action="assigned",
            changes=[FieldChange("assignee_user_id", old, body.assignee_user_id)],
            actor=actor,
            outbox_event_type="ticket.assigned",
            idempotency_key=f"ticket.assigned:{ticket.id}:{body.assignee_user_id}",
            outbox_payload={"ticket_id": str(ticket.id), "assignee": body.assignee_user_id},
            contact_id=ticket.contact_id,
            ticket_id=ticket.id,
        ),
    )
    await commit_and_notify(session)
    await session.refresh(ticket)
    return ticket


@router.get("/tickets/{ticket_id}/conversation", response_model=list[ConversationItemOut])
async def ticket_conversation(
    ticket_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket or ticket.workspace_id != workspace.id:
        raise HTTPException(404, "Ticket not found")

    channel = await channel_for_ticket(session, ticket)
    channel_kind = channel.kind if channel else None

    items: list[ConversationItemOut] = []

    msg_q = await session.execute(
        select(Message).where(Message.ticket_id == ticket_id).order_by(Message.occurred_at.asc())
    )
    for msg in msg_q.scalars().all():
        msg_channel = channel_kind
        if msg.channel_account_id:
            ca = await session.get(ChannelAccount, msg.channel_account_id)
            msg_channel = ca.kind if ca else channel_kind
        items.append(
            ConversationItemOut(
                id=msg.id,
                kind="message",
                direction=msg.direction,
                body=msg.body,
                occurred_at=msg.occurred_at,
                channel_kind=msg_channel,
                actor_type=(msg.raw or {}).get("actor_type"),
                actor_id=(msg.raw or {}).get("actor_id"),
                actor_name=(msg.raw or {}).get("actor_name"),
            )
        )

    # Message rows already appear above. Mutate also writes a timeline Interaction
    # with type "message" — skip those so inbound/outbound chat is not duplicated.
    skip_interaction_types = {"message", "received", "sent"}
    int_q = await session.execute(
        select(Interaction)
        .where(Interaction.ticket_id == ticket_id)
        .order_by(Interaction.occurred_at.asc())
    )
    for row in int_q.scalars().all():
        if (row.type or "").lower() in skip_interaction_types:
            continue
        items.append(
            ConversationItemOut(
                id=row.id,
                kind="interaction",
                direction=row.direction,
                body=row.body,
                occurred_at=row.occurred_at,
                actor_type=row.actor_type,
                actor_id=row.actor_id,
                channel_kind=channel_kind,
            )
        )

    call_q = await session.execute(
        select(Call).where(Call.ticket_id == ticket_id).order_by(Call.created_at.asc())
    )
    for call in call_q.scalars().all():
        transcript_status = None
        transcript_text = None
        t_q = await session.execute(select(Transcript).where(Transcript.call_id == call.id))
        transcript = t_q.scalar_one_or_none()
        if transcript:
            transcript_status = transcript.status
            transcript_text = transcript.text
        items.append(
            ConversationItemOut(
                id=call.id,
                kind="call",
                direction=call.direction,
                body=f"Call {call.status}"
                + (f" · {call.duration_seconds}s" if call.duration_seconds else ""),
                occurred_at=call.created_at,
                channel_kind="voice",
                call_status=call.status,
                transcript_status=transcript_status,
                transcript_text=transcript_text,
            )
        )

    items.sort(key=lambda item: item.occurred_at)
    return items


@router.get("/tickets/{ticket_id}/messages", response_model=list[MessageOut])
async def ticket_messages(
    ticket_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket or ticket.workspace_id != workspace.id:
        raise HTTPException(404, "Ticket not found")
    channel = await channel_for_ticket(session, ticket)
    channel_kind = channel.kind if channel else None

    q = await session.execute(
        select(Message).where(Message.ticket_id == ticket_id).order_by(Message.occurred_at.asc())
    )
    result = []
    for msg in q.scalars().all():
        result.append(
            MessageOut(
                id=msg.id,
                ticket_id=msg.ticket_id,
                contact_id=msg.contact_id,
                direction=msg.direction,
                body=msg.body,
                occurred_at=msg.occurred_at,
                channel_kind=channel_kind,
            )
        )
    return result


@router.post("/tickets/{ticket_id}/messages", response_model=MessageOut, status_code=201)
async def send_ticket_message(
    ticket_id: uuid.UUID,
    body: MessageCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    ticket = await session.get(Ticket, ticket_id)
    if not ticket or ticket.workspace_id != workspace.id:
        raise HTTPException(404, "Ticket not found")
    if not ticket.channel_account_id:
        raise HTTPException(400, "Ticket has no channel — cannot send a reply")

    channel = await channel_for_ticket(session, ticket)
    if not channel:
        raise HTTPException(400, "Channel account not found")

    now = datetime.now(UTC)
    message = Message(
        id=uuid.uuid4(),
        channel_account_id=channel.id,
        ticket_id=ticket.id,
        contact_id=ticket.contact_id,
        direction="outbound",
        body=body.body,
        occurred_at=now,
        raw={
            "actor_id": actor.id,
            "actor_type": actor.actor_type,
            "actor_name": actor.name or actor.email,
        },
    )
    session.add(message)
    await session.flush()

    contact = await session.get(Contact, ticket.contact_id)
    if channel.adapter_type == "gallabox":
        provider = GallaboxMessagingProvider.from_settings()
        if provider.configured():
            if not contact or not contact.primary_phone:
                raise HTTPException(400, "Contact has no phone number for WhatsApp")
            try:
                message.provider_message_id = await provider.send(
                    contact.primary_phone,
                    body.body,
                    name=contact.name,
                )
            except RuntimeError as exc:
                raise HTTPException(502, str(exc)) from exc
    elif channel.adapter_type == "google_group":
        provider = GoogleGroupEmailProvider.from_settings()
        if provider.configured():
            if not contact or not contact.primary_email:
                raise HTTPException(400, "Contact has no email address")
            subject = ticket.subject or "Support reply"
            token = f"[#TCK-{ticket.id}]"
            if token not in subject:
                subject = f"{subject} {token}"
            try:
                message.provider_message_id = await provider.send(
                    contact.primary_email, subject, body.body
                )
            except RuntimeError as exc:
                raise HTTPException(502, str(exc)) from exc

    touch_ticket_activity(ticket, now)

    if not ticket.first_response_at:
        ticket.first_response_at = now

    claimed = False
    if not ticket.assignee_user_id and actor.id:
        ticket.assignee_user_id = actor.id
        ticket.first_assigned_at = ticket.first_assigned_at or now
        claimed = True

    await mutate(
        session,
        MutateRequest(
            entity=message,
            entity_type="message",
            action="sent",
            changes=[FieldChange("body", None, body.body)],
            actor=actor,
            workspace_id=workspace.id,
            outbox_event_type="message.sent",
            idempotency_key=f"message.sent:{message.id}",
            outbox_payload={
                "message_id": str(message.id),
                "ticket_id": str(ticket.id),
                "contact_id": str(ticket.contact_id),
                "channel_kind": channel.kind,
            },
            timeline=True,
            timeline_type="message",
            timeline_body=body.body,
            contact_id=ticket.contact_id,
            ticket_id=ticket.id,
        ),
    )
    await commit_and_notify(session)
    publish_inbox_event(
        {
            "type": "message.sent",
            "ticket_id": str(ticket.id),
            "channel_kind": channel.kind,
            "assignee_user_id": ticket.assignee_user_id,
            "actor_id": actor.id,
        }
    )
    if claimed:
        publish_inbox_event(
            {"type": "ticket.claimed", "ticket_id": str(ticket.id), "actor_id": actor.id}
        )
    return MessageOut(
        id=message.id,
        ticket_id=message.ticket_id,
        contact_id=message.contact_id,
        direction=message.direction,
        body=message.body,
        occurred_at=message.occurred_at,
        channel_kind=channel.kind,
    )
