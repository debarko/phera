"""Test data builders shared by integration tests."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from phera.authz.actor import Actor
from phera.db.models import (
    ChannelAccount,
    Contact,
    Deal,
    Form,
    OwnershipProfile,
    Pipeline,
    Stage,
    Workspace,
)


def workspace(*, slug: str = "default", name: str = "Test Workspace") -> Workspace:
    return Workspace(id=uuid.uuid4(), name=name, slug=slug)


def ownership_profile(workspace_id: uuid.UUID) -> OwnershipProfile:
    return OwnershipProfile(workspace_id=workspace_id, mode="pipeline_centric", flags={})


def pipeline(workspace_id: uuid.UUID, *, slug: str = "ivf_consult", name: str = "IVF Consult") -> Pipeline:
    return Pipeline(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=name,
        slug=slug,
        resubmission_policy="reuse_open_deal",
    )


def stage(pipeline_id: uuid.UUID, *, name: str = "New", position: int = 0) -> Stage:
    return Stage(
        id=uuid.uuid4(),
        pipeline_id=pipeline_id,
        name=name,
        position=position,
        category="open",
    )


def form(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    entry_stage_id: uuid.UUID,
    *,
    slug: str = "ivf-intake",
    name: str = "IVF Intake",
) -> Form:
    return Form(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=name,
        slug=slug,
        target_pipeline_id=pipeline_id,
        entry_stage_id=entry_stage_id,
        matching_keys=["email", "phone"],
        source_default="form",
    )


def contact(
    workspace_id: uuid.UUID,
    *,
    email: str | None = "ada@example.com",
    phone: str | None = "+919999999999",
    name: str = "Ada",
) -> Contact:
    return Contact(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=name,
        primary_email=email,
        primary_phone=phone,
        source="test",
    )


def deal(
    workspace_id: uuid.UUID,
    contact_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    stage_id: uuid.UUID,
    *,
    status: str = "open",
    owner_user_id: str | None = None,
) -> Deal:
    now = datetime.now(UTC)
    return Deal(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        contact_id=contact_id,
        pipeline_id=pipeline_id,
        stage_id=stage_id,
        status=status,
        stage_entered_at=now,
        owner_user_id=owner_user_id,
    )


def channel_account(
    workspace_id: uuid.UUID,
    *,
    kind: str = "messaging",
    adapter_type: str = "gallabox",
    address: str = "+919876543210",
) -> ChannelAccount:
    return ChannelAccount(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind=kind,
        adapter_type=adapter_type,
        address=address,
        is_active=True,
    )


def staff_actor(*, user_id: str = "staff-1", unrestricted: bool = False) -> Actor:
    return Actor(
        id=user_id,
        email=f"{user_id}@example.com",
        name="Staff User",
        permissions={
            "crm.contacts.read": "allow",
            "crm.contacts.write": "allow",
            "crm.deals.read": "allow",
            "crm.deals.write": "allow",
            "crm.pipelines.read": "allow",
            "crm.tickets.read": "allow",
            "crm.tickets.write": "allow",
            "crm.tickets.claim": "allow",
        },
        unrestricted=unrestricted,
    )
