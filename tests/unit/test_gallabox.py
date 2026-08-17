from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet

from phera.db.models import Connector
from phera.modules.connectors.gallabox import (
    GallaboxMessagingProvider,
    is_status_or_outbound,
    parse_inbound,
    verify_signature,
)
from phera.modules.connectors.gallabox import (
    test_gallabox_credentials as check_gallabox_credentials,
)
from phera.security import crypto
from phera.settings import Settings


def _connector(monkeypatch, *, credentials: dict, secrets: dict | None) -> Connector:
    settings = Settings(credentials_encryption_key=Fernet.generate_key().decode())
    monkeypatch.setattr(crypto, "get_settings", lambda: settings)
    secrets_encrypted = crypto.encrypt_secrets(secrets) if secrets else None
    return Connector(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        type="gallabox",
        name="Test WhatsApp",
        credentials=credentials,
        secrets_encrypted=secrets_encrypted,
        is_active=True,
    )


def test_verify_signature_skipped_when_secret_empty():
    assert verify_signature(b"{}", None, "") is True


def test_verify_signature_matches_hmac():
    import hashlib
    import hmac

    secret = "s3cret"
    body = b'{"event":"Message.received"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, secret) is True
    assert verify_signature(body, "nope", secret) is False


def test_status_events_are_ignored():
    assert is_status_or_outbound({"event": "Message.WA.status.received"}) is True
    assert is_status_or_outbound({"direction": "outbound", "data": {"text": "hi"}}) is True


def test_parse_message_received_nested_contact():
    inbound = parse_inbound(
        {
            "event": "Message.received",
            "data": {
                "id": "msg-1",
                "conversationId": "conv-1",
                "channelId": "ch-1",
                "contact": {"name": "Riya", "phone": "919876543210"},
                "message": {"whatsapp": {"type": "text", "text": {"body": "Need a slot"}}},
            },
        }
    )
    assert inbound is not None
    assert inbound["contact_phone"] == "+919876543210"
    assert inbound["body"] == "Need a slot"
    assert inbound["thread_keys"]["provider_conversation_id"] == "conv-1"


def test_parse_simple_from_and_text():
    inbound = parse_inbound({"from": "+91 98765 43210", "text": "Hello", "id": "m2"})
    assert inbound is not None
    assert inbound["contact_phone"] == "+919876543210"
    assert inbound["body"] == "Hello"


def test_from_connector_maps_fields(monkeypatch):
    connector = _connector(
        monkeypatch,
        credentials={"account_id": "acc-1", "channel_id": "ch-1"},
        secrets={"api_key": "key-1", "api_secret": "secret-1"},
    )
    provider = GallaboxMessagingProvider.from_connector(connector)
    assert provider.account_id == "acc-1"
    assert provider.channel_id == "ch-1"
    assert provider.api_key == "key-1"
    assert provider.api_secret == "secret-1"
    assert provider.endpoint == "https://server.gallabox.com/devapi/messages/whatsapp"
    assert provider.configured() is True


def test_from_connector_custom_endpoint(monkeypatch):
    connector = _connector(
        monkeypatch,
        credentials={"account_id": "acc-1", "channel_id": "ch-1", "endpoint": "https://custom/api"},
        secrets={"api_key": "key-1", "api_secret": "secret-1"},
    )
    provider = GallaboxMessagingProvider.from_connector(connector)
    assert provider.endpoint == "https://custom/api"


def test_configured_false_when_missing_fields(monkeypatch):
    connector = _connector(monkeypatch, credentials={}, secrets=None)
    provider = GallaboxMessagingProvider.from_connector(connector)
    assert provider.configured() is False


def test_from_connector_without_secrets_has_empty_strings(monkeypatch):
    connector = _connector(
        monkeypatch, credentials={"account_id": "acc-1", "channel_id": "ch-1"}, secrets=None
    )
    provider = GallaboxMessagingProvider.from_connector(connector)
    assert provider.api_key == ""
    assert provider.api_secret == ""


@pytest.mark.asyncio
async def test_test_gallabox_credentials_ok():
    result = await check_gallabox_credentials(
        {"account_id": "a", "channel_id": "c"},
        {"api_key": "k", "api_secret": "s"},
    )
    assert result == {"ok": True, "error": None}


@pytest.mark.asyncio
async def test_test_gallabox_credentials_reports_missing_fields():
    result = await check_gallabox_credentials({}, {})
    assert result["ok"] is False
    assert "api_key" in result["error"]
    assert "account_id" in result["error"]
