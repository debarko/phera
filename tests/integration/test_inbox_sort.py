from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from phera.api.routes.inbox import list_inbox_tickets
from phera.authz.actor import Actor
from phera.authz.service import ensure_user_stub
from phera.db.models import Contact, Ticket


@pytest.mark.asyncio
async def test_inbox_tickets_sorted_by_last_activity_desc(db_session, workspace_bundle):
    workspace = workspace_bundle.workspace
    actor = Actor(id="agent-1", actor_type="user", email="agent@test.com")
    contact = Contact(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name="Test Contact",
        primary_email="test@example.com",
    )
    db_session.add(contact)
    await db_session.flush()
    await ensure_user_stub(db_session, actor, workspace.id)
    await db_session.flush()

    now = datetime.now(UTC)
    stale = Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        contact_id=contact.id,
        subject="Stale ticket",
        status="open",
        last_activity_at=now - timedelta(hours=3),
    )
    fresh = Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        contact_id=contact.id,
        subject="Fresh ticket",
        status="open",
        assignee_user_id=actor.id,
        last_activity_at=now - timedelta(minutes=5),
    )
    db_session.add_all([stale, fresh])
    await db_session.commit()

    rows = await list_inbox_tickets(
        bucket="mine",
        channel=None,
        session=db_session,
        workspace=workspace,
        actor=actor,
    )
    assert [row["subject"] for row in rows] == ["Fresh ticket"]

    q_rows = await list_inbox_tickets(
        bucket="unassigned",
        channel=None,
        session=db_session,
        workspace=workspace,
        actor=actor,
    )
    assert len(q_rows) == 1
    assert q_rows[0]["subject"] == "Stale ticket"


@pytest.mark.asyncio
async def test_inbox_sort_order_with_multiple_tickets(db_session, workspace_bundle):
    workspace = workspace_bundle.workspace
    actor = Actor(id="agent-1", actor_type="user", email="agent@test.com")
    contact = Contact(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name="Test Contact",
        primary_email="sort@example.com",
    )
    db_session.add(contact)
    await db_session.flush()
    await ensure_user_stub(db_session, actor, workspace.id)
    await db_session.flush()

    base = datetime.now(UTC)
    for offset, subject in [(120, "Middle"), (60, "Recent"), (180, "Oldest")]:
        db_session.add(
            Ticket(
                id=uuid.uuid4(),
                workspace_id=workspace.id,
                contact_id=contact.id,
                subject=subject,
                status="open",
                assignee_user_id=actor.id,
                last_activity_at=base - timedelta(minutes=offset),
            )
        )
    await db_session.commit()

    rows = await list_inbox_tickets(
        bucket="mine",
        channel=None,
        session=db_session,
        workspace=workspace,
        actor=actor,
    )
    assert [row["subject"] for row in rows] == ["Recent", "Middle", "Oldest"]


@pytest.mark.asyncio
async def test_unassigned_queue_priority_then_oldest_first(db_session, workspace_bundle):
    workspace = workspace_bundle.workspace
    actor = Actor(id="agent-1", actor_type="user", email="agent@test.com")
    contact = Contact(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name="Queue Contact",
        primary_email="queue@example.com",
    )
    db_session.add(contact)
    await db_session.flush()

    base = datetime.now(UTC)
    db_session.add_all(
        [
            Ticket(
                id=uuid.uuid4(),
                workspace_id=workspace.id,
                contact_id=contact.id,
                subject="Normal older",
                status="open",
                priority="normal",
                last_activity_at=base - timedelta(hours=2),
            ),
            Ticket(
                id=uuid.uuid4(),
                workspace_id=workspace.id,
                contact_id=contact.id,
                subject="High newer",
                status="open",
                priority="high",
                last_activity_at=base - timedelta(minutes=30),
            ),
            Ticket(
                id=uuid.uuid4(),
                workspace_id=workspace.id,
                contact_id=contact.id,
                subject="Normal newest",
                status="open",
                priority="normal",
                last_activity_at=base - timedelta(minutes=10),
            ),
        ]
    )
    await db_session.commit()

    rows = await list_inbox_tickets(
        bucket="unassigned",
        channel=None,
        session=db_session,
        workspace=workspace,
        actor=actor,
    )
    assert [row["subject"] for row in rows] == [
        "High newer",
        "Normal older",
        "Normal newest",
    ]


@pytest.mark.asyncio
async def test_offered_bucket_sorted_by_expiry_then_activity(db_session, workspace_bundle):
    from phera.db.models import TicketOffer

    workspace = workspace_bundle.workspace
    actor = Actor(id="agent-1", actor_type="user", email="agent@test.com")
    contact = Contact(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name="Offer Contact",
        primary_email="offer@example.com",
    )
    db_session.add(contact)
    await db_session.flush()
    await ensure_user_stub(db_session, actor, workspace.id)
    await db_session.flush()

    base = datetime.now(UTC)
    soon_ticket = Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        contact_id=contact.id,
        subject="Expiring soon",
        status="open",
        last_activity_at=base - timedelta(hours=1),
    )
    later_ticket = Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        contact_id=contact.id,
        subject="Expiring later",
        status="open",
        last_activity_at=base - timedelta(minutes=5),
    )
    db_session.add_all([soon_ticket, later_ticket])
    await db_session.flush()
    db_session.add_all(
        [
            TicketOffer(
                id=uuid.uuid4(),
                ticket_id=soon_ticket.id,
                user_id=actor.id,
                status="offered",
                expires_at=base + timedelta(minutes=5),
            ),
            TicketOffer(
                id=uuid.uuid4(),
                ticket_id=later_ticket.id,
                user_id=actor.id,
                status="offered",
                expires_at=base + timedelta(minutes=30),
            ),
        ]
    )
    await db_session.commit()

    rows = await list_inbox_tickets(
        bucket="offered",
        channel=None,
        session=db_session,
        workspace=workspace,
        actor=actor,
    )
    assert [row["subject"] for row in rows] == ["Expiring soon", "Expiring later"]
