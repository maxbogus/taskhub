"""Export notes / ledgers as a digest for writing posts (M10).

The digest joins every note with its project and (when linked) its work item so
a writing agent can turn raw ledgers into blog/social posts without extra
queries. Markdown is human-readable; JSON is for programmatic pipelines.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Note, NoteKind, Project, WorkItem
from taskhub.repo.projects import get_project_by_slug

SUPPORTED_FORMATS = ("markdown", "json")


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid 'since' value: {value}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


async def export_notes(
    session: AsyncSession,
    *,
    project_slug: str | None = None,
    kind: str | None = None,
    ledger_type: str | None = None,
    since: str | None = None,
    output_format: str = "markdown",
    limit: int = 500,
) -> str:
    """Return a markdown/JSON digest of notes joined with projects and tasks."""

    if output_format not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported format '{output_format}', use one of {SUPPORTED_FORMATS}")

    kind_enum: NoteKind | None = None
    if kind:
        try:
            kind_enum = NoteKind(kind)
        except ValueError as exc:
            raise ValueError(f"unknown note kind: {kind}") from exc

    stmt = select(Note).order_by(Note.created_at.desc()).limit(max(1, limit))
    if project_slug:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        stmt = stmt.where(Note.project_id == project.id)
    if kind_enum is not None:
        stmt = stmt.where(Note.kind == kind_enum)
    since_dt = _parse_since(since)
    if since_dt is not None:
        stmt = stmt.where(Note.created_at >= since_dt)

    notes = list((await session.execute(stmt)).scalars().all())
    project_cache: dict[str, Project | None] = {}
    item_cache: dict[str, WorkItem | None] = {}
    rows: list[dict[str, Any]] = []
    for note in notes:
        meta = note.meta or {}
        note_type = meta.get("ledger_type")
        if ledger_type and note_type != ledger_type:
            continue
        project = project_cache.get(note.project_id)
        if note.project_id not in project_cache:
            project = await session.get(Project, note.project_id)
            project_cache[note.project_id] = project
        item: WorkItem | None = None
        if note.work_item_id:
            if note.work_item_id not in item_cache:
                item_cache[note.work_item_id] = await session.get(WorkItem, note.work_item_id)
            item = item_cache[note.work_item_id]
        rows.append(
            {
                "id": note.id,
                "kind": note.kind.value,
                "ledger_type": note_type,
                "source": meta.get("source"),
                "scope": meta.get("scope", "project"),
                "content": note.content,
                "created_at": note.created_at.isoformat(),
                "project": (
                    {"slug": project.slug, "name": project.name} if project is not None else None
                ),
                "task": (
                    {
                        "id": item.id,
                        "title": item.title,
                        "status": item.status.value,
                    }
                    if item is not None
                    else None
                ),
            }
        )

    if output_format == "json":
        return json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "count": len(rows),
                "filters": {
                    "project": project_slug,
                    "kind": kind,
                    "ledger_type": ledger_type,
                    "since": since,
                },
                "notes": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    return _render_markdown(rows, project_slug, kind, ledger_type, since)


def _render_markdown(
    rows: list[dict[str, Any]],
    project_slug: str | None,
    kind: str | None,
    ledger_type: str | None,
    since: str | None,
) -> str:
    filters = ", ".join(
        part
        for part in (
            f"project={project_slug}" if project_slug else None,
            f"kind={kind}" if kind else None,
            f"ledger_type={ledger_type}" if ledger_type else None,
            f"since={since}" if since else None,
        )
        if part
    )
    lines = [
        "# TaskHub — notes digest",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Notes: {len(rows)}",
    ]
    if filters:
        lines.append(f"Filters: {filters}")
    lines.append("")
    if not rows:
        lines.append("_No notes matched the filters._")
        return "\n".join(lines)

    current_project: str | None = None
    for row in rows:
        project = row["project"]
        slug = project["slug"] if project else "unknown"
        if slug != current_project:
            current_project = slug
            name = project["name"] if project else "Unknown project"
            lines.extend(["", f"## {name} (`{slug}`)", ""])
        heading = f"### {row['created_at'][:19]} · {row['kind']}"
        if row.get("ledger_type"):
            heading += f" · {row['ledger_type']}"
        if row.get("scope") == "global":
            heading += " · global"
        lines.append(heading)
        lines.append("")
        lines.append(row["content"][:4000])
        task = row.get("task")
        if task:
            lines.append("")
            lines.append(f"> Linked task: {task['title']} (`{task['id']}`, {task['status']})")
        if row.get("source"):
            lines.append(f"> Source: `{row['source']}`")
        lines.append("")
    return "\n".join(lines)
