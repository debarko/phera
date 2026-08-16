from __future__ import annotations

import asyncio

import pytest

from phera.modules.tickets.inbox_events import publish_inbox_event, subscribe_inbox, unsubscribe_inbox


@pytest.mark.asyncio
async def test_publish_reaches_subscriber():
    queue = subscribe_inbox()
    try:
        publish_inbox_event({"type": "message.received", "ticket_id": "abc"})
        payload = await asyncio.wait_for(queue.get(), timeout=0.5)
        assert "message.received" in payload
        assert "abc" in payload
    finally:
        unsubscribe_inbox(queue)
