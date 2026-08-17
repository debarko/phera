"""Poll every DB-configured IMAP/SMTP email channel and ingest new mail into tickets.

One `ChannelAccount` (kind="email", adapter_type="imap_smtp") + its linked `Connector`
== one independent mailbox. A deployment can register any number of these (Gmail, Yahoo,
self-hosted, ...) and each is polled on its own watermark/interval — see
`phera/src/phera/modules/connectors/imap_smtp.py` for the actual IMAP/SMTP protocol code.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import ChannelAccount, Connector, EmailPollState, Workspace
from phera.modules.connectors.google_group import parse_rfc822, strip_quoted_reply
from phera.modules.connectors.imap_smtp import ImapSmtpEmailProvider
from phera.modules.tickets.inbound import ingest_inbound_message

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 20


async def poll_all_email_accounts(session: AsyncSession, workspace: Workspace) -> int:
    q = await session.execute(
        select(ChannelAccount).where(
            ChannelAccount.workspace_id == workspace.id,
            ChannelAccount.kind == "email",
            ChannelAccount.adapter_type == "imap_smtp",
            ChannelAccount.is_active.is_(True),
            ChannelAccount.connector_id.is_not(None),
        )
    )
    total = 0
    for account in q.scalars().all():
        total += await poll_one_email_account(session, workspace, account)
    return total


async def poll_one_email_account(
    session: AsyncSession, workspace: Workspace, account: ChannelAccount
) -> int:
    connector = await session.get(Connector, account.connector_id)
    if not connector or not connector.is_active:
        return 0

    credentials = connector.credentials or {}
    interval = int(credentials.get("poll_interval_seconds") or DEFAULT_POLL_INTERVAL_SECONDS)

    state = await session.get(EmailPollState, account.id)
    now = datetime.now(UTC)
    if state is None:
        state = EmailPollState(channel_account_id=account.id, last_uid=0)
        session.add(state)
        try:
            # Nested (SAVEPOINT) so a concurrent-insert failure only unwinds this insert,
            # not whatever else the worker loop's shared session already did this tick
            # (outbox dispatch, delayed wakes, ...).
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            # Another worker inserted this account's poll-state row concurrently (first
            # poll ever for this mailbox) — it owns this cycle, we back off.
            return 0
    elif state.status == "running":
        return 0
    else:
        last_polled = state.last_polled_at
        if last_polled and last_polled.tzinfo is None:
            last_polled = last_polled.replace(tzinfo=UTC)  # SQLite round-trips as naive
        if last_polled and (now - last_polled) < timedelta(seconds=interval):
            return 0

    # Atomic claim: two worker processes can both read status != "running" before either
    # commits. Only a conditional UPDATE — committed immediately, before any IMAP I/O — can
    # guarantee just one of them proceeds; the earlier flush()-only version left the
    # "running" status invisible to other transactions until the whole poll finished.
    claim = await session.execute(
        update(EmailPollState)
        .where(
            EmailPollState.channel_account_id == account.id,
            EmailPollState.status != "running",
        )
        .values(status="running")
    )
    if claim.rowcount == 0:
        return 0  # lost the race to another worker between the read above and this claim
    await session.commit()

    provider = ImapSmtpEmailProvider.from_connector(connector)
    if not provider.configured():
        state.status = "error"
        state.last_error = "Connector is missing required IMAP/SMTP fields"
        state.last_polled_at = now
        await session.commit()
        return 0

    since_uid = state.last_uid if state.last_uid > 0 else None
    try:
        raw_messages, new_watermark = await provider.fetch_new_messages(since_uid)
    except Exception as exc:
        logger.exception("IMAP poll failed for channel_account_id=%s", account.id)
        state.status = "error"
        state.last_error = str(exc)
        state.last_polled_at = now
        await session.commit()
        return 0

    own_address = credentials.get("from_address") or credentials.get("username") or ""
    own_address = own_address.strip().lower()
    ingested = 0
    for raw_msg in raw_messages:
        parsed = parse_rfc822(raw_msg["raw"])
        from_addr = (parsed.get("from") or "").strip().lower()
        if own_address and from_addr == own_address:
            continue  # our own send-as copy, looped back — must not re-ingest as new inbound

        message_id = parsed.get("message_id")
        references = parsed.get("references")
        if isinstance(references, str):
            references = [item for item in references.split() if item]

        inbound = {
            "channel_kind": "email",
            "adapter_type": "imap_smtp",
            "address_hint": parsed.get("to") or account.address,
            "body": strip_quoted_reply(parsed.get("text") or ""),
            "subject": parsed.get("subject"),
            "contact_name": parsed.get("name") or (from_addr.split("@")[0] if from_addr else None),
            "contact_email": from_addr or None,
            "contact_phone": None,
            "provider_message_id": message_id,
            "thread_keys": {
                "rfc_message_id": message_id,
                "in_reply_to": parsed.get("in_reply_to"),
                "references": references or [],
            },
            "occurred_at": now,
            "raw": {
                "source": "imap",
                "uid": raw_msg["uid"],
                # base64: Message.raw is JSONB and the source bytes may not be valid UTF-8
                # (parse_rfc822 handles that per-part already); this keeps the archived
                # original byte-for-byte recoverable regardless of the original charset.
                "raw_rfc822_b64": base64.b64encode(raw_msg["raw"]).decode("ascii"),
            },
        }
        try:
            result = await ingest_inbound_message(session, workspace, inbound)
            if not result.get("duplicate"):
                ingested += 1
        except ValueError:
            logger.exception("IMAP poll: could not ingest uid=%s", raw_msg["uid"])

    state.last_uid = new_watermark
    state.status = "idle"
    state.last_error = None
    state.last_polled_at = datetime.now(UTC)
    await session.commit()
    return ingested
