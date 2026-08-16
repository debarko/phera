from __future__ import annotations

from datetime import UTC, datetime

from phera.db.models import Ticket


def touch_ticket_activity(ticket: Ticket, at: datetime | None = None) -> None:
    """Bump denormalized inbox sort key when conversation or ticket state changes."""
    ticket.last_activity_at = at or datetime.now(UTC)
