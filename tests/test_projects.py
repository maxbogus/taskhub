"""Tests for project discovery and registration (collection prep)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.repo.projects import get_project_by_slug, list_projects
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


async def test_register_projects_is_idempotent(
    session: AsyncSession, tmp_path: Path
) -> None:
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

    assert len(report.projects) == 2
    assert report.registered == 0
    assert await list_projects(session) == []
