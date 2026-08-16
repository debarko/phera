from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor
from phera.db.models import OutboxEvent, Workflow, WorkflowNodeRun, WorkflowRun
from phera.db.mutate import FieldChange, MutateRequest, mutate
from phera.modules.connectors.stubs import MoEngageLifecycleProvider, StubMessagingProvider
from phera.modules.workflows.catalog import WORKFLOW_NODE_TYPES
from phera.modules.workflows.matching import event_matches_trigger

logger = logging.getLogger(__name__)

WORKFLOW_NODE_TYPES  # re-export


async def match_workflows(session: AsyncSession, event: OutboxEvent) -> list[Workflow]:
    q = await session.execute(
        select(Workflow).where(
            Workflow.workspace_id == event.workspace_id,
            Workflow.is_active.is_(True),
            Workflow.is_draft.is_(False),
        )
    )
    workflows = q.scalars().all()
    matched = []
    for wf in workflows:
        if event_matches_trigger(event.event_type, wf.trigger_filter):
            matched.append(wf)
    return matched


async def start_workflow_run(
    session: AsyncSession, workflow: Workflow, event: OutboxEvent
) -> WorkflowRun | None:
    idem = f"{workflow.id}:{event.idempotency_key}"
    existing = await session.execute(
        select(WorkflowRun).where(
            WorkflowRun.workflow_id == workflow.id,
            WorkflowRun.idempotency_key == idem,
        )
    )
    if existing.scalar_one_or_none():
        return None

    run = WorkflowRun(
        id=uuid.uuid4(),
        workflow_id=workflow.id,
        version=workflow.version,
        status="running",
        current_node="trigger",
        context={"trigger_event": event.payload, "event_type": event.event_type},
        idempotency_key=idem,
        deal_id=uuid.UUID(event.payload["deal_id"]) if event.payload.get("deal_id") else None,
        contact_id=(
            uuid.UUID(event.payload["contact_id"]) if event.payload.get("contact_id") else None
        ),
    )
    session.add(run)
    await session.flush()
    return run


async def execute_node(
    session: AsyncSession, run: WorkflowRun, workflow: Workflow, node_id: str
) -> str | None:
    graph = workflow.graph or {}
    nodes = {n["id"]: n for n in graph.get("nodes", [])}
    edges = graph.get("edges", [])
    node = nodes.get(node_id)
    if not node:
        run.status = "completed"
        return None

    node_type = node.get("type")
    config = node.get("data", {})

    node_run = WorkflowNodeRun(
        id=uuid.uuid4(),
        workflow_run_id=run.id,
        node_id=node_id,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(node_run)

    actor = Actor(actor_type="workflow", id=str(run.id))

    try:
        if node_type == "send":
            provider = StubMessagingProvider()
            to = config.get("to") or run.context.get("phone")
            msg_id = await provider.send(to, config.get("body", ""), config.get("template"))
            node_run.provider_message_id = msg_id
        elif node_type == "wait":
            hours = config.get("hours", 1)
            run.wake_at = datetime.now(UTC) + timedelta(hours=hours)
            run.current_node = node_id
            run.status = "waiting"
            node_run.status = "waiting"
            node_run.finished_at = datetime.now(UTC)
            return "delayed"
        elif node_type == "update_field":
            deal_id = run.deal_id
            if deal_id:
                from phera.db.models import Deal

                deal = await session.get(Deal, deal_id)
                if deal:
                    field = config.get("field")
                    value = config.get("value")
                    old = (
                        getattr(deal, field.split(".")[-1], None)
                        if "." not in field
                        else deal.custom_fields.get(field.split(".")[-1])
                    )
                    if field.startswith("custom_fields."):
                        key = field.split(".", 1)[1]
                        deal.custom_fields = {**deal.custom_fields, key: value}
                    await session.flush()
                    await mutate(
                        session,
                        MutateRequest(
                            entity=deal,
                            entity_type="deal",
                            action="updated",
                            changes=[FieldChange(field, old, value)],
                            actor=actor,
                            context={"workflow_run_id": str(run.id)},
                            deal_id=deal.id,
                            contact_id=deal.contact_id,
                        ),
                    )
        elif node_type == "emit_destination":
            provider = MoEngageLifecycleProvider("app", "key")
            await provider.track(
                config.get("event", "phera.event"),
                {"id": str(run.contact_id)},
                run.context,
            )
        node_run.status = "completed"
        node_run.finished_at = datetime.now(UTC)
    except Exception as exc:
        node_run.status = "failed"
        node_run.error = str(exc)
        run.status = "failed"
        raise

    next_edges = [e for e in edges if e.get("source") == node_id]
    if not next_edges:
        run.status = "completed"
        return None
    return next_edges[0].get("target")


async def continue_run(session: AsyncSession, run: WorkflowRun) -> None:
    workflow = await session.get(Workflow, run.workflow_id)
    if not workflow:
        return
    node_id = run.current_node or "trigger"
    if node_id == "trigger":
        nodes = workflow.graph.get("nodes", []) if workflow.graph else []
        triggers = [n for n in nodes if n.get("kind") == "trigger"]
        node_id = triggers[0]["id"] if triggers else None
    while node_id:
        result = await execute_node(session, run, workflow, node_id)
        if result == "delayed":
            break
        if not result:
            break
        node_id = result
        run.current_node = node_id
