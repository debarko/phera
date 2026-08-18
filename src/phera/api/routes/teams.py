from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import Team, TeamMember, User, Workspace

router = APIRouter(tags=["teams"])


def _can_manage_teams(actor: Actor) -> bool:
    if actor.unrestricted:
        return True
    if "admin" in actor.roles:
        return True
    return actor.has_permission("crm.pipelines.write")


def _require_manage(actor: Actor) -> None:
    if not _can_manage_teams(actor):
        raise HTTPException(403, "Missing team management permission")


class TeamOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class TeamCreate(BaseModel):
    name: str
    slug: str


class TeamMemberOut(BaseModel):
    team_id: uuid.UUID
    user_id: str
    user_email: str | None = None
    user_name: str | None = None
    role: str


class TeamMemberCreate(BaseModel):
    user_id: str
    role: str = "member"


async def _get_team_or_404(session: AsyncSession, workspace: Workspace, team_id: uuid.UUID) -> Team:
    team = await session.get(Team, team_id)
    if not team or team.workspace_id != workspace.id:
        raise HTTPException(404, "Team not found")
    return team


@router.get("/teams", response_model=list[TeamOut])
async def list_teams(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(
        select(Team).where(Team.workspace_id == workspace.id).order_by(Team.slug)
    )
    return list(q.scalars().all())


@router.post("/teams", response_model=TeamOut, status_code=201)
async def create_team(
    body: TeamCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    team = Team(id=uuid.uuid4(), workspace_id=workspace.id, name=body.name, slug=body.slug)
    session.add(team)
    await commit_and_notify(session)
    await session.refresh(team)
    return team


@router.get("/teams/{team_id}/members", response_model=list[TeamMemberOut])
async def list_team_members(
    team_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    await _get_team_or_404(session, workspace, team_id)
    q = await session.execute(select(TeamMember).where(TeamMember.team_id == team_id))
    members = list(q.scalars().all())
    out = []
    for member in members:
        user = await session.get(User, member.user_id)
        out.append(
            TeamMemberOut(
                team_id=member.team_id,
                user_id=member.user_id,
                user_email=user.email if user else None,
                user_name=user.name if user else None,
                role=member.role,
            )
        )
    return out


@router.post("/teams/{team_id}/members", response_model=TeamMemberOut, status_code=201)
async def add_team_member(
    team_id: uuid.UUID,
    body: TeamMemberCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    await _get_team_or_404(session, workspace, team_id)
    user = await session.get(User, body.user_id)
    if not user or user.workspace_id != workspace.id:
        raise HTTPException(404, "User not found in this workspace")

    member = await session.get(TeamMember, (team_id, body.user_id))
    if member is None:
        member = TeamMember(team_id=team_id, user_id=body.user_id, role=body.role)
        session.add(member)
    else:
        member.role = body.role
    await commit_and_notify(session)
    return TeamMemberOut(
        team_id=team_id,
        user_id=body.user_id,
        user_email=user.email,
        user_name=user.name,
        role=member.role,
    )


@router.delete("/teams/{team_id}/members/{user_id}", status_code=204)
async def remove_team_member(
    team_id: uuid.UUID,
    user_id: str,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    await _get_team_or_404(session, workspace, team_id)
    member = await session.get(TeamMember, (team_id, user_id))
    if not member:
        raise HTTPException(404, "Team member not found")
    await session.delete(member)
    await commit_and_notify(session)
