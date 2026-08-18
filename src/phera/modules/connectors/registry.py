"""Adapter registry — the one place that maps a `Connector.type` to its code.

Only DB-configured adapters (those with a `from_connector(connector)` constructor) are
registered here. Legacy `.env`-only adapters (`google_group`) keep their existing hardcoded
`if/elif` branches in `hooks.py`/`tickets.py` — different shape, not worth forcing in.

Adding a new vendor (a different WhatsApp API, a different email host, ...) means adding one
adapter module + one entry here — not touching `connectors.py`/`tickets.py`/`hooks.py`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from phera.modules.connectors.gallabox import (
    GallaboxMessagingProvider,
    test_gallabox_credentials,
)
from phera.modules.connectors.gallabox import (
    parse_inbound as gallabox_parse_inbound,
)
from phera.modules.connectors.gallabox import (
    verify_signature as gallabox_verify_signature,
)
from phera.modules.connectors.exotel import ExotelTelephonyProvider, test_exotel_credentials
from phera.modules.connectors.imap_smtp import ImapSmtpEmailProvider, test_imap_smtp_credentials


@dataclass
class AdapterSpec:
    type: str
    kind: str  # "email" | "messaging" | "voice"
    provider_cls: type
    test_fn: Callable[[dict, dict], Awaitable[dict]]
    webhook_connector_id: str | None = None
    verify_signature_fn: Callable[[bytes, str | None, str], bool] | None = None
    parse_inbound_fn: Callable[[dict], dict | None] | None = None
    signature_header: str | None = None


ADAPTERS: dict[str, AdapterSpec] = {
    "email_imap_smtp": AdapterSpec(
        type="email_imap_smtp",
        kind="email",
        provider_cls=ImapSmtpEmailProvider,
        test_fn=test_imap_smtp_credentials,
    ),
    "gallabox": AdapterSpec(
        type="gallabox",
        kind="messaging",
        provider_cls=GallaboxMessagingProvider,
        test_fn=test_gallabox_credentials,
        webhook_connector_id="gallabox",
        verify_signature_fn=gallabox_verify_signature,
        parse_inbound_fn=gallabox_parse_inbound,
        signature_header="x-gallabox-signature",
    ),
    "exotel": AdapterSpec(
        type="exotel",
        kind="voice",
        provider_cls=ExotelTelephonyProvider,
        test_fn=test_exotel_credentials,
    ),
}


def get_adapter(connector_type: str) -> AdapterSpec | None:
    return ADAPTERS.get(connector_type)
