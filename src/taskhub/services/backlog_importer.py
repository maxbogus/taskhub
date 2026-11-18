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
from typing import Any, cast

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub import sources
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
    "КРИТИЧЕСКИЙ": 0,
    "КРИТ": 0,
    "P1": 1,
    "HIGH": 1,
    "ВЫСОКИЙ": 1,
    "ВЫСШИЙ": 1,
    "P2": 2,
    "MEDIUM": 2,
    "MED": 2,
    "СРЕДНИЙ": 2,
    "P3": 3,
    "LOW": 3,
    "НИЗКИЙ": 3,
    "P4": 4,
    "📊": 4,
    "🥇": 1,
    "🥈": 2,
    "🥉": 3,
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
    match = re.search(r"P([0-4])", text, re.IGNORECASE)
    if match and match.group(1).isdigit():
        return int(match.group(1))
    for label, score in PRIORITY_BY_LABEL.items():
        if label in text:
            return score
    return default


def _status_from(value: str | None, default: WorkStatus = WorkStatus.backlog) -> WorkStatus:
    text = (value or "").strip().lower()
    if not text:
        return default
    if (
        "progress" in text
        or "🔄" in text
        or "в процессе" in text
        or "в работе" in text
        or text in ("active", "in-progress", "in_progress")
    ):
        return WorkStatus.in_progress
    if "done" in text or "заверш" in text or "выполн" in text or "готово" in text or "✅" in text:
        return WorkStatus.done
    if "archive" in text or "архив" in text:
        return WorkStatus.archived
    if "block" in text or "блок" in text:
        return WorkStatus.blocked
    if "review" in text or "ревью" in text:
        return WorkStatus.review
    if "ready" in text or "готов" in text:
        return WorkStatus.ready
    if "cancel" in text or "отмен" in text:
        return WorkStatus.cancelled
    return WorkStatus.backlog


def _normalize_hours(value: Any, unit: str | None = None) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    unit_text = (unit or "").lower()
    if any(token in unit_text for token in ("story", "points", "стори")) or unit_text.strip() in (
        "sp",
        "сп",
    ):
        return round(numeric * 8.0, 4)
    if any(token in unit_text for token in ("day", "дн", "день")):
        return round(numeric * 8.0, 4)
    return round(numeric, 4)


def _section(text: str, heading: str) -> str | None:
    """Return the longest section whose heading contains ``heading``."""

    pattern = (
        rf"^#{{2,4}}\s+[^\n]*{re.escape(heading)}[^\n]*\n(.*?)"
        rf"(?=^#{{2,4}}\s+|\Z)"
    )
    best: str | None = None
    for match in re.finditer(pattern, text, re.MULTILINE | re.DOTALL | re.IGNORECASE):
        content = match.group(1).strip()
        if content and (best is None or len(content) > len(best)):
            best = content
    return best


FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "status": ("Status", "Статус"),
    "priority": ("Priority", "Приоритет"),
    "depends on": ("Depends on", "Depends", "Зависит от", "Зависимости"),
}


def _bold_field(text: str, name: str) -> str | None:
    """Read a bold markdown field, tolerating RU labels and colon placement."""

    aliases = FIELD_ALIASES.get(name.lower(), (name,))
    for alias in aliases:
        for pattern in (
            rf"\*\*{re.escape(alias)}\s*[:\uff1a]\s*\*\*\s*(.+)",
            rf"\*\*{re.escape(alias)}\*\*\s*[:\uff1a]\s*(.+)",
        ):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return None


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
    )
    for key, label in patterns:
        match = re.search(
            rf"{label}\s*\**\s*[:=]\s*\**\s*([0-9]+(?:\.[0-9]+)?\s*%?)",
            section,
            re.IGNORECASE,
        )
        if not match:
            continue
        raw = match.group(1).replace(" ", "")
        value = float(raw.replace("%", ""))
        if key == "C" and raw.endswith("%"):
            value /= 100
        result[key] = value
    for score_pattern in (
        r"Score\s*\**\s*[:=][^\n]*?=\s*\**\s*([0-9]+(?:\.[0-9]+)?)",
        r"Score\s*\**\s*[:=][^\n]*?\*\*\s*([0-9]+(?:\.[0-9]+)?)\s*\*\*",
        r"Score\s*\**\s*[:=]\s*\**\s*([0-9]+(?:\.[0-9]+)?)",
    ):
        match = re.search(score_pattern, section, re.IGNORECASE)
        if match:
            result["score"] = float(match.group(1))
            break
    return result or None


def _effort_from_section(section: str | None) -> float | None:
    if not section:
        return None
    match = re.search(
        r"Effort\s*\**\s*[:=]\s*\**\s*([0-9]+(?:\.[0-9]+)?)\s*"
        r"([A-Za-z\u0410-\u044f ]*)",
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
    match = re.search(
        r"(?<![A-Za-z0-9])([A-Z][A-Z0-9_.-]*?)\s*[:\uff1a]\s*(.+)",
        title_line,
    )
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
        description=(
            _section(text, "Problem") or _section(text, "Проблема") or text.strip()[:20000]
        ),
        estimate_hours=_effort_from_section(rice_section),
        rice=_rice_from_bullets(rice_section),
        depends_on=_split_depends(_bold_field(text, "Depends on")),
        tags=_parse_tags(_section(text, "Tags")),
        acceptance_criteria=(
            _section(text, "Acceptance Criteria")
            or _section(text, "Критерии завершения")
            or _section(text, "Критерии приёмки")
            or _section(text, "Критерии приемки")
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
                estimate_hours=_normalize_hours(block.get("effort"), block.get("unit")),
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
                    status=_status_from(_field_value(block, "Статус"), WorkStatus.backlog),
                    priority=priority,
                    description=_field_value(block, "Описание"),
                    estimate_hours=(
                        _normalize_hours(effort_match.group(1), effort_match.group(2))
                        if effort_match
                        else None
                    ),
                    depends_on=_split_depends(_field_value(block, "Зависит от")),
                    acceptance_criteria=_field_value(block, "Критерии завершения"),
                    meta={"format": "shared_infra"},
                )
            )
    return tasks


_TASK_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_TASK_ID_START = re.compile(r"^([A-Z][A-Z0-9_.-]*-\d+)\s*[:\uff1a-]\s*(.+)")
_CHECKBOX = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(?!\[[ xX]\])(.+?)\s*$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.+?)\s*$")


def _heading_is_task_like(heading: str) -> bool:
    return sources.heading_is_task_like(heading)


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown into ``(heading, body)`` pairs by ATX headings."""

    sections: list[tuple[str, str]] = []
    matches = list(_TASK_HEADING.finditer(text))
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(2).strip(), text[start:end]))
    return sections


def _slug_id(text: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]
    return slug or f"item-{index}"


def parse_task_sections(path: Path) -> list[ParsedTask]:
    """Extract tasks from task-like sections of README / analysis files.

    Only headings that look like Roadmap / TODO / Backlog / Plan are
    considered; inside them checkbox items, bullets and numbered items become
    tasks. Files without such sections yield an empty list (no noise).
    """

    text = path.read_text(encoding="utf-8", errors="replace")
    tasks: list[ParsedTask] = []
    for heading, body in _split_sections(text):
        if not _heading_is_task_like(heading):
            continue
        for line in body.splitlines():
            status = WorkStatus.backlog
            content: str | None = None
            checkbox = _CHECKBOX.match(line)
            if checkbox:
                status = WorkStatus.done if checkbox.group(1).lower() == "x" else WorkStatus.backlog
                content = checkbox.group(2)
            else:
                bullet = _BULLET.match(line) or _NUMBERED.match(line)
                if bullet:
                    content = bullet.group(1)
            if not content:
                continue
            title = re.sub(r"\s+", " ", content).strip()
            if len(title) < 3:
                continue
            external_id = _slug_id(title, len(tasks) + 1)
            id_match = _TASK_ID_START.match(title)
            if id_match:
                external_id = id_match.group(1)
                title = id_match.group(2).strip()
            tasks.append(
                ParsedTask(
                    external_id=external_id,
                    title=title[:300],
                    source_path=path,
                    status=status,
                    priority=3,
                    description=title[:2000],
                    meta={"format": "task_sections", "section": heading},
                )
            )
            if len(tasks) >= 100:
                return tasks
    return tasks


def _table_is_id(value: str) -> bool:
    return value == "id" or value.startswith("id")


def _table_cell(cells: list[str], columns: dict[str, int], name: str) -> str | None:
    pos = columns.get(name)
    if pos is None or pos >= len(cells):
        return None
    value = cells[pos].strip()
    return value or None


def parse_backlog_table(path: Path) -> list[ParsedTask]:
    """Parse a markdown-table backlog (``| ID | Задача | P | SP | Dep |``)."""

    text = path.read_text(encoding="utf-8", errors="replace")
    tasks: list[ParsedTask] = []
    columns: dict[str, int] | None = None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            columns = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if columns is None:
            lowered = [cell.lower() for cell in cells]
            has_id = any(_table_is_id(cell) for cell in lowered)
            has_title = any(
                "задач" in cell or "task" in cell or "title" in cell for cell in lowered
            )
            if has_id and has_title:
                columns = {}
                for pos, name in enumerate(lowered):
                    if _table_is_id(name):
                        columns.setdefault("id", pos)
                    elif "задач" in name or "task" in name or "title" in name:
                        columns.setdefault("title", pos)
                    elif name in ("p", "приоритет", "priority"):
                        columns.setdefault("priority", pos)
                    elif name in ("sp", "effort", "оценка", "сторипоинты"):
                        columns.setdefault("effort", pos)
                    elif "dep" in name or "зависит" in name:
                        columns.setdefault("depends_on", pos)
            continue
        if all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        if "id" not in columns or "title" not in columns:
            continue

        external_id = _table_cell(cells, columns, "id")
        title = _table_cell(cells, columns, "title")
        if not external_id or not title:
            continue
        effort = _table_cell(cells, columns, "effort")
        tasks.append(
            ParsedTask(
                external_id=external_id.strip("`[] "),
                title=title[:300],
                source_path=path,
                priority=_priority_from(_table_cell(cells, columns, "priority")),
                estimate_hours=(_normalize_hours(effort, "story points") if effort else None),
                depends_on=_split_depends(_table_cell(cells, columns, "depends_on")),
                description=title[:2000],
                meta={"format": "backlog_table"},
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
    if path.name == "BACKLOG.md" and path.parent.name == "tasks":
        return parse_shared_infra_backlog(path)
    if path.name.lower() == "readme.md":
        return parse_task_sections(path)
    if path.name.lower() == "backlog.md":
        return parse_backlog_table(path)
    if path.name.upper() in SKIP_FILENAMES:
        return []
    if "ai" in path.parts and "analysis" in path.parts:
        return parse_task_sections(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if any("id" in block for block in parse_frontmatter_blocks(text)):
        return parse_frontmatter_file(path)
    task = parse_ai_task_file(path)
    return [task] if task is not None else []


def discover_task_sources(root: Path) -> list[sources.DiscoveredSource]:
    """Expand configured task sources under a project root."""

    return sources.discover_sources(root, "task")


def discover_sources(root: Path) -> list[Path]:
    """Backward-compatible list of task source paths under a project root."""

    return [item.path for item in discover_task_sources(root)]


def _explicit_task_spec() -> sources.SourceSpec:
    return sources.SourceSpec(name="explicit", kind="task", parser="auto")


def _parse_discovered(item: sources.DiscoveredSource, root: Path) -> list[ParsedTask]:
    return cast(list[ParsedTask], sources.parse_source(item.path, root, item.spec))


def _relative_ref(path: Path, root: Path, external_id: str) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        relative = path.as_posix()
    return f"{relative}:{external_id}"


def _apply_fields(item: WorkItem, task: ParsedTask, root: Path, now: datetime) -> bool:
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
    if source_path is not None:
        discovered = [sources.DiscoveredSource(path=Path(source_path), spec=_explicit_task_spec())]
    else:
        discovered = discover_task_sources(root)

    parsed: list[ParsedTask] = []
    seen: set[tuple[str, str]] = set()
    for source_item in discovered:
        try:
            for task in _parse_discovered(source_item, root):
                key = (task.source_path.as_posix(), task.external_id)
                if key in seen:
                    continue
                seen.add(key)
                parsed.append(task)
        except Exception as exc:
            report.errors.append(f"{source_item.path}: {exc}")

    if not dry_run and not skip_backup:
        await create_backup(backup_dir=backup_dir)

    existing = list(
        (await session.execute(select(WorkItem).where(WorkItem.project_id == project.id)))
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


# --------------------------------------------------------------- parsers
# Bind parser names (referenced from sources.yaml) to concrete functions.


def _parser_ai_task(path: Path, root: Path, spec: sources.SourceSpec) -> list[ParsedTask]:
    task = parse_ai_task_file(path)
    return [task] if task is not None else []


def _parser_frontmatter(path: Path, root: Path, spec: sources.SourceSpec) -> list[ParsedTask]:
    return parse_frontmatter_file(path)


def _parser_shared_infra(path: Path, root: Path, spec: sources.SourceSpec) -> list[ParsedTask]:
    return parse_shared_infra_backlog(path)


def _parser_task_sections(path: Path, root: Path, spec: sources.SourceSpec) -> list[ParsedTask]:
    return parse_task_sections(path)


def _parser_backlog_table(path: Path, root: Path, spec: sources.SourceSpec) -> list[ParsedTask]:
    return parse_backlog_table(path)


def _parser_auto(path: Path, root: Path, spec: sources.SourceSpec) -> list[ParsedTask]:
    return parse_path(path)


sources.register_parser("ai_task", _parser_ai_task)
sources.register_parser("frontmatter", _parser_frontmatter)
sources.register_parser("shared_infra", _parser_shared_infra)
sources.register_parser("task_sections", _parser_task_sections)
sources.register_parser("backlog_table", _parser_backlog_table)
sources.register_parser("auto", _parser_auto)
