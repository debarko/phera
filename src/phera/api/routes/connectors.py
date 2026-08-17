from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.api.schemas import ORMModel
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import ChannelAccount, Connector, Workspace
from phera.modules.connectors.registry import get_adapter
from phera.security.crypto import decrypt_secrets, encrypt_secrets

router = APIRouter(tags=["connectors"])


def _can_manage_channel_connectors(actor: Actor) -> bool:
    if actor.unrestricted:
        return True
    if "admin" in actor.roles or "support_admin" in actor.roles:
        return True
    return actor.has_permission("crm.settings.channels")


def _require_manage(actor: Actor) -> None:
    if not _can_manage_channel_connectors(actor):
        raise HTTPException(403, "Missing channel connector management permission")


class ConnectorOut(ORMModel):
    id: uuid.UUID
    type: str
    name: str
    credentials: dict
    is_active: bool
    has_secrets: bool


def _connector_out(connector: Connector) -> ConnectorOut:
    return ConnectorOut(
        id=connector.id,
        type=connector.type,
        name=connector.name,
        credentials=connector.credentials or {},
        is_active=connector.is_active,
        has_secrets=bool(connector.secrets_encrypted),
    )


class ConnectorCreate(BaseModel):
    type: str
    name: str
    credentials: dict = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class ConnectorUpdate(BaseModel):
    name: str | None = None
    credentials: dict | None = None
    secrets: dict[str, str] | None = None
    is_active: bool | None = None


class ConnectorTestResult(BaseModel):
    ok: bool | None = None
    imap_ok: bool | None = None
    smtp_ok: bool | None = None
    error: str | None = None


async def _get_connector_or_404(
    session: AsyncSession, workspace: Workspace, connector_id: uuid.UUID
) -> Connector:
    connector = await session.get(Connector, connector_id)
    if not connector or connector.workspace_id != workspace.id:
        raise HTTPException(404, "Connector not found")
    return connector


def _encrypted_secrets(secrets: dict[str, str]) -> str | None:
    if not secrets:
        return None
    return encrypt_secrets(secrets)


_SENSITIVE_KEY_MARKERS = ("password", "secret", "token", "key")


def _reject_secret_shaped_credentials(credentials: dict) -> None:
    """`credentials` is stored in plaintext JSONB and echoed back in API responses —
    anything secret-shaped belongs in `secrets` (encrypted), not here."""
    flagged = [k for k in credentials if any(m in k.lower() for m in _SENSITIVE_KEY_MARKERS)]
    if flagged:
        raise HTTPException(
            400,
            f"credentials contains secret-shaped key(s) {flagged} — put these in "
            "'secrets' instead, they are encrypted at rest there.",
        )


async def _test_connector_credentials(
    connector_type: str, credentials: dict, secrets: dict
) -> ConnectorTestResult:
    adapter = get_adapter(connector_type)
    if not adapter:
        raise HTTPException(400, f"Unknown connector type: {connector_type}")
    result = await adapter.test_fn(credentials, secrets)
    return ConnectorTestResult(**result)


@router.get("/connectors", response_model=list[ConnectorOut])
async def list_connectors(
    type: str | None = None,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    stmt = select(Connector).where(Connector.workspace_id == workspace.id)
    if type:
        stmt = stmt.where(Connector.type == type)
    q = await session.execute(stmt)
    return [_connector_out(c) for c in q.scalars().all()]


@router.post("/connectors", response_model=ConnectorOut, status_code=201)
async def create_connector(
    body: ConnectorCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    _reject_secret_shaped_credentials(body.credentials)
    connector = Connector(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        type=body.type,
        name=body.name,
        credentials=body.credentials,
        secrets_encrypted=_encrypted_secrets(body.secrets),
    )
    session.add(connector)
    await commit_and_notify(session)
    await session.refresh(connector)
    return _connector_out(connector)


@router.patch("/connectors/{connector_id}", response_model=ConnectorOut)
async def update_connector(
    connector_id: uuid.UUID,
    body: ConnectorUpdate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    connector = await _get_connector_or_404(session, workspace, connector_id)

    if body.name is not None:
        connector.name = body.name
    if body.credentials is not None:
        _reject_secret_shaped_credentials(body.credentials)
        connector.credentials = body.credentials
    if body.is_active is not None:
        connector.is_active = body.is_active
    if body.secrets:
        existing = (
            decrypt_secrets(connector.secrets_encrypted) if connector.secrets_encrypted else {}
        )
        merged = {**existing, **{k: v for k, v in body.secrets.items() if v}}
        if merged:
            connector.secrets_encrypted = encrypt_secrets(merged)
    # secrets omitted, or every submitted value blank -> keep existing secrets_encrypted untouched,
    # per-key: an omitted/blank key keeps that key's old value, only submitted non-empty keys change

    await commit_and_notify(session)
    await session.refresh(connector)
    return _connector_out(connector)


@router.delete("/connectors/{connector_id}", status_code=204)
async def delete_connector(
    connector_id: uuid.UUID,
    force: bool = False,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    connector = await _get_connector_or_404(session, workspace, connector_id)

    q = await session.execute(
        select(ChannelAccount).where(
            ChannelAccount.connector_id == connector.id,
            ChannelAccount.is_active.is_(True),
        )
    )
    dependents = list(q.scalars().all())
    if dependents and not force:
        raise HTTPException(
            409,
            f"{len(dependents)} active channel(s) still use this connector. "
            "Deactivate them first or pass ?force=true.",
        )
    for account in dependents:
        account.is_active = False

    # Soft-delete: ChannelAccount.connector_id is a hard FK, so a real DELETE would either
    # violate it or orphan dependent channels. is_active=False is consistent with how the
    # rest of the schema treats deactivation (Connector/ChannelAccount both already have it).
    connector.is_active = False
    await commit_and_notify(session)


@router.post("/connectors/{connector_id}/test", response_model=ConnectorTestResult)
async def test_connector(
    connector_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    connector = await _get_connector_or_404(session, workspace, connector_id)
    secrets = decrypt_secrets(connector.secrets_encrypted) if connector.secrets_encrypted else {}
    return await _test_connector_credentials(connector.type, connector.credentials or {}, secrets)


@router.post("/connectors/test", response_model=ConnectorTestResult)
async def test_connector_draft(
    body: ConnectorCreate,
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    return await _test_connector_credentials(body.type, body.credentials, body.secrets)


class EmailChannelCreate(BaseModel):
    name: str
    address: str
    credentials: dict = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    routing_policy_id: uuid.UUID | None = None


class EmailChannelOut(ORMModel):
    connector: ConnectorOut
    channel_account_id: uuid.UUID
    address: str


@router.post("/email-channels", response_model=EmailChannelOut, status_code=201)
async def create_email_channel(
    body: EmailChannelCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    _reject_secret_shaped_credentials(body.credentials)
    connector = Connector(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        type="email_imap_smtp",
        name=body.name,
        credentials=body.credentials,
        secrets_encrypted=_encrypted_secrets(body.secrets),
    )
    session.add(connector)
    await session.flush()

    account = ChannelAccount(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="email",
        adapter_type="imap_smtp",
        address=body.address,
        connector_id=connector.id,
        routing_policy_id=body.routing_policy_id,
        is_active=True,
    )
    session.add(account)
    await commit_and_notify(session)
    await session.refresh(connector)

    return EmailChannelOut(
        connector=_connector_out(connector),
        channel_account_id=account.id,
        address=account.address,
    )


class WhatsAppChannelCreate(BaseModel):
    name: str
    phone_number: str
    credentials: dict = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    routing_policy_id: uuid.UUID | None = None


class WhatsAppChannelOut(ORMModel):
    connector: ConnectorOut
    channel_account_id: uuid.UUID
    address: str


@router.post("/whatsapp-channels", response_model=WhatsAppChannelOut, status_code=201)
async def create_whatsapp_channel(
    body: WhatsAppChannelCreate,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    _reject_secret_shaped_credentials(body.credentials)
    connector = Connector(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        type="gallabox",
        name=body.name,
        credentials=body.credentials,
        secrets_encrypted=_encrypted_secrets(body.secrets),
    )
    session.add(connector)
    await session.flush()

    account = ChannelAccount(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="messaging",
        adapter_type="gallabox",
        address=body.phone_number,
        connector_id=connector.id,
        routing_policy_id=body.routing_policy_id,
        is_active=True,
    )
    session.add(account)
    await commit_and_notify(session)
    await session.refresh(connector)

    return WhatsAppChannelOut(
        connector=_connector_out(connector),
        channel_account_id=account.id,
        address=account.address,
    )
