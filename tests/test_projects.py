"""Tests for project discovery and registration (collection prep)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.repo.projects import create_project, get_project_by_slug, list_projects
from taskhub.services.projects import discover_projects, register_projects, slugify


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed(root: Path) -> None:
    _write(root / "ai-gateway" / "ai" / "tasks" / "backlog" / "T-1.md", "# T-1: x\n")
    _write(root / "ai-gateway" / "pyproject.toml", "")
    _write(root / "clients" / "acme" / "tasks" / "T-2.md", "# T-2: y\n")
    _write(root / "clients" / "acme" / "package.json", "{}")
    # A nested folder with a `tasks` dir but no project markers is not a project.
    _write(root / "ai-gateway" / "src" / "tasks" / "NOPE.md", "# NOPE: z\n")


def test_slugify() -> None:
    assert slugify("clients/acme") == "clients-acme"
    assert slugify("My Project!!") == "my-project"


def test_discover_projects(tmp_path: Path) -> None:
    _seed(tmp_path)

    candidates = discover_projects(tmp_path, max_depth=2)
    slugs = [candidate.slug for candidate in candidates]

    assert "ai-gateway" in slugs
    assert "clients-acme" in slugs
    assert all("src" not in slug for slug in slugs)


async def test_register_projects_is_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    _seed(tmp_path)

    first = await register_projects(session, tmp_path, max_depth=2)
    assert first.registered == 2
    assert first.existing == 0

    second = await register_projects(session, tmp_path, max_depth=2)
    assert second.registered == 0
    assert second.existing == 2

    projects = await list_projects(session)
    assert {project.slug for project in projects} == {"ai-gateway", "clients-acme"}
    project = await get_project_by_slug(session, "ai-gateway")
    assert project is not None
    assert project.root_path.endswith("ai-gateway")


async def test_register_projects_dry_run_writes_nothing(
    session: AsyncSession, tmp_path: Path
) -> None:
    _seed(tmp_path)

    report = await register_projects(session, tmp_path, max_depth=2, dry_run=True)

    # `registered` counts what the run registers (or would register in a dry run);
    # the database itself must stay untouched.
    assert len(report.projects) == 2
    assert report.registered == 2
    assert await list_projects(session) == []


async def test_register_reuses_slug_when_root_is_already_registered(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A known path never produces a second slug (the `landing` duplicate case)."""

    landing = tmp_path / "landings" / "landing"
    _write(landing / "docs" / "backlog" / "T-1.md", "# T-1\n")
    await create_project(session, slug="landing", name="landing", root_path=str(landing))

    report = await register_projects(session, tmp_path, max_depth=2)

    slugs = [project.slug for project in await list_projects(session)]
    assert slugs == ["landing"]
    assert report.registered == 0
    assert report.existing == 1
    skipped = {entry["slug"]: entry for entry in report.skipped}
    assert skipped["landings-landing"]["reason"] == "already_registered"
    assert skipped["landings-landing"]["keeper"] == "landing"


async def test_register_follows_slug_collision_to_the_new_root(
    session: AsyncSession, tmp_path: Path
) -> None:
    """`landings/landing` and `landings-landing` share one slug — the slug wins."""

    moved_to = tmp_path / "landings" / "landing"
    _write(moved_to / "docs" / "backlog" / "T-1.md", "# T-1\n")
    project = await create_project(
        session, slug="landings-landing", name="landing", root_path=str(tmp_path / "landing")
    )

    report = await register_projects(session, tmp_path, max_depth=2)

    await session.refresh(project)
    assert project.root_path == str(moved_to)
    assert report.registered == 0
    assert report.moved[0]["slug"] == "landings-landing"
    assert report.moved[0]["reason"] == "slug"
    assert report.moved[0]["to"] == str(moved_to)


async def test_register_reports_stale_projects_and_marks_them_detached(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A missing root is reported, not silently ignored."""

    await create_project(
        session, slug="detached-app", name="detached_app", root_path=str(tmp_path / "detached_app")
    )

    report = await register_projects(session, tmp_path, max_depth=1)

    assert [entry["slug"] for entry in report.stale] == ["detached-app"]
    assert [entry["slug"] for entry in report.detached] == ["detached-app"]
    project = await get_project_by_slug(session, "detached-app")
    assert project is not None
    assert project.config.get("detached") is True


async def test_discover_projects_survives_an_unreadable_directory(
    session: AsyncSession, tmp_path: Path
) -> None:
    """One unreadable folder must not abort a sync of the whole tree."""

    import os

    _seed(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    os.chmod(blocked, 0o000)
    try:
        candidates = discover_projects(tmp_path, max_depth=2)
    finally:
        os.chmod(blocked, 0o700)

    assert {candidate.slug for candidate in candidates} == {"ai-gateway", "clients-acme"}
