"""Seed demo support tickets for local inbox testing."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from phera.db.models import (
    ChannelAccount,
    Contact,
    Connector,
    Message,
    Ticket,
    Workspace,
)
from phera.db.session import SessionLocal


async def seed_support_demo() -> None:
    async with SessionLocal() as session:
        ws = (
            await session.execute(select(Workspace).where(Workspace.slug == "default"))
        ).scalar_one()

        connector = Connector(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            type="support",
            name="Local Demo",
            credentials={},
        )
        session.add(connector)
        await session.flush()

        channels = []
        for kind, adapter, address in (
            ("email", "google_group", "support@demo.superhealth.local"),
            ("messaging", "gallabox", "+919876543210"),
            ("voice", "acefone", "+911800000000"),
        ):
            account = ChannelAccount(
                id=uuid.uuid4(),
                workspace_id=ws.id,
                kind=kind,
                adapter_type=adapter,
                address=address,
                connector_id=connector.id,
            )
            session.add(account)
            channels.append(account)
        await session.flush()

        demos = [
            ("email", "Billing question on last invoice", "demo.email@test.com", "+919111111111", "Email User"),
            ("messaging", "WhatsApp: Need IVF consult slot", "demo.chat@test.com", "+919222222222", "Chat User"),
            ("voice", "Missed call — callback requested", "demo.call@test.com", "+919333333333", "Call User"),
            ("messaging", "Follow-up on skin treatment", "demo.chat2@test.com", "+919444444444", "Skin User"),
        ]

        for kind, subject, email, phone, name in demos:
            channel = next(c for c in channels if c.kind == kind)
            contact = Contact(
                id=uuid.uuid4(),
                workspace_id=ws.id,
                name=name,
                primary_email=email,
                primary_phone=phone,
                source="demo_seed",
            )
            session.add(contact)
            await session.flush()

            now = datetime.now(UTC)
            ticket = Ticket(
                id=uuid.uuid4(),
                workspace_id=ws.id,
                contact_id=contact.id,
                channel_account_id=channel.id,
                subject=subject,
                status="open",
                priority="normal" if kind != "voice" else "high",
                status_entered_at=now,
                last_activity_at=now,
            )
            session.add(ticket)
            await session.flush()

            inbound_body = (
                f"Hi, this is a demo {kind} message for local inbox testing."
                if kind != "voice"
                else "Customer called and left a voicemail requesting a callback."
            )
            msg_at = datetime.now(UTC)
            session.add(
                Message(
                    id=uuid.uuid4(),
                    channel_account_id=channel.id,
                    ticket_id=ticket.id,
                    contact_id=contact.id,
                    direction="inbound",
                    body=inbound_body,
                    occurred_at=msg_at,
                )
            )
            ticket.last_activity_at = msg_at

        await session.commit()
        print(f"Seeded {len(demos)} demo support tickets on workspace {ws.id}")


def main() -> None:
    asyncio.run(seed_support_demo())


if __name__ == "__main__":
    main()
