"""Tests for the notes export digest (M10)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import NoteKind
from taskhub.repo.notes import add_note
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.export import export_notes


async def _seed(session: AsyncSession):
    project = await create_project(session, slug="chem", name="Chemistry", root_path="/tmp/chem")
    item = await create_item(session, project_id=project.id, title="Fix baseline")
    await add_note(
        session,
        project_id=project.id,
        content="Picked LGBM over CatBoost",
        kind=NoteKind.decision,
        work_item_id=item.id,
        meta={"ledger_type": "decision", "source": "docs/ledger/decisions.jsonl"},
    )
    await add_note(
        session,
        project_id=project.id,
        content="Median slices per series = 30",
        kind=NoteKind.insight,
        meta={"ledger_type": "finding"},
    )
    await session.commit()
    return project, item


async def test_export_markdown_contains_notes_and_linked_task(
    session: AsyncSession,
) -> None:
    await _seed(session)

    text = await export_notes(session, project_slug="chem")

    assert "# TaskHub — notes digest" in text
    assert "Picked LGBM over CatBoost" in text
    assert "Median slices per series = 30" in text
    assert "> Linked task: Fix baseline" in text


async def test_export_json_respects_kind_filter(session: AsyncSession) -> None:
    await _seed(session)

    payload = json.loads(await export_notes(session, kind="insight", output_format="json"))

    assert payload["count"] == 1
    assert payload["notes"][0]["kind"] == "insight"
    assert payload["notes"][0]["task"] is None


async def test_export_rejects_unknown_project(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not found"):
        await export_notes(session, project_slug="missing")


async def test_export_rejects_bad_format_and_since(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="unsupported format"):
        await export_notes(session, output_format="xml")
    with pytest.raises(ValueError, match="invalid 'since'"):
        await export_notes(session, since="yesterday")
