"""Tests for template import and application (M6)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import WorkStatus
from taskhub.repo.projects import create_project
from taskhub.repo.templates import get_template, list_templates, upsert_template
from taskhub.services.templates import (
    apply_template,
    import_templates,
    split_frontmatter,
)

TEMPLATE_WITH_FRONTMATTER = """---
id: AI-777
type: task
title: Decompose architecture
rice:
  reach: 10
  impact: 2
  confidence: 80
  effort: 4
priority: HIGH
verification:
  - First criterion
  - Second criterion
---

## Description

Do the work in {MODULE}.
"""


def _template_root(tmp_path: Path) -> Path:
    root = tmp_path / "templates"
    root.mkdir()
    (root / "ticket.md").write_text(TEMPLATE_WITH_FRONTMATTER, encoding="utf-8")
    (root / "plain.md").write_text("# Plain\n\nNo frontmatter here.\n", encoding="utf-8")
    return root


async def _project(session: AsyncSession):
    project = await create_project(session, slug="p", name="P", root_path="/tmp/p")
    await session.commit()
    return project


def test_split_frontmatter_roundtrip() -> None:
    front, body = split_frontmatter(TEMPLATE_WITH_FRONTMATTER)

    assert front["id"] == "AI-777"
    assert body.startswith("## Description")


def test_split_frontmatter_without_block() -> None:
    front, body = split_frontmatter("# Heading\n\nBody")

    assert front == {}
    assert body == "# Heading\n\nBody"


async def test_import_is_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    root = _template_root(tmp_path)

    first = await import_templates(session, source_roots=[root])
    await session.commit()
    assert first.imported == 2
    assert first.updated == 0

    second = await import_templates(session, source_roots=[root])
    await session.commit()
    assert second.imported == 0
    assert second.updated == 2

    templates = await list_templates(session)
    assert {template.name for template in templates} == {"AI-777", "plain"}


async def test_import_reports_missing_sources(
    session: AsyncSession, tmp_path: Path
) -> None:
    report = await import_templates(
        session, source_roots=[tmp_path / "does-not-exist"]
    )

    assert report.imported == 0
    assert report.missing_sources


async def test_apply_template_maps_frontmatter(
    session: AsyncSession, tmp_path: Path
) -> None:
    root = _template_root(tmp_path)
    project = await _project(session)
    await import_templates(session, source_roots=[root])
    await session.commit()

    template = await get_template(session, "AI-777", project_id=project.id)
    assert template is not None

    item = await apply_template(
        session,
        project=project,
        template=template,
        variables={"MODULE": "taskhub"},
    )
    await session.commit()

    assert item.status is WorkStatus.backlog
    assert item.priority == 2
    assert item.estimate_hours == 4.0
    assert item.meta["rice"] == {"R": 10, "I": 2, "C": 0.8, "E": 4}
    assert item.acceptance_criteria == "- [ ] First criterion\n- [ ] Second criterion"
    assert "taskhub" in (item.description or "")
    assert "{MODULE}" not in (item.description or "")


async def test_get_template_prefers_project_scope(session: AsyncSession) -> None:
    project = await _project(session)
    await upsert_template(session, name="shared", body="global", type="task")
    await upsert_template(
        session, name="shared", body="project", type="task", project_id=project.id
    )
    await session.commit()

    template = await get_template(session, "shared", project_id=project.id)

    assert template is not None
    assert template.body == "project"
