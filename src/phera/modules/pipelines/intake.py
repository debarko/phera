from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor
from phera.db.models import (
    Contact,
    Deal,
    Form,
    FormSubmission,
    Interaction,
    Pipeline,
    Stage,
)
from phera.db.mutate import FieldChange, MutateRequest, compute_diff, mutate


async def upsert_contact(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    payload: dict,
    matching_keys: list[str],
    form: Form,
    is_new: bool,
) -> tuple[Contact, bool]:
    contact = None
    for key in matching_keys:
        val = payload.get(key) or payload.get(f"contact_{key}")
        if not val:
            continue
        if key in ("email", "primary_email"):
            q = await session.execute(
                select(Contact).where(
                    Contact.workspace_id == workspace_id,
                    Contact.primary_email == val,
                    Contact.is_deleted.is_(False),
                )
            )
            contact = q.scalar_one_or_none()
        elif key in ("phone", "primary_phone"):
            q = await session.execute(
                select(Contact).where(
                    Contact.workspace_id == workspace_id,
                    Contact.primary_phone == val,
                    Contact.is_deleted.is_(False),
                )
            )
            contact = q.scalar_one_or_none()
        if contact:
            break

    created = False
    if not contact:
        created = True
        contact = Contact(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            name=payload.get("name"),
            primary_email=payload.get("email") or payload.get("primary_email"),
            primary_phone=payload.get("phone") or payload.get("primary_phone"),
            source=form.source_default,
            custom_fields={k: v for k, v in payload.items() if k.startswith("custom_")},
        )
        session.add(contact)
        await session.flush()
    elif not is_new:
        before = {"name": contact.name, "primary_email": contact.primary_email}
        if payload.get("name"):
            contact.name = payload.get("name")
        session.add(contact)
        await session.flush()

    return contact, created


async def resolve_deal(
    session: AsyncSession,
    contact: Contact,
    pipeline: Pipeline,
    entry_stage: Stage,
    policy: str,
) -> tuple[Deal, str]:
    q = await session.execute(
        select(Deal).where(
            Deal.contact_id == contact.id,
            Deal.pipeline_id == pipeline.id,
            Deal.status == "open",
        )
    )
    existing = q.scalar_one_or_none()
    if existing and policy == "reuse_open_deal":
        return existing, "reused"

    if policy == "reopen_latest" and not existing:
        q2 = await session.execute(
            select(Deal)
            .where(Deal.contact_id == contact.id, Deal.pipeline_id == pipeline.id)
            .order_by(Deal.created_at.desc())
            .limit(1)
        )
        latest = q2.scalar_one_or_none()
        if latest and latest.status != "open":
            latest.status = "open"
            latest.stage_id = entry_stage.id
            latest.stage_entered_at = datetime.now(UTC)
            latest.closed_at = None
            return latest, "reopened"

    now = datetime.now(UTC)
    deal = Deal(
        id=uuid.uuid4(),
        workspace_id=contact.workspace_id,
        contact_id=contact.id,
        pipeline_id=pipeline.id,
        stage_id=entry_stage.id,
        status="open",
        stage_entered_at=now,
        source_form_id=None,
    )
    session.add(deal)
    await session.flush()
    return deal, "created"


async def process_form_submission(
    session: AsyncSession,
    form: Form,
    payload: dict,
    actor: Actor | None = None,
) -> dict:
    pipeline = await session.get(Pipeline, form.target_pipeline_id)
    entry_stage = await session.get(Stage, form.entry_stage_id)
    if not pipeline or not entry_stage:
        raise ValueError("Form pipeline/stage misconfigured")

    policy = form.resubmission_policy or pipeline.resubmission_policy or "reuse_open_deal"
    contact, contact_created = await upsert_contact(
        session, form.workspace_id, payload, form.matching_keys or ["email", "phone"], form, is_new=True
    )

    deal, deal_action = await resolve_deal(session, contact, pipeline, entry_stage, policy)
    deal.source_form_id = form.id

    submission = FormSubmission(
        id=uuid.uuid4(),
        form_id=form.id,
        contact_id=contact.id,
        deal_id=deal.id,
        payload=payload,
    )
    session.add(submission)
    await session.flush()

    actor = actor or Actor(actor_type="form", id=str(form.id))

    if contact_created:
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

    deal_event = f"deal.{deal_action}"
    await mutate(
        session,
        MutateRequest(
            entity=deal,
            entity_type="deal",
            action="created" if deal_action == "created" else deal_action,
            changes=[FieldChange("stage_id", None, str(deal.stage_id))],
            actor=actor,
            pipeline_id=pipeline.id,
            stage_to_id=deal.stage_id,
            after_snapshot={"status": deal.status, "stage_id": str(deal.stage_id)},
            outbox_event_type=deal_event,
            idempotency_key=f"{deal_event}:{deal.id}:{submission.id}",
            outbox_payload={
                "deal_id": str(deal.id),
                "contact_id": str(contact.id),
                "form_id": str(form.id),
                "submission_id": str(submission.id),
            },
            timeline=True,
            timeline_type="form_submitted",
            timeline_body=f"Form {form.name} submitted",
            contact_id=contact.id,
            deal_id=deal.id,
        ),
    )

    await mutate(
        session,
        MutateRequest(
            entity=submission,
            entity_type="form_submission",
            action="form_submitted",
            changes=[],
            actor=actor,
            workspace_id=form.workspace_id,
            outbox_event_type="form.submitted",
            idempotency_key=f"form.submitted:{submission.id}",
            outbox_payload={
                "form_id": str(form.id),
                "contact_id": str(contact.id),
                "deal_id": str(deal.id),
                "submission_id": str(submission.id),
            },
            contact_id=contact.id,
            deal_id=deal.id,
        ),
    )

    return {
        "contact_id": str(contact.id),
        "deal_id": str(deal.id),
        "submission_id": str(submission.id),
        "deal_action": deal_action,
    }
