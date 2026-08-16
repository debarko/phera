from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from phera.authz.service import ensure_user_stub
from phera.db.models import AgentPresence, RoutingPolicy, RoutingTier, Team, Ticket, TicketOffer, User
from phera.modules.routing.engine import agent_open_load, route_unassigned_ticket
from tests.support.factories import contact as make_contact
from tests.support.factories import staff_actor


@pytest.mark.asyncio
async def test_route_unassigned_ticket_creates_offer(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    actor = staff_actor(user_id="agent-1")
    await ensure_user_stub(db_session, actor, ws.id)
    await db_session.flush()

    team = Team(id=uuid.uuid4(), workspace_id=ws.id, name="Support", slug="support")
    db_session.add(team)
    await db_session.flush()

    policy = RoutingPolicy(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        name="Default",
        assignment_method="round_robin",
    )
    db_session.add(policy)
    await db_session.flush()

    tier = RoutingTier(
        id=uuid.uuid4(),
        policy_id=policy.id,
        position=0,
        name="L1",
        team_id=team.id,
    )
    db_session.add(tier)
    db_session.add(AgentPresence(user_id=actor.id, status="available"))
    await db_session.flush()

    c = make_contact(ws.id, email="routing@test.com")
    db_session.add(c)
    await db_session.flush()

    ticket = Ticket(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        contact_id=c.id,
        subject="Need help",
        status="open",
        priority="normal",
    )
    db_session.add(ticket)
    await db_session.flush()

    offer = await route_unassigned_ticket(db_session, ticket, policy)
    await db_session.commit()

    assert offer is not None
    assert offer.user_id == actor.id
    assert offer.ticket_id == ticket.id
    count = await db_session.scalar(select(func.count()).select_from(TicketOffer))
    assert count == 1


@pytest.mark.asyncio
async def test_agent_open_load_counts_open_tickets(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    actor = staff_actor(user_id="agent-load")
    await ensure_user_stub(db_session, actor, ws.id)
    await db_session.flush()

    c = make_contact(ws.id, email="load@test.com")
    db_session.add(c)
    await db_session.flush()

    for status in ("open", "pending", "closed"):
        db_session.add(
            Ticket(
                id=uuid.uuid4(),
                workspace_id=ws.id,
                contact_id=c.id,
                subject=f"Ticket {status}",
                status=status,
                priority="normal",
                assignee_user_id=actor.id,
            )
        )
    await db_session.commit()

    load = await agent_open_load(db_session, actor.id)
    assert load == 2
