from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from phera.db.models import Ticket
from phera.modules.tickets.activity import touch_ticket_activity


def test_touch_ticket_activity_sets_timestamp():
    ticket = Ticket(id=uuid.uuid4(), workspace_id=uuid.uuid4(), contact_id=uuid.uuid4())
    at = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    touch_ticket_activity(ticket, at)
    assert ticket.last_activity_at == at


def test_touch_ticket_activity_uses_now_when_missing():
    ticket = Ticket(id=uuid.uuid4(), workspace_id=uuid.uuid4(), contact_id=uuid.uuid4())
    before = datetime.now(UTC)
    touch_ticket_activity(ticket)
    assert ticket.last_activity_at >= before
