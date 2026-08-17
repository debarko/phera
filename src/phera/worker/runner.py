from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import (
    AgingSnapshotDaily,
    AuditEvent,
    Deal,
    LifecycleDestination,
    MetricDaily,
    OutboxEvent,
    RollupRun,
    WorkflowRun,
)
from phera.db.session import SessionLocal
from phera.modules.connectors.stubs import MoEngageLifecycleProvider
from phera.modules.workflows.engine import continue_run, match_workflows, start_workflow_run
from phera.observability.otel import init_otel, record_worker_job
from phera.settings import get_settings

logger = logging.getLogger(__name__)



async def dispatch_outbox_batch(session: AsyncSession, limit: int = 50) -> int:
    result = await session.execute(
        text(
            """
            SELECT id FROM outbox_events
            WHERE status = 'pending'
            ORDER BY occurred_at
            LIMIT :limit
            FOR UPDATE SKIP LOCKED
            """
        ),
        {"limit": limit},
    )
    ids = [row[0] for row in result.fetchall()]
    for oid in ids:
        await session.execute(
            update(OutboxEvent).where(OutboxEvent.id == oid).values(status="queued")
        )
        await process_outbox_event(session, oid)
    return len(ids)


async def process_outbox_event(session: AsyncSession, outbox_id: uuid.UUID) -> None:
    start = time.perf_counter()
    event = await session.get(OutboxEvent, outbox_id)
    if not event:
        return
    try:
        if "workflow" in get_settings().worker_queue_list:
            workflows = await match_workflows(session, event)
            for wf in workflows:
                run = await start_workflow_run(session, wf, event)
                if run:
                    await continue_run(session, run)

        if "lifecycle" in get_settings().worker_queue_list:
            await process_lifecycle(session, event)

        if event.event_type == "call.ended" and "maintenance" in get_settings().worker_queue_list:
            call_id = event.payload.get("call_id")
            if call_id:
                from phera.modules.transcription.job import transcribe_call

                await transcribe_call(session, uuid.UUID(call_id))

        if (
            event.event_type == "broadcast.requested"
            and "communication" in get_settings().worker_queue_list
        ):
            logger.info("Broadcast queued segment=%s", event.payload.get("segment_filter"))

        event.status = "processed"
        record_worker_job("outbox", event.event_type, time.perf_counter() - start)
    except Exception as exc:
        event.status = "dead" if event.attempts >= 5 else "pending"
        event.attempts += 1
        event.last_error = str(exc)
        record_worker_job("outbox", event.event_type, time.perf_counter() - start, error=True)
        logger.exception("Outbox processing failed %s", outbox_id)


async def process_lifecycle(session: AsyncSession, event: OutboxEvent) -> None:
    q = await session.execute(
        select(LifecycleDestination).where(
            LifecycleDestination.workspace_id == event.workspace_id,
            LifecycleDestination.is_active.is_(True),
        )
    )
    for dest in q.scalars().all():
        filt = dest.event_filter or {}
        types = filt.get("event_types") or []
        if types and event.event_type not in types and not any(
            event.event_type.startswith(t.rstrip("*")) for t in types if t.endswith("*")
        ):
            continue
        provider = MoEngageLifecycleProvider("app", "key")
        await provider.track(event.event_type, {"id": str(event.entity_id)}, event.payload)


async def process_delayed_wakes(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    q = await session.execute(
        select(WorkflowRun).where(
            WorkflowRun.status == "waiting",
            WorkflowRun.wake_at <= now,
        ).limit(20)
    )
    runs = q.scalars().all()
    for run in runs:
        run.status = "running"
        await continue_run(session, run)
    return len(runs)


async def run_daily_rollup(session: AsyncSession, workspace_id: uuid.UUID, day: date) -> None:
    existing = await session.get(RollupRun, (day, workspace_id))
    if existing is not None:
        if existing.status == "completed":
            return
        if existing.status == "running" and existing.finished_at is None:
            return

    run = existing or RollupRun(day=day, workspace_id=workspace_id)
    run.started_at = datetime.now(UTC)
    run.finished_at = None
    run.rows_in = 0
    run.rows_out = 0
    run.status = "running"
    if existing is None:
        session.add(run)
    await session.flush()

    start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    q = await session.execute(
        select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.occurred_at >= start,
            AuditEvent.occurred_at < end,
        )
    )
    events = q.scalars().all()
    run.rows_in = len(events)

    metrics = {"created": 0, "stage_changed": 0, "assigned": 0}
    for ev in events:
        if ev.action in metrics:
            metrics[ev.action] += 1

    session.add(
        MetricDaily(
            date=day,
            workspace_id=workspace_id,
            grain_hash="workspace",
            metrics=metrics,
        )
    )

    dq = await session.execute(
        select(Deal).where(Deal.workspace_id == workspace_id, Deal.status == "open")
    )
    open_deals = dq.scalars().all()
    for deal in open_deals:
        age = int((datetime.now(UTC) - (deal.created_at or datetime.now(UTC))).total_seconds())
        stage_age = int(
            (datetime.now(UTC) - (deal.stage_entered_at or datetime.now(UTC))).total_seconds()
        )
        session.add(
            AgingSnapshotDaily(
                date=day,
                workspace_id=workspace_id,
                entity_type="deal",
                entity_id=deal.id,
                pipeline_id=deal.pipeline_id,
                stage_id=deal.stage_id,
                owner_user_id=deal.owner_user_id,
                age_seconds=age,
                stage_age_seconds=stage_age,
                milestones={
                    "stage_entered_at": (
                        deal.stage_entered_at.isoformat() if deal.stage_entered_at else None
                    )
                },
            )
        )

    run.rows_out = 1 + len(open_deals)
    run.finished_at = datetime.now(UTC)
    run.status = "completed"


async def run_worker_loop() -> None:
    init_otel(role="worker")
    settings = get_settings()
    logger.info("Worker started queues=%s", settings.worker_queue_list)

    while True:
        try:
            async with SessionLocal() as session:
                if "maintenance" in settings.worker_queue_list:
                    from phera.db.models import Workspace

                    ws_q = await session.execute(select(Workspace).limit(1))
                    ws = ws_q.scalar_one_or_none()
                    if ws:
                        await run_daily_rollup(session, ws.id, date.today())

                if (
                    "workflow" in settings.worker_queue_list
                    or "lifecycle" in settings.worker_queue_list
                ):
                    await dispatch_outbox_batch(session)

                if "delayed" in settings.worker_queue_list:
                    await process_delayed_wakes(session)

                if "email_poll" in settings.worker_queue_list:
                    from phera.db.models import Workspace as _Workspace
                    from phera.modules.tickets.email_poll import poll_all_email_accounts

                    ws_q = await session.execute(select(_Workspace).limit(1))
                    ws = ws_q.scalar_one_or_none()
                    if ws:
                        await poll_all_email_accounts(session, ws)

                await session.commit()
        except Exception:
            logger.exception("Worker loop error")

        await asyncio.sleep(2)
