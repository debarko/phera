from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import ORMModel
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import ChannelAccount, Connector, LifecycleDestination, Workspace

router = APIRouter(tags=["channels"])


class ChannelAccountOut(ORMModel):
    id: uuid.UUID
    connector_id: uuid.UUID | None
    kind: str
    adapter_type: str
    address: str
    is_active: bool


class ChannelAccountCreate(BaseModel):
    connector_id: uuid.UUID | None = None
    kind: str
    adapter_type: str
    address: str


class LifecycleDestinationOut(ORMModel):
    id: uuid.UUID
    connector_id: uuid.UUID
    name: str
    event_filter: dict
    is_active: bool


class LifecycleDestinationCreate(BaseModel):
    connector_id: uuid.UUID
    name: str
    event_filter: dict = Field(default_factory=dict)


@router.get("/channel-accounts", response_model=list[ChannelAccountOut])
async def list_channel_accounts(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(
        select(ChannelAccount).where(ChannelAccount.workspace_id == workspace.id)
    )
    return q.scalars().all()


@router.post("/channel-accounts", response_model=ChannelAccountOut, status_code=201)
async def create_channel_account(
    body: ChannelAccountCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    if body.connector_id is not None:
        connector = await session.get(Connector, body.connector_id)
        if (
            not connector
            or connector.workspace_id != workspace.id
            or not connector.is_active
        ):
            raise HTTPException(400, "Unknown or inactive connector")

    account = ChannelAccount(id=uuid.uuid4(), workspace_id=workspace.id, **body.model_dump())
    session.add(account)
    await commit_and_notify(session)
    await session.refresh(account)
    return account


@router.get("/lifecycle-destinations", response_model=list[LifecycleDestinationOut])
async def list_destinations(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(
        select(LifecycleDestination).where(LifecycleDestination.workspace_id == workspace.id)
    )
    return q.scalars().all()


@router.post("/lifecycle-destinations", response_model=LifecycleDestinationOut, status_code=201)
async def create_destination(
    body: LifecycleDestinationCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    dest = LifecycleDestination(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        **body.model_dump(),
    )
    session.add(dest)
    await commit_and_notify(session)
    await session.refresh(dest)
    return dest
