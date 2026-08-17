from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(255))
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)


class Team(Base, TimestampMixin):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)

    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)


class OwnershipProfile(Base, TimestampMixin):
    __tablename__ = "ownership_profiles"

    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), default="pipeline_centric")
    flags: Mapped[dict] = mapped_column(JSONB, default=dict)


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)


class Contact(Base, TimestampMixin):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str | None] = mapped_column(String(255))
    primary_email: Mapped[str | None] = mapped_column(String(320), index=True)
    primary_phone: Mapped[str | None] = mapped_column(String(32), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    source: Mapped[str | None] = mapped_column(String(128))
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class Pipeline(Base, TimestampMixin):
    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    assignment_policy: Mapped[dict] = mapped_column(JSONB, default=dict)
    resubmission_policy: Mapped[str] = mapped_column(String(32), default="reuse_open_deal")

    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)
    stages: Mapped[list[Stage]] = relationship(back_populates="pipeline", order_by="Stage.position")


class Stage(Base, TimestampMixin):
    __tablename__ = "stages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(16), default="open")
    sla_hours: Mapped[int | None] = mapped_column(Integer)
    probability: Mapped[int | None] = mapped_column(Integer)
    required_fields: Mapped[dict] = mapped_column(JSONB, default=dict)

    pipeline: Mapped[Pipeline] = relationship(back_populates="stages")


class PipelineTeam(Base):
    __tablename__ = "pipeline_teams"

    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id"), primary_key=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    access: Mapped[str] = mapped_column(String(16), default="work")


class Deal(Base, TimestampMixin):
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("organizations.id"))
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id"), nullable=False)
    stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stages.id"), nullable=False)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    source_form_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("forms.id"))
    status: Mapped[str] = mapped_column(String(16), default="open")
    value: Mapped[float | None] = mapped_column()
    expected_close_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    stage_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_touched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_deals_open_contact_pipeline",
            "contact_id",
            "pipeline_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )


class DealMember(Base):
    __tablename__ = "deal_members"

    deal_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("deals.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), default="member")


class Form(Base, TimestampMixin):
    __tablename__ = "forms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    target_pipeline_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipelines.id"), nullable=False
    )
    entry_stage_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stages.id"), nullable=False)
    field_schema: Mapped[list] = mapped_column(JSONB, default=list)
    matching_keys: Mapped[list] = mapped_column(JSONB, default=lambda: ["email", "phone"])
    resubmission_policy: Mapped[str | None] = mapped_column(String(32))
    assignment_policy: Mapped[dict | None] = mapped_column(JSONB)
    source_default: Mapped[str | None] = mapped_column(String(128))
    campaign_default: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (UniqueConstraint("workspace_id", "slug"),)


class FormSubmission(Base):
    __tablename__ = "form_submissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    form_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forms.id"), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id"))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Interaction(Base):
    __tablename__ = "interactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id"))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id"))
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str | None] = mapped_column(String(16))
    body: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_type: Mapped[str | None] = mapped_column(String(32))
    actor_id: Mapped[str | None] = mapped_column(String(64))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage_to_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    changes: Mapped[list] = mapped_column(JSONB, default=list)
    after: Mapped[dict | None] = mapped_column(JSONB)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)

    __table_args__ = (
        Index("ix_audit_workspace_occurred", "workspace_id", "occurred_at"),
        Index("ix_audit_entity", "workspace_id", "entity_type", "entity_id", "occurred_at"),
        Index("ix_audit_action", "workspace_id", "action", "occurred_at"),
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class Ticket(Base, TimestampMixin):
    __tablename__ = "tickets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"), nullable=False)
    channel_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("channel_accounts.id"))
    assignee_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    routing_tier: Mapped[int] = mapped_column(Integer, default=1)
    short_id: Mapped[str | None] = mapped_column(String(16), unique=True)
    subject: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="open")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status_entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_tickets_workspace_last_activity", "workspace_id", "last_activity_at"),
    )


class ChannelAccount(Base, TimestampMixin):
    __tablename__ = "channel_accounts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    connector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("connectors.id"))
    routing_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("routing_policies.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Connector(Base, TimestampMixin):
    __tablename__ = "connectors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    credentials: Mapped[dict] = mapped_column(JSONB, default=dict)
    secrets_encrypted: Mapped[str | None] = mapped_column(Text)
    field_mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class EmailPollState(Base, TimestampMixin):
    __tablename__ = "email_poll_state"

    channel_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channel_accounts.id"), primary_key=True
    )
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="idle")
    last_error: Mapped[str | None] = mapped_column(Text)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("channel_accounts.id"))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"))
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    thread_keys: Mapped[dict] = mapped_column(JSONB, default=dict)
    body: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RoutingPolicy(Base, TimestampMixin):
    __tablename__ = "routing_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    assignment_method: Mapped[str] = mapped_column(String(32), default="round_robin")
    capacity_mode: Mapped[str] = mapped_column(String(32), default="unified")
    max_open_units: Mapped[int] = mapped_column(Integer, default=5)
    weights: Mapped[dict] = mapped_column(JSONB, default=dict)
    focus_on_voice: Mapped[bool] = mapped_column(Boolean, default=False)
    sticky_assignee: Mapped[bool] = mapped_column(Boolean, default=True)
    offer_ttl_seconds: Mapped[int] = mapped_column(Integer, default=30)


class RoutingTier(Base):
    __tablename__ = "routing_tiers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("routing_policies.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"))
    overflow_after_seconds: Mapped[int | None] = mapped_column(Integer)
    overflow_on: Mapped[dict] = mapped_column(JSONB, default=dict)


class AgentPresence(Base):
    __tablename__ = "agent_presence"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="offline")
    on_voice_call: Mapped[bool] = mapped_column(Boolean, default=False)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketOffer(Base):
    __tablename__ = "ticket_offers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="offered")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("ticket_id", "user_id"),)


class MetricDaily(Base):
    __tablename__ = "metric_daily"

    date: Mapped[datetime] = mapped_column(Date, primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    grain_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_id: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSONB, default=dict)


class AgingSnapshotDaily(Base):
    __tablename__ = "aging_snapshot_daily"

    date: Mapped[datetime] = mapped_column(Date, primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    pipeline_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    owner_user_id: Mapped[str | None] = mapped_column(String(64))
    age_seconds: Mapped[int] = mapped_column(Integer, default=0)
    stage_age_seconds: Mapped[int] = mapped_column(Integer, default=0)
    milestones: Mapped[dict] = mapped_column(JSONB, default=dict)


class RollupRun(Base):
    __tablename__ = "rollup_runs"

    day: Mapped[datetime] = mapped_column(Date, primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_in: Mapped[int] = mapped_column(Integer, default=0)
    rows_out: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    graph: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    trigger_filter: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="running")
    current_node: Mapped[str | None] = mapped_column(String(128))
    wake_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context: Mapped[dict] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"))
    deal_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("deals.id"))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("workflow_id", "idempotency_key"),)


class WorkflowNodeRun(Base):
    __tablename__ = "workflow_node_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LifecycleDestination(Base, TimestampMixin):
    __tablename__ = "lifecycle_destinations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("connectors.id"), nullable=False)
    event_filter: Mapped[dict] = mapped_column(JSONB, default=dict)
    field_mapping: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Call(Base, TimestampMixin):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("contacts.id"))
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id"))
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_call_id: Mapped[str | None] = mapped_column(String(255))
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    from_number: Mapped[str | None] = mapped_column(String(32))
    to_number: Mapped[str | None] = mapped_column(String(32))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    recording_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="initiated")


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id"), unique=True, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    segments: Mapped[list] = mapped_column(JSONB, default=list)
    provider: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    language: Mapped[str | None] = mapped_column(String(16))
    summary: Mapped[str | None] = mapped_column(Text)
    sentiment: Mapped[str | None] = mapped_column(String(32))


class AssignmentCursor(Base):
    __tablename__ = "assignment_cursors"

    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id"), primary_key=True)
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    last_user_id: Mapped[str | None] = mapped_column(String(64))


ENTITY_TABLE_MAP: dict[str, Any] = {
    "contact": Contact,
    "organization": Organization,
    "deal": Deal,
    "ticket": Ticket,
    "pipeline": Pipeline,
    "stage": Stage,
    "form": Form,
}
