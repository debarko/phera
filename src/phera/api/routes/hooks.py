from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_db, get_workspace
from phera.db.models import Workspace
from phera.modules.adapters.superhealth.webhooks import handle_service_event_completed
from phera.modules.connectors.gallabox import parse_inbound as parse_gallabox
from phera.modules.connectors.gallabox import verify_signature as verify_gallabox
from phera.modules.connectors.google_group import parse_inbound as parse_email
from phera.modules.connectors.google_group import verify_signature as verify_email
from phera.modules.tickets.inbound import ingest_inbound_message
from phera.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hooks"])


@router.post("/hooks/{connector_id}/{channel}")
async def inbound_webhook(
    connector_id: str,
    channel: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
):
    raw = await request.body()
    settings = get_settings()

    if connector_id == "gallabox":
        signature = request.headers.get("x-gallabox-signature")
        if not verify_gallabox(raw, signature, settings.gallabox_webhook_secret):
            raise HTTPException(401, "Invalid Gallabox signature")
    elif connector_id in ("google_group", "email"):
        signature = request.headers.get("x-email-signature") or request.headers.get("x-webhook-secret")
        if not verify_email(raw, signature, settings.email_webhook_secret):
            raise HTTPException(401, "Invalid email webhook signature")

    try:
        body = json.loads(raw.decode() or "{}") if raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON object required")

    if connector_id == "superhealth" and channel == "service_event":
        result = await handle_service_event_completed(session, workspace, body)
        return {"received": True, **result}

    if connector_id == "gallabox":
        inbound = parse_gallabox(body)
        if inbound is None:
            return {"received": True, "ignored": True, "reason": "status_or_unparseable"}
        try:
            result = await ingest_inbound_message(session, workspace, inbound)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"received": True, **result}

    if connector_id in ("google_group", "email"):
        inbound = parse_email(body)
        if inbound is None:
            return {"received": True, "ignored": True, "reason": "unparseable"}
        try:
            result = await ingest_inbound_message(session, workspace, inbound)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"received": True, **result}

    logger.info("Unhandled hook connector=%s channel=%s keys=%s", connector_id, channel, list(body.keys()))
    return {
        "received": True,
        "connector_id": connector_id,
        "channel": channel,
        "payload_keys": list(body.keys()),
    }
