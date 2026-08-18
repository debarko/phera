from __future__ import annotations

import pytest

from tests.support import factories

pytestmark = pytest.mark.integration

ACTOR = {
    "X-Actor-Id": "ca-actor",
    "X-Actor-Email": "ca-actor@test.com",
    "X-Actor-Unrestricted": "true",
}


@pytest.mark.asyncio
async def test_create_channel_account_rejects_connector_from_other_workspace(
    client, db_session, workspace_bundle
):
    other_ws = factories.workspace(slug="other")
    db_session.add(other_ws)
    await db_session.flush()
    foreign_connector = factories.connector(other_ws.id, type="gallabox")
    db_session.add(foreign_connector)
    await db_session.commit()

    resp = await client.post(
        "/v1/channel-accounts",
        headers=ACTOR,
        json={
            "connector_id": str(foreign_connector.id),
            "kind": "messaging",
            "adapter_type": "gallabox",
            "address": "+911234567890",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_channel_account_rejects_inactive_connector(
    client, db_session, workspace_bundle
):
    connector = factories.connector(
        workspace_bundle.workspace.id, type="gallabox", is_active=False
    )
    db_session.add(connector)
    await db_session.commit()

    resp = await client.post(
        "/v1/channel-accounts",
        headers=ACTOR,
        json={
            "connector_id": str(connector.id),
            "kind": "messaging",
            "adapter_type": "gallabox",
            "address": "+911234567890",
        },
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_channel_account_accepts_own_active_connector(
    client, db_session, workspace_bundle
):
    connector = factories.connector(workspace_bundle.workspace.id, type="gallabox")
    db_session.add(connector)
    await db_session.commit()

    resp = await client.post(
        "/v1/channel-accounts",
        headers=ACTOR,
        json={
            "connector_id": str(connector.id),
            "kind": "messaging",
            "adapter_type": "gallabox",
            "address": "+911234567890",
        },
    )
    assert resp.status_code == 201
