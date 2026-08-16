from __future__ import annotations

import pytest
from sqlalchemy import func, select

from phera.authz.actor import Actor
from phera.db.models import AuditEvent, Contact, Deal, FormSubmission, OutboxEvent
from phera.db.mutate import FieldChange, MutateRequest, mutate
from phera.modules.pipelines.intake import process_form_submission
from tests.support import factories


@pytest.mark.asyncio
async def test_mutate_persists_audit_and_outbox(db_session, workspace_bundle):
    ws_id = workspace_bundle.workspace.id

    from tests.support.factories import contact as make_contact

    c = make_contact(ws_id, email="mutate@test.com")
    db_session.add(c)
    await db_session.flush()

    await mutate(
        db_session,
        MutateRequest(
            entity=c,
            entity_type="contact",
            action="created",
            changes=[FieldChange("id", None, str(c.id))],
            actor=Actor(id="staff-1"),
            outbox_event_type="contact.created",
            idempotency_key=f"contact.created:{c.id}",
            contact_id=c.id,
        ),
    )
    await db_session.commit()

    audit_count = await db_session.scalar(select(func.count()).select_from(AuditEvent))
    outbox_count = await db_session.scalar(select(func.count()).select_from(OutboxEvent))
    assert audit_count == 1
    assert outbox_count == 1


@pytest.mark.asyncio
async def test_two_different_forms_create_two_deals(db_session, workspace_bundle):
    """Different forms on different pipelines → two deals for the same contact (PRD §8.3)."""
    ws = workspace_bundle.workspace
    skin_pipeline = factories.pipeline(ws.id, slug="skin_consult", name="Skin Consult")
    db_session.add(skin_pipeline)
    await db_session.flush()
    skin_stage = factories.stage(skin_pipeline.id, name="New", position=0)
    db_session.add(skin_stage)
    await db_session.flush()
    form_b = factories.form(
        ws.id, skin_pipeline.id, skin_stage.id, slug="skin-intake-b", name="Skin B"
    )
    db_session.add(form_b)
    await db_session.commit()

    actor = Actor(actor_type="form", id=str(workspace_bundle.form_a.id))
    payload = {"email": "two-forms@test.com", "phone": "+911111111111", "name": "Two Forms"}

    r1 = await process_form_submission(db_session, workspace_bundle.form_a, payload, actor)
    await db_session.commit()
    r2 = await process_form_submission(db_session, form_b, payload, actor)
    await db_session.commit()

    assert r1["deal_id"] != r2["deal_id"]
    deals = (await db_session.execute(select(Deal))).scalars().all()
    assert len(deals) == 2
    contacts = (await db_session.execute(select(Contact))).scalars().all()
    assert len(contacts) == 1


@pytest.mark.asyncio
async def test_two_ivf_submissions_reuse_open_deal(db_session, workspace_bundle):
    actor = Actor(actor_type="form", id=str(workspace_bundle.form_a.id))
    payload = {"email": "reuse@test.com", "phone": "+912222222222", "name": "Reuse Me"}

    r1 = await process_form_submission(db_session, workspace_bundle.form_a, payload, actor)
    await db_session.commit()
    r2 = await process_form_submission(db_session, workspace_bundle.form_a, payload, actor)
    await db_session.commit()

    assert r1["deal_id"] == r2["deal_id"]
    assert r2["deal_action"] == "reused"
    deals = (await db_session.execute(select(Deal))).scalars().all()
    assert len(deals) == 1
    submissions = (await db_session.execute(select(FormSubmission))).scalars().all()
    assert len(submissions) == 2


@pytest.mark.asyncio
async def test_form_submission_emits_audit_and_outbox(db_session, workspace_bundle):
    actor = Actor(actor_type="form", id=str(workspace_bundle.form_a.id))
    await process_form_submission(
        db_session,
        workspace_bundle.form_a,
        {"email": "audit@test.com", "phone": "+913333333333", "name": "Audit"},
        actor,
    )
    await db_session.commit()

    audits = (await db_session.execute(select(AuditEvent))).scalars().all()
    outbox = (await db_session.execute(select(OutboxEvent))).scalars().all()
    assert len(audits) >= 2
    assert any(e.event_type == "form.submitted" for e in outbox)
    assert any(e.event_type.startswith("deal.") for e in outbox)
