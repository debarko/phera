from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor
from phera.db.commit import track_outbox_notify
from phera.db.models import AuditEvent, Interaction, OutboxEvent, Ticket
from phera.modules.tickets.activity import touch_ticket_activity
from phera.observability.otel import get_tracer, record_mutate


@dataclass
class FieldChange:
    field: str
    from_value: Any
    to_value: Any

    def to_dict(self) -> dict:
        return {"field": self.field, "from": self.from_value, "to": self.to_value}


@dataclass
class MutateRequest:
    entity: Any
    entity_type: str
    action: str
    changes: list[FieldChange] = field(default_factory=list)
    actor: Actor | None = None
    pipeline_id: uuid.UUID | None = None
    stage_from_id: uuid.UUID | None = None
    stage_to_id: uuid.UUID | None = None
    after_snapshot: dict | None = None
    context: dict = field(default_factory=dict)
    timeline: bool = False
    timeline_body: str | None = None
    timeline_type: str | None = None
    outbox_event_type: str | None = None
    outbox_payload: dict | None = None
    idempotency_key: str | None = None
    contact_id: uuid.UUID | None = None
    deal_id: uuid.UUID | None = None
    ticket_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None


def _entity_to_dict(entity: Any) -> dict:
    data = {}
    for col in entity.__table__.columns:
        val = getattr(entity, col.name)
        if isinstance(val, uuid.UUID):
            val = str(val)
        elif isinstance(val, datetime):
            val = val.isoformat()
        data[col.name] = val
    return data


def compute_diff(before: dict, after: dict) -> list[FieldChange]:
    changes = []
    for key, new_val in after.items():
        if key in ("created_at", "updated_at", "last_activity_at"):
            continue
        old_val = before.get(key)
        if old_val != new_val:
            changes.append(FieldChange(field=key, from_value=old_val, to_value=new_val))
    return changes


async def mutate(session: AsyncSession, req: MutateRequest) -> AuditEvent | None:
    if not req.changes and req.action not in ("created", "form_submitted"):
        return None

    start = time.perf_counter()
    tracer = get_tracer()
    workspace_id = req.workspace_id or getattr(req.entity, "workspace_id", None)
    entity_id = getattr(req.entity, "id", None)
    now = datetime.now(UTC)

    with tracer.start_as_current_span(
        "phera.mutate",
        attributes={
            "entity_type": req.entity_type,
            "action": req.action,
            "workspace_id": str(workspace_id) if workspace_id else None,
        },
    ):
        audit = AuditEvent(
            id=uuid.uuid4(),
            occurred_at=now,
            workspace_id=workspace_id,
            actor_type=req.actor.actor_type if req.actor else "system",
            actor_id=req.actor.id if req.actor else None,
            entity_type=req.entity_type,
            entity_id=entity_id,
            action=req.action,
            pipeline_id=req.pipeline_id,
            stage_from_id=req.stage_from_id,
            stage_to_id=req.stage_to_id,
            changes=[c.to_dict() for c in req.changes],
            after=req.after_snapshot,
            context=req.context,
        )
        session.add(audit)

        if req.outbox_event_type and req.idempotency_key:
            outbox = OutboxEvent(
                id=uuid.uuid4(),
                occurred_at=now,
                workspace_id=workspace_id,
                event_type=req.outbox_event_type,
                entity_type=req.entity_type,
                entity_id=entity_id,
                idempotency_key=req.idempotency_key,
                payload=req.outbox_payload or {},
                status="pending",
            )
            session.add(outbox)
            track_outbox_notify(session, outbox.id)

        if req.timeline and req.contact_id:
            session.add(
                Interaction(
                    id=uuid.uuid4(),
                    workspace_id=workspace_id,
                    contact_id=req.contact_id,
                    deal_id=req.deal_id,
                    ticket_id=req.ticket_id,
                    type=req.timeline_type or req.action,
                    body=req.timeline_body,
                    occurred_at=now,
                    actor_type=req.actor.actor_type if req.actor else "system",
                    actor_id=req.actor.id if req.actor else None,
                )
            )

        if req.entity_type == "ticket" and isinstance(req.entity, Ticket):
            touch_ticket_activity(req.entity, now)
        elif req.ticket_id is not None:
            ticket = await session.get(Ticket, req.ticket_id)
            if ticket is not None:
                touch_ticket_activity(ticket, now)

        record_mutate(req.entity_type, req.action, time.perf_counter() - start)
        return audit
