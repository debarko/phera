"""Google Group email adapter — inbound JSON/MIME, outbound SMTP as the group address."""

from __future__ import annotations

import email
import hashlib
import hmac
import logging
import re
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from typing import Any

from email_reply_parser import EmailReplyParser

from phera.modules.connectors.interfaces import EmailProvider
from phera.settings import get_settings

logger = logging.getLogger(__name__)


def strip_quoted_reply(text: str) -> str:
    """Strip quoted history and signature blocks from a plain-text email body.

    Heuristic (via email_reply_parser) — not perfect across every client/locale's reply
    format, so falls back to the original text if stripping would leave nothing behind.
    """
    if not text:
        return text
    try:
        cleaned = EmailReplyParser.parse_reply(text).strip()
    except Exception:
        logger.exception("Failed to strip quoted reply text")
        return text.strip()
    return cleaned or text.strip()


def verify_signature(raw_body: bytes, signature: str | None, secret: str) -> bool:
    if not secret:
        return True
    if not signature:
        return False
    if hmac.compare_digest(signature.strip(), secret):
        return True
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.strip())


def _header(msg: email.message.Message, name: str) -> str | None:
    value = msg.get(name)
    return value.strip() if value else None


def parse_rfc822(raw: str | bytes) -> dict:
    # Parse from bytes when available (message_from_bytes) rather than decoding the whole
    # message to str first — each part's body is still decoded per its own declared
    # charset below, so this avoids a premature, lossy top-level UTF-8 decode of the
    # entire message for non-UTF-8 mail.
    msg = (
        email.message_from_bytes(raw)
        if isinstance(raw, bytes)
        else email.message_from_string(raw)
    )
    body = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and body is None:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        else:
            body = str(payload or "")
    _, from_addr = parseaddr(_header(msg, "From") or "")
    _, to_addr = parseaddr(_header(msg, "To") or "")
    return {
        "from": from_addr,
        "to": to_addr,
        "subject": _header(msg, "Subject"),
        "text": body,
        "message_id": _header(msg, "Message-ID"),
        "in_reply_to": _header(msg, "In-Reply-To"),
        "references": _header(msg, "References"),
        "name": parseaddr(_header(msg, "From") or "")[0] or None,
    }


def parse_inbound(payload: dict) -> dict | None:
    if payload.get("raw_rfc822") or payload.get("raw"):
        parsed = parse_rfc822(str(payload.get("raw_rfc822") or payload.get("raw")))
        payload = {**parsed, **{k: v for k, v in payload.items() if k not in ("raw_rfc822", "raw")}}

    from_addr = str(payload.get("from") or payload.get("sender") or "").strip()
    _, from_addr = parseaddr(from_addr)
    to_addr = str(payload.get("to") or payload.get("recipient") or "").strip()
    _, to_addr = parseaddr(to_addr)
    raw_body = str(payload.get("text") or payload.get("body") or payload.get("plain") or "").strip()
    body = strip_quoted_reply(raw_body)
    if not from_addr or not body:
        return None

    message_id = payload.get("message_id") or payload.get("messageId")
    in_reply_to = payload.get("in_reply_to") or payload.get("inReplyTo")
    references = payload.get("references")
    if isinstance(references, str):
        references = [item for item in re.split(r"\s+", references) if item]
    subject = payload.get("subject")

    return {
        "channel_kind": "email",
        "adapter_type": "google_group",
        "address_hint": to_addr or None,
        "body": body,
        "subject": subject,
        "contact_name": payload.get("name") or from_addr.split("@")[0],
        "contact_email": from_addr,
        "contact_phone": None,
        "provider_message_id": message_id,
        "thread_keys": {
            "rfc_message_id": message_id,
            "in_reply_to": in_reply_to,
            "references": references or [],
        },
        "occurred_at": datetime.now(UTC),
        "raw": payload,
    }


class GoogleGroupEmailProvider(EmailProvider):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        from_address: str,
        use_tls: bool,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_address = from_address
        self.use_tls = use_tls

    @classmethod
    def from_settings(cls) -> GoogleGroupEmailProvider:
        settings = get_settings()
        return cls(
            host=settings.smtp_host,
            port=settings.smtp_port,
            user=settings.smtp_user,
            password=settings.smtp_password,
            from_address=settings.smtp_from or settings.email_inbound_address,
            use_tls=settings.smtp_use_tls,
        )

    def configured(self) -> bool:
        return bool(self.host and self.from_address)

    async def send(self, to: str, subject: str, body: str, **kwargs: Any) -> str:
        if not self.configured():
            raise RuntimeError("SMTP is not configured — set SMTP_HOST and SMTP_FROM")

        message_id = kwargs.get("message_id") or make_msgid(domain=self.from_address.split("@")[-1])
        msg = EmailMessage()
        msg["From"] = self.from_address
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = message_id
        if kwargs.get("in_reply_to"):
            msg["In-Reply-To"] = kwargs["in_reply_to"]
        if kwargs.get("references"):
            refs = kwargs["references"]
            msg["References"] = " ".join(refs) if isinstance(refs, list) else str(refs)
        msg.set_content(body)

        def _send() -> None:
            with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                if self.use_tls:
                    smtp.starttls()
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)

        import asyncio

        await asyncio.to_thread(_send)
        logger.info("SMTP send to=%s subject=%s", to, subject)
        return str(message_id)
