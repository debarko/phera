from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from phera.authz.actor import Actor, actor_from_headers, parse_permissions_header, system_actor
from phera.authz.visibility import can_see_deal
from phera.db.models import Deal, OwnershipProfile


class TestParsePermissionsHeader:
    def test_empty(self):
        assert parse_permissions_header(None) == {}
        assert parse_permissions_header("") == {}

    def test_bare_codes_default_allow(self):
        assert parse_permissions_header("crm.deals.read,crm.tickets.claim") == {
            "crm.deals.read": "allow",
            "crm.tickets.claim": "allow",
        }

    def test_code_with_access(self):
        assert parse_permissions_header("crm.deals.read:own,crm.pipelines.read:allow") == {
            "crm.deals.read": "own",
            "crm.pipelines.read": "allow",
        }


class TestActorFromHeaders:
    def test_missing_actor_is_anonymous(self):
        actor = actor_from_headers({})
        assert actor.actor_type == "anonymous"
        assert actor.id is None

    def test_full_actor_headers(self):
        headers = {
            "x-actor-id": "user-42",
            "x-actor-email": "a@b.com",
            "x-actor-name": "Ada",
            "x-actor-roles": "support_l1,sales",
            "x-actor-permissions": "crm.deals.read:own,crm.tickets.claim:allow",
            "x-actor-unrestricted": "true",
        }
        actor = actor_from_headers(headers)
        assert actor.id == "user-42"
        assert actor.email == "a@b.com"
        assert actor.roles == ["support_l1", "sales"]
        assert actor.permissions["crm.deals.read"] == "own"
        assert actor.unrestricted is True


class TestActorPermissions:
    def test_unrestricted_grants_all(self):
        actor = Actor(unrestricted=True)
        assert actor.has_permission("anything")

    def test_allow_and_own(self):
        actor = Actor(permissions={"crm.deals.read": "own"})
        assert actor.has_permission("crm.deals.read", "own") is True
        assert actor.has_permission("crm.deals.read", "allow") is False

    def test_system_actor(self):
        actor = system_actor("workflow", "run-1")
        assert actor.unrestricted is True
        assert actor.actor_type == "workflow"


@pytest.mark.asyncio
class TestCanSeeDeal:
    async def test_unrestricted(self):
        deal = Deal(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            contact_id=uuid.uuid4(),
            pipeline_id=uuid.uuid4(),
            stage_id=uuid.uuid4(),
        )
        assert await can_see_deal(None, Actor(unrestricted=True), deal) is True

    async def test_global_read_permission(self):
        deal = Deal(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            contact_id=uuid.uuid4(),
            pipeline_id=uuid.uuid4(),
            stage_id=uuid.uuid4(),
            owner_user_id="other",
        )
        actor = Actor(id="me", permissions={"crm.deals.read": "allow"})
        assert await can_see_deal(None, actor, deal) is True

    async def test_own_read_permission(self):
        deal = Deal(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            contact_id=uuid.uuid4(),
            pipeline_id=uuid.uuid4(),
            stage_id=uuid.uuid4(),
            owner_user_id="me",
        )
        actor = Actor(id="me", permissions={"crm.deals.read": "own"})
        assert await can_see_deal(None, actor, deal) is True

    async def test_pipeline_centric_mode(self):
        deal = Deal(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            contact_id=uuid.uuid4(),
            pipeline_id=uuid.uuid4(),
            stage_id=uuid.uuid4(),
            owner_user_id="other",
        )
        profile = OwnershipProfile(workspace_id=deal.workspace_id, mode="pipeline_centric", flags={})
        actor = Actor(id="me", permissions={"crm.pipelines.read": "allow"})
        assert await can_see_deal(None, actor, deal, profile) is True

    async def test_default_owner_only(self):
        deal = Deal(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            contact_id=uuid.uuid4(),
            pipeline_id=uuid.uuid4(),
            stage_id=uuid.uuid4(),
            owner_user_id="owner-1",
        )
        actor = Actor(id="someone-else")
        assert await can_see_deal(None, actor, deal) is False
