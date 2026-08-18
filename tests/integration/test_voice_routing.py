from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from phera.authz.service import ensure_user_stub
from phera.db.models import AgentPresence
from phera.modules.routing.engine import select_agent_for_voice
from tests.support.factories import staff_actor

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_select_agent_for_voice_round_robins_by_last_assigned(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    first = staff_actor(user_id="voice-agent-a")
    second = staff_actor(user_id="voice-agent-b")
    await ensure_user_stub(db_session, first, ws.id)
    await ensure_user_stub(db_session, second, ws.id)
    await db_session.flush()
    db_session.add(
        AgentPresence(
            user_id=first.id,
            status="available",
            last_assigned_at=datetime.now(UTC),
        )
    )
    db_session.add(AgentPresence(user_id=second.id, status="available"))
    await db_session.flush()

    agent = await select_agent_for_voice(db_session, None)
    assert agent is not None
    assert agent.user_id == second.id
    assert agent.last_assigned_at is not None

    count = await db_session.scalar(
        select(func.count()).select_from(AgentPresence).where(AgentPresence.status == "available")
    )
    assert count == 2
