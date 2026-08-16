from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import os

os.environ.setdefault("OTEL_ENABLED", "0")

from phera.observability.otel import init_otel
from phera.settings import get_settings

_settings = get_settings()
_engine = create_async_engine(_settings.database_url, echo=False, pool_pre_ping=True)
init_otel(role=_settings.phera_role, sqlalchemy_engine=_engine.sync_engine)

SessionLocal = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_engine():
    return _engine
