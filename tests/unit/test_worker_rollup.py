from __future__ import annotations

import uuid
from datetime import date, datetime, UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from phera.db.models import RollupRun
from phera.worker.runner import run_daily_rollup


@pytest.mark.asyncio
async def test_run_daily_rollup_skips_when_already_completed():
    session = AsyncMock()
    workspace_id = uuid.uuid4()
    day = date(2026, 8, 16)
    completed = RollupRun(day=day, workspace_id=workspace_id, status="completed")
    session.get = AsyncMock(return_value=completed)

    await run_daily_rollup(session, workspace_id, day)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_daily_rollup_skips_when_already_running():
    session = AsyncMock()
    workspace_id = uuid.uuid4()
    day = date(2026, 8, 16)
    running = RollupRun(
        day=day,
        workspace_id=workspace_id,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.get = AsyncMock(return_value=running)

    await run_daily_rollup(session, workspace_id, day)

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_daily_rollup_creates_when_missing(monkeypatch):
    session = AsyncMock()
    workspace_id = uuid.uuid4()
    day = date(2026, 8, 16)
    session.get = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))

    await run_daily_rollup(session, workspace_id, day)

    assert session.add.call_count >= 1
    added = [call.args[0] for call in session.add.call_args_list]
    assert any(isinstance(obj, RollupRun) for obj in added)
    session.flush.assert_awaited()
