"""MCP resources: compact, on-demand views of tasks and projects."""

from __future__ import annotations

from sqlalchemy import select

from taskhub.db import session_scope
from taskhub.models import WorkItem, WorkStatus
from taskhub.repo.projects import get_project_by_slug
from taskhub.repo.work_items import get_item


async def task_doc(item_id: str) -> str:
    """Markdown view of a single task (bounded size)."""

    async with session_scope() as session:
        item = await get_item(session, item_id)
        if item is None:
            return f"# Not found\n\nWork item `{item_id}` does not exist."
        lines = [
            f"# {item.title}",
            "",
            f"- id: `{item.id}`",
            f"- status: `{item.status.value}`",
            f"- priority: {item.priority}",
            f"- type: `{item.type.value}`",
        ]
        if item.estimate_hours is not None:
            lines.append(f"- estimate: {item.estimate_hours}h")
        if item.due_at is not None:
            lines.append(f"- due: {item.due_at.isoformat()}")
        if item.external_ref:
            lines.append(f"- external_ref: `{item.external_ref}`")
        if item.description:
            lines.extend(["", "## Description", "", item.description[:2000]])
        if item.acceptance_criteria:
            lines.extend(["", "## Acceptance criteria", "", item.acceptance_criteria[:1000]])
        return "\n".join(lines)


async def task_context(item_id: str) -> str:
    """Compact context: parent, children and blockers."""

    async with session_scope() as session:
        item = await get_item(session, item_id)
        if item is None:
            return f"# Not found\n\nWork item `{item_id}` does not exist."
        lines = [f"# Context — {item.title}", "", f"- status: `{item.status.value}`"]
        if item.parent_id:
            parent = await get_item(session, item.parent_id)
            if parent is not None:
                lines.append(f"- parent: `{parent.id}` {parent.title} ({parent.status.value})")
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
        if children:
            lines.extend(["", "## Children"])
            for child in children:
                lines.append(
                    f"- `{child.id}` {child.title} ({child.status.value}, p{child.priority})"
                )
        from taskhub.services.gates import blocking_items

        blockers = await blocking_items(session, item.id)
        if blockers:
            lines.extend(["", "## Blocked by"])
            lines.extend(f"- `{blocker_id}`" for blocker_id in blockers)
        return "\n".join(lines)


async def project_backlog(slug: str) -> str:
    """Grouped backlog: in_progress / ready / blocked / backlog."""

    async with session_scope() as session:
        project = await get_project_by_slug(session, slug)
        if project is None:
            return f"# Not found\n\nProject `{slug}` does not exist."
        rows = list(
            (
                await session.execute(
                    select(WorkItem).where(
                        WorkItem.project_id == project.id,
                        WorkItem.archived_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        groups: dict[WorkStatus, list[WorkItem]] = {
            status: [] for status in WorkStatus
        }
        for item in rows:
            groups[item.status].append(item)

        lines = [f"# Backlog — {project.name}", ""]
        for status in (
            WorkStatus.in_progress,
            WorkStatus.ready,
            WorkStatus.blocked,
            WorkStatus.backlog,
            WorkStatus.review,
        ):
            items = sorted(groups[status], key=lambda i: (i.priority, i.order_index))
            if not items:
                continue
            lines.append(f"## {status.value}")
            for item in items:
                estimate = f" ({item.estimate_hours:.1f}h)" if item.estimate_hours else ""
                lines.append(f"- `{item.id}` {item.title} — p{item.priority}{estimate}")
            lines.append("")
        return "\n".join(lines)
