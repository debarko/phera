from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from phera.db.models import ChannelAccount, Connector
from phera.security.crypto import decrypt_secrets
from phera.settings import get_settings
from tests.support import factories

pytestmark = pytest.mark.integration

ADMIN = {
    "X-Actor-Id": "conn-admin",
    "X-Actor-Email": "conn-admin@test.com",
    "X-Actor-Roles": "support_admin",
}
AGENT = {
    "X-Actor-Id": "conn-agent",
    "X-Actor-Email": "conn-agent@test.com",
    "X-Actor-Roles": "support_agent",
}


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_list_connectors_requires_permission(client):
    resp = await client.get("/v1/connectors", headers=AGENT)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_connector_requires_permission(client):
    resp = await client.post(
        "/v1/connectors",
        headers=AGENT,
        json={"type": "email_imap_smtp", "name": "x", "credentials": {}},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_and_list_connector_hides_secrets(client, db_session, workspace_bundle):
    resp = await client.post(
        "/v1/connectors",
        headers=ADMIN,
        json={
            "type": "email_imap_smtp",
            "name": "Support Gmail",
            "credentials": {
                "imap_host": "imap.gmail.com",
                "smtp_host": "smtp.gmail.com",
                "username": "support@example.com",
            },
            "secrets": {"password": "app-password-123"},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_secrets"] is True
    assert "secrets" not in body
    assert "secrets_encrypted" not in body
    assert "password" not in str(body["credentials"])

    listed = await client.get("/v1/connectors", headers=ADMIN)
    assert listed.status_code == 200
    rows = listed.json()
    assert any(r["id"] == body["id"] and r["has_secrets"] for r in rows)

    q = await db_session.execute(select(Connector).where(Connector.id == uuid.UUID(body["id"])))
    row = q.scalar_one()
    assert row.secrets_encrypted is not None
    assert decrypt_secrets(row.secrets_encrypted) == {"password": "app-password-123"}


@pytest.mark.asyncio
async def test_list_connectors_filters_by_type(client, db_session, workspace_bundle):
    db_session.add(
        factories.connector(workspace_bundle.workspace.id, type="moengage", name="MoEngage")
    )
    await db_session.commit()

    await client.post(
        "/v1/connectors",
        headers=ADMIN,
        json={
            "type": "email_imap_smtp",
            "name": "Support Gmail",
            "credentials": {},
            "secrets": {"password": "x"},
        },
    )

    scoped = await client.get("/v1/connectors?type=email_imap_smtp", headers=ADMIN)
    names = [row["name"] for row in scoped.json()]
    assert names == ["Support Gmail"]

    unscoped = await client.get("/v1/connectors", headers=ADMIN)
    assert {row["name"] for row in unscoped.json()} == {"Support Gmail", "MoEngage"}


@pytest.mark.asyncio
async def test_patch_without_secrets_keeps_existing_password(client, db_session, workspace_bundle):
    create = await client.post(
        "/v1/connectors",
        headers=ADMIN,
        json={
            "type": "email_imap_smtp",
            "name": "Yahoo",
            "credentials": {},
            "secrets": {"password": "orig-pass"},
        },
    )
    connector_id = create.json()["id"]

    patch = await client.patch(
        f"/v1/connectors/{connector_id}",
        headers=ADMIN,
        json={"name": "Yahoo Renamed"},
    )
    assert patch.status_code == 200
    assert patch.json()["name"] == "Yahoo Renamed"
    assert patch.json()["has_secrets"] is True

    q = await db_session.execute(select(Connector).where(Connector.id == uuid.UUID(connector_id)))
    row = q.scalar_one()
    assert decrypt_secrets(row.secrets_encrypted) == {"password": "orig-pass"}


@pytest.mark.asyncio
async def test_patch_with_blank_password_keeps_existing(client, db_session, workspace_bundle):
    create = await client.post(
        "/v1/connectors",
        headers=ADMIN,
        json={
            "type": "email_imap_smtp",
            "name": "Yahoo",
            "credentials": {},
            "secrets": {"password": "orig-pass"},
        },
    )
    connector_id = create.json()["id"]

    patch = await client.patch(
        f"/v1/connectors/{connector_id}",
        headers=ADMIN,
        json={"secrets": {"password": ""}},
    )
    assert patch.status_code == 200

    q = await db_session.execute(select(Connector).where(Connector.id == uuid.UUID(connector_id)))
    row = q.scalar_one()
    assert decrypt_secrets(row.secrets_encrypted) == {"password": "orig-pass"}


@pytest.mark.asyncio
async def test_patch_with_new_password_replaces_secret(client, db_session, workspace_bundle):
    create = await client.post(
        "/v1/connectors",
        headers=ADMIN,
        json={
            "type": "email_imap_smtp",
            "name": "Yahoo",
            "credentials": {},
            "secrets": {"password": "orig-pass"},
        },
    )
    connector_id = create.json()["id"]

    patch = await client.patch(
        f"/v1/connectors/{connector_id}",
        headers=ADMIN,
        json={"secrets": {"password": "new-pass"}},
    )
    assert patch.status_code == 200

    q = await db_session.execute(select(Connector).where(Connector.id == uuid.UUID(connector_id)))
    row = q.scalar_one()
    assert decrypt_secrets(row.secrets_encrypted) == {"password": "new-pass"}


@pytest.mark.asyncio
async def test_delete_blocked_when_channel_account_active(client, db_session, workspace_bundle):
    conn = factories.connector(workspace_bundle.workspace.id)
    db_session.add(conn)
    await db_session.flush()
    db_session.add(
        factories.channel_account(
            workspace_bundle.workspace.id,
            kind="email",
            adapter_type="imap_smtp",
            address="support@example.com",
            connector_id=conn.id,
        )
    )
    await db_session.commit()

    resp = await client.delete(f"/v1/connectors/{conn.id}", headers=ADMIN)
    assert resp.status_code == 409

    forced = await client.delete(f"/v1/connectors/{conn.id}?force=true", headers=ADMIN)
    assert forced.status_code == 204

    q = await db_session.execute(
        select(ChannelAccount).where(ChannelAccount.connector_id == conn.id)
    )
    account = q.scalar_one()
    assert account.is_active is False

    q2 = await db_session.execute(select(Connector).where(Connector.id == conn.id))
    deactivated = q2.scalar_one()
    assert deactivated.is_active is False


@pytest.mark.asyncio
async def test_create_email_channel_combined(client, db_session, workspace_bundle):
    resp = await client.post(
        "/v1/email-channels",
        headers=ADMIN,
        json={
            "name": "Support Gmail",
            "address": "support@example.com",
            "credentials": {
                "imap_host": "imap.gmail.com",
                "smtp_host": "smtp.gmail.com",
                "username": "support@example.com",
                "provider_preset": "gmail",
            },
            "secrets": {"password": "app-password"},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["connector"]["has_secrets"] is True
    assert body["address"] == "support@example.com"

    q = await db_session.execute(
        select(ChannelAccount).where(ChannelAccount.id == uuid.UUID(body["channel_account_id"]))
    )
    account = q.scalar_one()
    assert account.kind == "email"
    assert account.adapter_type == "imap_smtp"
    assert str(account.connector_id) == body["connector"]["id"]


@pytest.mark.asyncio
async def test_connector_test_endpoint_uses_provider_check(client, monkeypatch, workspace_bundle):
    async def fake_test(credentials, secrets):
        return {"imap_ok": True, "smtp_ok": False, "error": "SMTP: boom"}

    from phera.modules.connectors import registry

    monkeypatch.setattr(registry.ADAPTERS["email_imap_smtp"], "test_fn", fake_test)

    resp = await client.post(
        "/v1/connectors/test",
        headers=ADMIN,
        json={
            "type": "email_imap_smtp",
            "name": "Draft",
            "credentials": {"imap_host": "imap.example.com", "smtp_host": "smtp.example.com"},
            "secrets": {"password": "x"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": None, "imap_ok": True, "smtp_ok": False, "error": "SMTP: boom"}
