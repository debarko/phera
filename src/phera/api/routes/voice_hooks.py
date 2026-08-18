"""Exotel voice webhooks — routing decision + call lifecycle.

Two distinct callbacks, both authenticated with a `?token=` query param bound to the
specific exophone's Connector (not "any active Exotel connector in the workspace") —
Exotel's callbacks are unsigned, so this token is the substitute for the HMAC-header
`verify_signature_fn` pattern Gallabox uses, and binding it per-resolved-channel (rather
than checking against every configured secret) avoids the same cross-number auth-bypass
shape that was previously found and fixed in the Gallabox webhook.

- `/hooks/exotel/route` — the Exotel ExoML Connect-applet **dynamic URL**. Called per
  inbound call, before anyone's phone rings. Resolves/creates the contact+ticket, decides
  (or reuses) the assigned agent, and returns that agent's SIP identity as plain text —
  this is the moment "a call gets assigned to someone" actually happens.
- `/hooks/exotel/call` — StatusCallback/Passthru call-lifecycle events (ringing/answered/
  ended). Updates the `Call` row already created by the routing webhook and publishes SSE
  events for the dashboard's DialerWidget.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_db, get_workspace
from phera.db.commit import commit_and_notify
from phera.db.models import AgentPresence, AgentTelephonyIdentity, Call, Connector, RoutingPolicy, Ticket, Workspace
from phera.modules.routing.engine import select_agent_for_voice
from phera.modules.tickets.inbound import resolve_call_ticket, resolve_channel_account
from phera.modules.tickets.inbox_events import publish_inbox_event
from phera.modules.transcription.job import transcribe_call
from phera.security.crypto import decrypt_secrets

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voice-hooks"])

_ANSWERED_STATUSES = {"in-progress", "in_progress", "answered"}
_ENDED_STATUSES = {
    "completed",
    "busy",
    "no-answer",
    "no_answer",
    "failed",
    "canceled",
    "cancelled",
}
_RINGING_STATUSES = {"ringing", "queued", "initiated"}


def _first(*values: Any) -> str | None:
    for value in values:
        if value not in (None, ""):
            return str(value)
    return None


async def _request_params(request: Request) -> dict[str, str]:
    params = dict(request.query_params)
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            params.update({k: str(v) for k, v in form.items()})
    return params


async def _verify_bound_token(
    session: AsyncSession, channel_connector_id: uuid.UUID | None, token: str
) -> None:
    """Check `token` against the specific resolved channel's own Connector secret —
    never against "any active Exotel connector" (see module docstring)."""
    bound_token = ""
    if channel_connector_id:
        connector = await session.get(Connector, channel_connector_id)
        if connector and connector.secrets_encrypted:
            bound_token = decrypt_secrets(connector.secrets_encrypted).get("webhook_token", "")
    if not bound_token or token != bound_token:
        raise HTTPException(401, "Invalid webhook token")


def _lifecycle_event_type(call_status: str | None) -> str:
    status = (call_status or "").strip().lower()
    if status in _ANSWERED_STATUSES:
        return "call.answered"
    if status in _ENDED_STATUSES:
        return "call.ended"
    if status in _RINGING_STATUSES:
        return "call.ringing"
    return "call.updated"


@router.get(
    "/hooks/exotel/route", response_class=PlainTextResponse, operation_id="exotel_route_call_get"
)
@router.post(
    "/hooks/exotel/route", response_class=PlainTextResponse, operation_id="exotel_route_call_post"
)
async def exotel_route_call(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
) -> PlainTextResponse:
    params = await _request_params(request)
    from_number = _first(params.get("From"), params.get("CallFrom"), params.get("from"))
    to_number = _first(params.get("To"), params.get("CallTo"), params.get("to"))
    call_sid = _first(params.get("CallSid"), params.get("CallSID"), params.get("call_sid"))
    if not from_number or not to_number:
        raise HTTPException(400, "Missing From/To in routing webhook payload")

    channel = await resolve_channel_account(
        session, workspace.id, kind="voice", adapter_type="exotel", address_hint=to_number
    )
    if not channel:
        raise HTTPException(404, "No active Exotel voice channel for this exophone")
    await _verify_bound_token(session, channel.connector_id, token)

    _, contact, ticket, created = await resolve_call_ticket(
        session,
        workspace,
        adapter_type="exotel",
        from_number=from_number,
        to_number=to_number,
    )

    target_user_id = ticket.assignee_user_id
    if not target_user_id:
        policy = (
            await session.get(RoutingPolicy, channel.routing_policy_id)
            if channel.routing_policy_id
            else None
        )
        agent = await select_agent_for_voice(session, policy)
        if agent:
            target_user_id = agent.user_id
            ticket.assignee_user_id = target_user_id
            ticket.first_assigned_at = ticket.first_assigned_at or datetime.now(UTC)

    if not target_user_id:
        await commit_and_notify(session)
        logger.warning(
            "No available agent for inbound Exotel call to=%s from=%s", to_number, from_number
        )
        return PlainTextResponse("")

    identity = await session.get(AgentTelephonyIdentity, target_user_id)
    if not identity or not identity.is_active:
        await commit_and_notify(session)
        logger.warning("Assigned agent %s has no active voice identity", target_user_id)
        return PlainTextResponse("")

    call = Call(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        contact_id=contact.id,
        ticket_id=ticket.id,
        provider="exotel",
        provider_call_id=call_sid,
        direction="inbound",
        from_number=from_number,
        to_number=to_number,
        status="routing",
    )
    session.add(call)
    await commit_and_notify(session)
    await session.refresh(call)

    publish_inbox_event(
        {
            "type": "call.assigned",
            "ticket_id": str(ticket.id),
            "call_id": str(call.id),
            "provider_call_id": call_sid,
            "assignee_user_id": target_user_id,
            "contact_id": str(contact.id),
            "created_ticket": created,
        }
    )

    return PlainTextResponse(identity.sip_user)


@router.get("/hooks/exotel/call", operation_id="exotel_call_event_get")
@router.post("/hooks/exotel/call", operation_id="exotel_call_event_post")
async def exotel_call_event(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
) -> dict:
    params = await _request_params(request)
    call_sid = _first(params.get("CallSid"), params.get("CallSID"), params.get("call_sid"))
    if not call_sid:
        raise HTTPException(400, "Missing CallSid in call-event webhook payload")

    q = await session.execute(
        select(Call).where(Call.workspace_id == workspace.id, Call.provider_call_id == call_sid)
    )
    call = q.scalar_one_or_none()
    if not call:
        raise HTTPException(404, "Unknown call_sid — was the routing webhook called first?")

    channel = await resolve_channel_account(
        session, workspace.id, kind="voice", adapter_type="exotel", address_hint=call.to_number
    )
    await _verify_bound_token(session, channel.connector_id if channel else None, token)

    call_status = _first(params.get("CallStatus"), params.get("DialCallStatus"), params.get("Status"))
    duration = _first(
        params.get("DialCallDuration"), params.get("Duration"), params.get("CallDuration")
    )
    recording_url = _first(params.get("RecordingUrl"), params.get("RecordingURL"))

    event_type = _lifecycle_event_type(call_status)
    if call_status:
        call.status = str(call_status).lower()
    if duration:
        try:
            call.duration_seconds = int(duration)
        except ValueError:
            pass
    if recording_url:
        call.recording_url = recording_url

    ticket = await session.get(Ticket, call.ticket_id) if call.ticket_id else None
    if ticket and ticket.assignee_user_id and event_type in ("call.answered", "call.ended"):
        presence = await session.get(AgentPresence, ticket.assignee_user_id)
        if presence:
            presence.on_voice_call = event_type == "call.answered"

    await commit_and_notify(session)

    if call.ticket_id:
        publish_inbox_event(
            {
                "type": event_type,
                "ticket_id": str(call.ticket_id),
                "call_id": str(call.id),
                "provider_call_id": call.provider_call_id,
                "assignee_user_id": ticket.assignee_user_id if ticket else None,
                "status": call.status,
            }
        )

    if event_type == "call.ended" and call.recording_url:
        try:
            await transcribe_call(session, call.id)
            await commit_and_notify(session)
        except Exception:
            logger.exception("Failed to transcribe call %s", call.id)

    return {"received": True, "call_id": str(call.id), "event": event_type}
