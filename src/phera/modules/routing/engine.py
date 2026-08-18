from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import AgentPresence, RoutingPolicy, RoutingTier, Ticket, TicketOffer


async def _select_available_agent(
    session: AsyncSession, *, exclude_on_voice_call: bool = False
) -> AgentPresence | None:
    stmt = select(AgentPresence).where(AgentPresence.status == "available")
    if exclude_on_voice_call:
        stmt = stmt.where(AgentPresence.on_voice_call.is_(False))
    agents = (await session.execute(stmt.limit(10))).scalars().all()
    return agents[0] if agents else None


async def route_unassigned_ticket(
    session: AsyncSession,
    ticket: Ticket,
    policy: RoutingPolicy,
) -> TicketOffer | None:
    tier_q = await session.execute(
        select(RoutingTier).where(RoutingTier.policy_id == policy.id).order_by(RoutingTier.position)
    )
    tier = tier_q.scalars().first()
    if not tier or not tier.team_id:
        return None

    agent = await _select_available_agent(session, exclude_on_voice_call=policy.focus_on_voice)
    if not agent:
        return None

    offer = TicketOffer(
        id=uuid.uuid4(),
        ticket_id=ticket.id,
        user_id=agent.user_id,
        status="offered",
        expires_at=datetime.now(UTC),
    )
    session.add(offer)
    return offer


async def select_agent_for_voice(
    session: AsyncSession, policy: RoutingPolicy | None
) -> AgentPresence | None:
    """Pick an agent to ring for an inbound call — direct assignment, not an offer/claim
    step like `route_unassigned_ticket`, since the call is already ringing by the time
    Exotel's routing webhook calls this."""
    exclude_on_voice_call = bool(policy and policy.focus_on_voice)
    return await _select_available_agent(session, exclude_on_voice_call=exclude_on_voice_call)


async def agent_open_load(session: AsyncSession, user_id: str) -> int:
    q = await session.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.assignee_user_id == user_id,
            Ticket.status.in_(["open", "pending"]),
        )
    )
    return q.scalar() or 0
