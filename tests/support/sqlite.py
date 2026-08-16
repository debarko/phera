"""Ephemeral SQLite schema for integration tests — created and destroyed per session."""
from __future__ import annotations

from sqlalchemy import JSON, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_element, compiler, **_kw):
    return compiler.visit_JSON(JSON(), **_kw)


def _strip_postgres_only_indexes(metadata) -> None:
    deals = metadata.tables.get("deals")
    if deals is None:
        return
    for idx in list(deals.indexes):
        if idx.name == "ix_deals_open_contact_pipeline":
            deals.indexes.discard(idx)


def create_test_engine() -> AsyncEngine:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def init_schema(engine: AsyncEngine) -> None:
    from phera.db.models import Base

    _strip_postgres_only_indexes(Base.metadata)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_schema(engine: AsyncEngine) -> None:
    from phera.db.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


def session_factory(engine: AsyncEngine) -> async_sessionmaker:
    from sqlalchemy.ext.asyncio import AsyncSession

    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
