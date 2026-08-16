from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import FormCreate, FormOut, FormSubmitPayload
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import Form, Workspace
from phera.modules.pipelines.intake import process_form_submission

router = APIRouter(tags=["forms"])


@router.get("/forms", response_model=list[FormOut])
async def list_forms(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(select(Form).where(Form.workspace_id == workspace.id))
    return q.scalars().all()


@router.post("/forms", response_model=FormOut, status_code=201)
async def create_form(
    body: FormCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    form = Form(id=uuid.uuid4(), workspace_id=workspace.id, **body.model_dump())
    session.add(form)
    await commit_and_notify(session)
    await session.refresh(form)
    return form


@router.post("/forms/{slug}/submit")
async def submit_form_authenticated(
    slug: str,
    body: FormSubmitPayload,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(
        select(Form).where(Form.workspace_id == workspace.id, Form.slug == slug, Form.is_active.is_(True))
    )
    form = q.scalar_one_or_none()
    if not form:
        raise HTTPException(404, "Form not found")
    result = await process_form_submission(session, form, body.data, actor)
    await commit_and_notify(session)
    return result
