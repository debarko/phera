from __future__ import annotations

import asyncio

import pytest

from phera.modules.tickets.inbox_events import (
    publish_inbox_event,
    subscribe_inbox,
    unsubscribe_inbox,
)


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


def test_inbox_event_visible_scopes_by_workspace():
    import json

    from phera.api.routes.inbox import inbox_event_visible

    ws = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    other = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    same = json.dumps({"type": "deal.created", "workspace_id": ws})
    foreign = json.dumps({"type": "deal.created", "workspace_id": other})
    assert inbox_event_visible(same, ws)
    assert not inbox_event_visible(foreign, ws)
    assert not inbox_event_visible('{"type": "ticket.claimed"}', ws)
    assert not inbox_event_visible("not-json", ws)
    assert not inbox_event_visible("[]", ws)
    assert not inbox_event_visible("1", ws)
