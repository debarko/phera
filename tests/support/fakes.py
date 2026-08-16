"""In-memory fakes for unit tests — never touch a real database."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any


class RecordingSession:
    """Minimal async session that records ORM objects added via mutate()."""

    def __init__(self) -> None:
        self.added: list[Any] = []
        self.info: dict = {}
        self.committed = False
        self.flushed = False

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushed = True

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


async def recording_session() -> AsyncGenerator[RecordingSession, None]:
    yield RecordingSession()
