"""MCP tools for TaskHub (M1 subset)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from taskhub.db import session_scope
from taskhub.models import Note, Project, WorkItem, WorkItemType
from taskhub.repo.projects import get_project_by_slug
from taskhub.repo.projects import list_projects as repo_list_projects
from taskhub.repo.work_items import create_item, get_item, next_item
from taskhub.services.backlog_importer import import_backlog as import_backlog_service
from taskhub.services.backup import create_backup
from taskhub.services.evaluation import evaluate_project as evaluate_project_service
from taskhub.services.gates import blocking_items
from taskhub.services.scope import resolve_scope


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _work_item_type(value: str) -> WorkItemType:
    try:
        return WorkItemType(value)
    except ValueError as exc:
        raise ValueError(f"unknown work item type: {value}") from exc


def _ref(item: WorkItem | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {"id": item.id, "title": item.title, "status": item.status.value}


def serialize_item(item: WorkItem, *, compact: bool = True) -> dict[str, Any]:
    """Serialise an item using a compact shape by default."""

    data: dict[str, Any] = {
        "id": item.id,
        "project_id": item.project_id,
        "type": item.type.value,
        "title": item.title,
        "status": item.status.value,
        "priority": item.priority,
        "order_index": item.order_index,
        "estimate_hours": item.estimate_hours,
        "due_at": item.due_at.isoformat() if item.due_at else None,
        "required_skill": item.required_skill,
        "external_ref": item.external_ref,
        "parent_id": item.parent_id,
        "meta": item.meta or {},
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "done_at": item.done_at.isoformat() if item.done_at else None,
    }
    if not compact:
        data["description"] = item.description
        data["acceptance_criteria"] = item.acceptance_criteria
    return data


async def create_task(
    project_slug: str,
    title: str,
    type: str = "task",
    description: str | None = None,
    parent_id: str | None = None,
    priority: int = 3,
    estimate_hours: float | None = None,
    required_skill: str | None = None,
    due_at: str | None = None,
    external_ref: str | None = None,
) -> dict[str, Any]:
    """Create a work item in a project."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        item = await create_item(
            session,
            project_id=project.id,
            title=title,
            type=_work_item_type(type),
            description=description,
            parent_id=parent_id,
            priority=priority,
            estimate_hours=estimate_hours,
            due_at=_parse_dt(due_at),
            required_skill=required_skill,
            external_ref=external_ref,
        )
        await session.commit()
        return serialize_item(item, compact=False)


async def get_context(item_id: str, note_limit: int = 5) -> dict[str, Any]:
    """Return compact task context: parent, children, blockers, recent notes."""

    async with session_scope() as session:
        item = await get_item(session, item_id)
        if item is None:
            raise ValueError(f"work item '{item_id}' not found")
        project = await session.get(Project, item.project_id)
        parent = await get_item(session, item.parent_id) if item.parent_id else None
        children = list(
            (
                await session.execute(
                    select(WorkItem)
                    .where(WorkItem.parent_id == item.id)
                    .order_by(WorkItem.priority.asc())
                )
            )
            .scalars()
            .all()
        )
        blockers = await blocking_items(session, item.id)
        notes = list(
            (
                await session.execute(
                    select(Note)
                    .where(Note.work_item_id == item.id)
                    .order_by(Note.created_at.desc())
                    .limit(note_limit)
                )
            )
            .scalars()
            .all()
        )
        return {
            "item": serialize_item(item),
            "parent": _ref(parent),
            "children": [_ref(child) for child in children],
            "blocking": blockers,
            "last_notes": [
                {
                    "kind": note.kind.value,
                    "content": note.content[:500],
                    "author": note.author,
                    "created_at": note.created_at.isoformat(),
                }
                for note in notes
            ],
            "links": {
                "task": f"task://{item.id}",
                "context": f"task://{item.id}/context",
                "backlog": f"project://{project.slug}/backlog" if project else None,
            },
        }


async def get_next_item(
    project_slug: str | None = None,
    cwd: str | None = None,
    required_skill: str | None = None,
) -> dict[str, Any]:
    """Return the highest-priority available task for the resolved scope."""

    async with session_scope() as session:
        scope = await resolve_scope(session, cwd=cwd, project_slug=project_slug)
        if scope.project is None:
            return {"item": None, "reason": "global scope: pass project_slug or cwd"}
        item = await next_item(
            session, scope.project.id, required_skill=required_skill
        )
        if item is None:
            return {
                "item": None,
                "reason": "no available items",
                "project": scope.project.slug,
            }
        return {"item": serialize_item(item), "project": scope.project.slug}


async def list_projects() -> list[dict[str, Any]]:
    """Return the global project map with per-status counters."""

    async with session_scope() as session:
        projects = await repo_list_projects(session)
        result: list[dict[str, Any]] = []
        for project in projects:
            rows = (
                await session.execute(
                    select(WorkItem.status, func.count())
                    .where(WorkItem.project_id == project.id)
                    .group_by(WorkItem.status)
                )
            ).all()
            result.append(
                {
                    "slug": project.slug,
                    "name": project.name,
                    "root_path": project.root_path,
                    "by_status": {status.value: count for status, count in rows},
                }
            )
        return result


async def project_status(project_slug: str) -> dict[str, Any]:
    """Compact project snapshot: counters, EVM and a short forecast."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        evaluation = await evaluate_project_service(
            session, project, n_iterations=1000, random_seed=42
        )
        forecast = evaluation["forecast"]
        return {
            "project": project.slug,
            "generated_at": evaluation["generated_at"],
            "total_items": evaluation["items"]["total"],
            "by_status": evaluation["items"]["by_status"],
            "progress": evaluation["progress"],
            "forecast": {
                "status": forecast.get("status"),
                "p50": forecast.get("p50"),
                "p90": forecast.get("p90"),
                "pct_completed": forecast.get("pct_completed"),
            },
            "health": evaluation["health"],
        }


async def evaluate_project(
    project_slug: str, n_iterations: int = 2000
) -> dict[str, Any]:
    """Full project evaluation: RICE, EVM, Monte Carlo and health."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        return await evaluate_project_service(
            session, project, n_iterations=n_iterations
        )


async def forecast_project(project_slug: str) -> dict[str, Any]:
    """Forecast-only view for agents that do not need the full evaluation."""

    evaluation = await evaluate_project(project_slug, n_iterations=2000)
    return {
        "project": evaluation["project"],
        "forecast": evaluation["forecast"],
        "progress": evaluation["progress"],
    }


async def import_backlog(
    project_slug: str,
    path: str | None = None,
    dry_run: bool = False,
    skip_backup: bool = False,
) -> dict[str, Any]:
    """Import existing file backlogs into a project (backup first)."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        source = Path(path).expanduser() if path else None
        report = await import_backlog_service(
            session,
            project,
            source_path=source,
            dry_run=dry_run,
            skip_backup=skip_backup,
        )
        payload = report.to_dict()
        payload["project"] = project.slug
        payload["dry_run"] = dry_run
        return payload


async def backup_db(backup_dir: str | None = None) -> dict[str, Any]:
    """Create an online SQLite backup and return its path."""

    target = await create_backup(
        backup_dir=Path(backup_dir).expanduser() if backup_dir else None
    )
    return {"status": "ok", "path": str(target)}
