"""Seed default workspace."""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from phera.db.models import OwnershipProfile, Workspace
from phera.db.session import SessionLocal
from phera.modules.adapters.superhealth.seed import seed_superhealth_workspace


async def seed() -> None:
    async with SessionLocal() as session:
        q = await session.execute(select(Workspace).where(Workspace.slug == "default"))
        ws = q.scalar_one_or_none()
        if not ws:
            ws = Workspace(id=uuid.uuid4(), name="Default", slug="default")
            session.add(ws)
            await session.flush()
            session.add(OwnershipProfile(workspace_id=ws.id, mode="pipeline_centric", flags={}))
        await seed_superhealth_workspace(session, ws)
        await session.commit()
        print(f"Seeded workspace {ws.id}")


def main() -> None:
    asyncio.run(seed())


if __name__ == "__main__":
    main()
