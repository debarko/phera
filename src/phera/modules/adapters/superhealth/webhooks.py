"""Map data-server webhooks to Phera domain events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify, track_outbox_notify
from phera.db.models import Contact, Deal, OutboxEvent, Workspace
from phera.db.mutate import FieldChange, MutateRequest, mutate


async def _find_or_create_contact(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    phone: str | None,
    email: str | None,
    name: str | None,
) -> Contact:
    contact = None
    if email:
        q = await session.execute(
            select(Contact).where(
                Contact.workspace_id == workspace_id,
                Contact.primary_email == email,
                Contact.is_deleted.is_(False),
            )
        )
        contact = q.scalar_one_or_none()
    if not contact and phone:
        q = await session.execute(
            select(Contact).where(
                Contact.workspace_id == workspace_id,
                Contact.primary_phone == phone,
                Contact.is_deleted.is_(False),
            )
        )
        contact = q.scalar_one_or_none()
    if contact:
        return contact

    contact = Contact(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name=name,
        primary_email=email,
        primary_phone=phone,
        source="superhealth_webhook",
    )
    session.add(contact)
    await session.flush()
    return contact


async def handle_service_event_completed(
    session: AsyncSession,
    workspace: Workspace,
    payload: dict,
) -> dict:
    """Superhealth: appointment/service completed → Contact + Deal milestone."""
    contact = await _find_or_create_contact(
        session,
        workspace.id,
        phone=payload.get("phone") or payload.get("patient_phone"),
        email=payload.get("email") or payload.get("patient_email"),
        name=payload.get("name") or payload.get("patient_name"),
    )

    deal_id = payload.get("deal_id")
    if deal_id:
        deal = await session.get(Deal, uuid.UUID(deal_id))
        if deal:
            await mutate(
                session,
                MutateRequest(
                    entity=deal,
                    entity_type="deal",
                    action="service_event.completed",
                    changes=[
                        FieldChange(
                            "custom_fields.service_event",
                            None,
                            payload.get("event_type"),
                        )
                    ],
                    actor=Actor(actor_type="connector", id="superhealth"),
                    pipeline_id=deal.pipeline_id,
                    outbox_event_type="service_event.completed",
                    idempotency_key=(
                        f"service_event.completed:{payload.get('event_id', deal.id)}"
                    ),
                    outbox_payload={
                        "deal_id": str(deal.id),
                        "contact_id": str(contact.id),
                        **payload,
                    },
                    contact_id=contact.id,
                    deal_id=deal.id,
                ),
            )
    else:
        outbox = OutboxEvent(
            id=uuid.uuid4(),
            occurred_at=datetime.now(UTC),
            workspace_id=workspace.id,
            event_type="service_event.completed",
            entity_type="contact",
            entity_id=contact.id,
            idempotency_key=f"service_event.completed:{payload.get('event_id', contact.id)}",
            payload={"contact_id": str(contact.id), **payload},
            status="pending",
        )
        session.add(outbox)
        track_outbox_notify(session, outbox.id)

    await commit_and_notify(session)
    return {"contact_id": str(contact.id), "deal_id": deal_id}
