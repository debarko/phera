from __future__ import annotations

from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api import deps
from phera.db.models import Form, Pipeline, Stage, Workspace
from phera.db.session import get_db
from phera.main import app
from tests.support import factories
from tests.support.sqlite import create_test_engine, drop_schema, init_schema, session_factory

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Ephemeral in-memory SQLite — schema created before each test and destroyed after."""
    engine = create_test_engine()
    await init_schema(engine)
    SessionLocal = session_factory(engine)
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await drop_schema(engine)


@dataclass
class WorkspaceBundle:
    workspace: Workspace
    pipeline: Pipeline
    stage_new: Stage
    stage_qualified: Stage
    form_a: Form
    form_b: Form


@pytest_asyncio.fixture
async def workspace_bundle(db_session: AsyncSession) -> WorkspaceBundle:
    ws = factories.workspace()
    db_session.add(ws)
    await db_session.flush()
    db_session.add(factories.ownership_profile(ws.id))

    pipeline = factories.pipeline(ws.id, slug="ivf_consult")
    db_session.add(pipeline)
    await db_session.flush()

    stage_new = factories.stage(pipeline.id, name="New", position=0)
    stage_qualified = factories.stage(pipeline.id, name="Qualified", position=1)
    db_session.add(stage_new)
    db_session.add(stage_qualified)
    await db_session.flush()

    form_a = factories.form(ws.id, pipeline.id, stage_new.id, slug="ivf-intake-a", name="IVF A")
    form_b = factories.form(ws.id, pipeline.id, stage_new.id, slug="ivf-intake-b", name="IVF B")
    db_session.add(form_a)
    db_session.add(form_b)
    await db_session.flush()

    return WorkspaceBundle(
        workspace=ws,
        pipeline=pipeline,
        stage_new=stage_new,
        stage_qualified=stage_qualified,
        form_a=form_a,
        form_b=form_b,
    )


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, workspace_bundle: WorkspaceBundle):
    async def override_db():
        yield db_session

    async def override_workspace():
        return workspace_bundle.workspace

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[deps.get_workspace] = override_workspace
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
