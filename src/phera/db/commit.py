from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from phera.worker.notify import notify_outbox


def track_outbox_notify(session: AsyncSession, outbox_id: uuid.UUID) -> None:
    pending: list[uuid.UUID] = session.info.setdefault("outbox_notify_ids", [])
    pending.append(outbox_id)


async def commit_and_notify(session: AsyncSession) -> None:
    pending: list[uuid.UUID] = session.info.pop("outbox_notify_ids", [])
    await session.commit()
    for outbox_id in pending:
        await notify_outbox(outbox_id)
