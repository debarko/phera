from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from phera.db.models import Ticket
from phera.modules.tickets.short_id import insert_ticket_with_short_id
from tests.support import factories

pytestmark = pytest.mark.integration


def _new_ticket(workspace_id, contact_id) -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        contact_id=contact_id,
        status="open",
    )


@pytest.mark.asyncio
async def test_insert_succeeds_and_sets_short_id(db_session, workspace_bundle):
    contact = factories.contact(workspace_bundle.workspace.id)
    db_session.add(contact)
    await db_session.flush()

    ticket = _new_ticket(workspace_bundle.workspace.id, contact.id)
    await insert_ticket_with_short_id(db_session, ticket)

    assert ticket.short_id is not None
    q = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
    assert q.scalar_one().short_id == ticket.short_id


@pytest.mark.asyncio
async def test_retries_on_collision_and_session_stays_usable(
    db_session, workspace_bundle, monkeypatch
):
    """Regression guard for the begin_nested()-ordering bug: a collision must only unwind
    the failed insert (via its SAVEPOINT), not poison the whole session — the caller must
    be able to keep using `db_session` afterward for unrelated work."""
    contact = factories.contact(workspace_bundle.workspace.id)
    db_session.add(contact)
    await db_session.flush()

    taken = _new_ticket(workspace_bundle.workspace.id, contact.id)
    taken.short_id = "260818-999999"
    db_session.add(taken)
    await db_session.commit()

    from phera.modules.tickets import short_id as short_id_module

    calls = iter(["260818-999999", "260818-999999", "260818-000111"])
    monkeypatch.setattr(
        short_id_module, "generate_ticket_short_id", lambda session: _next(calls)
    )

    ticket = _new_ticket(workspace_bundle.workspace.id, contact.id)
    await insert_ticket_with_short_id(db_session, ticket)

    assert ticket.short_id == "260818-000111"

    # The session must still be usable — a poisoned outer transaction would raise here.
    another_contact = factories.contact(workspace_bundle.workspace.id, email="second@example.com")
    db_session.add(another_contact)
    await db_session.flush()
    await db_session.commit()

    q = await db_session.execute(
        select(Ticket).where(Ticket.workspace_id == workspace_bundle.workspace.id)
    )
    short_ids = {t.short_id for t in q.scalars().all()}
    assert short_ids == {"260818-999999", "260818-000111"}


async def _next(it):
    return next(it)
