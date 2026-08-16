"""Stub adapters for Superhealth vendors — replace credentials in deployment."""

from __future__ import annotations

import logging
from typing import Any

from phera.modules.connectors.interfaces import (
    EmailProvider,
    LifecycleProvider,
    MessagingProvider,
    TelephonyProvider,
    TranscriptionProvider,
)

logger = logging.getLogger(__name__)


class StubMessagingProvider(MessagingProvider):
    async def send(self, to: str, body: str, template: str | None = None, **kwargs: Any) -> str:
        logger.info("WhatsApp stub to=%s template=%s", to, template)
        return f"stub-msg-{to}"


class StubEmailProvider(EmailProvider):
    async def send(self, to: str, subject: str, body: str, **kwargs: Any) -> str:
        logger.info("Email stub to=%s subject=%s", to, subject)
        return f"stub-email-{to}"


class StubTelephonyProvider(TelephonyProvider):
    async def click_to_call(self, from_user: str, to_number: str, **kwargs: Any) -> dict:
        return {"call_id": f"stub-{from_user}-{to_number}"}


class StubTranscriptionProvider(TranscriptionProvider):
    async def transcribe(self, recording_url: str, **kwargs: Any) -> dict:
        return {"text": "", "segments": [], "status": "completed"}


class MoEngageLifecycleProvider(LifecycleProvider):
    def __init__(self, app_id: str, api_key: str, base_url: str = "https://api.moengage.com"):
        self.app_id = app_id
        self.api_key = api_key
        self.base_url = base_url

    async def identify(self, contact: dict) -> None:
        logger.info("MoEngage identify contact_id=%s", contact.get("id"))

    async def track(self, event_type: str, contact: dict, payload: dict) -> None:
        logger.info("MoEngage track event=%s contact_id=%s", event_type, contact.get("id"))
