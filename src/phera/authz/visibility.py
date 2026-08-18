from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor
from phera.db.models import Deal, OwnershipProfile


def can_see_pipeline(
    actor: Actor,
    granted_team_ids: set[uuid.UUID],
    actor_team_ids: set[uuid.UUID],
) -> bool:
    if actor.unrestricted:
        return True
    if actor.has_permission("crm.pipelines.read"):
        return True
    if not granted_team_ids:
        # No PipelineTeam grants configured for this pipeline at all -> visible to
        # everyone (confirmed default: existing pipelines don't vanish for anyone
        # once team-scoping ships, only newly-scoped ones get restricted).
        return True
    return bool(granted_team_ids & actor_team_ids)


async def can_see_deal(
    session: AsyncSession,
    actor: Actor,
    deal: Deal,
    profile: OwnershipProfile | None = None,
) -> bool:
    if actor.unrestricted:
        return True
    if actor.has_permission("crm.deals.read"):
        return True
    if actor.has_permission("crm.deals.read", "own") and deal.owner_user_id == actor.id:
        return True
    if profile and profile.mode == "pipeline_centric":
        return actor.has_permission("crm.pipelines.read")
    return deal.owner_user_id == actor.id
