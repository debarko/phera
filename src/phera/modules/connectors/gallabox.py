"""Gallabox WhatsApp adapter — parse inbound webhooks and send session/template messages."""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from phera.modules.connectors.interfaces import MessagingProvider
from phera.settings import get_settings

logger = logging.getLogger(__name__)

STATUS_EVENTS = {
    "message.wa.status.received",
    "message.wa.status.failed",
    "message.wa.payment.status.received",
    "template.status",
}


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    if not digits:
        return None
    return f"+{digits}"


def _walk(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _as_text(
            _first(value.get("body"), value.get("text"), value.get("caption"), value.get("message"))
        )
    return str(value)


def event_name(payload: dict) -> str:
    raw = _first(
        payload.get("event"),
        payload.get("eventType"),
        payload.get("event_type"),
        payload.get("type"),
        _walk(payload, "data", "event"),
    )
    return str(raw or "").strip()


def is_status_or_outbound(payload: dict) -> bool:
    name = event_name(payload).lower()
    if name in STATUS_EVENTS or "status" in name:
        return True
    direction = str(
        _first(
            payload.get("direction"),
            _walk(payload, "data", "direction"),
            _walk(payload, "message", "direction"),
        )
        or "inbound"
    ).lower()
    return direction in ("outbound", "out", "sent")


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not secret:
        return True
    if not signature:
        return False
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


def parse_inbound(payload: dict) -> dict | None:
    """Normalize Gallabox webhook JSON into an inbound message dict, or None to ignore."""
    if is_status_or_outbound(payload):
        return None

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    message = data.get("message") if isinstance(data.get("message"), dict) else data
    contact = _first(
        data.get("contact"),
        payload.get("contact"),
        message.get("contact") if isinstance(message, dict) else None,
    )
    contact = contact if isinstance(contact, dict) else {}
    whatsapp = message.get("whatsapp") if isinstance(message, dict) else None
    whatsapp = whatsapp if isinstance(whatsapp, dict) else {}

    phone = normalize_phone(
        _first(
            contact.get("phone"),
            contact.get("whatsappNumber"),
            contact.get("number"),
            data.get("from"),
            data.get("phone"),
            payload.get("from"),
            payload.get("recipient_id"),
            _walk(whatsapp, "from"),
        )
    )
    body = _as_text(
        _first(
            data.get("content"),
            data.get("text"),
            data.get("body"),
            message.get("text") if isinstance(message, dict) else None,
            message.get("body") if isinstance(message, dict) else None,
            whatsapp.get("text"),
            whatsapp.get("body"),
            _walk(data, "content", "text"),
        )
    )
    if not phone or not body:
        return None

    name = _first(
        contact.get("name"), contact.get("fullName"), data.get("name"), payload.get("name")
    )
    provider_id = str(
        _first(
            data.get("id"),
            data.get("message_id"),
            data.get("messageId"),
            message.get("id") if isinstance(message, dict) else None,
            payload.get("id"),
            payload.get("localMessageId"),
        )
        or ""
    )
    conversation_id = _first(
        data.get("conversationId"),
        data.get("conversation_id"),
        message.get("conversationId") if isinstance(message, dict) else None,
    )
    channel_id = _first(data.get("channelId"), data.get("channel_id"), payload.get("channelId"))
    business_number = normalize_phone(
        _first(
            data.get("whatsappNumber"),
            _walk(data, "channel", "whatsappNumber"),
            payload.get("whatsappNumber"),
        )
    )

    return {
        "channel_kind": "messaging",
        "adapter_type": "gallabox",
        "address_hint": business_number,
        "body": body,
        "subject": None,
        "contact_name": str(name) if name else None,
        "contact_email": contact.get("email"),
        "contact_phone": phone,
        "provider_message_id": provider_id or None,
        "thread_keys": {
            "provider_conversation_id": conversation_id,
            "provider_message_id": provider_id or None,
            "channel_id": channel_id,
        },
        "occurred_at": datetime.now(UTC),
        "raw": payload,
    }


class GallaboxMessagingProvider(MessagingProvider):
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        endpoint: str,
        account_id: str,
        channel_id: str,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.endpoint = endpoint
        self.account_id = account_id
        self.channel_id = channel_id

    @classmethod
    def from_settings(cls) -> GallaboxMessagingProvider:
        settings = get_settings()
        return cls(
            api_key=settings.gallabox_api_key,
            api_secret=settings.gallabox_api_secret,
            endpoint=settings.gallabox_api_endpoint,
            account_id=settings.gallabox_account_id,
            channel_id=settings.gallabox_channel_id,
        )

    def configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.account_id and self.channel_id)

    async def send(self, to: str, body: str, template: str | None = None, **kwargs: Any) -> str:
        if not self.configured():
            raise RuntimeError(
                "Gallabox is not configured — set GALLABOX_API_KEY/SECRET/ACCOUNT_ID/CHANNEL_ID"
            )

        phone = normalize_phone(to) or to
        name = kwargs.get("name") or phone
        if template:
            whatsapp = {
                "type": "template",
                "template": {
                    "templateName": template,
                    "bodyValues": kwargs.get("body_values") or {},
                },
            }
        else:
            whatsapp = {"type": "text", "text": {"body": body}}

        payload = {
            "accountId": self.account_id,
            "channelId": self.channel_id,
            "channelType": "whatsapp",
            "recipient": {"name": name, "phone": phone},
            "whatsapp": whatsapp,
        }
        headers = {
            "apiKey": self.api_key,
            "apiSecret": self.api_secret,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.endpoint, json=payload, headers=headers)
        if response.status_code not in (200, 201, 202):
            logger.error(
                "Gallabox send failed status=%s body=%s", response.status_code, response.text
            )
            raise RuntimeError(f"Gallabox send failed ({response.status_code}): {response.text}")
        data = response.json() if response.content else {}
        return str(data.get("id") or data.get("messageId") or f"gallabox-{phone}")
