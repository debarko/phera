from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ContactCreate(BaseModel):
    name: str | None = None
    primary_email: str | None = None
    primary_phone: str | None = None
    organization_id: uuid.UUID | None = None
    owner_user_id: str | None = None
    source: str | None = None
    custom_fields: dict = Field(default_factory=dict)


class ContactUpdate(BaseModel):
    name: str | None = None


class ContactOut(ORMModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str | None
    primary_email: str | None
    primary_phone: str | None
    owner_user_id: str | None
    source: str | None
    custom_fields: dict


class OrganizationCreate(BaseModel):
    name: str
    owner_user_id: str | None = None
    custom_fields: dict = Field(default_factory=dict)


class OrganizationOut(ORMModel):
    id: uuid.UUID
    name: str
    owner_user_id: str | None
    custom_fields: dict


class StageOut(ORMModel):
    id: uuid.UUID
    name: str
    position: int
    category: str


class PipelineCreate(BaseModel):
    name: str
    slug: str
    description: str | None = None
    resubmission_policy: str = "reuse_open_deal"


class PipelineOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    resubmission_policy: str
    stages: list[StageOut] = Field(default_factory=list)


class DealCreate(BaseModel):
    contact_id: uuid.UUID
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID
    owner_user_id: str | None = None
    value: float | None = None


class DealAssign(BaseModel):
    owner_user_id: str


class DealUpdateStage(BaseModel):
    stage_id: uuid.UUID


class DealOut(ORMModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    pipeline_id: uuid.UUID
    stage_id: uuid.UUID
    owner_user_id: str | None
    status: str
    value: float | None
    stage_entered_at: datetime | None


class FormCreate(BaseModel):
    name: str
    slug: str
    target_pipeline_id: uuid.UUID
    entry_stage_id: uuid.UUID
    field_schema: list = Field(default_factory=list)
    matching_keys: list[str] = Field(default_factory=lambda: ["email", "phone"])


class FormOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    target_pipeline_id: uuid.UUID
    entry_stage_id: uuid.UUID


class FormSubmitPayload(BaseModel):
    data: dict = Field(default_factory=dict)


class TicketCreate(BaseModel):
    contact_id: uuid.UUID
    subject: str | None = None
    priority: str = "normal"


class PresenceUpdate(BaseModel):
    status: str = "available"


class TicketAssign(BaseModel):
    assignee_user_id: str


class TicketOut(ORMModel):
    id: uuid.UUID
    contact_id: uuid.UUID
    assignee_user_id: str | None
    subject: str | None
    status: str
    priority: str


class TicketDetailOut(TicketOut):
    channel_account_id: uuid.UUID | None = None
    channel_kind: str | None = None
    channel_address: str | None = None
    channel_adapter_type: str | None = None
    routing_tier: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_activity_at: datetime | None = None
    status_entered_at: datetime | None = None
    first_assigned_at: datetime | None = None
    first_response_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None


class TicketUpdate(BaseModel):
    status: str | None = None
    priority: str | None = None
    subject: str | None = None


class MessageCreate(BaseModel):
    body: str


class MessageOut(ORMModel):
    id: uuid.UUID
    ticket_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    direction: str
    body: str | None
    occurred_at: datetime
    channel_kind: str | None = None


class ConversationItemOut(ORMModel):
    id: uuid.UUID
    kind: str
    direction: str | None = None
    body: str | None = None
    occurred_at: datetime
    actor_type: str | None = None
    actor_id: str | None = None
    actor_name: str | None = None
    channel_kind: str | None = None
    call_status: str | None = None
    transcript_status: str | None = None
    transcript_text: str | None = None


class PresenceOut(BaseModel):
    user_id: str
    status: str


class AuditEventOut(ORMModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    entity_type: str
    entity_id: uuid.UUID
    action: str
    changes: list
    context: dict


class WorkflowCreate(BaseModel):
    name: str
    graph: dict = Field(default_factory=dict)
    trigger_filter: dict = Field(default_factory=dict)


class WorkflowOut(ORMModel):
    id: uuid.UUID
    name: str
    version: int
    is_active: bool
    is_draft: bool
    graph: dict
    trigger_filter: dict
