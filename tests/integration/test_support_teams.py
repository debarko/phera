from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from phera.db.models import RoutingPolicy, RoutingTier, Team
from phera.modules.routing.support_teams import ensure_support_routing


@pytest.mark.asyncio
async def test_ensure_renames_legacy_l1_l2_teams(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    db_session.add(Team(id=uuid.uuid4(), workspace_id=ws.id, name="support_l1", slug="support_l1"))
    db_session.add(Team(id=uuid.uuid4(), workspace_id=ws.id, name="support_l2", slug="support_l2"))
    policy = RoutingPolicy(id=uuid.uuid4(), workspace_id=ws.id, name="default_support")
    db_session.add(policy)
    await db_session.flush()

    await ensure_support_routing(db_session, ws.id)

    teams = {
        row.slug: row.name
        for row in (await db_session.execute(select(Team).where(Team.workspace_id == ws.id))).scalars()
    }
    assert teams["support_agent"] == "Support agents"
    assert teams["support_admin"] == "Support admins"
    assert "support_l1" not in teams
    assert "support_l2" not in teams

    tiers = list(
        (
            await db_session.execute(
                select(RoutingTier).where(RoutingTier.policy_id == policy.id).order_by(RoutingTier.position)
            )
        ).scalars()
    )
    assert [tier.name for tier in tiers] == ["Support agents", "Support admins"]
    assert tiers[0].overflow_after_seconds == 120
    assert tiers[1].overflow_after_seconds is None
    assert teams["support_agent"]
