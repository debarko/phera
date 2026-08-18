from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from phera.authz.service import ensure_user_stub
from phera.db.models import (
    AgentPresence,
    AgentTelephonyIdentity,
    Call,
    OutboxEvent,
    Team,
    Transcript,
)
from phera.security.crypto import encrypt_secrets
from phera.settings import get_settings
from tests.support import factories

pytestmark = pytest.mark.integration

ADMIN = {
    "X-Actor-Id": "voice-admin",
    "X-Actor-Email": "voice-admin@test.com",
    "X-Actor-Roles": "admin",
}
TOKEN = "a" * 32
EXOPHONE = "+911800000001"


@pytest.fixture(autouse=True)
def _crypto_key(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed_voice_agent(db_session, workspace, user_id: str = "voice-agent"):
    actor = factories.staff_actor(user_id=user_id)
    await ensure_user_stub(db_session, actor, workspace.id)
    await db_session.flush()
    db_session.add(AgentPresence(user_id=user_id, status="available"))
    db_session.add(
        AgentTelephonyIdentity(
            user_id=user_id,
            workspace_id=workspace.id,
            provider="exotel",
            sip_user="sip-user-1",
            sip_secret_encrypted=encrypt_secrets({"secret": "sip-secret"}),
            sip_domain="sip.exotel.com",
            sip_port=443,
            is_active=True,
        )
    )
    await db_session.flush()
    return user_id


async def _seed_exotel_channel(
    db_session, workspace, *, token: str = TOKEN, address: str = EXOPHONE
):
    connector = factories.connector(
        workspace.id,
        type="exotel",
        name="Exotel",
        secrets_encrypted=encrypt_secrets({"webhook_token": token}),
    )
    db_session.add(connector)
    await db_session.flush()
    account = factories.channel_account(
        workspace.id,
        kind="voice",
        adapter_type="exotel",
        address=address,
        connector_id=connector.id,
    )
    db_session.add(account)
    await db_session.flush()
    return account


@pytest.mark.asyncio
async def test_voice_channel_rejects_short_webhook_token(client):
    resp = await client.post(
        "/v1/voice-channels",
        headers=ADMIN,
        json={
            "name": "Clinic line",
            "exophone": "+91 1800 000 002",
            "secrets": {"webhook_token": "short"},
        },
    )
    assert resp.status_code == 400
    assert "32" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_voice_channel_normalizes_exophone(client, db_session):
    resp = await client.post(
        "/v1/voice-channels",
        headers=ADMIN,
        json={"name": "Clinic line", "exophone": "+91 1800 000 003"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["address"] == "+911800000003"
    assert len(body["webhook_token"]) >= 32


@pytest.mark.asyncio
async def test_exotel_route_is_idempotent_and_requires_call_sid(
    client, db_session, workspace_bundle
):
    ws = workspace_bundle.workspace
    await _seed_exotel_channel(db_session, ws)
    await _seed_voice_agent(db_session, ws)

    missing = await client.post(
        "/hooks/exotel/route",
        params={"token": TOKEN, "From": "+919811110001", "To": EXOPHONE},
    )
    assert missing.status_code == 400

    unauthorized = await client.post(
        "/hooks/exotel/route",
        params={
            "token": "wrong-token-wrong-token-wrong-12",
            "From": "+919811110001",
            "To": EXOPHONE,
            "CallSid": "sid-1",
        },
    )
    assert unauthorized.status_code == 401

    unknown_phone = await client.post(
        "/hooks/exotel/route",
        params={
            "token": TOKEN,
            "From": "+919811110001",
            "To": "+911800999999",
            "CallSid": "sid-1",
        },
    )
    assert unknown_phone.status_code == 401

    first = await client.post(
        "/hooks/exotel/route",
        params={
            "token": TOKEN,
            "From": "+919811110001",
            "To": EXOPHONE,
            "CallSid": "sid-1",
        },
    )
    assert first.status_code == 200
    assert first.text == "sip-user-1"

    retry = await client.post(
        "/hooks/exotel/route",
        params={
            "token": TOKEN,
            "From": "+919811110001",
            "To": EXOPHONE,
            "CallSid": "sid-1",
        },
    )
    assert retry.status_code == 200
    assert retry.text == "sip-user-1"

    count = await db_session.scalar(select(func.count()).select_from(Call))
    assert count == 1


@pytest.mark.asyncio
async def test_exotel_call_event_enqueues_transcription_and_clears_presence(
    client, db_session, workspace_bundle
):
    ws = workspace_bundle.workspace
    await _seed_exotel_channel(db_session, ws)
    user_id = await _seed_voice_agent(db_session, ws)

    routed = await client.post(
        "/hooks/exotel/route",
        params={
            "token": TOKEN,
            "From": "+919811110002",
            "To": EXOPHONE,
            "CallSid": "sid-ended",
        },
    )
    assert routed.status_code == 200

    ended = await client.post(
        "/hooks/exotel/call",
        params={
            "token": TOKEN,
            "CallSid": "sid-ended",
            "To": EXOPHONE,
            "CallStatus": "completed",
            "RecordingUrl": "https://example.com/rec.wav",
        },
    )
    assert ended.status_code == 200
    assert ended.json()["event"] == "call.ended"

    presence = await db_session.get(AgentPresence, user_id)
    assert presence is not None
    assert presence.on_voice_call is False

    outbox = (await db_session.execute(select(OutboxEvent))).scalars().all()
    assert any(event.event_type == "call.ended" for event in outbox)
    transcripts = (await db_session.execute(select(Transcript))).scalars().all()
    assert transcripts == []


@pytest.mark.asyncio
async def test_replace_pipeline_teams_rejects_foreign_and_duplicate_ids(
    client, db_session, workspace_bundle
):
    ws = workspace_bundle.workspace
    team = Team(id=uuid.uuid4(), workspace_id=ws.id, name="Sales", slug="sales")
    other_ws = factories.workspace(slug="other", name="Other")
    db_session.add_all([team, other_ws])
    await db_session.flush()
    foreign = Team(id=uuid.uuid4(), workspace_id=other_ws.id, name="Other", slug="other")
    db_session.add(foreign)
    await db_session.flush()

    pipeline_id = workspace_bundle.pipeline.id
    duplicates = await client.put(
        f"/v1/pipelines/{pipeline_id}/teams",
        headers=ADMIN,
        json={"team_ids": [str(team.id), str(team.id)]},
    )
    assert duplicates.status_code == 422

    foreign_resp = await client.put(
        f"/v1/pipelines/{pipeline_id}/teams",
        headers=ADMIN,
        json={"team_ids": [str(foreign.id)]},
    )
    assert foreign_resp.status_code == 422

    ok = await client.put(
        f"/v1/pipelines/{pipeline_id}/teams",
        headers=ADMIN,
        json={"team_ids": [str(team.id)]},
    )
    assert ok.status_code == 200
    assert ok.json() == [str(team.id)]


@pytest.mark.asyncio
async def test_telephony_upsert_rejects_other_workspace_identity(
    client, db_session, workspace_bundle
):
    ws = workspace_bundle.workspace
    actor = factories.staff_actor(user_id="moved-agent")
    await ensure_user_stub(db_session, actor, ws.id)
    other_ws = factories.workspace(slug="other-tel", name="Other Tel")
    db_session.add(other_ws)
    await db_session.flush()
    db_session.add(
        AgentTelephonyIdentity(
            user_id="moved-agent",
            workspace_id=other_ws.id,
            provider="exotel",
            sip_user="old",
            sip_secret_encrypted=encrypt_secrets({"secret": "old"}),
            sip_domain="sip.exotel.com",
            sip_port=443,
        )
    )
    await db_session.flush()

    resp = await client.post(
        "/v1/telephony/agents",
        headers=ADMIN,
        json={
            "user_id": "moved-agent",
            "sip_user": "new",
            "sip_secret": "secret",
            "sip_domain": "sip.exotel.com",
        },
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_transcribe_call_is_idempotent(db_session, workspace_bundle):
    from phera.modules.transcription.job import transcribe_call

    ws = workspace_bundle.workspace
    contact = factories.contact(ws.id)
    db_session.add(contact)
    await db_session.flush()
    call = Call(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        contact_id=contact.id,
        provider="exotel",
        provider_call_id="sid-tx",
        direction="inbound",
        recording_url="https://example.com/rec.wav",
        status="completed",
    )
    db_session.add(call)
    await db_session.flush()

    first = await transcribe_call(db_session, call.id)
    second = await transcribe_call(db_session, call.id)
    await db_session.commit()

    assert first is not None
    assert second is not None
    assert first.id == second.id
    count = await db_session.scalar(select(func.count()).select_from(Transcript))
    assert count == 1
