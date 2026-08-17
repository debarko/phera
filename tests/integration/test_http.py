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
        headers={
            "X-Actor-Id": "agent-1",
            "X-Actor-Email": "agent@test.com",
            "X-Actor-Unrestricted": "true",
        },
    )
    assert conv.status_code == 200
    items = conv.json()
    bodies = [row["body"] for row in items]
    assert bodies == ["Need an IVF slot", "Still waiting"]
    assert [row["kind"] for row in items] == ["message", "message"]


@pytest.mark.asyncio
async def test_gallabox_webhook_verifies_per_connector_secret(
    client, db_session, workspace_bundle, monkeypatch
):
    import hashlib
    import hmac

    from cryptography.fernet import Fernet

    from phera.security import crypto
    from phera.settings import Settings, get_settings

    settings = Settings(credentials_encryption_key=Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    get_settings.cache_clear()

    conn_a = factories.connector(
        workspace_bundle.workspace.id,
        type="gallabox",
        name="Number A",
        secrets_encrypted=crypto.encrypt_secrets({"webhook_secret": "secret-a"}),
    )
    conn_b = factories.connector(
        workspace_bundle.workspace.id,
        type="gallabox",
        name="Number B",
        secrets_encrypted=crypto.encrypt_secrets({"webhook_secret": "secret-b"}),
    )
    db_session.add_all([conn_a, conn_b])
    await db_session.flush()
    db_session.add_all(
        [
            factories.channel_account(
                workspace_bundle.workspace.id,
                kind="messaging",
                adapter_type="gallabox",
                address="+911111111111",
                connector_id=conn_a.id,
            ),
            factories.channel_account(
                workspace_bundle.workspace.id,
                kind="messaging",
                adapter_type="gallabox",
                address="+922222222222",
                connector_id=conn_b.id,
            ),
        ]
    )
    await db_session.commit()

    def _payload_for(number: str, msg_id: str) -> bytes:
        import json

        return json.dumps(
            {
                "event": "Message.received",
                "data": {
                    "id": msg_id,
                    "whatsappNumber": number,
                    "contact": {"name": "Patient", "phone": "919000000000"},
                    "message": {"whatsapp": {"type": "text", "text": {"body": "hi"}}},
                },
            }
        ).encode()

    body_a = _payload_for("+911111111111", "msg-a-1")
    sig_a = hmac.new(b"secret-a", body_a, hashlib.sha256).hexdigest()
    resp_a = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body_a,
        headers={"content-type": "application/json", "x-gallabox-signature": sig_a},
    )
    assert resp_a.status_code == 200
    assert resp_a.json()["created_ticket"] is True

    body_b = _payload_for("+922222222222", "msg-b-1")
    sig_b = hmac.new(b"secret-b", body_b, hashlib.sha256).hexdigest()
    resp_b = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body_b,
        headers={"content-type": "application/json", "x-gallabox-signature": sig_b},
    )
    assert resp_b.status_code == 200
    assert resp_b.json()["created_ticket"] is True

    body_c = _payload_for("+911111111111", "msg-a-2")
    resp_wrong = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body_c,
        headers={"content-type": "application/json", "x-gallabox-signature": "not-a-real-sig"},
    )
    assert resp_wrong.status_code == 401

    get_settings.cache_clear()


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
    resp = await client.post(
        "/public/forms/does-not-exist/submit", json={"data": {"email": "a@b.com"}}
    )
    assert resp.status_code == 404
