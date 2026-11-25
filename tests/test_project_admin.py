"""Tests for project administration: moves, archiving and the health audit (M16).

Policy A: the slug is immutable, ``root_path`` is mutable. Moving a project
never touches ``work_item`` rows — the items stay attached to the same
``Project.id`` — and archiving never deletes anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Note, NoteKind, Project, WorkItem
from taskhub.repo.notes import add_note
from taskhub.repo.projects import create_project, get_project_by_slug, list_projects
from taskhub.repo.work_items import create_item
from taskhub.services.projects import (
    archive_project,
    audit_projects,
    move_project,
    sync_projects,
)


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_sources(root: Path) -> None:
    _write(root / "docs" / "backlog" / "T-1.md", "# T-1\n")


async def _project(
    session: AsyncSession,
    *,
    slug: str,
    root_path: Path,
    items: int = 0,
    notes: list[str] | None = None,
) -> Project:
    project = await create_project(session, slug=slug, name=slug, root_path=str(root_path))
    for index in range(items):
        await create_item(
            session,
            project_id=project.id,
            title=f"T-{index}",
            external_ref=f"R-{index}",
        )
    for content in notes or []:
        await add_note(
            session,
            project_id=project.id,
            content=content,
            kind=NoteKind.comment,
        )
    return project


async def _counts(session: AsyncSession, project_id: str) -> tuple[int, int]:
    items = (
        await session.execute(
            select(func.count()).select_from(WorkItem).where(WorkItem.project_id == project_id)
        )
    ).scalar_one()
    notes = (
        await session.execute(
            select(func.count()).select_from(Note).where(Note.project_id == project_id)
        )
    ).scalar_one()
    return int(items), int(notes)


async def test_move_project_keeps_items_and_notes(session: AsyncSession, tmp_path: Path) -> None:
    """A move re-points the root and leaves the work attached to the project."""

    project = await _project(
        session,
        slug="legacy-app",
        root_path=tmp_path / "legacy-app",
        items=2,
        notes=["decision one"],
    )
    target = tmp_path / "career" / "legacy-app"
    target.mkdir(parents=True)

    payload = await move_project(session, "legacy-app", str(target))

    await session.refresh(project)
    assert payload["moved"] is True
    assert payload["to"] == str(target)
    assert project.root_path == str(target)
    assert await _counts(session, project.id) == (2, 1)

    again = await move_project(session, "legacy-app", str(target))
    assert again["moved"] is False


async def test_move_project_rejects_missing_target(session: AsyncSession, tmp_path: Path) -> None:
    project = await _project(session, slug="gone", root_path=tmp_path / "gone")

    with pytest.raises(ValueError, match="path not found"):
        await move_project(session, "gone", str(tmp_path / "nowhere"))

    await session.refresh(project)
    assert project.root_path == str(tmp_path / "gone")


async def test_move_project_rejects_target_of_another_project(
    session: AsyncSession, tmp_path: Path
) -> None:
    await _project(session, slug="old", root_path=tmp_path / "old")
    target = tmp_path / "shared"
    target.mkdir()
    await _project(session, slug="taken", root_path=target)

    with pytest.raises(ValueError, match="already registered"):
        await move_project(session, "old", str(target))


async def test_archive_project_refuses_work_items_without_force(
    session: AsyncSession, tmp_path: Path
) -> None:
    project = await _project(session, slug="busy", root_path=tmp_path / "busy", items=3)

    with pytest.raises(ValueError, match="work items"):
        await archive_project(session, "busy")

    await session.refresh(project)
    assert project.archived_at is None

    payload = await archive_project(session, "busy", force=True)
    await session.refresh(project)
    assert payload["archived"] is True
    assert project.archived_at is not None


async def test_archived_project_keeps_data_and_can_be_restored(
    session: AsyncSession, tmp_path: Path
) -> None:
    project = await _project(session, slug="demo", root_path=tmp_path / "demo", notes=["eval seed"])

    await archive_project(session, "demo")
    active = [item.slug for item in await list_projects(session)]
    archived = [item.slug for item in await list_projects(session, include_archived=True)]
    assert active == []
    assert archived == ["demo"]
    assert await _counts(session, project.id) == (0, 1)

    await archive_project(session, "demo", restore=True)
    await session.refresh(project)
    assert project.archived_at is None


async def test_archive_project_rejects_unknown_slug(session: AsyncSession, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        await archive_project(session, "missing")


async def test_sync_archives_duplicate_when_notes_are_copies(
    session: AsyncSession, tmp_path: Path
) -> None:
    """The keeper is the project with the data; the empty twin is archived."""

    shared = tmp_path / "data-service"
    _seed_sources(shared)
    keeper = await _project(
        session, slug="data-service", root_path=shared, items=2, notes=["same note"]
    )
    loser = await _project(session, slug="legal-data-service", root_path=shared, notes=["same note"])

    report = await sync_projects(session, tmp_path, archive_duplicates=True)

    await session.refresh(loser)
    await session.refresh(keeper)
    assert loser.archived_at is not None
    assert keeper.archived_at is None
    assert report.duplicates[0]["keeper"] == "data-service"
    assert [entry["slug"] for entry in report.archived] == ["legal-data-service"]
    assert await _counts(session, keeper.id) == (2, 1)


async def test_sync_refuses_duplicate_archive_when_notes_are_unique(
    session: AsyncSession, tmp_path: Path
) -> None:
    shared = tmp_path / "data-service"
    _seed_sources(shared)
    await _project(session, slug="data-service", root_path=shared, items=1)
    loser = await _project(session, slug="legal-data-service", root_path=shared, notes=["only here"])

    report = await sync_projects(session, tmp_path, archive_duplicates=True)

    await session.refresh(loser)
    assert loser.archived_at is None
    skipped = {entry["slug"]: entry for entry in report.skipped}
    assert skipped["legal-data-service"]["reason"] == "notes_not_covered"


async def test_sync_archives_missing_root_when_flag_is_on(
    session: AsyncSession, tmp_path: Path
) -> None:
    project = await _project(session, slug="offline-app", root_path=tmp_path / "offline-app")

    report = await sync_projects(session, tmp_path, archive_missing=True)

    await session.refresh(project)
    assert project.archived_at is not None
    assert [entry["slug"] for entry in report.archived] == ["offline-app"]


async def test_sync_refuses_archiving_missing_root_with_items(
    session: AsyncSession, tmp_path: Path
) -> None:
    project = await _project(
        session, slug="legacy-app", root_path=tmp_path / "legacy-app", items=1
    )

    report = await sync_projects(session, tmp_path, archive_missing=True)

    await session.refresh(project)
    assert project.archived_at is None
    skipped = {entry["slug"]: entry for entry in report.skipped}
    assert skipped["legacy-app"]["reason"] == "has_work_items"


async def test_sync_moves_project_root_and_keeps_data(
    session: AsyncSession, tmp_path: Path
) -> None:
    project = await _project(
        session, slug="legacy-app", root_path=tmp_path / "legacy-app", items=2
    )
    moved_to = tmp_path / "career" / "legacy-app"
    _seed_sources(moved_to)

    report = await sync_projects(
        session,
        tmp_path,
        moves={"legacy-app": str(moved_to)},
        archive_duplicates=True,
    )

    await session.refresh(project)
    assert project.root_path == str(moved_to)
    assert await _counts(session, project.id) == (2, 0)
    assert report.moved[0]["reason"] == "explicit"
    # Discovery sees the moved root and reports the path-derived slug as a
    # duplicate of the keeper instead of creating a second project.
    assert report.registered == 0
    assert await get_project_by_slug(session, "career-legacy-app") is None
    skipped = {entry["slug"]: entry for entry in report.skipped}
    assert skipped["career-legacy-app"]["reason"] == "already_registered"
    assert skipped["career-legacy-app"]["keeper"] == "legacy-app"


async def test_sync_dry_run_writes_nothing(session: AsyncSession, tmp_path: Path) -> None:
    project = await _project(session, slug="offline-app", root_path=tmp_path / "offline-app")

    report = await sync_projects(session, tmp_path, archive_missing=True, dry_run=True)

    await session.refresh(project)
    assert project.archived_at is None
    assert project.config.get("detached") is not True
    assert [entry["slug"] for entry in report.stale] == ["offline-app"]


async def test_audit_projects_reports_health(session: AsyncSession, tmp_path: Path) -> None:
    shared = tmp_path / "data-service"
    _seed_sources(shared)
    await _project(session, slug="data-service", root_path=shared, items=2)
    await _project(session, slug="legal-data-service", root_path=shared)
    await _project(
        session, slug="legacy-app", root_path=tmp_path / "legacy-app", items=3
    )
    _seed_sources(tmp_path / "fresh")

    report = await audit_projects(session, tmp_path)
    payload = report.to_dict()

    assert payload["active"] == 3
    assert payload["orphan_items"] == 3
    assert [entry["slug"] for entry in payload["stale"]] == ["legacy-app"]
    assert payload["duplicates"][0]["keeper"] == "data-service"
    assert [entry["path"] for entry in payload["uncovered"]] == [str(tmp_path / "fresh")]
    markdown = report.to_markdown()
    assert "legacy-app" in markdown
    assert "|" in markdown


async def test_sync_dry_run_previews_duplicates_created_by_a_move(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A dry run predicts the archive without touching either project."""

    moved_to = tmp_path / "legal" / "data-service"
    _seed_sources(moved_to)
    keeper = await _project(
        session, slug="data-service", root_path=tmp_path / "data-service", items=2, notes=["same"]
    )
    twin = await _project(session, slug="legal-data-service", root_path=moved_to, notes=["same"])

    report = await sync_projects(
        session,
        tmp_path,
        moves={"data-service": str(moved_to)},
        archive_duplicates=True,
        dry_run=True,
    )

    await session.refresh(keeper)
    await session.refresh(twin)
    assert keeper.root_path == str(tmp_path / "data-service")
    assert twin.archived_at is None
    assert report.duplicates[0]["keeper"] == "data-service"
    preview = {entry["slug"]: entry for entry in report.archived}
    assert preview["legal-data-service"]["reason"] == "duplicate"
    assert preview["legal-data-service"]["dry_run"] is True


async def test_sync_moves_accept_paths_relative_to_the_root(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A moves file stays machine-independent: targets resolve against --root."""

    project = await _project(session, slug="legacy-tool", root_path=tmp_path / "legacy-tool")
    target = tmp_path / "legacy" / "legacy-tool"
    target.mkdir(parents=True)

    report = await sync_projects(session, tmp_path, moves={"legacy-tool": "legacy/legacy-tool"})

    await session.refresh(project)
    assert project.root_path == str(target)
    assert report.moved[0]["to"] == str(target)


async def test_sync_skips_moves_for_archived_projects(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A re-run of a migration file must not resurrect an archived twin."""

    target = tmp_path / "legal" / "data-service"
    target.mkdir(parents=True)
    project = await _project(session, slug="legal-data-service", root_path=target)
    await archive_project(session, "legal-data-service")

    report = await sync_projects(session, tmp_path, moves={"legal-data-service": "legal/data-service"})

    await session.refresh(project)
    assert project.archived_at is not None
    skipped = {entry["slug"]: entry for entry in report.skipped}
    assert skipped["legal-data-service"]["reason"] == "archived"
