from __future__ import annotations

from phera.modules.connectors.gallabox import is_status_or_outbound, parse_inbound, verify_signature


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
