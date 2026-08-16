from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import OutboxEvent
from phera.db.session import get_db
from phera.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok", "service": "phera"}


@router.get("/ready")
async def ready(session: AsyncSession = Depends(get_db)):
    checks = {"postgres": False, "redis": None}
    try:
        await session.execute(text("SELECT 1"))
        checks["postgres"] = True
    except Exception as exc:
        checks["postgres_error"] = str(exc)

    settings = get_settings()
    if settings.redis_url:
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(settings.redis_url)
            await client.ping()
            checks["redis"] = True
            await client.aclose()
        except Exception as exc:
            checks["redis"] = False
            checks["redis_error"] = str(exc)

    ok = checks["postgres"] and (checks["redis"] is not False or settings.redis_url is None)
    return {"ready": ok, "checks": checks}


@router.get("/metrics/outbox")
async def outbox_metrics(session: AsyncSession = Depends(get_db)):
    q = await session.execute(
        select(OutboxEvent.status, func.count()).group_by(OutboxEvent.status)
    )
    return {"outbox_by_status": {row[0]: row[1] for row in q.all()}}
