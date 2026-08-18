from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from phera.api.deps import get_authenticated_actor, get_db, get_workspace
from phera.authz.actor import Actor
from phera.db.commit import commit_and_notify
from phera.db.models import AgentTelephonyIdentity, User, Workspace
from phera.security.crypto import decrypt_secrets, encrypt_secrets

router = APIRouter(tags=["telephony"])


def _can_manage_telephony(actor: Actor) -> bool:
    if actor.unrestricted:
        return True
    if "admin" in actor.roles or "support_admin" in actor.roles:
        return True
    return actor.has_permission("crm.settings.channels")


def _require_manage(actor: Actor) -> None:
    if not _can_manage_telephony(actor):
        raise HTTPException(403, "Missing telephony management permission")


class AgentTelephonyIdentityIn(BaseModel):
    user_id: str
    sip_user: str
    sip_secret: str
    sip_domain: str
    sip_port: int = 443
    provider: str = "exotel"


class AgentTelephonyIdentityOut(BaseModel):
    user_id: str
    user_email: str | None = None
    user_name: str | None = None
    provider: str
    sip_user: str
    sip_domain: str
    sip_port: int
    is_active: bool


class SipCredentialsOut(BaseModel):
    sip_user: str
    sip_secret: str
    sip_domain: str
    sip_port: int
    security: str = "wss"
    user_name: str | None = None
    display_name: str | None = None


async def _identity_out(
    session: AsyncSession,
    identity: AgentTelephonyIdentity,
    user: User | None = None,
) -> AgentTelephonyIdentityOut:
    if user is None:
        user = await session.get(User, identity.user_id)
    return AgentTelephonyIdentityOut(
        user_id=identity.user_id,
        user_email=user.email if user else None,
        user_name=user.name if user else None,
        provider=identity.provider,
        sip_user=identity.sip_user,
        sip_domain=identity.sip_domain,
        sip_port=identity.sip_port,
        is_active=identity.is_active,
    )


@router.get("/telephony/agents", response_model=list[AgentTelephonyIdentityOut])
async def list_agent_identities(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    q = await session.execute(
        select(AgentTelephonyIdentity, User)
        .outerjoin(User, User.id == AgentTelephonyIdentity.user_id)
        .where(AgentTelephonyIdentity.workspace_id == workspace.id)
    )
    return [await _identity_out(session, identity, user) for identity, user in q.all()]


@router.post("/telephony/agents", response_model=AgentTelephonyIdentityOut, status_code=201)
async def upsert_agent_identity(
    body: AgentTelephonyIdentityIn,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    user = await session.get(User, body.user_id)
    if not user or user.workspace_id != workspace.id:
        raise HTTPException(404, "User not found in this workspace")

    identity = await session.get(AgentTelephonyIdentity, body.user_id)
    if identity is None:
        identity = AgentTelephonyIdentity(user_id=body.user_id, workspace_id=workspace.id)
        session.add(identity)
    elif identity.workspace_id != workspace.id:
        raise HTTPException(409, "Agent telephony identity belongs to another workspace")
    identity.provider = body.provider
    identity.sip_user = body.sip_user
    identity.sip_secret_encrypted = encrypt_secrets({"secret": body.sip_secret})
    identity.sip_domain = body.sip_domain
    identity.sip_port = body.sip_port
    identity.is_active = True

    await commit_and_notify(session)
    await session.refresh(identity)
    return await _identity_out(session, identity)


@router.delete("/telephony/agents/{user_id}", status_code=204)
async def delete_agent_identity(
    user_id: str,
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    _require_manage(actor)
    identity = await session.get(AgentTelephonyIdentity, user_id)
    if not identity or identity.workspace_id != workspace.id:
        raise HTTPException(404, "Agent telephony identity not found")
    await session.delete(identity)
    await commit_and_notify(session)


@router.get("/me/telephony/sip-credentials", response_model=SipCredentialsOut)
async def get_my_sip_credentials(
    session: AsyncSession = Depends(get_db),
    workspace: Workspace = Depends(get_workspace),
    actor: Actor = Depends(get_authenticated_actor),
):
    if not actor.id:
        raise HTTPException(403, "No authenticated agent identity")
    identity = await session.get(AgentTelephonyIdentity, actor.id)
    if not identity or identity.workspace_id != workspace.id or not identity.is_active:
        raise HTTPException(404, "No voice/SIP identity provisioned for this agent")

    secret = decrypt_secrets(identity.sip_secret_encrypted).get("secret", "")
    return SipCredentialsOut(
        sip_user=identity.sip_user,
        sip_secret=secret,
        sip_domain=identity.sip_domain,
        sip_port=identity.sip_port,
        user_name=actor.email,
        display_name=actor.name or actor.email,
    )
