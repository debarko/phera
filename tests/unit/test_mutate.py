from __future__ import annotations

import uuid

import pytest

from phera.authz.actor import Actor
from phera.db.models import AuditEvent, Interaction, OutboxEvent
from phera.db.mutate import FieldChange, MutateRequest, compute_diff, mutate
from tests.support.fakes import RecordingSession


class TestComputeDiff:
    def test_detects_changes(self):
        before = {"name": "Ada", "stage_id": "a"}
        after = {"name": "Ada", "stage_id": "b"}
        changes = compute_diff(before, after)
        assert len(changes) == 1
        assert changes[0].field == "stage_id"

    def test_ignores_timestamps(self):
        before = {"created_at": "t1", "name": "Ada"}
        after = {"created_at": "t2", "name": "Ada"}
        assert compute_diff(before, after) == []

    def test_field_change_to_dict(self):
        assert FieldChange("status", "open", "closed").to_dict() == {
            "field": "status",
            "from": "open",
            "to": "closed",
        }


@pytest.mark.asyncio
class TestMutate:
    async def test_noop_when_no_changes_and_action_not_special(self):
        session = RecordingSession()

        class Entity:
            id = uuid.uuid4()
            workspace_id = uuid.uuid4()

        result = await mutate(
            session,
            MutateRequest(entity=Entity(), entity_type="deal", action="updated", changes=[]),
        )
        assert result is None
        assert session.added == []

    async def test_writes_audit_outbox_and_timeline(self):
        session = RecordingSession()
        ws_id = uuid.uuid4()
        contact_id = uuid.uuid4()
        deal_id = uuid.uuid4()

        class DealEntity:
            id = deal_id
            workspace_id = ws_id

        actor = Actor(id="staff-1")
        audit = await mutate(
            session,
            MutateRequest(
                entity=DealEntity(),
                entity_type="deal",
                action="created",
                changes=[FieldChange("stage_id", None, "stage-1")],
                actor=actor,
                outbox_event_type="deal.created",
                idempotency_key=f"deal.created:{deal_id}",
                outbox_payload={"deal_id": str(deal_id)},
                timeline=True,
                timeline_body="Created",
                contact_id=contact_id,
                deal_id=deal_id,
            ),
        )

        assert audit is not None
        assert isinstance(audit, AuditEvent)
        assert len(session.added) == 3
        assert any(isinstance(o, OutboxEvent) for o in session.added)
        assert any(isinstance(o, Interaction) for o in session.added)
        assert session.info["outbox_notify_ids"]

    async def test_form_submitted_without_field_changes(self):
        session = RecordingSession()
        ws_id = uuid.uuid4()

        class Submission:
            id = uuid.uuid4()

        audit = await mutate(
            session,
            MutateRequest(
                entity=Submission(),
                entity_type="form_submission",
                action="form_submitted",
                changes=[],
                workspace_id=ws_id,
                outbox_event_type="form.submitted",
                idempotency_key="form.submitted:1",
            ),
        )
        assert audit is not None
        assert audit.workspace_id == ws_id
        assert len(session.added) >= 2
