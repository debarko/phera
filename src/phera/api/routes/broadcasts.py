from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify, track_outbox_notify
from phera.db.models import OutboxEvent, Workspace

router = APIRouter(tags=["broadcasts"])


class BroadcastCreate(BaseModel):
    segment_filter: dict = Field(default_factory=dict)
    channel: str = "whatsapp"
    template: str | None = None
    body: str


@router.post("/broadcasts", status_code=202)
async def create_broadcast(
    body: BroadcastCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    broadcast_id = uuid.uuid4()
    outbox = OutboxEvent(
        id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        workspace_id=workspace.id,
        event_type="broadcast.requested",
        entity_type="broadcast",
        entity_id=broadcast_id,
        idempotency_key=f"broadcast.requested:{broadcast_id}",
        payload={
            "broadcast_id": str(broadcast_id),
            "segment_filter": body.segment_filter,
            "channel": body.channel,
            "template": body.template,
            "body": body.body,
            "requested_by": actor.id,
        },
        status="pending",
    )
    session.add(outbox)
    track_outbox_notify(session, outbox.id)
    await commit_and_notify(session)
    return {"id": str(broadcast_id), "status": "queued"}
