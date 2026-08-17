"""Compact ticket display id — date bucket + random suffix, no sequence counter.

Format: YYMMDD-NNNNNN (e.g. 260818-482793). The date prefix makes collisions naturally
rare without needing a global counter or lock; a pre-insert uniqueness check plus the DB's
own unique constraint (belt-and-suspenders) rule out the remaining tiny chance of a clash.
Deliberately not sequential — a running number would leak total ticket volume at a glance.

This id is also used (in modules.tickets.inbound) to route an inbound webhook message onto
an existing ticket by matching it in the email subject line — an unauthenticated or
weakly-authenticated caller who can guess a live short_id could otherwise inject a message
into someone else's ticket. Two things push back on that: `secrets.randbelow` (a CSPRNG,
not the non-cryptographic `random` module) so the value isn't predictable from prior output,
and a six-digit suffix (1,000,000 values/day) rather than four, making blind brute-forcing
impractical rather than merely inconvenient.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import Ticket

_MAX_ATTEMPTS = 10
_SUFFIX_DIGITS = 6
_SUFFIX_MAX = 10**_SUFFIX_DIGITS


async def generate_ticket_short_id(session: AsyncSession) -> str:
    date_prefix = datetime.now(UTC).strftime("%y%m%d")
    for _ in range(_MAX_ATTEMPTS):
        candidate = f"{date_prefix}-{secrets.randbelow(_SUFFIX_MAX):0{_SUFFIX_DIGITS}d}"
        q = await session.execute(select(Ticket.id).where(Ticket.short_id == candidate))
        if q.scalar_one_or_none() is None:
            return candidate
    # Vanishingly unlikely after _MAX_ATTEMPTS collisions — widen the suffix as a fallback.
    return f"{date_prefix}-{secrets.randbelow(10**9):09d}"
