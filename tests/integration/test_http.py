from __future__ import annotations

import pytest

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
async def test_public_form_submit_unknown_returns_404(client):
    resp = await client.post("/public/forms/does-not-exist/submit", json={"data": {"email": "a@b.com"}})
    assert resp.status_code == 404
