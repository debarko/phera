from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import Call, Interaction, Transcript
from phera.modules.connectors.stubs import StubTranscriptionProvider


async def process_call_transcription(session: AsyncSession, call_id: uuid.UUID) -> None:
    call = await session.get(Call, call_id)
    if not call or not call.recording_url:
        return

    provider = StubTranscriptionProvider()
    result = await provider.transcribe(call.recording_url)

    transcript = Transcript(
        id=uuid.uuid4(),
        call_id=call.id,
        text=result.get("text"),
        segments=result.get("segments", []),
        provider="stub",
        status=result.get("status", "completed"),
    )
    session.add(transcript)

    if call.contact_id:
        session.add(
            Interaction(
                id=uuid.uuid4(),
                workspace_id=call.workspace_id,
                contact_id=call.contact_id,
                ticket_id=call.ticket_id,
                type="call",
                body=result.get("text"),
                occurred_at=datetime.now(UTC),
                actor_type="system",
            )
        )
