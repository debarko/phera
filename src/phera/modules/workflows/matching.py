from __future__ import annotations


def event_matches_trigger(event_type: str, trigger_filter: dict | None) -> bool:
    """Return True when an outbox event type matches a workflow trigger filter."""
    filt = trigger_filter or {}
    types = filt.get("event_types") or []
    if not types:
        return True
    if event_type in types:
        return True
    return any(
        event_type.startswith(t.rstrip("*"))
        for t in types
        if isinstance(t, str) and t.endswith("*")
    )
