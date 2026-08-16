"""In-process fan-out for Support Inbox live updates (SSE)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

_inbox_subscribers: set[asyncio.Queue[str]] = set()


def subscribe_inbox() -> asyncio.Queue[str]:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
    _inbox_subscribers.add(queue)
    return queue


def unsubscribe_inbox(queue: asyncio.Queue[str]) -> None:
    _inbox_subscribers.discard(queue)


def publish_inbox_event(payload: dict[str, Any]) -> None:
    data = json.dumps(payload)
    for queue in list(_inbox_subscribers):
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            pass
