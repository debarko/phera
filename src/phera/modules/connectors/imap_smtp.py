"""Generic IMAP receive + SMTP send email adapter — one adapter, many DB-configured accounts.

Configuration and secrets come from a `Connector` row (`credentials` JSONB + encrypted
`secrets_encrypted`), not from `.env`/Settings. Any vendor that speaks plain IMAP+SMTP
(Gmail, Yahoo, generic mailboxes, self-hosted Postfix/Dovecot, ...) uses this one adapter —
differentiation between accounts is data (host/port/username), not code.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Any

from phera.db.models import Connector
from phera.modules.connectors.interfaces import EmailProvider
from phera.security.crypto import decrypt_secrets

logger = logging.getLogger(__name__)


class ImapSmtpEmailProvider(EmailProvider):
    def __init__(
        self,
        *,
        imap_host: str,
        imap_port: int,
        imap_use_ssl: bool,
        smtp_host: str,
        smtp_port: int,
        smtp_use_tls: bool,
        username: str,
        password: str,
        from_address: str,
        poll_folder: str = "INBOX",
    ):
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_use_ssl = imap_use_ssl
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_use_tls = smtp_use_tls
        self.username = username
        self.password = password
        self.from_address = from_address
        self.poll_folder = poll_folder

    @classmethod
    def from_connector(cls, connector: Connector) -> ImapSmtpEmailProvider:
        creds = connector.credentials or {}
        secrets = (
            decrypt_secrets(connector.secrets_encrypted) if connector.secrets_encrypted else {}
        )
        username = creds.get("username", "")
        return cls(
            imap_host=creds.get("imap_host", ""),
            imap_port=int(creds.get("imap_port", 993)),
            imap_use_ssl=bool(creds.get("imap_use_ssl", True)),
            smtp_host=creds.get("smtp_host", ""),
            smtp_port=int(creds.get("smtp_port", 587)),
            smtp_use_tls=bool(creds.get("smtp_use_tls", True)),
            username=username,
            password=secrets.get("password", ""),
            from_address=creds.get("from_address") or username,
            poll_folder=creds.get("poll_folder", "INBOX"),
        )

    def configured(self) -> bool:
        return bool(
            self.imap_host
            and self.smtp_host
            and self.username
            and self.password
            and self.from_address
        )

    async def send(self, to: str, subject: str, body: str, **kwargs: Any) -> str:
        if not self.smtp_host or not self.from_address:
            raise RuntimeError("SMTP is not configured for this connector")

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
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as smtp:
                if self.smtp_use_tls:
                    smtp.starttls(context=ssl.create_default_context())
                if self.username:
                    smtp.login(self.username, self.password)
                smtp.send_message(msg)

        await asyncio.to_thread(_send)
        logger.info("IMAP/SMTP connector send message_id=%s", message_id)
        return str(message_id)

    async def fetch_new_messages(self, since_uid: int | None) -> tuple[list[dict], int]:
        return await asyncio.to_thread(self._fetch_new_messages_sync, since_uid)

    def _fetch_new_messages_sync(self, since_uid: int | None) -> tuple[list[dict], int]:
        from imapclient import IMAPClient

        client = IMAPClient(self.imap_host, port=self.imap_port, ssl=self.imap_use_ssl, timeout=20)
        try:
            client.login(self.username, self.password)
            client.select_folder(self.poll_folder)

            if since_uid is None:
                status = client.folder_status(self.poll_folder, ["UIDNEXT"])
                uidnext = int(status.get(b"UIDNEXT") or status.get("UIDNEXT") or 1)
                return [], max(uidnext - 1, 0)

            uids = [u for u in client.search(["UID", f"{since_uid + 1}:*"]) if u > since_uid]
            messages: list[dict] = []
            new_watermark = since_uid
            if uids:
                response = client.fetch(uids, ["RFC822"])
                for uid, data in response.items():
                    raw = data.get(b"RFC822")
                    if raw is None:
                        continue
                    # Keep the raw bytes as-is — parse_rfc822 accepts bytes and decodes
                    # each body part per its own declared charset, so no lossy top-level
                    # UTF-8 decode happens here.
                    messages.append({"uid": uid, "raw": raw})
                    new_watermark = max(new_watermark, uid)
            return messages, new_watermark
        finally:
            try:
                client.logout()
            except Exception:
                pass


def _test_credentials_sync(credentials: dict, secrets: dict) -> dict:
    imap_ok = False
    smtp_ok = False
    errors: list[str] = []
    username = credentials.get("username", "")
    password = secrets.get("password", "")

    try:
        from imapclient import IMAPClient

        client = IMAPClient(
            credentials.get("imap_host", ""),
            port=int(credentials.get("imap_port", 993)),
            ssl=bool(credentials.get("imap_use_ssl", True)),
            timeout=10,
        )
        client.login(username, password)
        client.logout()
        imap_ok = True
    except Exception as exc:
        errors.append(f"IMAP: {exc}")

    try:
        with smtplib.SMTP(
            credentials.get("smtp_host", ""), int(credentials.get("smtp_port", 587)), timeout=10
        ) as smtp:
            if credentials.get("smtp_use_tls", True):
                smtp.starttls(context=ssl.create_default_context())
            if username:
                smtp.login(username, password)
        smtp_ok = True
    except Exception as exc:
        errors.append(f"SMTP: {exc}")

    return {"imap_ok": imap_ok, "smtp_ok": smtp_ok, "error": "; ".join(errors) or None}


async def test_imap_smtp_credentials(credentials: dict, secrets: dict) -> dict:
    return await asyncio.to_thread(_test_credentials_sync, credentials, secrets)
