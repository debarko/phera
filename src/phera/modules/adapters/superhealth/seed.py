"""Superhealth-specific seed and webhook mappings — hospital vocab ONLY here."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from phera.db.models import (
    ChannelAccount,
    Connector,
    LifecycleDestination,
    OwnershipProfile,
    Pipeline,
    RoutingPolicy,
    RoutingTier,
    Stage,
    Team,
    Workspace,
)


SUPERHEALTH_PIPELINES = [
    ("ivf_consult", "IVF Consult", ["New", "Contacted", "Qualified", "Won", "Lost"]),
    ("skin_consult", "Skin Consult", ["New", "Contacted", "Qualified", "Won", "Lost"]),
    ("dental_consult", "Dental Consult", ["New", "Contacted", "Qualified", "Won", "Lost"]),
    ("camp_conversion", "Camp Conversion", ["New", "Follow-up", "Converted", "Lost"]),
    ("partner_referral", "Partner Referral", ["New", "In Progress", "Qualified", "Converted", "Lost"]),
]


async def seed_superhealth_workspace(session: AsyncSession, workspace: Workspace) -> None:
    profile = await session.get(OwnershipProfile, workspace.id)
    if not profile:
        session.add(OwnershipProfile(workspace_id=workspace.id, mode="pipeline_centric", flags={}))

    for slug, name, stage_names in SUPERHEALTH_PIPELINES:
        pipeline = Pipeline(
            id=uuid.uuid4(),
            workspace_id=workspace.id,
            name=name,
            slug=slug,
            resubmission_policy="reuse_open_deal",
        )
        session.add(pipeline)
        await session.flush()
        for i, sname in enumerate(stage_names):
            cat = "won" if sname.lower() == "won" else "lost" if sname.lower() == "lost" else "open"
            session.add(
                Stage(
                    id=uuid.uuid4(),
                    pipeline_id=pipeline.id,
                    name=sname,
                    position=i,
                    category=cat,
                )
            )

    for team_slug, team_name in (
        ("support_agent", "Support agents"),
        ("support_admin", "Support admins"),
        ("fertility_sales", "Fertility sales"),
        ("skin_sales", "Skin sales"),
    ):
        session.add(Team(id=uuid.uuid4(), workspace_id=workspace.id, name=team_name, slug=team_slug))

    policy = RoutingPolicy(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name="default_support",
        assignment_method="round_robin",
        capacity_mode="unified",
        max_open_units=5,
    )
    session.add(policy)
    await session.flush()

    team_q = await session.execute(select(Team).where(Team.workspace_id == workspace.id))
    teams = {team.slug: team for team in team_q.scalars().all()}
    agents = teams.get("support_agent")
    admins = teams.get("support_admin")
    if agents:
        session.add(
            RoutingTier(
                id=uuid.uuid4(),
                policy_id=policy.id,
                name="Support agents",
                position=0,
                team_id=agents.id,
                overflow_after_seconds=120,
            )
        )
    if admins:
        session.add(
            RoutingTier(
                id=uuid.uuid4(),
                policy_id=policy.id,
                name="Support admins",
                position=1,
                team_id=admins.id,
            )
        )

    moengage = Connector(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        type="moengage",
        name="MoEngage",
        credentials={},
    )
    session.add(moengage)
    await session.flush()

    session.add(
        LifecycleDestination(
            id=uuid.uuid4(),
            workspace_id=workspace.id,
            connector_id=moengage.id,
            event_filter={"event_types": ["form.submitted", "deal.*"]},
            field_mapping={},
        )
    )

    session.add(
        ChannelAccount(
            id=uuid.uuid4(),
            workspace_id=workspace.id,
            kind="email",
            adapter_type="google_group",
            address="contact@superhealth.co.in",
            routing_policy_id=policy.id,
        )
    )
    session.add(
        ChannelAccount(
            id=uuid.uuid4(),
            workspace_id=workspace.id,
            kind="messaging",
            adapter_type="gallabox",
            address="+91XXXXXXXXXX",
            routing_policy_id=policy.id,
        )
    )


async def handle_service_event_completed(session: AsyncSession, payload: dict) -> dict:
    """Map data-server service_event.completed → outbox event (stub)."""
    return {"mapped": True, "event_type": "service_event.completed", "payload": payload}
