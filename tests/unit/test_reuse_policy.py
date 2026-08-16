from __future__ import annotations

from phera.modules.tickets.reuse_policy import (
    ASSIGNEE_KEEP,
    ASSIGNEE_QUEUE,
    DEFAULT_WINDOW_SECONDS,
    effective_reuse_policy,
    parse_ticket_reuse,
    support_agent_ids,
)


def test_parse_defaults():
    parsed = parse_ticket_reuse(None)
    assert parsed["window_seconds"] == DEFAULT_WINDOW_SECONDS
    assert parsed["reopen_resolved"] is True
    assert parsed["reopen_closed"] is True
    assert parsed["on_reopen_assignee"] == ASSIGNEE_KEEP
    assert parsed["channels"] == {}


def test_parse_custom_and_channel_override():
    parsed = parse_ticket_reuse(
        {
            "window_seconds": 10 * 3600,
            "reopen_closed": False,
            "on_reopen_assignee": "queue",
            "channels": {"messaging": {"window_seconds": 3600, "reopen_resolved": False}},
        }
    )
    assert parsed["window_seconds"] == 10 * 3600
    assert parsed["reopen_closed"] is False
    assert parsed["on_reopen_assignee"] == ASSIGNEE_QUEUE
    chat = effective_reuse_policy(parsed, "messaging")
    assert chat.window_seconds == 3600
    assert chat.reopen_resolved is False
    assert chat.reopen_closed is False
    email = effective_reuse_policy(parsed, "email")
    assert email.window_seconds == 10 * 3600
    assert email.reopen_resolved is True
    assert email.allows_status("resolved") is True
    assert chat.allows_status("resolved") is False


def test_support_agent_ids_from_flags():
    assert support_agent_ids(None) == set()
    members = {"support_agents": [{"user_id": "a", "access": "agent"}]}
    assert support_agent_ids(members) == {"a"}
    assert support_agent_ids({"support_agent_user_ids": ["b"]}) == {"b"}
