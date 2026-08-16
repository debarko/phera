from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolate_app_from_database():
    """Unit tests must never open a real database connection."""
    from phera.api import deps
    from phera.db.models import Workspace
    from phera.db.session import get_db
    from phera.main import app

    ws = Workspace(id=uuid.uuid4(), name="Unit Test", slug="default")

    class NoDatabaseSession:
        def __init__(self) -> None:
            self.info: dict = {}

        async def execute(self, *_args, **_kwargs):
            raise AssertionError("Unit tests must not execute SQL")

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    async def override_db():
        yield NoDatabaseSession()

    async def override_workspace():
        return ws

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[deps.get_workspace] = override_workspace
    yield
    app.dependency_overrides.clear()
