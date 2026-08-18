from __future__ import annotations

from phera.modules.connectors.google_group import parse_inbound, parse_rfc822, strip_quoted_reply


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


def test_strip_quoted_reply_removes_history_and_signature():
    text = (
        "Yes, it worked.\n\n"
        "On Tue, 18 Aug 2026 at 00:33, <support-mail@superhealth.co.in> wrote:\n\n"
        "> I am checking whether this worked or not\n>\n\n"
        "--\n"
        "Regards,\n"
        "Debarko De\n"
        "______________\n"
        "SUPERHEALTH\n"
        "<https://www.superhealth.co.in/>\n"
        "Bangalore, India\n"
    )
    assert strip_quoted_reply(text) == "Yes, it worked."


def test_strip_quoted_reply_keeps_body_when_only_signature():
    text = "DO NOT REPLY to this.\n\n--\nRegards,\nDebarko De\n"
    assert strip_quoted_reply(text) == "DO NOT REPLY to this."


def test_strip_quoted_reply_falls_back_on_empty_result():
    # A message that is entirely quoted (nothing new to show) should not become an
    # empty ticket body — fall back to the original text rather than losing content.
    text = "> entirely quoted, nothing new"
    assert strip_quoted_reply(text) != ""


def test_strip_quoted_reply_handles_empty_input():
    assert strip_quoted_reply("") == ""


def test_parse_inbound_strips_quoted_reply_but_keeps_raw_full_text():
    raw = (
        "From: Debarko De <support-mail@superhealth.co.in>\r\n"
        "To: contact@superhealth.co.in\r\n"
        "Subject: Re: Test\r\n"
        "Message-ID: <2@example.com>\r\n"
        "\r\n"
        "Yes, it worked.\r\n\r\n"
        "On Tue, 18 Aug 2026 at 00:33, <support-mail@superhealth.co.in> wrote:\r\n\r\n"
        "> I am checking whether this worked or not\r\n"
    )
    inbound = parse_inbound({"raw_rfc822": raw})
    assert inbound is not None
    assert inbound["body"] == "Yes, it worked."
    # the full unstripped text is still recoverable from raw, nothing is lost
    assert "I am checking whether this worked or not" in inbound["raw"]["text"]
