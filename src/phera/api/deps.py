from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor, actor_from_headers
from phera.authz.service import ensure_user_stub
from phera.db.models import Workspace
from phera.db.session import get_db

DEFAULT_WORKSPACE_SLUG = "default"


async def get_actor(
    request: Request,
    x_actor_id: Annotated[str | None, Header(alias="X-Actor-Id")] = None,
) -> Actor:
    headers = {k.lower(): v for k, v in request.headers.items()}
    actor = actor_from_headers(headers)
    if actor.id:
        return actor
    if request.url.path.startswith("/public/") or request.url.path.startswith("/hooks/"):
        return Actor(actor_type="connector")
    raise HTTPException(status_code=401, detail="Missing X-Actor-Id — proxy misconfigured")


async def get_workspace(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> Workspace:
    q = await session.execute(select(Workspace).where(Workspace.slug == DEFAULT_WORKSPACE_SLUG))
    ws = q.scalar_one_or_none()
    if not ws:
        raise HTTPException(
            status_code=503, detail="Workspace not initialized — run migrations/seed"
        )
    return ws


async def get_authenticated_actor(
    actor: Annotated[Actor, Depends(get_actor)],
    session: Annotated[AsyncSession, Depends(get_db)],
    workspace: Annotated[Workspace, Depends(get_workspace)],
) -> Actor:
    if not actor.id:
        raise HTTPException(status_code=401, detail="Authentication required")
    await ensure_user_stub(session, actor, workspace.id)
    return actor
