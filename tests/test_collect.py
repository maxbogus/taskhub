"""Tests for bulk backlog collection across projects."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import WorkItem
from taskhub.repo.projects import create_project, list_projects
from taskhub.services.collect import import_all
from taskhub.services.projects import register_projects

AI_TASK = """# T-1: First task

**Priority:** HIGH
**Status:** backlog

## RICE
- Reach: 10
- Impact: 2
- Confidence: 0.8
- Effort: 4 hours
"""

FRONTMATTER_TASK = """---
id: F-1
title: Beta task
priority: P2
effort: 6
unit: hours
status: backlog
---

# body
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed(root: Path) -> None:
    _write(root / "alpha" / "ai" / "tasks" / "backlog" / "T-1.md", AI_TASK)
    _write(root / "beta" / "docs" / "backlog" / "f1.md", FRONTMATTER_TASK)


async def test_import_all_dry_run_then_real(session: AsyncSession, tmp_path: Path) -> None:
    _seed(tmp_path)
    await register_projects(session, tmp_path, max_depth=1)
    projects = await list_projects(session)
    assert len(projects) == 2

    dry = await import_all(session, projects, dry_run=True, skip_backup=True)
    assert dry.totals["created"] == 2
    assert list((await session.execute(select(WorkItem))).scalars().all()) == []

    real = await import_all(session, projects, skip_backup=True)
    assert real.totals["created"] == 2
    items = list((await session.execute(select(WorkItem))).scalars().all())
    assert len(items) == 2

    again = await import_all(session, projects, skip_backup=True)
    assert again.totals["created"] == 0
    assert len(list((await session.execute(select(WorkItem))).scalars().all())) == 2


async def test_import_all_reports_detached_projects_instead_of_silence(
    session: AsyncSession, tmp_path: Path
) -> None:
    """A project whose root is gone is reported, not skipped without a trace."""

    gone = await create_project(
        session, slug="detached-app", name="detached_app", root_path=str(tmp_path / "detached_app")
    )

    report = await import_all(session, [gone], skip_backup=True, dry_run=True)

    assert report.totals["detached"] == 1
    assert report.totals["errors"] == 0
    assert report.projects[0]["slug"] == "detached-app"
    assert report.projects[0]["detached"] is True
