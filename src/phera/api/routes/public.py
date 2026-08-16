from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_db, get_workspace
from phera.api.schemas import FormSubmitPayload
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import Form, Workspace
from phera.modules.pipelines.intake import process_form_submission

router = APIRouter(tags=["public"])


@router.post("/public/forms/{slug}/submit")
async def public_form_submit(
    slug: str,
    body: FormSubmitPayload,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
):
    q = await session.execute(
        select(Form).where(
            Form.workspace_id == workspace.id, Form.slug == slug, Form.is_active.is_(True)
        )
    )
    form = q.scalar_one_or_none()
    if not form:
        raise HTTPException(status_code=404, detail="Form not found")
    actor = Actor(actor_type="form", id=str(form.id))
    result = await process_form_submission(session, form, body.data, actor)
    await commit_and_notify(session)
    return result
