"""Keep support routing teams/tiers aligned with staff roles.

L1 = support_agent, L2 = support_admin. Older support_l1 / support_l2 slugs are renamed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import RoutingPolicy, RoutingTier, Team

SUPPORT_TEAM_DEFS = (
    ("support_agent", "Support agents"),
    ("support_admin", "Support admins"),
)

_LEGACY_TEAM_SLUGS = {
    "support_l1": ("support_agent", "Support agents"),
    "support_l2": ("support_admin", "Support admins"),
}


async def ensure_support_routing(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    team_q = await session.execute(select(Team).where(Team.workspace_id == workspace_id))
    teams = {team.slug: team for team in team_q.scalars().all()}

    for old_slug, (new_slug, new_name) in _LEGACY_TEAM_SLUGS.items():
        if old_slug in teams and new_slug not in teams:
            team = teams.pop(old_slug)
            team.slug = new_slug
            team.name = new_name
            teams[new_slug] = team

    for slug, name in SUPPORT_TEAM_DEFS:
        if slug in teams:
            if teams[slug].name != name:
                teams[slug].name = name
            continue
        team = Team(id=uuid.uuid4(), workspace_id=workspace_id, name=name, slug=slug)
        session.add(team)
        teams[slug] = team
    await session.flush()

    policy_q = await session.execute(
        select(RoutingPolicy)
        .where(RoutingPolicy.workspace_id == workspace_id)
        .order_by(RoutingPolicy.created_at.asc())
        .limit(1)
    )
    policy = policy_q.scalar_one_or_none()
    if not policy:
        return

    tier_q = await session.execute(
        select(RoutingTier).where(RoutingTier.policy_id == policy.id).order_by(RoutingTier.position.asc())
    )
    existing = list(tier_q.scalars().all())
    wanted = [
        (0, "Support agents", teams["support_agent"].id, 120),
        (1, "Support admins", teams["support_admin"].id, None),
    ]
    by_position = {tier.position: tier for tier in existing}
    for position, name, team_id, overflow in wanted:
        tier = by_position.get(position)
        if tier:
            if tier.name != name:
                tier.name = name
            if tier.team_id != team_id:
                tier.team_id = team_id
            if position == 0 and tier.overflow_after_seconds is None:
                tier.overflow_after_seconds = overflow
            continue
        session.add(
            RoutingTier(
                id=uuid.uuid4(),
                policy_id=policy.id,
                position=position,
                name=name,
                team_id=team_id,
                overflow_after_seconds=overflow,
            )
        )
    await session.flush()
