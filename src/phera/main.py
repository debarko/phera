from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from phera.api.routes import (
    analytics,
    audit,
    broadcasts,
    calls,
    channels,
    connectors,
    contacts,
    forms,
    health,
    hooks,
    inbox,
    public,
    routing_settings,
    support_settings,
    teams,
    telephony,
    tickets,
    voice_hooks,
    workflows,
)
from phera.observability.otel import instrument_fastapi


def create_app(*, run_worker: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker_task: asyncio.Task | None = None
        if run_worker:
            from phera.worker.runner import run_worker_loop

            worker_task = asyncio.create_task(run_worker_loop())
        yield
        if worker_task is not None:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
        from phera.db.session import get_engine

        await get_engine().dispose()

    app = FastAPI(
        title="Phera",
        description="Phera — headless CRM (ফেরা, the return)",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(public.router)
    app.include_router(hooks.router)
    app.include_router(voice_hooks.router)
    app.include_router(contacts.router, prefix="/v1")
    app.include_router(forms.router, prefix="/v1")
    app.include_router(tickets.router, prefix="/v1")
    app.include_router(audit.router, prefix="/v1")
    app.include_router(workflows.router, prefix="/v1")
    app.include_router(analytics.router, prefix="/v1")
    app.include_router(inbox.router, prefix="/v1")
    app.include_router(routing_settings.router, prefix="/v1")
    app.include_router(support_settings.router, prefix="/v1")
    app.include_router(channels.router, prefix="/v1")
    app.include_router(connectors.router, prefix="/v1")
    app.include_router(calls.router, prefix="/v1")
    app.include_router(telephony.router, prefix="/v1")
    app.include_router(teams.router, prefix="/v1")
    app.include_router(broadcasts.router, prefix="/v1")

    instrument_fastapi(app)
    return app


app = create_app()
