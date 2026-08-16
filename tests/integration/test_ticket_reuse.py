from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from phera.authz.service import ensure_user_stub
from phera.db.models import OwnershipProfile, Ticket
from phera.modules.tickets.inbound import ingest_inbound_message
from phera.modules.tickets.reuse_policy import SUPPORT_AGENTS_FLAG, TICKET_REUSE_FLAG
from tests.support import factories

pytestmark = pytest.mark.integration

ADMIN = {
    "X-Actor-Id": "reuse-admin",
    "X-Actor-Email": "reuse-admin@test.com",
    "X-Actor-Unrestricted": "true",
    "X-Actor-Roles": "support_admin",
}


def _inbound(phone: str, body: str, msg_id: str) -> dict:
    return {
        "channel_kind": "messaging",
        "adapter_type": "gallabox",
        "contact_name": "Reuse Patient",
        "contact_phone": phone,
        "provider_message_id": msg_id,
        "body": body,
        "thread_keys": {},
        "raw": {},
    }


async def _ingest(db_session, ws, phone: str, body: str, msg_id: str) -> dict:
    return await ingest_inbound_message(db_session, ws, _inbound(phone, body, msg_id))


async def _ticket(db_session, result: dict) -> Ticket:
    return await db_session.get(Ticket, UUID(str(result["ticket_id"])))


async def _set_reuse(db_session, workspace_id, **fields) -> None:
    profile = await db_session.get(OwnershipProfile, workspace_id)
    flags = dict(profile.flags or {})
    current = dict(flags.get(TICKET_REUSE_FLAG) or {})
    current.update(fields)
    flags[TICKET_REUSE_FLAG] = current
    profile.flags = flags
    await db_session.commit()


@pytest.mark.asyncio
async def test_resolved_ticket_reopens_within_window(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    db_session.add(factories.channel_account(ws.id))
    await db_session.flush()

    first = await _ingest(db_session, ws, "+919700000001", "Need a slot", "reuse-1")
    ticket = await _ticket(db_session, first)
    ticket.status = "resolved"
    ticket.resolved_at = datetime.now(UTC)
    await db_session.commit()

    second = await _ingest(db_session, ws, "+919700000001", "Still need a slot", "reuse-2")
    assert second["created_ticket"] is False
    assert second["ticket_id"] == first["ticket_id"]
    ticket = await _ticket(db_session, first)
    assert ticket.status == "open"
    assert ticket.resolved_at is None


@pytest.mark.asyncio
async def test_closed_stays_closed_when_reopen_disabled(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    db_session.add(factories.channel_account(ws.id))
    await db_session.flush()
    await _set_reuse(db_session, ws.id, reopen_closed=False)

    first = await _ingest(db_session, ws, "+919700000002", "Hello", "reuse-3")
    ticket = await _ticket(db_session, first)
    ticket.status = "closed"
    ticket.closed_at = datetime.now(UTC)
    await db_session.commit()

    second = await _ingest(db_session, ws, "+919700000002", "Again", "reuse-4")
    assert second["created_ticket"] is True
    assert second["ticket_id"] != first["ticket_id"]
    closed = await _ticket(db_session, first)
    assert closed.status == "closed"


@pytest.mark.asyncio
async def test_outside_window_creates_new_ticket(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    db_session.add(factories.channel_account(ws.id))
    await db_session.flush()
    await _set_reuse(db_session, ws.id, window_seconds=60)

    first = await _ingest(db_session, ws, "+919700000003", "Old", "reuse-5")
    ticket = await _ticket(db_session, first)
    ticket.status = "resolved"
    old = datetime.now(UTC) - timedelta(hours=2)
    ticket.resolved_at = old
    ticket.last_activity_at = old
    ticket.updated_at = old
    await db_session.commit()

    second = await _ingest(db_session, ws, "+919700000003", "Much later", "reuse-6")
    assert second["created_ticket"] is True
    assert second["ticket_id"] != first["ticket_id"]


@pytest.mark.asyncio
async def test_reopen_can_return_ticket_to_queue(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    actor = factories.staff_actor(user_id="reuse-agent", unrestricted=True)
    await ensure_user_stub(db_session, actor, ws.id)
    db_session.add(factories.channel_account(ws.id))
    await db_session.flush()
    await _set_reuse(db_session, ws.id, on_reopen_assignee="queue")

    first = await _ingest(db_session, ws, "+919700000004", "Claimed", "reuse-7")
    ticket = await _ticket(db_session, first)
    ticket.assignee_user_id = actor.id
    ticket.status = "resolved"
    ticket.resolved_at = datetime.now(UTC)
    await db_session.commit()

    await _ingest(db_session, ws, "+919700000004", "Back again", "reuse-8")
    ticket = await _ticket(db_session, first)
    assert ticket.status == "open"
    assert ticket.assignee_user_id is None


@pytest.mark.asyncio
async def test_reopen_keeps_assignee_by_default(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    actor = factories.staff_actor(user_id="reuse-keep", unrestricted=True)
    await ensure_user_stub(db_session, actor, ws.id)
    db_session.add(factories.channel_account(ws.id))
    await db_session.flush()

    first = await _ingest(db_session, ws, "+919700000005", "Mine", "reuse-9")
    ticket = await _ticket(db_session, first)
    ticket.assignee_user_id = actor.id
    ticket.status = "resolved"
    ticket.resolved_at = datetime.now(UTC)
    await db_session.commit()

    await _ingest(db_session, ws, "+919700000005", "Ping", "reuse-10")
    ticket = await _ticket(db_session, first)
    assert ticket.assignee_user_id == actor.id
    assert ticket.status == "open"


@pytest.mark.asyncio
async def test_keep_assignee_returns_former_agent_to_queue(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    actor = factories.staff_actor(user_id="reuse-former", unrestricted=True)
    await ensure_user_stub(db_session, actor, ws.id)
    db_session.add(factories.channel_account(ws.id))
    await db_session.flush()

    profile = await db_session.get(OwnershipProfile, ws.id)
    flags = dict(profile.flags or {})
    flags[SUPPORT_AGENTS_FLAG] = [{"user_id": "someone-else", "access": "agent"}]
    profile.flags = flags
    await db_session.commit()

    first = await _ingest(db_session, ws, "+919700000007", "Old owner", "reuse-13")
    ticket = await _ticket(db_session, first)
    ticket.assignee_user_id = actor.id
    ticket.status = "resolved"
    ticket.resolved_at = datetime.now(UTC)
    await db_session.commit()

    await _ingest(db_session, ws, "+919700000007", "Ping", "reuse-14")
    ticket = await _ticket(db_session, first)
    assert ticket.status == "open"
    assert ticket.assignee_user_id is None


@pytest.mark.asyncio
async def test_channel_override_window(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    db_session.add(factories.channel_account(ws.id))
    await db_session.flush()
    await _set_reuse(
        db_session,
        ws.id,
        window_seconds=7 * 24 * 3600,
        channels={"messaging": {"window_seconds": 60}},
    )

    first = await _ingest(db_session, ws, "+919700000006", "Short", "reuse-11")
    ticket = await _ticket(db_session, first)
    old = datetime.now(UTC) - timedelta(hours=2)
    ticket.status = "resolved"
    ticket.resolved_at = old
    ticket.last_activity_at = old
    ticket.updated_at = old
    await db_session.commit()

    second = await _ingest(db_session, ws, "+919700000006", "Expired chat", "reuse-12")
    assert second["created_ticket"] is True


@pytest.mark.asyncio
async def test_support_settings_exposes_and_saves_ticket_reuse(client, workspace_bundle):
    got = await client.get("/v1/support/settings", headers=ADMIN)
    assert got.status_code == 200
    reuse = got.json()["ticket_reuse"]
    assert reuse["window_seconds"] == 7 * 24 * 3600
    assert reuse["reopen_resolved"] is True
    assert reuse["on_reopen_assignee"] == "keep"

    saved = await client.patch(
        "/v1/support/settings",
        headers=ADMIN,
        json={
            "ticket_reuse": {
                "window_seconds": 10 * 3600,
                "reopen_closed": False,
                "on_reopen_assignee": "queue",
                "channels": {"email": {"window_seconds": 86400}},
            }
        },
    )
    assert saved.status_code == 200
    body = saved.json()["ticket_reuse"]
    assert body["window_seconds"] == 10 * 3600
    assert body["reopen_closed"] is False
    assert body["on_reopen_assignee"] == "queue"
    assert body["channels"]["email"]["window_seconds"] == 86400
