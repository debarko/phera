from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import ORMModel
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import RoutingPolicy, RoutingTier, Workspace

router = APIRouter(tags=["routing"])


class RoutingPolicyOut(ORMModel):
    id: uuid.UUID
    name: str
    assignment_method: str
    capacity_mode: str
    max_open_units: int


class RoutingPolicyCreate(BaseModel):
    name: str
    assignment_method: str = "round_robin"
    capacity_mode: str = "unified"
    max_open_units: int = 5


class RoutingTierCreate(BaseModel):
    policy_id: uuid.UUID
    name: str = "Support agents"
    position: int = 0
    team_id: uuid.UUID | None = None


class RoutingTierOut(ORMModel):
    id: uuid.UUID
    policy_id: uuid.UUID
    name: str
    position: int


@router.get("/routing/policies", response_model=list[RoutingPolicyOut])
async def list_policies(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(
        select(RoutingPolicy).where(RoutingPolicy.workspace_id == workspace.id)
    )
    return q.scalars().all()


@router.post("/routing/policies", response_model=RoutingPolicyOut, status_code=201)
async def create_policy(
    body: RoutingPolicyCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    policy = RoutingPolicy(id=uuid.uuid4(), workspace_id=workspace.id, **body.model_dump())
    session.add(policy)
    await commit_and_notify(session)
    await session.refresh(policy)
    return policy


@router.get("/routing/tiers", response_model=list[RoutingTierOut])
async def list_tiers(
    policy_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = select(RoutingTier).join(RoutingPolicy).where(RoutingPolicy.workspace_id == workspace.id)
    if policy_id:
        q = q.where(RoutingTier.policy_id == policy_id)
    result = await session.execute(q.order_by(RoutingTier.position))
    return result.scalars().all()


@router.post("/routing/tiers", response_model=RoutingTierOut, status_code=201)
async def create_tier(
    body: RoutingTierCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    tier = RoutingTier(id=uuid.uuid4(), **body.model_dump())
    session.add(tier)
    await commit_and_notify(session)
    await session.refresh(tier)
    return tier
