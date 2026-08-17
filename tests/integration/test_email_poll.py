from __future__ import annotations

import base64

import pytest
from sqlalchemy import select

from phera.db.models import EmailPollState, Message, Ticket
from phera.modules.connectors.imap_smtp import ImapSmtpEmailProvider
from phera.modules.tickets.email_poll import poll_all_email_accounts, poll_one_email_account
from tests.support import factories

pytestmark = pytest.mark.integration


def _raw_email(*, from_addr: str, to_addr: str, subject: str, message_id: str, body: str) -> bytes:
    # bytes, matching what imap_smtp.py's real IMAP fetch now yields (no premature
    # top-level decode) — parse_rfc822 handles bytes directly.
    return (
        f"From: {from_addr}\r\n"
        f"To: {to_addr}\r\n"
        f"Subject: {subject}\r\n"
        f"Message-ID: {message_id}\r\n"
        "\r\n"
        f"{body}\r\n"
    ).encode()


class FakeProvider:
    """Stands in for ImapSmtpEmailProvider at the fetch_new_messages boundary."""

    def __init__(self, batches: list[tuple[list[dict], int]]):
        self._batches = list(batches)
        self.calls = 0

    def configured(self) -> bool:
        return True

    async def fetch_new_messages(self, since_uid):
        self.calls += 1
        if not self._batches:
            return [], since_uid or 0
        return self._batches.pop(0)


async def _setup_channel(
    db_session,
    workspace_bundle,
    *,
    address="support@example.com",
    username="support@example.com",
):
    conn = factories.connector(
        workspace_bundle.workspace.id,
        credentials={
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
            "username": username,
        },
    )
    db_session.add(conn)
    await db_session.flush()
    account = factories.channel_account(
        workspace_bundle.workspace.id,
        kind="email",
        adapter_type="imap_smtp",
        address=address,
        connector_id=conn.id,
    )
    db_session.add(account)
    await db_session.commit()
    return conn, account


@pytest.mark.asyncio
async def test_first_poll_establishes_baseline_without_backfill(
    db_session, workspace_bundle, monkeypatch
):
    conn, account = await _setup_channel(db_session, workspace_bundle)
    fake = FakeProvider(batches=[([], 42)])
    monkeypatch.setattr(ImapSmtpEmailProvider, "from_connector", lambda c: fake)

    ingested = await poll_one_email_account(db_session, workspace_bundle.workspace, account)
    assert ingested == 0

    state = await db_session.get(EmailPollState, account.id)
    assert state.last_uid == 42
    assert state.status == "idle"

    q = await db_session.execute(select(Ticket))
    assert q.scalars().all() == []


@pytest.mark.asyncio
async def test_poll_creates_ticket_and_is_idempotent_on_rerun(
    db_session, workspace_bundle, monkeypatch
):
    conn, account = await _setup_channel(db_session, workspace_bundle)
    raw = _raw_email(
        from_addr="patient@example.com",
        to_addr="support@example.com",
        subject="Need reports",
        message_id="<msg-1@example.com>",
        body="Please send my reports.",
    )
    fake = FakeProvider(batches=[([{"uid": 1, "raw": raw}], 1)])
    monkeypatch.setattr(ImapSmtpEmailProvider, "from_connector", lambda c: fake)

    ingested = await poll_one_email_account(db_session, workspace_bundle.workspace, account)
    assert ingested == 1

    q = await db_session.execute(select(Ticket))
    tickets = q.scalars().all()
    assert len(tickets) == 1

    q2 = await db_session.execute(select(Message))
    messages = q2.scalars().all()
    assert len(messages) == 1
    assert messages[0].provider_message_id == "<msg-1@example.com>"

    # Re-poll: state now has last_uid=1, throttled by last_polled_at unless we reset it.
    state = await db_session.get(EmailPollState, account.id)
    state.last_polled_at = None
    await db_session.commit()

    fake_rerun = FakeProvider(batches=[([{"uid": 1, "raw": raw}], 1)])
    monkeypatch.setattr(ImapSmtpEmailProvider, "from_connector", lambda c: fake_rerun)
    ingested_again = await poll_one_email_account(db_session, workspace_bundle.workspace, account)
    assert ingested_again == 0  # dedup via provider_message_id inside ingest_inbound_message

    q3 = await db_session.execute(select(Ticket))
    assert len(q3.scalars().all()) == 1


@pytest.mark.asyncio
async def test_poll_strips_quoted_reply_but_keeps_full_text_in_raw(
    db_session, workspace_bundle, monkeypatch
):
    conn, account = await _setup_channel(db_session, workspace_bundle)
    raw = _raw_email(
        from_addr="patient@example.com",
        to_addr="support@example.com",
        subject="Re: Need reports",
        message_id="<msg-quoted-1@example.com>",
        body=(
            "Yes, it worked.\r\n\r\n"
            "On Tue, 18 Aug 2026 at 00:33, <support@example.com> wrote:\r\n\r\n"
            "> I am checking whether this worked or not\r\n"
        ),
    )
    fake = FakeProvider(batches=[([{"uid": 1, "raw": raw}], 1)])
    monkeypatch.setattr(ImapSmtpEmailProvider, "from_connector", lambda c: fake)

    ingested = await poll_one_email_account(db_session, workspace_bundle.workspace, account)
    assert ingested == 1

    q = await db_session.execute(select(Message))
    message = q.scalars().one()
    assert message.body == "Yes, it worked."
    stored_original = base64.b64decode(message.raw["raw_rfc822_b64"]).decode("utf-8")
    assert "I am checking whether this worked or not" in stored_original


@pytest.mark.asyncio
async def test_poll_skips_own_outbound_copy(db_session, workspace_bundle, monkeypatch):
    conn, account = await _setup_channel(
        db_session, workspace_bundle, username="support@example.com"
    )
    raw = _raw_email(
        from_addr="support@example.com",
        to_addr="patient@example.com",
        subject="Re: Need reports",
        message_id="<agent-reply-1@example.com>",
        body="Here are your reports.",
    )
    fake = FakeProvider(batches=[([{"uid": 5, "raw": raw}], 5)])
    monkeypatch.setattr(ImapSmtpEmailProvider, "from_connector", lambda c: fake)

    ingested = await poll_one_email_account(db_session, workspace_bundle.workspace, account)
    assert ingested == 0

    q = await db_session.execute(select(Ticket))
    assert q.scalars().all() == []


@pytest.mark.asyncio
async def test_self_throttle_skips_recent_poll(db_session, workspace_bundle, monkeypatch):
    conn, account = await _setup_channel(db_session, workspace_bundle)
    conn.credentials = {**conn.credentials, "poll_interval_seconds": 3600}
    await db_session.commit()

    fake = FakeProvider(batches=[([], 10)])
    monkeypatch.setattr(ImapSmtpEmailProvider, "from_connector", lambda c: fake)
    await poll_one_email_account(db_session, workspace_bundle.workspace, account)
    assert fake.calls == 1

    fake2 = FakeProvider(batches=[([], 20)])
    monkeypatch.setattr(ImapSmtpEmailProvider, "from_connector", lambda c: fake2)
    await poll_one_email_account(db_session, workspace_bundle.workspace, account)
    assert fake2.calls == 0  # throttled, IMAP never hit again


@pytest.mark.asyncio
async def test_poll_all_email_accounts_polls_independently(
    db_session, workspace_bundle, monkeypatch
):
    conn_a, account_a = await _setup_channel(
        db_session,
        workspace_bundle,
        address="gmail-support@example.com",
        username="gmail-support@example.com",
    )
    conn_b, account_b = await _setup_channel(
        db_session,
        workspace_bundle,
        address="yahoo-support@example.com",
        username="yahoo-support@example.com",
    )

    raw_a = _raw_email(
        from_addr="patient-a@example.com",
        to_addr="gmail-support@example.com",
        subject="Question A",
        message_id="<a-1@example.com>",
        body="Hi from A",
    )
    raw_b = _raw_email(
        from_addr="patient-b@example.com",
        to_addr="yahoo-support@example.com",
        subject="Question B",
        message_id="<b-1@example.com>",
        body="Hi from B",
    )

    providers = {
        account_a.id: FakeProvider(batches=[([{"uid": 1, "raw": raw_a}], 1)]),
        account_b.id: FakeProvider(batches=[([{"uid": 1, "raw": raw_b}], 1)]),
    }

    def _pick_provider(connector):
        return providers[account_a.id if connector.id == conn_a.id else account_b.id]

    monkeypatch.setattr(ImapSmtpEmailProvider, "from_connector", staticmethod(_pick_provider))

    total = await poll_all_email_accounts(db_session, workspace_bundle.workspace)
    assert total == 2

    state_a = await db_session.get(EmailPollState, account_a.id)
    state_b = await db_session.get(EmailPollState, account_b.id)
    assert state_a.last_uid == 1
    assert state_b.last_uid == 1

    q = await db_session.execute(select(Ticket))
    tickets = q.scalars().all()
    assert len(tickets) == 2
