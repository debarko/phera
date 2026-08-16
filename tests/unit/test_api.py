from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from phera.main import app
from phera.settings import get_settings


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "phera"


@pytest.mark.asyncio
async def test_openapi():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert "Phera" in resp.json()["info"]["title"]
    paths = resp.json()["paths"]
    assert "/health" in paths
    assert "/v1/contacts" in paths
    assert "/public/forms/{slug}/submit" in paths


@pytest.mark.asyncio
async def test_protected_route_requires_actor():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/contacts")
    assert resp.status_code == 401
    assert "proxy misconfigured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_worker_notify_skips_without_redis():
    get_settings.cache_clear()
    with patch.dict("os.environ", {"REDIS_URL": ""}, clear=False):
        get_settings.cache_clear()
        import uuid

        from phera.worker.notify import notify_outbox

        # Should return immediately without connecting
        await notify_outbox(uuid.uuid4())
    get_settings.cache_clear()
