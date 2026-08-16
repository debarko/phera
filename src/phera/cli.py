from __future__ import annotations

import asyncio
import logging

import typer
import uvicorn

from phera.main import create_app
from phera.settings import get_settings

app = typer.Typer(name="phera", help="Phera — headless CRM")


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))


@app.command("api")
def run_api(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    """Run HTTP API only."""
    _configure_logging()
    uvicorn.run("phera.main:app", host=host, port=port, reload=reload, factory=False)


@app.command("worker")
def run_worker() -> None:
    """Run queue workers only."""
    _configure_logging()
    from phera.db.session import get_engine
    from phera.worker.runner import run_worker_loop

    async def _main() -> None:
        try:
            await run_worker_loop()
        finally:
            await get_engine().dispose()

    asyncio.run(_main())


@app.command("all")
def run_all(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run API + worker in one process (local dev)."""
    _configure_logging()
    uvicorn.run(create_app(run_worker=True), host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
