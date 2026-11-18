"""Tests for the ledger/notes importer (M10)."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Note, NoteKind, Project
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item
from taskhub.services.note_importer import (
    discover_note_sources,
    import_memory,
    import_notes,
    parse_jsonl,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def _project_with_task(session: AsyncSession, tmp_path: Path):
    project = await create_project(session, slug="chem", name="Chem", root_path=str(tmp_path))
    item = await create_item(
        session,
        project_id=project.id,
        title="Fix baseline",
        meta={"external_id": "T-001"},
        external_ref="ai/tasks/active/T-001.md:T-001",
    )
    await session.commit()
    return project, item


async def test_parse_jsonl_is_schema_agnostic(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "ledger" / "decisions.jsonl"
    _write(
        path,
        "\n".join(
            [
                json.dumps({"id": "ADR-001", "title": "Pick LGBM", "decision": "yes"}),
                json.dumps({"ts": "not-a-date", "topic": "eda", "value": "x"}),
                "not json at all",
                "",
            ]
        ),
    )

    notes = parse_jsonl(path, tmp_path)

    assert len(notes) == 2
    assert notes[0].external_key == "ADR-001"
    assert notes[0].kind is NoteKind.decision
    assert "Pick LGBM" in notes[0].content
    assert notes[1].created_at is None


async def test_import_notes_links_ticket_to_task(session: AsyncSession, tmp_path: Path) -> None:
    project, item = await _project_with_task(session, tmp_path)
    _write(
        tmp_path / "docs" / "ledger" / "decisions.jsonl",
        json.dumps(
            {
                "ts": "2026-10-06T15:48:52+03:00",
                "id": "ADR-001",
                "title": "Use contract pattern",
                "tickets": ["T-001"],
            }
        )
        + "\n",
    )

    report = await import_notes(session, project, skip_backup=True)

    assert report.created == 1
    assert report.linked == 1
    note = (await session.execute(select(Note).where(Note.project_id == project.id))).scalar_one()
    assert note.work_item_id == item.id
    assert note.kind is NoteKind.decision
    assert note.external_ref == "docs/ledger/decisions.jsonl:ADR-001"


async def test_import_notes_is_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    project, _ = await _project_with_task(session, tmp_path)
    _write(
        tmp_path / "docs" / "ledger" / "findings.jsonl",
        json.dumps({"ts": "2026-09-29", "topic": "nan", "value": "15%"}) + "\n",
    )

    first = await import_notes(session, project, skip_backup=True)
    second = await import_notes(session, project, skip_backup=True)

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1
    notes = list(
        (await session.execute(select(Note).where(Note.project_id == project.id))).scalars().all()
    )
    assert len(notes) == 1


async def test_import_notes_handles_handoff_and_rules(
    session: AsyncSession, tmp_path: Path
) -> None:
    project, _ = await _project_with_task(session, tmp_path)
    _write(tmp_path / "docs" / "HANDOFF.md", "# Handoff\n\nWorked on T-001 today.")
    _write(
        tmp_path / ".clinerules" / "14-decisions-ledger.md",
        "# Decisions ledger\n\nWrite to docs/ledger/decisions.jsonl.",
    )

    report = await import_notes(session, project, skip_backup=True)

    assert report.created == 2
    notes = {
        note.meta["ledger_type"]: note
        for note in (await session.execute(select(Note).where(Note.project_id == project.id)))
        .scalars()
        .all()
    }
    assert notes["handoff"].kind is NoteKind.handoff
    assert notes["rule"].kind is NoteKind.ledger
    assert notes["handoff"].work_item_id is not None


async def test_import_memory_creates_pseudo_project(session: AsyncSession, tmp_path: Path) -> None:
    await _project_with_task(session, tmp_path)
    memory_root = tmp_path / "memory"
    _write(
        memory_root / "sessions.jsonl",
        json.dumps({"ts": "2026-10-14T02:00:54+03:00", "task": "adr", "note": "did T-001"}) + "\n",
    )

    report = await import_memory(session, memory_root=memory_root, skip_backup=True)

    assert report.created == 1
    assert report.linked == 1
    memory_project = (
        await session.execute(select(Project).where(Project.slug == "memory"))
    ).scalar_one()
    note = (
        await session.execute(select(Note).where(Note.project_id == memory_project.id))
    ).scalar_one()
    assert note.meta["scope"] == "global"
    assert note.work_item_id is not None


async def test_discover_note_sources_finds_all_kinds(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "ledger" / "decisions.jsonl", "{}\n")
    _write(tmp_path / "docs" / "notes" / "a.md", "note")
    _write(tmp_path / "docs" / "sessions" / "s1.md", "session")
    _write(tmp_path / "reports" / "sessions" / "r1.md", "report")
    _write(tmp_path / "docs" / "HANDOFF.md", "handoff")
    _write(tmp_path / ".clinerules" / "09-expds-ledger.md", "rule")

    sources = {path.name for path in discover_note_sources(tmp_path)}

    assert sources == {
        "decisions.jsonl",
        "a.md",
        "s1.md",
        "r1.md",
        "HANDOFF.md",
        "09-expds-ledger.md",
    }


async def test_parse_jsonl_tolerates_dict_fields(tmp_path: Path) -> None:
    path = tmp_path / "docs" / "ledger" / "findings.jsonl"
    _write(
        path,
        json.dumps({"topic": "x", "context": {"nested": 1}, "tickets": ["T-9"]}) + "\n",
    )

    notes = parse_jsonl(path, tmp_path)

    assert len(notes) == 1
    assert "T-9" in notes[0].task_ids

