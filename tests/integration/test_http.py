from __future__ import annotations

import pytest

from tests.support import factories

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_public_form_submit_creates_deal(client, workspace_bundle):
    resp = await client.post(
        f"/public/forms/{workspace_bundle.form_a.slug}/submit",
        json={"data": {"email": "public@test.com", "phone": "+916666666666", "name": "Public"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["contact_id"]
    assert body["deal_id"]
    assert body["deal_action"] == "created"


@pytest.mark.asyncio
async def test_hooks_http_accepts_connector_without_staff_actor(client):
    """Public hooks must not require X-Actor-Id."""
    resp = await client.post(
        "/hooks/superhealth/service_event",
        json={
            "event_id": "evt-http-1",
            "email": "hook-http@test.com",
            "phone": "+915555555555",
            "name": "Hook HTTP",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["contact_id"]


@pytest.mark.asyncio
async def test_gallabox_webhook_creates_ticket(client, db_session, workspace_bundle):
    db_session.add(
        factories.channel_account(
            workspace_bundle.workspace.id,
            kind="messaging",
            adapter_type="gallabox",
            address="+919876543210",
        )
    )
    await db_session.flush()

    first = await client.post(
        "/hooks/gallabox/whatsapp",
        json={
            "event": "Message.received",
            "data": {
                "id": "gb-msg-1",
                "contact": {"name": "Riya", "phone": "919811112222"},
                "message": {"whatsapp": {"type": "text", "text": {"body": "Need an IVF slot"}}},
            },
        },
    )
    assert first.status_code == 200
    body = first.json()
    assert body["created_ticket"] is True
    assert body["ticket_id"]
    assert body["contact_id"]

    follow = await client.post(
        "/hooks/gallabox/whatsapp",
        json={
            "event": "Message.received",
            "data": {
                "id": "gb-msg-2",
                "contact": {"name": "Riya", "phone": "919811112222"},
                "message": {"whatsapp": {"type": "text", "text": {"body": "Still waiting"}}},
            },
        },
    )
    assert follow.status_code == 200
    assert follow.json()["created_ticket"] is False
    assert follow.json()["ticket_id"] == body["ticket_id"]

    conv = await client.get(
        f"/v1/tickets/{body['ticket_id']}/conversation",
        headers={"X-Actor-Id": "agent-1", "X-Actor-Email": "agent@test.com", "X-Actor-Unrestricted": "true"},
    )
    assert conv.status_code == 200
    items = conv.json()
    bodies = [row["body"] for row in items]
    assert bodies == ["Need an IVF slot", "Still waiting"]
    assert [row["kind"] for row in items] == ["message", "message"]


@pytest.mark.asyncio
async def test_google_group_webhook_creates_ticket(client, db_session, workspace_bundle):
    db_session.add(
        factories.channel_account(
            workspace_bundle.workspace.id,
            kind="email",
            adapter_type="google_group",
            address="support@example.com",
        )
    )
    await db_session.flush()

    resp = await client.post(
        "/hooks/google_group/email",
        json={
            "from": "patient@example.com",
            "to": "support@example.com",
            "subject": "Lab report",
            "text": "Please share my reports.",
            "message_id": "<mail-1@example.com>",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created_ticket"] is True
    assert body["ticket_id"]


@pytest.mark.asyncio
async def test_public_form_submit_unknown_returns_404(client):
    resp = await client.post("/public/forms/does-not-exist/submit", json={"data": {"email": "a@b.com"}})
    assert resp.status_code == 404
