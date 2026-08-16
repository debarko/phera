from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from phera.db.models import OutboxEvent, Workflow, WorkflowRun
from phera.modules.workflows.engine import continue_run, match_workflows, start_workflow_run
from phera.worker.runner import process_outbox_event


@pytest.mark.asyncio
async def test_workflow_matches_form_submitted(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    wf = Workflow(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        name="On submit",
        version=1,
        is_active=True,
        is_draft=False,
        graph={
            "nodes": [
                {"id": "trigger", "kind": "trigger", "type": "event"},
                {"id": "send-1", "type": "send", "data": {"body": "Thanks"}},
            ],
            "edges": [{"id": "e1", "source": "trigger", "target": "send-1"}],
        },
        trigger_filter={"event_types": ["form.submitted"]},
    )
    db_session.add(wf)
    await db_session.commit()

    event = OutboxEvent(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        event_type="form.submitted",
        entity_type="form_submission",
        entity_id=uuid.uuid4(),
        idempotency_key="form.submitted:1",
        payload={"contact_id": str(uuid.uuid4()), "deal_id": str(uuid.uuid4())},
        status="pending",
    )
    matched = await match_workflows(db_session, event)
    assert len(matched) == 1
    assert matched[0].id == wf.id


@pytest.mark.asyncio
async def test_workflow_run_idempotent(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    wf = Workflow(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        name="Idempotent",
        version=1,
        is_active=True,
        is_draft=False,
        graph={"nodes": [{"id": "trigger", "kind": "trigger", "type": "event"}], "edges": []},
        trigger_filter={"event_types": ["deal.created"]},
    )
    db_session.add(wf)
    await db_session.flush()

    from tests.support.factories import contact as make_contact
    from tests.support.factories import deal as make_deal

    contact = make_contact(ws.id, email="wf@test.com")
    db_session.add(contact)
    await db_session.flush()
    deal = make_deal(
        ws.id,
        contact.id,
        workspace_bundle.pipeline.id,
        workspace_bundle.stage_new.id,
    )
    db_session.add(deal)
    await db_session.flush()

    event = OutboxEvent(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        event_type="deal.created",
        entity_type="deal",
        entity_id=deal.id,
        idempotency_key="deal.created:abc",
        payload={"deal_id": str(deal.id), "contact_id": str(contact.id)},
        status="pending",
    )
    run1 = await start_workflow_run(db_session, wf, event)
    run2 = await start_workflow_run(db_session, wf, event)
    await db_session.commit()
    assert run1 is not None
    assert run2 is None


@pytest.mark.asyncio
async def test_process_outbox_marks_event_processed(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    event = OutboxEvent(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        event_type="contact.created",
        entity_type="contact",
        entity_id=uuid.uuid4(),
        idempotency_key="contact.created:xyz",
        payload={"contact_id": str(uuid.uuid4())},
        status="pending",
    )
    db_session.add(event)
    await db_session.commit()

    with patch("phera.worker.runner.get_settings") as gs:
        gs.return_value.worker_queue_list = ["lifecycle"]
        await process_outbox_event(db_session, event.id)
    await db_session.commit()

    refreshed = await db_session.get(OutboxEvent, event.id)
    assert refreshed.status == "processed"
