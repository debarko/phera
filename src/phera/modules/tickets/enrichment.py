from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import ChannelAccount, Contact, Ticket


async def channel_for_ticket(session: AsyncSession, ticket: Ticket) -> ChannelAccount | None:
    if not ticket.channel_account_id:
        return None
    return await session.get(ChannelAccount, ticket.channel_account_id)


async def ticket_detail_dict(session: AsyncSession, ticket: Ticket) -> dict:
    channel = await channel_for_ticket(session, ticket)
    contact = await session.get(Contact, ticket.contact_id)
    return {
        "id": ticket.id,
        "contact_id": ticket.contact_id,
        "assignee_user_id": ticket.assignee_user_id,
        "subject": ticket.subject,
        "status": ticket.status,
        "priority": ticket.priority,
        "last_activity_at": ticket.last_activity_at,
        "channel_account_id": ticket.channel_account_id,
        "channel_kind": channel.kind if channel else None,
        "channel_address": channel.address if channel else None,
        "channel_adapter_type": channel.adapter_type if channel else None,
        "contact_name": contact.name if contact else None,
        "contact_phone": contact.primary_phone if contact else None,
        "contact_email": contact.primary_email if contact else None,
        "routing_tier": ticket.routing_tier,
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "status_entered_at": ticket.status_entered_at,
        "first_assigned_at": ticket.first_assigned_at,
        "first_response_at": ticket.first_response_at,
        "resolved_at": ticket.resolved_at,
        "closed_at": ticket.closed_at,
    }
