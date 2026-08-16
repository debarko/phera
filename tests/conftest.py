"""Root pytest configuration — no database connections here."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("OTEL_ENABLED", "0")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


def pytest_collection_modifyitems(config, items) -> None:
    for item in items:
        path = str(item.fspath)
        if "/tests/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
