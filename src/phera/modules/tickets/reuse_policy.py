"""Workspace-level ticket reuse / reopen policy.

Stored on OwnershipProfile.flags["ticket_reuse"] so each company can choose
the window, whether resolved/closed tickets come back, and whether the last
assignee is kept or the ticket returns to the queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from phera.db.models import OwnershipProfile

TICKET_REUSE_FLAG = "ticket_reuse"
SUPPORT_AGENT_IDS_FLAG = "support_agent_user_ids"
SUPPORT_AGENTS_FLAG = "support_agents"
DEFAULT_WINDOW_SECONDS = 7 * 24 * 60 * 60
WINDOW_MIN_SECONDS = 60
WINDOW_MAX_SECONDS = 365 * 24 * 60 * 60
CHANNEL_KINDS = ("messaging", "email", "voice")
ASSIGNEE_KEEP = "keep"
ASSIGNEE_QUEUE = "queue"
OPEN_STATUSES = ("open", "pending", "waiting")


@dataclass(frozen=True)
class EffectiveReusePolicy:
    window_seconds: int
    reopen_resolved: bool
    reopen_closed: bool
    on_reopen_assignee: str

    def reusable_statuses(self) -> tuple[str, ...]:
        statuses = list(OPEN_STATUSES)
        if self.reopen_resolved:
            statuses.append("resolved")
        if self.reopen_closed:
            statuses.append("closed")
        return tuple(statuses)

    def allows_status(self, status: str | None) -> bool:
        return (status or "") in self.reusable_statuses()


def _clamp_window(value: Any, fallback: int = DEFAULT_WINDOW_SECONDS) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(WINDOW_MIN_SECONDS, min(WINDOW_MAX_SECONDS, seconds))


def _as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1, "0", "1", "true", "false", "True", "False"):
        return value in (True, 1, "1", "true", "True")
    return fallback


def _assignee_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in ("queue", "unassign", "unassigned"):
        return ASSIGNEE_QUEUE
    return ASSIGNEE_KEEP


def support_agent_ids(flags: dict[str, Any] | None) -> set[str]:
    """User ids currently listed as support agents/admins for this workspace."""
    data = flags or {}
    ids: set[str] = set()
    raw_members = data.get(SUPPORT_AGENTS_FLAG)
    if isinstance(raw_members, list):
        for item in raw_members:
            if isinstance(item, dict):
                user_id = str(item.get("user_id") or "").strip()
            else:
                user_id = str(item).strip()
            if user_id:
                ids.add(user_id)
    raw_ids = data.get(SUPPORT_AGENT_IDS_FLAG)
    if isinstance(raw_ids, list):
        for item in raw_ids:
            user_id = str(item).strip()
            if user_id:
                ids.add(user_id)
    return ids


def _channel_override(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    override: dict[str, Any] = {}
    if "window_seconds" in raw and raw["window_seconds"] is not None:
        override["window_seconds"] = _clamp_window(raw["window_seconds"])
    if "reopen_resolved" in raw and raw["reopen_resolved"] is not None:
        override["reopen_resolved"] = _as_bool(raw["reopen_resolved"], True)
    if "reopen_closed" in raw and raw["reopen_closed"] is not None:
        override["reopen_closed"] = _as_bool(raw["reopen_closed"], True)
    return override or None


def parse_ticket_reuse(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    channels: dict[str, dict[str, Any]] = {}
    nested = data.get("channels") if isinstance(data.get("channels"), dict) else {}
    for kind in CHANNEL_KINDS:
        override = _channel_override(nested.get(kind))
        if override:
            channels[kind] = override
    return {
        "window_seconds": _clamp_window(data.get("window_seconds"), DEFAULT_WINDOW_SECONDS),
        "reopen_resolved": _as_bool(data.get("reopen_resolved"), True),
        "reopen_closed": _as_bool(data.get("reopen_closed"), True),
        "on_reopen_assignee": _assignee_mode(data.get("on_reopen_assignee")),
        "channels": channels,
    }


def effective_reuse_policy(
    parsed: dict[str, Any], channel_kind: str | None
) -> EffectiveReusePolicy:
    override = {}
    if channel_kind in CHANNEL_KINDS:
        raw_override = parsed.get("channels") or {}
        if isinstance(raw_override, dict):
            override = raw_override.get(channel_kind) or {}
    resolved = parsed["reopen_resolved"]
    closed = parsed["reopen_closed"]
    if "reopen_resolved" in override:
        resolved = override["reopen_resolved"]
    if "reopen_closed" in override:
        closed = override["reopen_closed"]
    return EffectiveReusePolicy(
        window_seconds=int(override.get("window_seconds") or parsed["window_seconds"]),
        reopen_resolved=bool(resolved),
        reopen_closed=bool(closed),
        on_reopen_assignee=str(parsed["on_reopen_assignee"]),
    )


async def load_reuse_policy(
    session: AsyncSession, workspace_id, channel_kind: str | None
) -> EffectiveReusePolicy:
    profile = await session.get(OwnershipProfile, workspace_id)
    flags = dict(profile.flags or {}) if profile else {}
    parsed = parse_ticket_reuse(flags.get(TICKET_REUSE_FLAG))
    return effective_reuse_policy(parsed, channel_kind)
