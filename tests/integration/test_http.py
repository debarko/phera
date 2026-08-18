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

    # The actual cross-number bypass: a payload addressed to Number A, correctly signed
    # with Number B's *valid* secret, must NOT be accepted for Number A. Verification is
    # bound to the connector resolved from the payload's own target number, not "any
    # active secret in the workspace".
    body_d = _payload_for("+911111111111", "msg-a-3")
    sig_using_b_secret = hmac.new(b"secret-b", body_d, hashlib.sha256).hexdigest()
    resp_cross = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body_d,
        headers={"content-type": "application/json", "x-gallabox-signature": sig_using_b_secret},
    )
    assert resp_cross.status_code == 401

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_gallabox_webhook_resolved_channel_never_falls_back_to_other_secrets(
    client, db_session, workspace_bundle, monkeypatch
):
    """A resolved channel with no secret of its own is open (matches the documented
    local-dev "no secret configured = unsigned" policy) — but a *different*, secured
    channel's own protection must not be weakened by that. Regression guard for the
    fallback-truthiness fix: a resolved-but-unsecreted channel must not silently borrow
    validation against a workspace-wide secret pool."""
    import hashlib
    import hmac
    import json

    from cryptography.fernet import Fernet

    from phera.security import crypto
    from phera.settings import Settings, get_settings

    settings = Settings(credentials_encryption_key=Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    get_settings.cache_clear()

    conn_open = factories.connector(
        workspace_bundle.workspace.id, type="gallabox", name="Open Number"
    )  # no secrets_encrypted at all
    conn_secured = factories.connector(
        workspace_bundle.workspace.id,
        type="gallabox",
        name="Secured Number",
        secrets_encrypted=crypto.encrypt_secrets({"webhook_secret": "real-secret"}),
    )
    db_session.add_all([conn_open, conn_secured])
    await db_session.flush()
    db_session.add_all(
        [
            factories.channel_account(
                workspace_bundle.workspace.id,
                kind="messaging",
                adapter_type="gallabox",
                address="+911111111111",
                connector_id=conn_open.id,
            ),
            factories.channel_account(
                workspace_bundle.workspace.id,
                kind="messaging",
                adapter_type="gallabox",
                address="+922222222222",
                connector_id=conn_secured.id,
            ),
        ]
    )
    await db_session.commit()

    def _payload_for(number: str, msg_id: str) -> bytes:
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

    # Open Number has no secret of its own — an unsigned/garbage-signed request for it
    # is accepted, matching today's "no secret configured" policy scoped to that channel.
    body_open = _payload_for("+911111111111", "open-1")
    resp_open = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body_open,
        headers={"content-type": "application/json", "x-gallabox-signature": "garbage"},
    )
    assert resp_open.status_code == 200

    # Secured Number's own protection is untouched by Open Number's lack of a secret —
    # a garbage signature for it is still rejected.
    body_secured = _payload_for("+922222222222", "secured-1")
    resp_secured_bad = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body_secured,
        headers={"content-type": "application/json", "x-gallabox-signature": "garbage"},
    )
    assert resp_secured_bad.status_code == 401

    sig = hmac.new(b"real-secret", body_secured, hashlib.sha256).hexdigest()
    resp_secured_ok = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body_secured,
        headers={"content-type": "application/json", "x-gallabox-signature": sig},
    )
    assert resp_secured_ok.status_code == 200

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_gallabox_webhook_env_fallback_used_when_connectors_have_blank_secret(
    client, db_session, workspace_bundle, monkeypatch
):
    """Regression guard: candidates-or-env-fallback must filter empty webhook_secret
    values BEFORE deciding whether the env secret applies, not after — otherwise a
    workspace where every connector's secrets_encrypted is set but webhook_secret is
    blank would silently ignore a real GALLABOX_WEBHOOK_SECRET env value."""
    import hashlib
    import hmac
    import json

    from cryptography.fernet import Fernet

    from phera.security import crypto
    from phera.settings import Settings, get_settings

    settings = Settings(
        credentials_encryption_key=Fernet.generate_key().decode(),
        gallabox_webhook_secret="env-secret",
    )
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    import phera.api.routes.hooks as hooks_module

    monkeypatch.setattr(hooks_module, "get_settings", lambda: settings)
    get_settings.cache_clear()

    conn = factories.connector(
        workspace_bundle.workspace.id,
        type="gallabox",
        name="Blank Secret",
        secrets_encrypted=crypto.encrypt_secrets({"webhook_secret": ""}),
    )
    db_session.add(conn)
    await db_session.commit()

    # An unresolvable payload (status event) exercises the broad fallback path directly.
    body = json.dumps({"event": "Message.WA.status.received", "data": {}}).encode()
    sig = hmac.new(b"env-secret", body, hashlib.sha256).hexdigest()
    resp_ok = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body,
        headers={"content-type": "application/json", "x-gallabox-signature": sig},
    )
    assert resp_ok.status_code == 200

    resp_bad = await client.post(
        "/hooks/gallabox/whatsapp",
        content=body,
        headers={"content-type": "application/json", "x-gallabox-signature": "garbage"},
    )
    assert resp_bad.status_code == 401

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
async def test_webhook_rejects_non_utf8_body_with_400_not_500(client):
    """Regression guard: json.loads(raw.decode()) let UnicodeDecodeError (not a subclass
    of JSONDecodeError) escape uncaught, turning a malformed body into a 500 instead of
    the intended 400. json.loads accepts bytes directly and both failure modes now raise
    a ValueError subclass that gets caught."""
    resp = await client.post(
        "/hooks/gallabox/whatsapp",
        content=b"\xff\xfe not valid utf-8 or json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_public_form_submit_unknown_returns_404(client):
    resp = await client.post(
        "/public/forms/does-not-exist/submit", json={"data": {"email": "a@b.com"}}
    )
    assert resp.status_code == 404
