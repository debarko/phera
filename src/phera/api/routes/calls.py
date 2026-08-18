from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import ORMModel
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify, track_outbox_notify
from phera.db.models import Call, Contact, OutboxEvent, Ticket, Transcript, Workspace
from phera.modules.connectors.stubs import StubTelephonyProvider
from phera.modules.tickets.activity import touch_ticket_activity
from phera.modules.tickets.inbox_events import publish_inbox_event

router = APIRouter(tags=["calls"])


class CallCreate(BaseModel):
    contact_id: uuid.UUID
    to_number: str
    ticket_id: uuid.UUID | None = None


class CallOut(ORMModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    ticket_id: uuid.UUID | None = None
    direction: str
    status: str
    provider_call_id: str | None
    to_number: str | None = None
    from_number: str | None = None
    duration_seconds: int | None = None


class TranscriptOut(ORMModel):
    id: uuid.UUID
    call_id: uuid.UUID
    status: str
    text: str | None
    summary: str | None


@router.post("/calls", response_model=CallOut, status_code=201)
async def start_call(
    body: CallCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    contact = await session.get(Contact, body.contact_id)
    if not contact or contact.workspace_id != workspace.id:
        raise HTTPException(404, "Contact not found")

    provider = StubTelephonyProvider()
    result = await provider.click_to_call(actor.id or "", body.to_number)
    call = Call(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        contact_id=body.contact_id,
        ticket_id=body.ticket_id,
        provider="stub",
        direction="outbound",
        status="initiated",
        provider_call_id=result.get("call_id"),
        to_number=body.to_number,
    )
    session.add(call)
    await session.flush()
    if body.ticket_id:
        ticket = await session.get(Ticket, body.ticket_id)
        if ticket is not None and ticket.workspace_id == workspace.id:
            touch_ticket_activity(ticket)
            if not ticket.assignee_user_id and actor.id:
                ticket.assignee_user_id = actor.id
                ticket.first_assigned_at = ticket.first_assigned_at or datetime.now(UTC)

    outbox = OutboxEvent(
        id=uuid.uuid4(),
        occurred_at=datetime.now(UTC),
        workspace_id=workspace.id,
        event_type="call.started",
        entity_type="call",
        entity_id=call.id,
        idempotency_key=f"call.started:{call.id}",
        payload={"call_id": str(call.id), "contact_id": str(body.contact_id)},
        status="pending",
    )
    session.add(outbox)
    track_outbox_notify(session, outbox.id)
    await commit_and_notify(session)
    if body.ticket_id:
        publish_inbox_event(
            {
                "type": "call.started",
                "workspace_id": str(workspace.id),
                "ticket_id": str(body.ticket_id),
                "actor_id": actor.id,
            }
        )
    await session.refresh(call)
    return call


@router.get("/calls/{call_id}/transcript", response_model=TranscriptOut | None)
async def get_transcript(
    call_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    call = await session.get(Call, call_id)
    if not call or call.workspace_id != workspace.id:
        raise HTTPException(404, "Call not found")
    q = await session.execute(select(Transcript).where(Transcript.call_id == call_id))
    return q.scalar_one_or_none()
