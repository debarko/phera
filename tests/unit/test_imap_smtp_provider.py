from __future__ import annotations

import uuid

from cryptography.fernet import Fernet

from phera.db.models import Connector
from phera.modules.connectors.imap_smtp import ImapSmtpEmailProvider
from phera.security import crypto
from phera.settings import Settings


def _connector(monkeypatch, *, credentials: dict, password: str | None) -> Connector:
    settings = Settings(credentials_encryption_key=Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    secrets_encrypted = crypto.encrypt_secrets({"password": password}) if password else None
    return Connector(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        type="email_imap_smtp",
        name="Test Gmail",
        credentials=credentials,
        secrets_encrypted=secrets_encrypted,
        is_active=True,
    )


def test_from_connector_maps_fields(monkeypatch):
    connector = _connector(
        monkeypatch,
        credentials={
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "username": "support@example.com",
            "poll_folder": "INBOX",
        },
        password="app-password",
    )
    provider = ImapSmtpEmailProvider.from_connector(connector)
    assert provider.imap_host == "imap.gmail.com"
    assert provider.smtp_host == "smtp.gmail.com"
    assert provider.username == "support@example.com"
    assert provider.password == "app-password"
    assert provider.from_address == "support@example.com"
    assert provider.configured() is True


def test_from_connector_uses_explicit_from_address(monkeypatch):
    connector = _connector(
        monkeypatch,
        credentials={
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
            "username": "pipe-user",
            "from_address": "contact@example.com",
        },
        password="secret",
    )
    provider = ImapSmtpEmailProvider.from_connector(connector)
    assert provider.from_address == "contact@example.com"


def test_configured_false_when_missing_fields(monkeypatch):
    connector = _connector(monkeypatch, credentials={}, password=None)
    provider = ImapSmtpEmailProvider.from_connector(connector)
    assert provider.configured() is False


def test_from_connector_without_secrets_has_empty_password(monkeypatch):
    connector = _connector(
        monkeypatch,
        credentials={
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
            "username": "u",
        },
        password=None,
    )
    provider = ImapSmtpEmailProvider.from_connector(connector)
    assert provider.password == ""


def test_configured_false_when_only_password_missing(monkeypatch):
    """Regression guard: every other field present, only the password blank — must still
    report unconfigured, otherwise the worker attempts an IMAP/SMTP login with an empty
    password instead of failing cleanly with a clear "not configured" error."""
    connector = _connector(
        monkeypatch,
        credentials={
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
            "username": "u",
            "from_address": "u@example.com",
        },
        password=None,
    )
    provider = ImapSmtpEmailProvider.from_connector(connector)
    assert provider.configured() is False
