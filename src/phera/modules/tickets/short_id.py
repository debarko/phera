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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import Ticket

_MAX_ATTEMPTS = 10
_SUFFIX_DIGITS = 6
_SUFFIX_MAX = 10**_SUFFIX_DIGITS


async def generate_ticket_short_id(session: AsyncSession) -> str:
    """A candidate that passed this pre-check is not yet reserved — callers that insert a
    Ticket with it should still handle a unique-constraint violation (see
    `tickets.py::create_ticket` / `inbound.py::_reuse_or_create_ticket`), since a concurrent
    insert can win the same id between this check and the caller's own commit."""
    date_prefix = datetime.now(UTC).strftime("%y%m%d")
    for _ in range(_MAX_ATTEMPTS):
        candidate = f"{date_prefix}-{secrets.randbelow(_SUFFIX_MAX):0{_SUFFIX_DIGITS}d}"
        q = await session.execute(select(Ticket.id).where(Ticket.short_id == candidate))
        if q.scalar_one_or_none() is None:
            return candidate
    # 10 straight collisions in a 1,000,000-value keyspace is astronomically unlikely — if
    # it ever happens, fail loudly rather than silently emit an id outside the documented
    # YYMMDD-NNNNNN format (which the subject-token regex would then never match again).
    raise RuntimeError(
        f"Could not allocate a unique ticket short_id for {date_prefix} after "
        f"{_MAX_ATTEMPTS} attempts"
    )


_INSERT_RETRY_ATTEMPTS = 3


async def insert_ticket_with_short_id(session: AsyncSession, ticket: Ticket) -> None:
    """Assign a short_id to `ticket` and insert it, retrying with a fresh id if a
    concurrent request already reserved the same one between generate_ticket_short_id's
    own pre-check and this insert (belt-and-suspenders around the DB's unique constraint).
    `ticket` must be fully populated except for `short_id` and must not already be added
    to the session.
    """
    last_error: IntegrityError | None = None
    for _ in range(_INSERT_RETRY_ATTEMPTS):
        ticket.short_id = await generate_ticket_short_id(session)
        try:
            # Nested (SAVEPOINT) so a collision only unwinds this insert, not other
            # unrelated pending work already in the caller's session. `begin_nested()`
            # itself flushes any ALREADY-pending state before opening the SAVEPOINT, so
            # `session.add(ticket)` must happen *inside* this block — adding it first
            # would let that pre-savepoint flush attempt (and possibly fail) the insert
            # outside the SAVEPOINT's protection, poisoning the outer transaction instead
            # of being safely contained by it.
            async with session.begin_nested():
                session.add(ticket)
                await session.flush()
            return
        except IntegrityError as exc:
            # begin_nested()'s own rollback-on-exception already detaches `ticket` (it
            # was only ever added inside the SAVEPOINT) — no manual expunge needed, and
            # attempting one here would raise (the instance is no longer in the session).
            last_error = exc
    raise RuntimeError("Could not insert ticket with a unique short_id") from last_error
