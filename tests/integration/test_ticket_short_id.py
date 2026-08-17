from __future__ import annotations

import random
import re
import uuid
from datetime import UTC, datetime

import pytest

from phera.db.models import Ticket
from phera.modules.tickets.short_id import generate_ticket_short_id
from tests.support import factories

pytestmark = pytest.mark.integration

SHORT_ID_PATTERN = re.compile(r"^\d{6}-\d{4}$")


def _make_ticket(workspace_id, contact_id, short_id: str) -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        contact_id=contact_id,
        short_id=short_id,
        status="open",
    )


@pytest.mark.asyncio
async def test_generated_id_matches_expected_format(db_session, workspace_bundle):
    short_id = await generate_ticket_short_id(db_session)
    assert SHORT_ID_PATTERN.match(short_id), short_id


@pytest.mark.asyncio
async def test_generated_ids_are_unique_across_calls(db_session, workspace_bundle):
    contact = factories.contact(workspace_bundle.workspace.id)
    db_session.add(contact)
    await db_session.flush()

    seen = set()
    for _ in range(20):
        short_id = await generate_ticket_short_id(db_session)
        assert short_id not in seen
        seen.add(short_id)
        # Persist it, like a real ticket creation would, so the next call's uniqueness
        # check has something real to collide against.
        db_session.add(_make_ticket(workspace_bundle.workspace.id, contact.id, short_id))
        await db_session.flush()


@pytest.mark.asyncio
async def test_generation_avoids_an_already_taken_candidate(
    db_session, workspace_bundle, monkeypatch
):
    date_prefix = datetime.now(UTC).strftime("%y%m%d")
    contact = factories.contact(workspace_bundle.workspace.id)
    db_session.add(contact)
    await db_session.flush()
    db_session.add(_make_ticket(workspace_bundle.workspace.id, contact.id, f"{date_prefix}-0001"))
    await db_session.flush()

    calls = iter([1, 1, 2])  # first two candidates collide with "-0001", third is free
    monkeypatch.setattr(random, "randint", lambda a, b: next(calls))

    result = await generate_ticket_short_id(db_session)
    assert result == f"{date_prefix}-0002"
