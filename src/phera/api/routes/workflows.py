from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import WorkflowCreate, WorkflowOut
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import Workflow, Workspace
from phera.modules.workflows.engine import WORKFLOW_NODE_TYPES

router = APIRouter(tags=["workflows"])


@router.get("/workflow-node-types")
async def workflow_node_types():
    return WORKFLOW_NODE_TYPES


@router.get("/workflows", response_model=list[WorkflowOut])
async def list_workflows(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    q = await session.execute(select(Workflow).where(Workflow.workspace_id == workspace.id))
    return q.scalars().all()


@router.post("/workflows", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    body: WorkflowCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    wf = Workflow(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        name=body.name,
        graph=body.graph,
        trigger_filter=body.trigger_filter,
        is_draft=True,
    )
    session.add(wf)
    await commit_and_notify(session)
    await session.refresh(wf)
    return wf


@router.post("/workflows/{workflow_id}/publish", response_model=WorkflowOut)
async def publish_workflow(
    workflow_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    wf = await session.get(Workflow, workflow_id)
    if not wf or wf.workspace_id != workspace.id:
        raise HTTPException(404, "Workflow not found")
    wf.version += 1
    wf.is_draft = False
    wf.is_active = True
    await commit_and_notify(session)
    await session.refresh(wf)
    return wf
