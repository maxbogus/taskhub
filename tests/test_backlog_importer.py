"""Tests for the M3 backlog importer."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Dependency, WorkItem, WorkStatus
from taskhub.repo.projects import create_project
from taskhub.services.backlog_importer import import_backlog, parse_ai_task_file

AI_TASK = """# TASK-MEDIUM-002: Add Docker non-root user

**Priority:** MEDIUM
**Status:** TODO

## RICE
- Reach: 2
- Impact: 2
- Confidence: 90%
- Effort: 1 story point
- **Score: 3.6**

## Tags
docker, security

## Problem
Docker images should run as non-root.

## Acceptance Criteria
- [ ] Add non-root user
"""

DONE_TASK = """# TASK-DONE-001: Finished thing

## Problem
Already done.
"""

FRONTMATTER_TASK = """---
id: F4.33
phase: F4
title: API security regression suite
priority: P1
effort: 16
unit: hours
rice:
  R: 100
  I: 3
  C: 0.9
  score: 16.88
depends_on: [TASK-MEDIUM-002]
tags: [security, backend]
status: in-progress
---

## Acceptance Criteria
- [ ] Suite passes
"""

SHARED_INFRA = """# Backlog

### **P0: Base**

#### **SI-1: First contract**
- **Описание**: First task
- **Статус**: ✅ Завершено
- **Effort**: 2 story points
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_project(tmp_path: Path) -> None:
    _write(tmp_path / "ai" / "tasks" / "backlog" / "TASK-MEDIUM-002.md", AI_TASK)
    _write(tmp_path / "ai" / "tasks" / "done" / "TASK-DONE-001.md", DONE_TASK)
    _write(tmp_path / "docs" / "backlog" / "f4-33.md", FRONTMATTER_TASK)
    _write(tmp_path / "tasks" / "BACKLOG.md", SHARED_INFRA)


async def test_import_all_formats(session: AsyncSession, tmp_path: Path) -> None:
    _seed_project(tmp_path)
    project = await create_project(
        session, slug="demo", name="Demo", root_path=str(tmp_path)
    )
    await session.commit()

    report = await import_backlog(session, project, skip_backup=True)

    assert report.created == 4
    assert report.errors == []
    items = list(
        (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )
    by_external = {item.meta["external_id"]: item for item in items}
    assert by_external["TASK-MEDIUM-002"].priority == 2
    assert by_external["TASK-MEDIUM-002"].estimate_hours == 8.0
    assert by_external["TASK-MEDIUM-002"].meta["rice"]["C"] == 0.9
    assert by_external["TASK-MEDIUM-002"].status is WorkStatus.backlog
    assert by_external["TASK-DONE-001"].status is WorkStatus.done
    assert by_external["F4.33"].status is WorkStatus.in_progress
    assert by_external["F4.33"].priority == 1
    assert by_external["F4.33"].estimate_hours == 16.0
    assert by_external["SI-1"].status is WorkStatus.done
    assert by_external["SI-1"].priority == 0
    assert by_external["SI-1"].estimate_hours == 16.0

    dependencies = list((await session.execute(select(Dependency))).scalars().all())
    assert len(dependencies) == 1
    assert dependencies[0].from_id == by_external["TASK-MEDIUM-002"].id
    assert dependencies[0].to_id == by_external["F4.33"].id


async def test_import_is_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    _write(tmp_path / "ai" / "tasks" / "backlog" / "TASK-MEDIUM-002.md", AI_TASK)
    project = await create_project(
        session, slug="demo", name="Demo", root_path=str(tmp_path)
    )
    await session.commit()

    first = await import_backlog(session, project, skip_backup=True)
    second = await import_backlog(session, project, skip_backup=True)

    assert first.created == 1
    assert second.created == 0
    assert second.updated + second.skipped == 1
    items = list(
        (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(items) == 1


async def test_dry_run_does_not_write(session: AsyncSession, tmp_path: Path) -> None:
    _write(tmp_path / "ai" / "tasks" / "backlog" / "TASK-MEDIUM-002.md", AI_TASK)
    project = await create_project(
        session, slug="demo", name="Demo", root_path=str(tmp_path)
    )
    await session.commit()

    report = await import_backlog(
        session, project, dry_run=True, skip_backup=True
    )

    assert report.created == 1
    items = list((await session.execute(select(WorkItem))).scalars().all())
    assert items == []


EMOJI_TASK = """# 🎯 RAG-ARCH-1: Интеграция RAG

**Статус:** \u0412 процессе
**Приоритет:** Высокий (RICE 0.92)

## 📊 RICE оценка

- **Reach**: 3 (агент)
- **Impact**: 5
- **Confidence**: 0.8 (80%)
- **Effort**: 13 story points
- **RICE Score**: (3 \u00d7 5 \u00d7 0.8) / 13 = **0.92**

## Критерии завершения

- [ ] RAG работает
"""


def test_parse_ai_task_file_handles_ru_and_emoji(tmp_path: Path) -> None:
    path = tmp_path / "rag.md"
    path.write_text(EMOJI_TASK, encoding="utf-8")

    task = parse_ai_task_file(path)

    assert task is not None
    assert task.external_id == "RAG-ARCH-1"
    assert task.title == "Интеграция RAG"
    assert task.status is WorkStatus.in_progress
    assert task.priority == 1
    assert task.estimate_hours == 104.0
    assert task.rice == {"R": 3.0, "I": 5.0, "C": 0.8, "score": 0.92}
    assert task.acceptance_criteria is not None
    assert "RAG работает" in task.acceptance_criteria
