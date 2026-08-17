from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from phera.db.models import Message, Ticket
from phera.modules.connectors.imap_smtp import ImapSmtpEmailProvider
from tests.support import factories

pytestmark = pytest.mark.integration

AGENT = {
    "X-Actor-Id": "agent-1",
    "X-Actor-Email": "agent@test.com",
    "X-Actor-Unrestricted": "true",
}


async def _setup_ticket_with_history(db_session, workspace_bundle):
    conn = factories.connector(
        workspace_bundle.workspace.id,
        credentials={"imap_host": "imap.example.com", "smtp_host": "smtp.example.com"},
    )
    db_session.add(conn)
    await db_session.flush()
    account = factories.channel_account(
        workspace_bundle.workspace.id,
        kind="email",
        adapter_type="imap_smtp",
        address="support@example.com",
        connector_id=conn.id,
    )
    contact = factories.contact(workspace_bundle.workspace.id, email="patient@example.com")
    db_session.add_all([account, contact])
    await db_session.flush()

    ticket = Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace_bundle.workspace.id,
        contact_id=contact.id,
        channel_account_id=account.id,
        subject="Need reports",
        status="open",
        last_activity_at=datetime.now(UTC),
        status_entered_at=datetime.now(UTC),
    )
    db_session.add(ticket)
    await db_session.flush()

    inbound_message = Message(
        id=uuid.uuid4(),
        channel_account_id=account.id,
        ticket_id=ticket.id,
        contact_id=contact.id,
        direction="inbound",
        provider_message_id="<inbound-1@example.com>",
        body="Please send my reports.",
        occurred_at=datetime.now(UTC),
    )
    db_session.add(inbound_message)
    await db_session.commit()
    return ticket, contact


@pytest.mark.asyncio
async def test_reply_threads_off_prior_inbound_message(client, db_session, workspace_bundle):
    ticket, contact = await _setup_ticket_with_history(db_session, workspace_bundle)

    captured = {}

    async def fake_send(to, subject, body, **kwargs):
        captured["to"] = to
        captured["in_reply_to"] = kwargs.get("in_reply_to")
        captured["references"] = kwargs.get("references")
        return "<reply-1@example.com>"

    class FakeProvider:
        def configured(self):
            return True

        send = staticmethod(fake_send)

    def _fake_from_connector(connector):
        return FakeProvider()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ImapSmtpEmailProvider, "from_connector", _fake_from_connector)
        resp = await client.post(
            f"/v1/tickets/{ticket.id}/messages",
            headers=AGENT,
            json={"body": "Here are your reports."},
        )

    assert resp.status_code == 201
    assert captured["in_reply_to"] == "<inbound-1@example.com>"
    assert captured["references"] == ["<inbound-1@example.com>"]

    body = resp.json()
    assert body["direction"] == "outbound"


@pytest.mark.asyncio
async def test_second_reply_extends_the_references_chain(client, db_session, workspace_bundle):
    ticket, contact = await _setup_ticket_with_history(db_session, workspace_bundle)

    calls = []

    async def fake_send(to, subject, body, **kwargs):
        calls.append(
            {"in_reply_to": kwargs.get("in_reply_to"), "references": kwargs.get("references")}
        )
        return f"<reply-{len(calls)}@example.com>"

    class FakeProvider:
        def configured(self):
            return True

        send = staticmethod(fake_send)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(ImapSmtpEmailProvider, "from_connector", lambda connector: FakeProvider())

        first = await client.post(
            f"/v1/tickets/{ticket.id}/messages",
            headers=AGENT,
            json={"body": "First reply."},
        )
        assert first.status_code == 201

        second = await client.post(
            f"/v1/tickets/{ticket.id}/messages",
            headers=AGENT,
            json={"body": "Second reply."},
        )
        assert second.status_code == 201

    assert calls[0]["references"] == ["<inbound-1@example.com>"]
    assert calls[1]["in_reply_to"] == "<reply-1@example.com>"
    assert calls[1]["references"] == ["<inbound-1@example.com>", "<reply-1@example.com>"]
