from __future__ import annotations

import pytest

from phera.modules.tickets.inbound import resolve_channel_account
from tests.support import factories

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_single_active_account_resolves_without_hint(db_session, workspace_bundle):
    account = factories.channel_account(
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address="+911111111111",
    )
    db_session.add(account)
    await db_session.commit()

    resolved = await resolve_channel_account(
        db_session,
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address_hint=None,
    )
    assert resolved is not None
    assert resolved.id == account.id


@pytest.mark.asyncio
async def test_address_hint_disambiguates_multiple_accounts(db_session, workspace_bundle):
    account_a = factories.channel_account(
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address="+911111111111",
    )
    account_b = factories.channel_account(
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address="+922222222222",
    )
    db_session.add_all([account_a, account_b])
    await db_session.commit()

    resolved = await resolve_channel_account(
        db_session,
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address_hint="+922222222222",
    )
    assert resolved is not None
    assert resolved.id == account_b.id


@pytest.mark.asyncio
async def test_ambiguous_match_is_rejected_not_guessed(db_session, workspace_bundle):
    """Regression guard: with 2+ active accounts and no address match, routing to an
    arbitrary one (accounts[0]) could place a customer's message on the wrong mailbox or
    WhatsApp number. Must return None instead."""
    account_a = factories.channel_account(
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address="+911111111111",
    )
    account_b = factories.channel_account(
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address="+922222222222",
    )
    db_session.add_all([account_a, account_b])
    await db_session.commit()

    resolved = await resolve_channel_account(
        db_session,
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address_hint="+933333333333",  # matches neither account
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_no_active_accounts_returns_none(db_session, workspace_bundle):
    resolved = await resolve_channel_account(
        db_session,
        workspace_bundle.workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address_hint=None,
    )
    assert resolved is None
