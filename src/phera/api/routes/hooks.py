from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_db, get_workspace
from phera.db.models import Workspace
from phera.modules.adapters.superhealth.webhooks import handle_service_event_completed

router = APIRouter(tags=["hooks"])


@router.post("/hooks/{connector_id}/{channel}")
async def inbound_webhook(
    connector_id: str,
    channel: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
):
    body = await request.json()

    if connector_id == "superhealth" and channel == "service_event":
        result = await handle_service_event_completed(session, workspace, body)
        return {"received": True, **result}

    return {
        "received": True,
        "connector_id": connector_id,
        "channel": channel,
        "payload_keys": list(body.keys()),
    }
