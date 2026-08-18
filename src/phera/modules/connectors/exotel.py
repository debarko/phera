"""Exotel voice adapter — credential shape + telephony provider stub for the WebRTC/CCM flow.

Unlike Gallabox/IMAP, this adapter's `click_to_call` is not exercised by the primary flow:
calls are routed via Exotel's Connect-applet dynamic URL (`voice_hooks.py`) and answered
in-browser via the WebRTC SDK, not server-triggered. The `TelephonyProvider` implementation
exists for interface symmetry and as a seam for a future outbound-dial feature.
"""

from __future__ import annotations

from typing import Any

from phera.db.models import Connector
from phera.modules.connectors.interfaces import TelephonyProvider
from phera.security.crypto import decrypt_secrets


class ExotelTelephonyProvider(TelephonyProvider):
    def __init__(
        self,
        *,
        account_sid: str,
        subdomain: str,
        sip_domain: str,
        api_key: str,
        api_token: str,
    ):
        self.account_sid = account_sid
        self.subdomain = subdomain
        self.sip_domain = sip_domain
        self.api_key = api_key
        self.api_token = api_token

    @classmethod
    def from_connector(cls, connector: Connector) -> ExotelTelephonyProvider:
        creds = connector.credentials or {}
        secrets = (
            decrypt_secrets(connector.secrets_encrypted) if connector.secrets_encrypted else {}
        )
        return cls(
            account_sid=creds.get("account_sid", ""),
            subdomain=creds.get("subdomain", ""),
            sip_domain=creds.get("sip_domain", ""),
            api_key=secrets.get("api_key", ""),
            api_token=secrets.get("api_token", ""),
        )

    def configured(self) -> bool:
        return bool(
            self.account_sid
            and self.subdomain
            and self.sip_domain
            and self.api_key
            and self.api_token
        )

    async def click_to_call(self, from_user: str, to_number: str, **kwargs: Any) -> dict:
        raise NotImplementedError(
            "Exotel calls are routed via the WebRTC SDK / Connect-applet dynamic URL, "
            "not server-triggered click-to-call. See src/phera/api/routes/voice_hooks.py."
        )


async def test_exotel_credentials(credentials: dict, secrets: dict) -> dict:
    """Field-presence check only — no confirmed safe read-only endpoint to ping."""
    errors = []
    if not credentials.get("account_sid"):
        errors.append("account_sid is required")
    if not credentials.get("subdomain"):
        errors.append("subdomain is required")
    if not credentials.get("sip_domain"):
        errors.append("sip_domain is required")
    if not secrets.get("api_key"):
        errors.append("api_key is required")
    if not secrets.get("api_token"):
        errors.append("api_token is required")
    return {"ok": not errors, "error": "; ".join(errors) or None}
