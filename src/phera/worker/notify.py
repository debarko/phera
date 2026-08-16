from __future__ import annotations

import logging
import uuid

from phera.settings import get_settings

logger = logging.getLogger(__name__)


async def notify_outbox(outbox_id: uuid.UUID) -> None:
    settings = get_settings()
    if not settings.redis_url:
        return
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url)
        await client.lpush("phera:outbox", str(outbox_id))
        await client.aclose()
    except Exception:
        logger.exception("Redis notify failed for outbox %s", outbox_id)
