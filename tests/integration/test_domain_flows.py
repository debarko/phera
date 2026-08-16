from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from phera.authz.actor import Actor
from phera.authz.service import ensure_user_stub, filter_deals_for_actor
from phera.db.models import Ticket
from phera.db.mutate import FieldChange, MutateRequest, mutate
from phera.modules.adapters.superhealth.webhooks import handle_service_event_completed
from tests.support.factories import contact as make_contact
from tests.support.factories import deal as make_deal
from tests.support.factories import staff_actor


@pytest.mark.asyncio
async def test_ensure_user_stub_idempotent(db_session, workspace_bundle):
    actor = staff_actor(user_id="stub-user-1")
    await ensure_user_stub(db_session, actor, workspace_bundle.workspace.id)
    await ensure_user_stub(db_session, actor, workspace_bundle.workspace.id)
    await db_session.commit()

    from phera.db.models import User

    users = (await db_session.execute(select(User))).scalars().all()
    assert len(users) == 1


@pytest.mark.asyncio
async def test_filter_deals_for_actor_own_vs_global(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    c = make_contact(ws.id, email="visibility@test.com")
    db_session.add(c)
    await db_session.flush()

    await ensure_user_stub(db_session, Actor(id="owner-a"), ws.id)
    await ensure_user_stub(db_session, Actor(id="owner-b"), ws.id)
    await db_session.flush()

    d1 = make_deal(
        ws.id,
        c.id,
        workspace_bundle.pipeline.id,
        workspace_bundle.stage_new.id,
        owner_user_id="owner-a",
    )
    d2 = make_deal(
        ws.id,
        c.id,
        workspace_bundle.pipeline.id,
        workspace_bundle.stage_qualified.id,
        owner_user_id="owner-b",
    )
    db_session.add(d1)
    db_session.add(d2)
    await db_session.commit()

    own_actor = Actor(id="owner-a", permissions={"crm.deals.read": "own"})
    visible = await filter_deals_for_actor(db_session, own_actor, ws.id, [d1, d2])
    assert len(visible) == 1
    assert visible[0].owner_user_id == "owner-a"

    global_actor = Actor(id="admin", permissions={"crm.deals.read": "allow"})
    visible_all = await filter_deals_for_actor(db_session, global_actor, ws.id, [d1, d2])
    assert len(visible_all) == 2


@pytest.mark.asyncio
async def test_ticket_create_via_mutate(db_session, workspace_bundle):
    ws = workspace_bundle.workspace
    c = make_contact(ws.id, email="ticket@test.com")
    db_session.add(c)
    await db_session.flush()

    ticket = Ticket(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        contact_id=c.id,
        subject="Help",
        status="open",
        priority="normal",
    )
    db_session.add(ticket)
    await db_session.flush()

    await mutate(
        db_session,
        MutateRequest(
            entity=ticket,
            entity_type="ticket",
            action="created",
            changes=[FieldChange("status", None, "open")],
            actor=staff_actor(),
            outbox_event_type="ticket.created",
            idempotency_key=f"ticket.created:{ticket.id}",
            contact_id=c.id,
            ticket_id=ticket.id,
        ),
    )
    await db_session.commit()

    from phera.db.models import OutboxEvent

    events = (await db_session.execute(select(OutboxEvent))).scalars().all()
    assert any(e.event_type == "ticket.created" for e in events)


@pytest.mark.asyncio
async def test_superhealth_webhook_creates_contact(db_session, workspace_bundle):
    result = await handle_service_event_completed(
        db_session,
        workspace_bundle.workspace,
        {
            "event_id": "evt-1",
            "email": "webhook@test.com",
            "phone": "+914444444444",
            "name": "Webhook Patient",
            "event_type": "appointment.completed",
        },
    )
    assert result["contact_id"]
    from phera.db.models import Contact

    contacts = (await db_session.execute(select(Contact))).scalars().all()
    assert len(contacts) == 1


