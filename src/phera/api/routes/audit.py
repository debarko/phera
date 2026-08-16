from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import AuditEventOut
from phera.authz.actor import Actor
from phera.db.models import AuditEvent, Workspace

router = APIRouter(tags=["audit"])


@router.get("/{entity_type}/{entity_id}/history", response_model=list[AuditEventOut])
async def entity_history(
    entity_type: str,
    entity_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == workspace.id,
            AuditEvent.entity_type == entity_type,
            AuditEvent.entity_id == entity_id,
        )
        .order_by(AuditEvent.occurred_at.desc())
        .limit(200)
    )
    return q.scalars().all()
