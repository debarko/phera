from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Actor:
    id: str | None = None
    email: str | None = None
    name: str | None = None
    roles: list[str] = field(default_factory=list)
    permissions: dict[str, str] = field(default_factory=dict)
    unrestricted: bool = False
    actor_type: str = "user"

    def has_permission(self, code: str, access: str = "allow") -> bool:
        if self.unrestricted:
            return True
        perm = self.permissions.get(code)
        if perm == "allow":
            return True
        if access == "own" and perm == "own":
            return True
        return False


def parse_permissions_header(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    result: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            code, access = part.split(":", 1)
            result[code.strip()] = access.strip()
        else:
            result[part] = "allow"
    return result


def actor_from_headers(headers: dict[str, str]) -> Actor:
    actor_id = headers.get("x-actor-id") or headers.get("X-Actor-Id")
    if not actor_id:
        return Actor(actor_type="anonymous")

    roles_raw = headers.get("x-actor-roles") or headers.get("X-Actor-Roles") or ""
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]
    perms_raw = headers.get("x-actor-permissions") or headers.get("X-Actor-Permissions")
    unrestricted_raw = headers.get("x-actor-unrestricted") or headers.get("X-Actor-Unrestricted")

    return Actor(
        id=actor_id,
        email=headers.get("x-actor-email") or headers.get("X-Actor-Email"),
        name=headers.get("x-actor-name") or headers.get("X-Actor-Name"),
        roles=roles,
        permissions=parse_permissions_header(perms_raw),
        unrestricted=str(unrestricted_raw).lower() in ("true", "1", "yes"),
        actor_type="user",
    )


def system_actor(actor_type: str = "system", actor_id: str | None = None) -> Actor:
    return Actor(id=actor_id, actor_type=actor_type, unrestricted=True)
