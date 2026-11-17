"""Import existing file backlogs into TaskHub (M3).

Supported sources:

* ``<project>/ai/tasks/{active,backlog,done,archive}/*.md`` — markdown with
  bold fields (``**Priority:**``, ``**Status:**``) and RICE bullets.
* ``<project>/{docs/backlog,backlog,tasks}/*.md`` — YAML frontmatter with
  ``id``, ``title``, ``priority``, ``effort``, ``rice``, ``depends_on``,
  ``tags``, ``status``, ``phase``.
* ``<project>/tasks/BACKLOG.md`` — heading-based shared-infrastructure format.

Import is idempotent: items are upserted by ``(project_id, external_ref)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import (
    Dependency,
    DependencyType,
    Project,
    WorkItem,
    WorkItemType,
    WorkStatus,
)
from taskhub.repo.work_items import create_item
from taskhub.services.backup import create_backup

STATUS_BY_DIR = {
    "active": WorkStatus.in_progress,
    "in_progress": WorkStatus.in_progress,
    "backlog": WorkStatus.backlog,
    "ready": WorkStatus.ready,
    "review": WorkStatus.review,
    "blocked": WorkStatus.blocked,
    "done": WorkStatus.done,
    "archive": WorkStatus.archived,
    "archived": WorkStatus.archived,
}

PRIORITY_BY_LABEL = {
    "P0": 0,
    "CRITICAL": 0,
    "CRIT": 0,
    "P1": 1,
    "HIGH": 1,
    "P2": 2,
    "MEDIUM": 2,
    "MED": 2,
    "P3": 3,
    "LOW": 3,
    "P4": 4,
}


@dataclass
class ParsedTask:
    """Normalised task extracted from a source file."""

    external_id: str
    title: str
    source_path: Path
    status: WorkStatus = WorkStatus.backlog
    priority: int = 3
    description: str | None = None
    estimate_hours: float | None = None
    rice: dict[str, float] | None = None
    depends_on: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    acceptance_criteria: str | None = None
    type: WorkItemType = WorkItemType.task
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "errors": self.errors,
            "items": self.items,
        }


def parse_frontmatter_blocks(text: str) -> list[dict[str, Any]]:
    """Parse every YAML frontmatter block in a markdown file."""

    pattern = r"^---\s*\n(.*?)\n---"
    results: list[dict[str, Any]] = []
    for match in re.finditer(pattern, text, re.DOTALL | re.MULTILINE):
        try:
            data = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            continue
        if isinstance(data, dict):
            results.append(data)
    return results


def _priority_from(value: str | int | None, default: int = 3) -> int:
    if value is None:
        return default
    text = str(value).strip().upper()
    if text in PRIORITY_BY_LABEL:
        return PRIORITY_BY_LABEL[text]
    if text.isdigit():
        return max(0, min(4, int(text)))
    match = re.search(r"P([0-4])", text)
    return int(match.group(1)) if match else default


def _status_from(
    value: str | None, default: WorkStatus = WorkStatus.backlog
) -> WorkStatus:
    text = (value or "").strip().lower()
    if not text:
        return default
    if "progress" in text or "🔄" in text or text in (
        "active",
        "in-progress",
        "in_progress",
    ):
        return WorkStatus.in_progress
    if "done" in text or "заверш" in text or "✅" in text:
        return WorkStatus.done
    if "archive" in text or "архив" in text:
        return WorkStatus.archived
    if "block" in text:
        return WorkStatus.blocked
    if "review" in text or "ревью" in text:
        return WorkStatus.review
    if "ready" in text:
        return WorkStatus.ready
    return WorkStatus.backlog


def _normalize_hours(value: Any, unit: str | None = None) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    unit_text = (unit or "").lower()
    if "story" in unit_text or unit_text.strip() in ("sp", "points"):
        return round(numeric * 8.0, 4)
    return round(numeric, 4)


def _section(text: str, heading: str) -> str | None:
    pattern = rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def _bold_field(text: str, name: str) -> str | None:
    match = re.search(rf"\*\*{re.escape(name)}:\*\*\s*(.+)", text)
    return match.group(1).strip() if match else None


def _rice_from_mapping(data: dict[str, Any]) -> dict[str, float] | None:
    rice = data.get("rice")
    if not isinstance(rice, dict):
        return None
    result: dict[str, float] = {}
    for key in ("R", "I", "C", "score"):
        value = rice.get(key, rice.get(key.lower()))
        if value is not None:
            result[key] = float(value)
    return result or None


def _rice_from_bullets(section: str | None) -> dict[str, float] | None:
    if not section:
        return None
    result: dict[str, float] = {}
    patterns = (
        ("R", "Reach"),
        ("I", "Impact"),
        ("C", "Confidence"),
        ("score", "Score"),
    )
    for key, label in patterns:
        match = re.search(
            rf"{label}\s*[:=]\s*\**\s*([0-9]+(?:\.[0-9]+)?%?)",
            section,
            re.IGNORECASE,
        )
        if not match:
            continue
        raw = match.group(1)
        value = float(raw.replace("%", ""))
        if key == "C" and raw.endswith("%"):
            value /= 100
        result[key] = value
    return result or None


def _effort_from_section(section: str | None) -> float | None:
    if not section:
        return None
    match = re.search(
        r"Effort\s*[:=]\s*\**\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z ]*)",
        section,
        re.IGNORECASE,
    )
    return _normalize_hours(match.group(1), match.group(2)) if match else None


def _parse_tags(section: str | None) -> list[str]:
    if not section:
        return []
    line = section.strip().splitlines()[0]
    return [tag.strip() for tag in line.split(",") if tag.strip()]


def _split_depends(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[,;]", value) if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _status_from_path(path: Path) -> WorkStatus:
    for part in reversed(path.parts):
        status = STATUS_BY_DIR.get(part.lower())
        if status is not None:
            return status
    return WorkStatus.backlog


SKIP_FILENAMES = {
    "INDEX.MD",
    "CURRENT.MD",
    "README.MD",
    "IMPLEMENTATION-PLAN.MD",
    "TASK-TEMPLATE.MD",
    "LEAN-TEMPLATE.MD",
    "DEPLOYMENT-CHECKLIST.MD",
}


def _field_value(block: str, name: str) -> str | None:
    match = re.search(rf"-\s*\*\*{re.escape(name)}\*\*:\s*(.+)", block)
    return match.group(1).strip() if match else None


def parse_ai_task_file(path: Path) -> ParsedTask | None:
    """Parse a markdown task with bold fields and RICE bullets."""

    text = path.read_text(encoding="utf-8", errors="replace")
    heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if heading is None:
        return None
    title_line = heading.group(1).strip()
    external_id = path.stem
    title = title_line
    match = re.match(r"([A-Z][A-Z0-9_.-]*?):\s*(.+)", title_line)
    if match:
        external_id = match.group(1)
        title = match.group(2).strip()

    rice_section = _section(text, "RICE")
    return ParsedTask(
        external_id=external_id,
        title=title,
        source_path=path,
        status=_status_from(_bold_field(text, "Status"), _status_from_path(path)),
        priority=_priority_from(_bold_field(text, "Priority")),
        description=_section(text, "Problem") or text.strip()[:20000],
        estimate_hours=_effort_from_section(rice_section),
        rice=_rice_from_bullets(rice_section),
        depends_on=_split_depends(_bold_field(text, "Depends on")),
        tags=_parse_tags(_section(text, "Tags")),
        acceptance_criteria=(
            _section(text, "Acceptance Criteria")
            or _section(text, "Критерии завершения")
        ),
        meta={"format": "ai_tasks"},
    )


def parse_frontmatter_file(path: Path) -> list[ParsedTask]:
    """Parse frontmatter-based backlog files (possibly multi-entry)."""

    text = path.read_text(encoding="utf-8", errors="replace")
    tasks: list[ParsedTask] = []
    for block in parse_frontmatter_blocks(text):
        if "id" not in block:
            continue
        external_id = str(block["id"])
        tasks.append(
            ParsedTask(
                external_id=external_id,
                title=str(block.get("title") or external_id),
                source_path=path,
                status=_status_from(block.get("status"), _status_from_path(path)),
                priority=_priority_from(block.get("priority")),
                description=text.strip()[:20000],
                estimate_hours=_normalize_hours(
                    block.get("effort"), block.get("unit")
                ),
                rice=_rice_from_mapping(block),
                depends_on=_split_depends(block.get("depends_on")),
                tags=[str(tag) for tag in (block.get("tags") or [])],
                acceptance_criteria=_section(text, "Acceptance Criteria"),
                meta={"format": "frontmatter", "phase": block.get("phase")},
            )
        )
    return tasks


def parse_shared_infra_backlog(path: Path) -> list[ParsedTask]:
    """Parse the heading-based ``tasks/BACKLOG.md`` format."""

    text = path.read_text(encoding="utf-8", errors="replace")
    tasks: list[ParsedTask] = []
    sections = re.split(r"^###\s+\*\*P(\d):[^\n]*\*\*\s*$", text, flags=re.MULTILINE)
    task_pattern = re.compile(
        r"^####\s+\*\*([A-Z]+-\d+):\s*(.+?)\*\*\s*$(.*?)(?=^####\s+|^###\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    for index in range(1, len(sections), 2):
        priority = int(sections[index])
        for match in task_pattern.finditer(sections[index + 1]):
            external_id = match.group(1)
            title = match.group(2).strip()
            block = match.group(3)
            effort_match = re.search(
                r"\*\*Effort\*\*:\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z ]*)",
                block,
                re.IGNORECASE,
            )
            tasks.append(
                ParsedTask(
                    external_id=external_id,
                    title=title,
                    source_path=path,
                    status=_status_from(
                        _field_value(block, "Статус"), WorkStatus.backlog
                    ),
                    priority=priority,
                    description=_field_value(block, "Описание"),
                    estimate_hours=(
                        _normalize_hours(
                            effort_match.group(1), effort_match.group(2)
                        )
                        if effort_match
                        else None
                    ),
                    depends_on=_split_depends(_field_value(block, "Зависит от")),
                    acceptance_criteria=_field_value(block, "Критерии завершения"),
                    meta={"format": "shared_infra"},
                )
            )
    return tasks


def parse_path(path: Path) -> list[ParsedTask]:
    """Parse a file or directory into normalised tasks."""

    if path.is_dir():
        results: list[ParsedTask] = []
        for child in sorted(path.glob("*.md")):
            results.extend(parse_path(child))
        return results
    if path.name.upper() in SKIP_FILENAMES:
        return []
    if path.name == "BACKLOG.md" and path.parent.name == "tasks":
        return parse_shared_infra_backlog(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if any("id" in block for block in parse_frontmatter_blocks(text)):
        return parse_frontmatter_file(path)
    task = parse_ai_task_file(path)
    return [task] if task is not None else []


def discover_sources(root: Path) -> list[Path]:
    """Discover backlog files under a project root."""

    seen: set[Path] = set()
    paths: list[Path] = []

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            paths.append(path)

    tasks_dir = root / "ai" / "tasks"
    if tasks_dir.is_dir():
        for folder in STATUS_BY_DIR:
            base = tasks_dir / folder
            if base.is_dir():
                for path in sorted(base.glob("*.md")):
                    add(path)
        for path in sorted(tasks_dir.glob("*.md")):
            add(path)

    for backlog_dir in (root / "docs" / "backlog", root / "backlog", root / "tasks"):
        if backlog_dir.is_dir():
            for path in sorted(backlog_dir.glob("*.md")):
                add(path)
            archive = backlog_dir / "archive"
            if archive.is_dir():
                for path in sorted(archive.glob("*.md")):
                    add(path)

    return paths


def _relative_ref(path: Path, root: Path, external_id: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.as_posix()
    return f"{relative}:{external_id}"


def _apply_fields(
    item: WorkItem, task: ParsedTask, root: Path, now: datetime
) -> bool:
    """Update an existing item; return True when anything changed."""

    changed = False
    values: dict[str, Any] = {
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "estimate_hours": task.estimate_hours,
        "acceptance_criteria": task.acceptance_criteria,
        "type": task.type,
    }
    for field_name, value in values.items():
        if getattr(item, field_name) != value:
            setattr(item, field_name, value)
            changed = True

    meta = dict(item.meta or {})
    meta.update(
        {
            "external_id": task.external_id,
            "source": _relative_ref(task.source_path, root, task.external_id).split(":")[0],
            "tags": task.tags,
            "rice": task.rice,
        }
    )
    meta.setdefault("imported_at", now.isoformat())
    meta.update(task.meta)
    if item.meta != meta:
        item.meta = meta
        changed = True
    return changed


async def import_backlog(
    session: AsyncSession,
    project: Project,
    *,
    source_path: Path | None = None,
    dry_run: bool = False,
    skip_backup: bool = False,
    backup_dir: Path | None = None,
) -> ImportReport:
    """Import all discovered backlog files into a project.

    A backup is created before any write unless ``dry_run`` or
    ``skip_backup`` is set. The upsert key is ``(project_id, external_ref)``,
    so running the importer twice reports updates/skips, not duplicates.
    """

    report = ImportReport()
    root = Path(project.root_path).expanduser()
    sources = [source_path] if source_path else discover_sources(root)

    parsed: list[ParsedTask] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        try:
            for task in parse_path(Path(source)):
                key = (task.source_path.as_posix(), task.external_id)
                if key in seen:
                    continue
                seen.add(key)
                parsed.append(task)
        except Exception as exc:
            report.errors.append(f"{source}: {exc}")

    if not dry_run and not skip_backup:
        await create_backup(backup_dir=backup_dir)

    existing = list(
        (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == project.id)
            )
        )
        .scalars()
        .all()
    )
    by_ref = {item.external_ref: item for item in existing if item.external_ref}
    now = datetime.now(UTC).replace(tzinfo=None)
    item_by_external_id: dict[str, WorkItem] = {}

    for task in parsed:
        if not task.title:
            report.skipped += 1
            continue
        ref = _relative_ref(task.source_path, root, task.external_id)
        item = by_ref.get(ref)
        if item is None:
            if dry_run:
                report.created += 1
                report.items.append(
                    {
                        "external_ref": ref,
                        "external_id": task.external_id,
                        "title": task.title,
                        "status": task.status.value,
                        "action": "created",
                    }
                )
                continue
            item = await create_item(
                session,
                project_id=project.id,
                title=task.title,
                type=task.type,
                description=task.description,
                priority=task.priority,
                estimate_hours=task.estimate_hours,
                acceptance_criteria=task.acceptance_criteria,
                external_ref=ref,
            )
            _apply_fields(item, task, root, now)
            by_ref[ref] = item
            report.created += 1
            action = "created"
        else:
            if _apply_fields(item, task, root, now):
                report.updated += 1
                action = "updated"
            else:
                report.skipped += 1
                action = "skipped"
        item_by_external_id[task.external_id] = item
        report.items.append(
            {
                "external_ref": ref,
                "external_id": task.external_id,
                "title": task.title,
                "status": task.status.value,
                "action": action,
            }
        )

    if not dry_run:
        await session.flush()
        for task in parsed:
            item = item_by_external_id.get(task.external_id)
            if item is None:
                continue
            for dependency_id in task.depends_on:
                blocker = item_by_external_id.get(dependency_id)
                if blocker is None or blocker.id == item.id:
                    continue
                existing_dependency = (
                    await session.execute(
                        select(Dependency).where(
                            Dependency.from_id == blocker.id,
                            Dependency.to_id == item.id,
                            Dependency.type == DependencyType.blocked_by,
                        )
                    )
                ).scalar_one_or_none()
                if existing_dependency is None:
                    session.add(
                        Dependency(
                            from_id=blocker.id,
                            to_id=item.id,
                            type=DependencyType.blocked_by,
                        )
                    )
        await session.commit()

    return report



