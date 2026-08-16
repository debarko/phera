from __future__ import annotations

from phera.modules.connectors.google_group import parse_inbound, parse_rfc822


def test_parse_json_email():
    inbound = parse_inbound(
        {
            "from": "Patient Name <patient@example.com>",
            "to": "support@example.com",
            "subject": "Need records",
            "text": "Please send my reports.",
            "message_id": "<abc@mail.gmail.com>",
        }
    )
    assert inbound is not None
    assert inbound["contact_email"] == "patient@example.com"
    assert inbound["subject"] == "Need records"
    assert inbound["thread_keys"]["rfc_message_id"] == "<abc@mail.gmail.com>"


def test_parse_rfc822_round_trip():
    raw = (
        "From: Ada <ada@example.com>\r\n"
        "To: support@example.com\r\n"
        "Subject: Hello\r\n"
        "Message-ID: <1@example.com>\r\n"
        "\r\n"
        "Body text\r\n"
    )
    parsed = parse_rfc822(raw)
    assert parsed["from"] == "ada@example.com"
    assert parsed["subject"] == "Hello"
    assert "Body text" in (parsed["text"] or "")

    inbound = parse_inbound({"raw_rfc822": raw})
    assert inbound is not None
    assert inbound["contact_email"] == "ada@example.com"
    assert inbound["body"].strip() == "Body text"
