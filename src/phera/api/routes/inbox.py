from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import PresenceOut, PresenceUpdate, TicketDetailOut
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import AgentPresence, ChannelAccount, Ticket, TicketOffer, Workspace
from phera.modules.tickets.activity import touch_ticket_activity
from phera.modules.tickets.enrichment import ticket_detail_dict

router = APIRouter(tags=["inbox"])

_inbox_subscribers: set[asyncio.Queue[str]] = set()

_PRIORITY_RANK = case(
    (Ticket.priority.in_(("high", "urgent")), 0),
    (Ticket.priority == "low", 2),
    else_=1,
)


def _apply_inbox_order(q, bucket: str):
    if bucket == "mine":
        return q.order_by(Ticket.last_activity_at.desc())
    if bucket == "unassigned":
        return q.order_by(_PRIORITY_RANK.asc(), Ticket.last_activity_at.asc())
    if bucket == "offered":
        return q.order_by(
            TicketOffer.expires_at.asc().nulls_last(),
            Ticket.last_activity_at.asc(),
        )
    return q


def _publish_inbox_event(payload: dict) -> None:
    data = json.dumps(payload)
    for queue in list(_inbox_subscribers):
        try:
            queue.put_nowait(data)
        except asyncio.QueueFull:
            pass


@router.get("/inbox/tickets", response_model=list[TicketDetailOut])
async def list_inbox_tickets(
    bucket: str = "mine",
    channel: str | None = None,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = select(Ticket).where(Ticket.workspace_id == workspace.id)
    if bucket == "mine":
        q = q.where(Ticket.assignee_user_id == actor.id)
    elif bucket == "offered":
        q = q.join(
            TicketOffer,
            (TicketOffer.ticket_id == Ticket.id)
            & (TicketOffer.user_id == actor.id)
            & (TicketOffer.status == "offered"),
        )
    elif bucket == "unassigned":
        q = q.where(Ticket.assignee_user_id.is_(None), Ticket.status == "open")
    else:
        raise HTTPException(400, "Invalid bucket")

    if channel:
        q = q.join(ChannelAccount, Ticket.channel_account_id == ChannelAccount.id).where(
            ChannelAccount.kind == channel
        )

    result = await session.execute(_apply_inbox_order(q, bucket).limit(100))
    tickets = result.scalars().all()
    return [await ticket_detail_dict(session, ticket) for ticket in tickets]


@router.post("/inbox/tickets/{ticket_id}/claim", response_model=TicketDetailOut)
async def claim_ticket(
    ticket_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    if not actor.has_permission("crm.tickets.claim"):
        raise HTTPException(403, "Missing crm.tickets.claim")
    ticket = await session.get(Ticket, ticket_id)
    if not ticket or ticket.workspace_id != workspace.id:
        raise HTTPException(404, "Ticket not found")
    ticket.assignee_user_id = actor.id
    ticket.status = "open"
    touch_ticket_activity(ticket)
    await commit_and_notify(session)
    await session.refresh(ticket)
    _publish_inbox_event({"type": "ticket.claimed", "ticket_id": str(ticket.id)})
    return await ticket_detail_dict(session, ticket)


@router.get("/me/presence", response_model=PresenceOut)
async def get_presence(
    session: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(select(AgentPresence).where(AgentPresence.user_id == actor.id))
    presence = q.scalar_one_or_none()
    status = presence.status if presence else "offline"
    return PresenceOut(user_id=actor.id or "", status=status)


@router.patch("/me/presence")
async def update_presence(
    body: PresenceUpdate,
    session: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(select(AgentPresence).where(AgentPresence.user_id == actor.id))
    presence = q.scalar_one_or_none()
    if not presence:
        presence = AgentPresence(user_id=actor.id, status=body.status, updated_at=datetime.now(UTC))
        session.add(presence)
    else:
        presence.status = body.status
        presence.updated_at = datetime.now(UTC)
    await commit_and_notify(session)
    _publish_inbox_event({"type": "presence.updated", "user_id": actor.id, "status": body.status})
    return {"user_id": actor.id, "status": body.status}


@router.get("/inbox/stream")
async def inbox_stream(
    request: Request,
    actor: Actor = Depends(get_authenticated_actor),
):
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=50)
    _inbox_subscribers.add(queue)

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'user_id': actor.id})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _inbox_subscribers.discard(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
