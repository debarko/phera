from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor
from phera.db.models import Deal, OwnershipProfile


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
