from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import (
    ContactCreate,
    ContactOut,
    ContactUpdate,
    DealCreate,
    DealOut,
    DealAssign,
    DealUpdateStage,
    OrganizationCreate,
    OrganizationOut,
    PipelineCreate,
    PipelineOut,
    StageOut,
)
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import Contact, Deal, Organization, Pipeline, Stage, Workspace
from phera.db.mutate import FieldChange, MutateRequest, mutate

router = APIRouter(tags=["contacts"])


@router.get("/contacts", response_model=list[ContactOut])
async def list_contacts(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(
        select(Contact).where(Contact.workspace_id == workspace.id, Contact.is_deleted.is_(False))
    )
    return q.scalars().all()


@router.post("/contacts", response_model=ContactOut, status_code=201)
async def create_contact(
    body: ContactCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    contact = Contact(id=uuid.uuid4(), workspace_id=workspace.id, **body.model_dump())
    session.add(contact)
    await session.flush()
    await mutate(
        session,
        MutateRequest(
            entity=contact,
            entity_type="contact",
            action="created",
            changes=[FieldChange("id", None, str(contact.id))],
            actor=actor,
            outbox_event_type="contact.created",
            idempotency_key=f"contact.created:{contact.id}",
            outbox_payload={"contact_id": str(contact.id)},
            contact_id=contact.id,
        ),
    )
    await commit_and_notify(session)
    await session.refresh(contact)
    return contact


@router.get("/contacts/{contact_id}", response_model=ContactOut)
async def get_contact(
    contact_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    contact = await session.get(Contact, contact_id)
    if not contact or contact.workspace_id != workspace.id:
        raise HTTPException(404, "Contact not found")
    return contact


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID,
    body: ContactUpdate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    contact = await session.get(Contact, contact_id)
    if not contact or contact.workspace_id != workspace.id or contact.is_deleted:
        raise HTTPException(404, "Contact not found")

    changes: list[FieldChange] = []
    if body.name is not None and body.name != contact.name:
        changes.append(FieldChange("name", contact.name, body.name))
        contact.name = body.name

    if changes:
        now = datetime.now(UTC)
        await session.flush()
        await mutate(
            session,
            MutateRequest(
                entity=contact,
                entity_type="contact",
                action="updated",
                changes=changes,
                actor=actor,
                outbox_event_type="contact.updated",
                idempotency_key=f"contact.updated:{contact.id}:{int(now.timestamp())}",
                outbox_payload={"contact_id": str(contact.id)},
                contact_id=contact.id,
            ),
        )
    await commit_and_notify(session)
    await session.refresh(contact)
    return contact


@router.get("/organizations", response_model=list[OrganizationOut])
async def list_organizations(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(select(Organization).where(Organization.workspace_id == workspace.id))
    return q.scalars().all()


@router.post("/organizations", response_model=OrganizationOut, status_code=201)
async def create_organization(
    body: OrganizationCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    org = Organization(id=uuid.uuid4(), workspace_id=workspace.id, **body.model_dump())
    session.add(org)
    await commit_and_notify(session)
    await session.refresh(org)
    return org


@router.get("/pipelines", response_model=list[PipelineOut])
async def list_pipelines(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(select(Pipeline).where(Pipeline.workspace_id == workspace.id))
    pipelines = q.scalars().all()
    result = []
    for p in pipelines:
        sq = await session.execute(select(Stage).where(Stage.pipeline_id == p.id).order_by(Stage.position))
        stages = sq.scalars().all()
        result.append(
            PipelineOut(
                id=p.id,
                name=p.name,
                slug=p.slug,
                is_active=p.is_active,
                resubmission_policy=p.resubmission_policy,
                stages=[StageOut.model_validate(s) for s in stages],
            )
        )
    return result


@router.post("/pipelines", response_model=PipelineOut, status_code=201)
async def create_pipeline(
    body: PipelineCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    pipeline = Pipeline(id=uuid.uuid4(), workspace_id=workspace.id, **body.model_dump())
    session.add(pipeline)
    await commit_and_notify(session)
    await session.refresh(pipeline)
    return PipelineOut(
        id=pipeline.id,
        name=pipeline.name,
        slug=pipeline.slug,
        is_active=pipeline.is_active,
        resubmission_policy=pipeline.resubmission_policy,
        stages=[],
    )


@router.get("/deals", response_model=list[DealOut])
async def list_deals(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(select(Deal).where(Deal.workspace_id == workspace.id))
    return q.scalars().all()


@router.post("/deals", response_model=DealOut, status_code=201)
async def create_deal(
    body: DealCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    now = datetime.now(UTC)
    deal = Deal(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        stage_entered_at=now,
        status="open",
        **body.model_dump(),
    )
    session.add(deal)
    await session.flush()
    await mutate(
        session,
        MutateRequest(
            entity=deal,
            entity_type="deal",
            action="created",
            changes=[FieldChange("stage_id", None, str(deal.stage_id))],
            actor=actor,
            pipeline_id=deal.pipeline_id,
            stage_to_id=deal.stage_id,
            outbox_event_type="deal.created",
            idempotency_key=f"deal.created:{deal.id}",
            outbox_payload={"deal_id": str(deal.id), "contact_id": str(deal.contact_id)},
            contact_id=deal.contact_id,
            deal_id=deal.id,
        ),
    )
    await commit_and_notify(session)
    await session.refresh(deal)
    return deal


@router.patch("/deals/{deal_id}/stage", response_model=DealOut)
async def move_deal_stage(
    deal_id: uuid.UUID,
    body: DealUpdateStage,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    deal = await session.get(Deal, deal_id)
    if not deal or deal.workspace_id != workspace.id:
        raise HTTPException(404, "Deal not found")
    old_stage = deal.stage_id
    deal.stage_id = body.stage_id
    deal.stage_entered_at = datetime.now(UTC)
    await session.flush()
    await mutate(
        session,
        MutateRequest(
            entity=deal,
            entity_type="deal",
            action="stage_changed",
            changes=[FieldChange("stage_id", str(old_stage), str(body.stage_id))],
            actor=actor,
            pipeline_id=deal.pipeline_id,
            stage_from_id=old_stage,
            stage_to_id=body.stage_id,
            after_snapshot={"stage_id": str(deal.stage_id), "status": deal.status},
            outbox_event_type="deal.stage_changed",
            idempotency_key=f"deal.stage_changed:{deal.id}:{body.stage_id}:{deal.stage_entered_at.isoformat()}",
            outbox_payload={
                "deal_id": str(deal.id),
                "stage_from": str(old_stage),
                "stage_to": str(body.stage_id),
            },
            timeline=True,
            timeline_type="status-change",
            timeline_body=f"Stage changed",
            contact_id=deal.contact_id,
            deal_id=deal.id,
        ),
    )
    await commit_and_notify(session)
    await session.refresh(deal)
    return deal


@router.patch("/deals/{deal_id}/assign", response_model=DealOut)
async def assign_deal(
    deal_id: uuid.UUID,
    body: DealAssign,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    deal = await session.get(Deal, deal_id)
    if not deal or deal.workspace_id != workspace.id:
        raise HTTPException(404, "Deal not found")
    old_owner = deal.owner_user_id
    deal.owner_user_id = body.owner_user_id
    await session.flush()
    await mutate(
        session,
        MutateRequest(
            entity=deal,
            entity_type="deal",
            action="assigned",
            changes=[FieldChange("owner_user_id", old_owner, body.owner_user_id)],
            actor=actor,
            pipeline_id=deal.pipeline_id,
            outbox_event_type="deal.assigned",
            idempotency_key=f"deal.assigned:{deal.id}:{body.owner_user_id}",
            outbox_payload={"deal_id": str(deal.id), "owner_user_id": body.owner_user_id},
            contact_id=deal.contact_id,
            deal_id=deal.id,
        ),
    )
    await commit_and_notify(session)
    await session.refresh(deal)
    return deal
