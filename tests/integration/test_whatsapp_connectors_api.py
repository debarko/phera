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
    "X-Actor-Id": "wa-admin",
    "X-Actor-Email": "wa-admin@test.com",
    "X-Actor-Roles": "support_admin",
}
AGENT = {
    "X-Actor-Id": "wa-agent",
    "X-Actor-Email": "wa-agent@test.com",
    "X-Actor-Roles": "support_agent",
}


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_create_whatsapp_channel_requires_permission(client):
    resp = await client.post(
        "/v1/whatsapp-channels",
        headers=AGENT,
        json={"name": "x", "phone_number": "+911234567890"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_whatsapp_channel_creates_connector_and_channel_account(
    client, db_session, workspace_bundle
):
    resp = await client.post(
        "/v1/whatsapp-channels",
        headers=ADMIN,
        json={
            "name": "Support WhatsApp",
            "phone_number": "+911234567890",
            "credentials": {"account_id": "acc-1", "channel_id": "ch-1"},
            "secrets": {"api_key": "key-1", "api_secret": "secret-1", "webhook_secret": "whsec-1"},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["connector"]["has_secrets"] is True
    assert body["connector"]["type"] == "gallabox"
    assert body["address"] == "+911234567890"

    q = await db_session.execute(
        select(ChannelAccount).where(ChannelAccount.id == uuid.UUID(body["channel_account_id"]))
    )
    account = q.scalar_one()
    assert account.kind == "messaging"
    assert account.adapter_type == "gallabox"

    q2 = await db_session.execute(
        select(Connector).where(Connector.id == uuid.UUID(body["connector"]["id"]))
    )
    connector = q2.scalar_one()
    assert decrypt_secrets(connector.secrets_encrypted) == {
        "api_key": "key-1",
        "api_secret": "secret-1",
        "webhook_secret": "whsec-1",
    }


@pytest.mark.asyncio
async def test_list_connectors_filters_gallabox_type(client, db_session, workspace_bundle):
    db_session.add(
        factories.connector(workspace_bundle.workspace.id, type="moengage", name="MoEngage")
    )
    await db_session.commit()

    await client.post(
        "/v1/whatsapp-channels",
        headers=ADMIN,
        json={
            "name": "Support WhatsApp",
            "phone_number": "+911234567890",
            "credentials": {"account_id": "a", "channel_id": "c"},
            "secrets": {"api_key": "k", "api_secret": "s"},
        },
    )

    scoped = await client.get("/v1/connectors?type=gallabox", headers=ADMIN)
    names = [row["name"] for row in scoped.json()]
    assert names == ["Support WhatsApp"]


@pytest.mark.asyncio
async def test_patch_merges_secrets_per_key(client, db_session, workspace_bundle):
    create = await client.post(
        "/v1/whatsapp-channels",
        headers=ADMIN,
        json={
            "name": "Support WhatsApp",
            "phone_number": "+911234567890",
            "credentials": {"account_id": "a", "channel_id": "c"},
            "secrets": {"api_key": "key-1", "api_secret": "secret-1", "webhook_secret": "whsec-1"},
        },
    )
    connector_id = create.json()["connector"]["id"]

    patch = await client.patch(
        f"/v1/connectors/{connector_id}",
        headers=ADMIN,
        json={"secrets": {"webhook_secret": "whsec-2"}},
    )
    assert patch.status_code == 200

    q = await db_session.execute(select(Connector).where(Connector.id == uuid.UUID(connector_id)))
    row = q.scalar_one()
    assert decrypt_secrets(row.secrets_encrypted) == {
        "api_key": "key-1",
        "api_secret": "secret-1",
        "webhook_secret": "whsec-2",
    }


@pytest.mark.asyncio
async def test_whatsapp_test_endpoint_reports_missing_fields(client, workspace_bundle):
    resp = await client.post(
        "/v1/connectors/test",
        headers=ADMIN,
        json={"type": "gallabox", "name": "Draft", "credentials": {}, "secrets": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "api_key" in body["error"]
