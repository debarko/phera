from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import OwnershipProfile, RoutingPolicy, RoutingTier, Team, Workspace
from phera.modules.routing.support_teams import ensure_support_routing
from phera.modules.tickets.reuse_policy import (
    ASSIGNEE_KEEP,
    ASSIGNEE_QUEUE,
    CHANNEL_KINDS,
    SUPPORT_AGENT_IDS_FLAG,
    SUPPORT_AGENTS_FLAG,
    TICKET_REUSE_FLAG,
    WINDOW_MAX_SECONDS,
    WINDOW_MIN_SECONDS,
    parse_ticket_reuse,
)

router = APIRouter(tags=["support-settings"])


def _can_manage_support_settings(actor: Actor) -> bool:
    if actor.unrestricted:
        return True
    if "admin" in actor.roles or "support_admin" in actor.roles:
        return True
    return actor.has_permission("crm.settings.admin") or actor.has_permission("crm.routing.write")


class TeamOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str


class RoutingTierOut(BaseModel):
    id: uuid.UUID
    policy_id: uuid.UUID
    name: str
    position: int
    team_id: uuid.UUID | None
    overflow_after_seconds: int | None


class RoutingPolicyDetailOut(BaseModel):
    id: uuid.UUID
    name: str
    assignment_method: str
    capacity_mode: str
    max_open_units: int
    weights: dict
    focus_on_voice: bool
    sticky_assignee: bool
    offer_ttl_seconds: int


class SupportAgentMember(BaseModel):
    user_id: str
    access: str = Field(pattern="^(agent|admin)$")
    email: str | None = None
    name: str | None = None


class TicketReuseChannelOverride(BaseModel):
    window_seconds: int | None = Field(default=None, ge=WINDOW_MIN_SECONDS, le=WINDOW_MAX_SECONDS)
    reopen_resolved: bool | None = None
    reopen_closed: bool | None = None


class TicketReusePolicyOut(BaseModel):
    window_seconds: int
    reopen_resolved: bool
    reopen_closed: bool
    on_reopen_assignee: str
    channels: dict[str, TicketReuseChannelOverride]


class TicketReusePolicyUpdate(BaseModel):
    window_seconds: int | None = Field(default=None, ge=WINDOW_MIN_SECONDS, le=WINDOW_MAX_SECONDS)
    reopen_resolved: bool | None = None
    reopen_closed: bool | None = None
    on_reopen_assignee: str | None = Field(default=None, pattern="^(keep|queue)$")
    channels: dict[str, TicketReuseChannelOverride] | None = None


class SupportSettingsOut(BaseModel):
    agents: list[SupportAgentMember]
    agent_user_ids: list[str]
    routing_policy: RoutingPolicyDetailOut | None
    routing_tiers: list[RoutingTierOut]
    teams: list[TeamOut]
    ticket_reuse: TicketReusePolicyOut


class SupportSettingsUpdate(BaseModel):
    agents: list[SupportAgentMember] | None = None
    agent_user_ids: list[str] | None = None
    routing_policy: RoutingPolicyUpdate | None = None
    ticket_reuse: TicketReusePolicyUpdate | None = None


class RoutingPolicyUpdate(BaseModel):
    assignment_method: str | None = None
    capacity_mode: str | None = None
    max_open_units: int | None = Field(default=None, ge=1, le=100)
    focus_on_voice: bool | None = None
    sticky_assignee: bool | None = None
    offer_ttl_seconds: int | None = Field(default=None, ge=5, le=600)


async def _get_or_create_profile(
    session: AsyncSession, workspace_id: uuid.UUID
) -> OwnershipProfile:
    profile = await session.get(OwnershipProfile, workspace_id)
    if profile:
        return profile
    profile = OwnershipProfile(workspace_id=workspace_id, mode="pipeline_centric", flags={})
    session.add(profile)
    await session.flush()
    return profile


async def _default_policy(session: AsyncSession, workspace_id: uuid.UUID) -> RoutingPolicy | None:
    q = await session.execute(
        select(RoutingPolicy)
        .where(RoutingPolicy.workspace_id == workspace_id)
        .order_by(RoutingPolicy.created_at.asc())
        .limit(1)
    )
    return q.scalar_one_or_none()


def _policy_out(policy: RoutingPolicy) -> RoutingPolicyDetailOut:
    return RoutingPolicyDetailOut(
        id=policy.id,
        name=policy.name,
        assignment_method=policy.assignment_method,
        capacity_mode=policy.capacity_mode,
        max_open_units=policy.max_open_units,
        weights=policy.weights or {},
        focus_on_voice=policy.focus_on_voice,
        sticky_assignee=policy.sticky_assignee,
        offer_ttl_seconds=policy.offer_ttl_seconds,
    )


def _normalize_agents(flags: dict) -> list[SupportAgentMember]:
    raw_members = flags.get(SUPPORT_AGENTS_FLAG)
    members: list[SupportAgentMember] = []
    seen: set[str] = set()
    if isinstance(raw_members, list):
        for item in raw_members:
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("user_id") or "").strip()
            if not user_id or user_id in seen:
                continue
            access = item.get("access") if item.get("access") in ("agent", "admin") else "agent"
            seen.add(user_id)
            members.append(
                SupportAgentMember(
                    user_id=user_id,
                    access=access,
                    email=item.get("email"),
                    name=item.get("name"),
                )
            )
    if members:
        return members
    raw_ids = flags.get(SUPPORT_AGENT_IDS_FLAG, [])
    for item in raw_ids if isinstance(raw_ids, list) else []:
        user_id = str(item).strip()
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        members.append(SupportAgentMember(user_id=user_id, access="agent"))
    return members


def _ticket_reuse_out(flags: dict) -> TicketReusePolicyOut:
    parsed = parse_ticket_reuse(flags.get(TICKET_REUSE_FLAG))
    channels = {
        kind: TicketReuseChannelOverride(**override)
        for kind, override in (parsed.get("channels") or {}).items()
        if kind in CHANNEL_KINDS
    }
    return TicketReusePolicyOut(
        window_seconds=parsed["window_seconds"],
        reopen_resolved=parsed["reopen_resolved"],
        reopen_closed=parsed["reopen_closed"],
        on_reopen_assignee=parsed["on_reopen_assignee"],
        channels=channels,
    )


async def _build_settings(session: AsyncSession, workspace: Workspace) -> SupportSettingsOut:
    profile = await session.get(OwnershipProfile, workspace.id)
    flags = dict(profile.flags or {}) if profile else {}
    agents = _normalize_agents(flags)
    agent_user_ids = [member.user_id for member in agents]

    policy = await _default_policy(session, workspace.id)
    tiers: list[RoutingTierOut] = []
    policy_out = None
    if policy:
        policy_out = _policy_out(policy)
        tq = await session.execute(
            select(RoutingTier)
            .where(RoutingTier.policy_id == policy.id)
            .order_by(RoutingTier.position.asc())
        )
        tiers = [
            RoutingTierOut(
                id=tier.id,
                policy_id=tier.policy_id,
                name=tier.name,
                position=tier.position,
                team_id=tier.team_id,
                overflow_after_seconds=tier.overflow_after_seconds,
            )
            for tier in tq.scalars().all()
        ]

    team_q = await session.execute(
        select(Team).where(Team.workspace_id == workspace.id).order_by(Team.slug)
    )
    teams = [
        TeamOut(id=team.id, name=team.name, slug=team.slug)
        for team in team_q.scalars().all()
    ]

    return SupportSettingsOut(
        agents=agents,
        agent_user_ids=agent_user_ids,
        routing_policy=policy_out,
        routing_tiers=tiers,
        teams=teams,
        ticket_reuse=_ticket_reuse_out(flags),
    )


@router.get("/support/settings", response_model=SupportSettingsOut)
async def get_support_settings(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    if not _can_manage_support_settings(actor):
        raise HTTPException(403, "Missing support settings permission")
    await ensure_support_routing(session, workspace.id)
    if session.new or session.dirty or session.deleted:
        await commit_and_notify(session)
    return await _build_settings(session, workspace)


@router.patch("/support/settings", response_model=SupportSettingsOut)
async def update_support_settings(
    body: SupportSettingsUpdate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    if not _can_manage_support_settings(actor):
        raise HTTPException(403, "Missing support settings permission")

    await ensure_support_routing(session, workspace.id)

    profile = await _get_or_create_profile(session, workspace.id)
    flags = dict(profile.flags or {})

    if body.agents is not None:
        unique: list[SupportAgentMember] = []
        seen: set[str] = set()
        for member in body.agents:
            if member.user_id in seen:
                continue
            seen.add(member.user_id)
            unique.append(member)
        flags[SUPPORT_AGENTS_FLAG] = [item.model_dump() for item in unique]
        flags[SUPPORT_AGENT_IDS_FLAG] = [item.user_id for item in unique]
    elif body.agent_user_ids is not None:
        flags[SUPPORT_AGENT_IDS_FLAG] = list(dict.fromkeys(body.agent_user_ids))
        flags[SUPPORT_AGENTS_FLAG] = [
            SupportAgentMember(user_id=user_id, access="agent").model_dump()
            for user_id in flags[SUPPORT_AGENT_IDS_FLAG]
        ]

    if body.ticket_reuse is not None:
        current = parse_ticket_reuse(flags.get(TICKET_REUSE_FLAG))
        updates = body.ticket_reuse.model_dump(exclude_unset=True)
        if "window_seconds" in updates and updates["window_seconds"] is not None:
            current["window_seconds"] = updates["window_seconds"]
        if "reopen_resolved" in updates and updates["reopen_resolved"] is not None:
            current["reopen_resolved"] = updates["reopen_resolved"]
        if "reopen_closed" in updates and updates["reopen_closed"] is not None:
            current["reopen_closed"] = updates["reopen_closed"]
        if "on_reopen_assignee" in updates and updates["on_reopen_assignee"] in (
            ASSIGNEE_KEEP,
            ASSIGNEE_QUEUE,
        ):
            current["on_reopen_assignee"] = updates["on_reopen_assignee"]
        if "channels" in updates:
            merged: dict[str, dict] = {}
            raw_channels = updates["channels"] or {}
            for kind, override in raw_channels.items():
                if kind not in CHANNEL_KINDS or not isinstance(override, dict):
                    continue
                cleaned = {key: value for key, value in override.items() if value is not None}
                if cleaned:
                    merged[kind] = cleaned
            current["channels"] = merged
        flags[TICKET_REUSE_FLAG] = parse_ticket_reuse(current)

    if body.routing_policy is not None:
        policy = await _default_policy(session, workspace.id)
        if not policy:
            raise HTTPException(404, "No routing policy configured")
        updates = body.routing_policy.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(policy, key, value)

    profile.flags = flags
    await commit_and_notify(session)
    return await _build_settings(session, workspace)
