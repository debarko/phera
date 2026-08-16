from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.authz.actor import Actor
from phera.db.models import AgingSnapshotDaily, MetricDaily, Workspace

router = APIRouter(tags=["analytics"])


@router.get("/metrics/daily")
async def get_metric_daily(
    day: date | None = None,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = select(MetricDaily).where(MetricDaily.workspace_id == workspace.id)
    if day:
        q = q.where(MetricDaily.date == day)
    result = await session.execute(q.limit(500))
    return [row.metrics for row in result.scalars().all()]


@router.get("/metrics/aging")
async def get_aging_snapshots(
    day: date | None = None,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = select(AgingSnapshotDaily).where(AgingSnapshotDaily.workspace_id == workspace.id)
    if day:
        q = q.where(AgingSnapshotDaily.date == day)
    result = await session.execute(q.limit(500))
    return [
        {
            "entity_type": r.entity_type,
            "entity_id": str(r.entity_id),
            "age_seconds": r.age_seconds,
            "stage_age_seconds": r.stage_age_seconds,
        }
        for r in result.scalars().all()
    ]
