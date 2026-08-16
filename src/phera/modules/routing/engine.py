from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import AgentPresence, RoutingPolicy, RoutingTier, Ticket, TicketOffer, User


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

    agents_q = await session.execute(
        select(AgentPresence).where(AgentPresence.status == "available").limit(10)
    )
    agents = agents_q.scalars().all()
    if not agents:
        return None

    agent = agents[0]
    offer = TicketOffer(
        id=uuid.uuid4(),
        ticket_id=ticket.id,
        user_id=agent.user_id,
        status="offered",
        expires_at=datetime.now(UTC),
    )
    session.add(offer)
    return offer


async def agent_open_load(session: AsyncSession, user_id: str) -> int:
    q = await session.execute(
        select(func.count()).select_from(Ticket).where(
            Ticket.assignee_user_id == user_id,
            Ticket.status.in_(["open", "pending"]),
        )
    )
    return q.scalar() or 0
