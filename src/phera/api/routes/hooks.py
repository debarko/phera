from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_db, get_workspace
from phera.db.models import Connector, Workspace
from phera.modules.adapters.superhealth.webhooks import handle_service_event_completed
from phera.modules.connectors.gallabox import parse_inbound as parse_gallabox
from phera.modules.connectors.gallabox import verify_signature as verify_gallabox
from phera.modules.connectors.google_group import parse_inbound as parse_email
from phera.modules.connectors.google_group import verify_signature as verify_email
from phera.modules.tickets.inbound import ingest_inbound_message, resolve_channel_account
from phera.security.crypto import decrypt_secrets
from phera.settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hooks"])


async def _gallabox_candidate_secrets(session: AsyncSession, workspace: Workspace) -> list[str]:
    """Every active Gallabox connector's webhook_secret, for the fallback path when a
    payload can't be bound to one specific channel (status events, unparseable bodies,
    unknown/ambiguous number)."""
    q = await session.execute(
        select(Connector).where(
            Connector.workspace_id == workspace.id,
            Connector.type == "gallabox",
            Connector.is_active.is_(True),
        )
    )
    return [
        decrypt_secrets(c.secrets_encrypted).get("webhook_secret", "")
        for c in q.scalars().all()
        if c.secrets_encrypted
    ]


async def _verify_gallabox_webhook(
    session: AsyncSession,
    workspace: Workspace,
    inbound: dict | None,
    raw: bytes,
    signature: str | None,
) -> None:
    settings = get_settings()
    bound_secret: str | None = None

    if inbound is not None:
        # A real message with a resolvable target — verify against ONLY that number's
        # connector secret, so one number's secret can't authenticate another number's
        # payload (a valid signature from Number B must not let a message be attributed
        # to Number A's ticket).
        channel = await resolve_channel_account(
            session,
            workspace.id,
            kind="messaging",
            adapter_type="gallabox",
            address_hint=inbound.get("address_hint"),
        )
        if channel and channel.connector_id:
            connector = await session.get(Connector, channel.connector_id)
            if connector and connector.secrets_encrypted:
                bound_secret = decrypt_secrets(connector.secrets_encrypted).get(
                    "webhook_secret", ""
                )

    if bound_secret:
        if not verify_gallabox(raw, signature, bound_secret):
            raise HTTPException(401, "Invalid Gallabox signature")
        return

    # No specific channel could be bound (status/unparseable event, unknown or ambiguous
    # number, or a matched channel with no connector secret configured) — these payloads
    # cannot be attributed to any one ticket/channel, so there is nothing for a forged
    # signature to misroute. Fall back to checking against every configured secret.
    candidates = await _gallabox_candidate_secrets(session, workspace)
    non_empty = candidates or (
        [settings.gallabox_webhook_secret] if settings.gallabox_webhook_secret else []
    )
    non_empty = [s for s in non_empty if s]
    if non_empty and not any(verify_gallabox(raw, signature, s) for s in non_empty):
        raise HTTPException(401, "Invalid Gallabox signature")
    # non_empty == [] means no Gallabox connector (DB or legacy env) has a secret
    # configured anywhere — open, matching today's local-dev default.


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

    try:
        body = json.loads(raw.decode() or "{}") if raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Invalid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON object required")

    gallabox_inbound: dict | None = None
    if connector_id == "gallabox":
        signature = request.headers.get("x-gallabox-signature")
        gallabox_inbound = parse_gallabox(body)
        await _verify_gallabox_webhook(session, workspace, gallabox_inbound, raw, signature)
    elif connector_id in ("google_group", "email"):
        signature = request.headers.get("x-email-signature") or request.headers.get(
            "x-webhook-secret"
        )
        if not verify_email(raw, signature, settings.email_webhook_secret):
            raise HTTPException(401, "Invalid email webhook signature")

    if connector_id == "superhealth" and channel == "service_event":
        result = await handle_service_event_completed(session, workspace, body)
        return {"received": True, **result}

    if connector_id == "gallabox":
        if gallabox_inbound is None:
            return {"received": True, "ignored": True, "reason": "status_or_unparseable"}
        try:
            result = await ingest_inbound_message(session, workspace, gallabox_inbound)
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

    logger.info(
        "Unhandled hook connector=%s channel=%s keys=%s", connector_id, channel, list(body.keys())
    )
    return {
        "received": True,
        "connector_id": connector_id,
        "channel": channel,
        "payload_keys": list(body.keys()),
    }
