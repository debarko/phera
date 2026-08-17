"""Compact ticket display id — date bucket + random suffix, no sequence counter.

Format: YYMMDD-NNNN (e.g. 260818-4827). The date prefix makes collisions naturally rare
without needing a global counter or lock; a pre-insert uniqueness check plus the DB's own
unique constraint (belt-and-suspenders) rule out the remaining tiny chance of a clash.
Deliberately not sequential — a running number would leak total ticket volume at a glance.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import Ticket

_MAX_ATTEMPTS = 10


async def generate_ticket_short_id(session: AsyncSession) -> str:
    date_prefix = datetime.now(UTC).strftime("%y%m%d")
    for _ in range(_MAX_ATTEMPTS):
        candidate = f"{date_prefix}-{random.randint(0, 9999):04d}"
        q = await session.execute(select(Ticket.id).where(Ticket.short_id == candidate))
        if q.scalar_one_or_none() is None:
            return candidate
    # Vanishingly unlikely after _MAX_ATTEMPTS collisions — widen the suffix as a fallback.
    return f"{date_prefix}-{random.randint(0, 999999):06d}"
