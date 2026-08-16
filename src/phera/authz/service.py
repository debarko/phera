from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor
from phera.authz.visibility import can_see_deal
from phera.db.models import Deal, OwnershipProfile, User


async def ensure_user_stub(session: AsyncSession, actor: Actor, workspace_id: uuid.UUID) -> None:
    if not actor.id:
        return
    existing = await session.get(User, actor.id)
    if existing:
        return
    session.add(
        User(
            id=actor.id,
            email=actor.email,
            name=actor.name,
            workspace_id=workspace_id,
        )
    )


async def get_ownership_profile(session: AsyncSession, workspace_id: uuid.UUID) -> OwnershipProfile | None:
    return await session.get(OwnershipProfile, workspace_id)


async def filter_deals_for_actor(
    session: AsyncSession, actor: Actor, workspace_id: uuid.UUID, deals: list[Deal]
) -> list[Deal]:
    profile = await get_ownership_profile(session, workspace_id)
    visible = []
    for deal in deals:
        if await can_see_deal(session, actor, deal, profile):
            visible.append(deal)
    return visible
