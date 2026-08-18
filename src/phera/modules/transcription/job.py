from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import Call, Interaction, Transcript
from phera.modules.connectors.stubs import StubTranscriptionProvider

logger = logging.getLogger(__name__)


async def transcribe_call(session: AsyncSession, call_id: uuid.UUID) -> Transcript | None:
    call = await session.get(Call, call_id)
    if not call or not call.recording_url:
        logger.warning("Call %s missing recording_url", call_id)
        return None

    existing_q = await session.execute(select(Transcript).where(Transcript.call_id == call.id))
    existing = existing_q.scalar_one_or_none()
    if existing:
        return existing

    provider = StubTranscriptionProvider()
    result = await provider.transcribe(call.recording_url)

    transcript = Transcript(
        id=uuid.uuid4(),
        call_id=call.id,
        status="completed",
        text=result.get("text"),
        summary=result.get("summary"),
        sentiment=result.get("sentiment"),
    )
    interaction = Interaction(
        id=uuid.uuid4(),
        workspace_id=call.workspace_id,
        contact_id=call.contact_id,
        ticket_id=call.ticket_id,
        type="call_transcript",
        body=result.get("text"),
        occurred_at=datetime.now(UTC),
        actor_type="system",
    )
    try:
        async with session.begin_nested():
            session.add(transcript)
            session.add(interaction)
            await session.flush()
    except IntegrityError:
        existing_q = await session.execute(select(Transcript).where(Transcript.call_id == call.id))
        return existing_q.scalar_one_or_none()

    call.status = "completed"
    return transcript
