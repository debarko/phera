from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from phera.authz.service import ensure_user_stub
from phera.db.models import AgentPresence, AgentTelephonyIdentity
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
    for user_id, sip_user in ((first.id, "sip-a"), (second.id, "sip-b")):
        db_session.add(
            AgentTelephonyIdentity(
                user_id=user_id,
                workspace_id=ws.id,
                provider="exotel",
                sip_user=sip_user,
                sip_secret_encrypted="encrypted",
                sip_domain="sip.exotel.com",
            )
        )
    await db_session.flush()

    agent = await select_agent_for_voice(db_session, None, ws.id)
    assert agent is not None
    assert agent.user_id == second.id
    assert agent.last_assigned_at is not None

    count = await db_session.scalar(
        select(func.count()).select_from(AgentPresence).where(AgentPresence.status == "available")
    )
    assert count == 2


@pytest.mark.asyncio
async def test_select_agent_for_voice_ignores_other_workspace_identities(
    db_session, workspace_bundle
):
    from tests.support import factories

    ws = workspace_bundle.workspace
    local = staff_actor(user_id="voice-local")
    foreign = staff_actor(user_id="voice-foreign")
    await ensure_user_stub(db_session, local, ws.id)
    other_ws = factories.workspace(slug="other-voice", name="Other")
    db_session.add(other_ws)
    await db_session.flush()
    await ensure_user_stub(db_session, foreign, other_ws.id)
    await db_session.flush()

    db_session.add(AgentPresence(user_id=foreign.id, status="available"))
    db_session.add(AgentPresence(user_id=local.id, status="available"))
    db_session.add(
        AgentTelephonyIdentity(
            user_id=foreign.id,
            workspace_id=other_ws.id,
            provider="exotel",
            sip_user="sip-foreign",
            sip_secret_encrypted="encrypted",
            sip_domain="sip.exotel.com",
        )
    )
    db_session.add(
        AgentTelephonyIdentity(
            user_id=local.id,
            workspace_id=ws.id,
            provider="exotel",
            sip_user="sip-local",
            sip_secret_encrypted="encrypted",
            sip_domain="sip.exotel.com",
        )
    )
    await db_session.flush()

    agent = await select_agent_for_voice(db_session, None, ws.id)
    assert agent is not None
    assert agent.user_id == local.id
