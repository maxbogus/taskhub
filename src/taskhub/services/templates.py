"""Markdown task templates: import from disk and apply to new work items (M6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Project, TaskTemplate, WorkItem
from taskhub.repo.templates import upsert_template
from taskhub.repo.work_items import create_item

PRIORITY_MAP: dict[str, int] = {
    "CRITICAL": 1,
    "HIGH": 2,
    "MEDIUM": 3,
    "LOW": 4,
}

REPOSITORIES_ROOT = Path.home() / "Repositories"
DEFAULT_TEMPLATE_SOURCES: tuple[Path, ...] = (
    REPOSITORIES_ROOT / "telegram-chat-analysis/ai/tasks/templates",
    REPOSITORIES_ROOT / "mcp-servers/scripts/templates",
    REPOSITORIES_ROOT / "books/common/templates",
)


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split an optional leading YAML frontmatter block from a markdown body."""

    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        data = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return cast("dict[str, Any]", data), parts[2].lstrip("\n")


def render(text: str, variables: dict[str, str] | None) -> str:
    """Replace ``{KEY}`` and ``[KEY]`` placeholders with provided values."""

    if not variables:
        return text
    rendered = text
    for key, value in variables.items():
        rendered = rendered.replace(f"{{{key}}}", value)
        rendered = rendered.replace(f"[{key}]", value)
    return rendered


def _priority(front: dict[str, Any]) -> int:
    raw = front.get("priority")
    if isinstance(raw, bool):
        return 3
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        return PRIORITY_MAP.get(raw.upper(), 3)
    return 3


def _estimate_hours(front: dict[str, Any]) -> float | None:
    rice = front.get("rice")
    if isinstance(rice, dict):
        effort = rice.get("effort")
        if isinstance(effort, (int, float)) and not isinstance(effort, bool):
            return float(effort)
    raw = front.get("estimate_hours")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    return None


def _rice_meta(front: dict[str, Any]) -> dict[str, Any] | None:
    rice = front.get("rice")
    if not isinstance(rice, dict):
        return None
    confidence = rice.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and confidence > 1:
        confidence = confidence / 100
    return {
        "R": rice.get("reach"),
        "I": rice.get("impact"),
        "C": confidence,
        "E": rice.get("effort"),
    }


def _acceptance_criteria(front: dict[str, Any]) -> str | None:
    raw = front.get("verification") or front.get("acceptance_criteria")
    if isinstance(raw, list):
        return "\n".join(f"- [ ] {entry}" for entry in raw)
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


async def apply_template(
    session: AsyncSession,
    *,
    project: Project,
    template: TaskTemplate,
    title: str | None = None,
    variables: dict[str, str] | None = None,
) -> WorkItem:
    """Create a backlog work item from a stored markdown template."""

    front, body = split_frontmatter(template.body)
    rendered_body = render(body, variables)
    resolved_title = title or front.get("title") or front.get("name") or template.name
    meta: dict[str, Any] = {
        "template": template.name,
        "template_type": template.type,
    }
    rice = _rice_meta(front)
    if rice is not None:
        meta["rice"] = rice
    return await create_item(
        session,
        project_id=project.id,
        title=str(resolved_title),
        description=rendered_body,
        priority=_priority(front),
        estimate_hours=_estimate_hours(front),
        acceptance_criteria=_acceptance_criteria(front),
        meta=meta,
    )


@dataclass
class ImportReport:
    """Summary of a template import run."""

    imported: int = 0
    updated: int = 0
    names: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "updated": self.updated,
            "names": self.names,
            "missing_sources": self.missing_sources,
        }


def _template_name(path: Path, front: dict[str, Any]) -> str:
    raw = front.get("id") or front.get("name")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return path.stem


def _template_type(front: dict[str, Any]) -> str:
    raw = front.get("type")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "task"


async def import_templates(
    session: AsyncSession,
    *,
    source_roots: list[Path],
    project_id: str | None = None,
) -> ImportReport:
    """Import ``*.md`` templates from the given roots (idempotent by name)."""

    report = ImportReport()
    for root in source_roots:
        resolved = root.expanduser()
        if not resolved.is_dir():
            report.missing_sources.append(str(resolved))
            continue
        for path in sorted(resolved.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            front, _ = split_frontmatter(text)
            name = _template_name(path, front)
            _, created = await upsert_template(
                session,
                name=name,
                body=text,
                type=_template_type(front),
                project_id=project_id,
            )
            if created:
                report.imported += 1
            else:
                report.updated += 1
            report.names.append(name)
    return report
