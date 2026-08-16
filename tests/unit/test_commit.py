from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from phera.db.commit import commit_and_notify, track_outbox_notify
from tests.support.fakes import RecordingSession


@pytest.mark.asyncio
async def test_track_outbox_notify_accumulates_ids():
    session = RecordingSession()
    oid = uuid.uuid4()
    track_outbox_notify(session, oid)
    track_outbox_notify(session, uuid.uuid4())
    assert session.info["outbox_notify_ids"] == [oid, session.info["outbox_notify_ids"][1]]


@pytest.mark.asyncio
async def test_commit_and_notify_calls_redis_after_commit():
    session = RecordingSession()
    oid = uuid.uuid4()
    track_outbox_notify(session, oid)

    with patch("phera.db.commit.notify_outbox", new_callable=AsyncMock) as notify:
        await commit_and_notify(session)

    assert session.committed is True
    notify.assert_awaited_once_with(oid)
