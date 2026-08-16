from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from phera.db.models import Contact, Deal, Form, Pipeline, Stage
from phera.modules.pipelines.intake import resolve_deal, upsert_contact


def _session(*, scalar_result=None) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=scalar_result))
    )
    session.flush = AsyncMock()
    return session


def _form(**kwargs) -> Form:
    defaults = dict(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="Test Form",
        slug="test-form",
        target_pipeline_id=uuid.uuid4(),
        entry_stage_id=uuid.uuid4(),
        matching_keys=["email"],
        source_default="form",
    )
    defaults.update(kwargs)
    return Form(**defaults)


@pytest.mark.asyncio
async def test_upsert_contact_creates_when_no_match():
    session = _session()
    form = _form()

    contact, created = await upsert_contact(
        session,
        form.workspace_id,
        {"email": "new@test.com", "name": "New"},
        ["email"],
        form,
        is_new=True,
    )

    assert created is True
    assert contact.primary_email == "new@test.com"
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_upsert_contact_reuses_existing_by_email():
    existing = Contact(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="Existing",
        primary_email="exists@test.com",
        source="form",
    )
    session = _session(scalar_result=existing)
    form = _form(workspace_id=existing.workspace_id)

    contact, created = await upsert_contact(
        session,
        existing.workspace_id,
        {"email": "exists@test.com", "name": "Updated"},
        ["email"],
        form,
        is_new=True,
    )

    assert created is False
    assert contact.id == existing.id


@pytest.mark.asyncio
async def test_resolve_deal_reuses_open_deal():
    contact = Contact(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="Ada",
        primary_email="ada@test.com",
        source="form",
    )
    pipeline = Pipeline(id=uuid.uuid4(), workspace_id=contact.workspace_id, name="IVF", slug="ivf")
    stage = Stage(id=uuid.uuid4(), pipeline_id=pipeline.id, name="New", position=0, category="open")
    existing = Deal(
        id=uuid.uuid4(),
        workspace_id=contact.workspace_id,
        contact_id=contact.id,
        pipeline_id=pipeline.id,
        stage_id=stage.id,
        status="open",
        stage_entered_at=datetime.now(UTC),
    )

    session = _session(scalar_result=existing)

    deal, action = await resolve_deal(session, contact, pipeline, stage, "reuse_open_deal")

    assert deal.id == existing.id
    assert action == "reused"
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_deal_creates_when_no_open_deal():
    contact = Contact(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        name="Ada",
        primary_email="ada@test.com",
        source="form",
    )
    pipeline = Pipeline(id=uuid.uuid4(), workspace_id=contact.workspace_id, name="IVF", slug="ivf")
    stage = Stage(id=uuid.uuid4(), pipeline_id=pipeline.id, name="New", position=0, category="open")

    session = _session()

    deal, action = await resolve_deal(session, contact, pipeline, stage, "reuse_open_deal")

    assert action == "created"
    assert deal.contact_id == contact.id
    assert deal.pipeline_id == pipeline.id
    session.add.assert_called_once()
