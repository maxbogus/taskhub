"""Workflow transitions, gates and role permissions (M5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import (
    AgentRole,
    Dependency,
    DependencyType,
    Note,
    NoteKind,
    Project,
    WorkItem,
    WorkStatus,
)
from taskhub.repo.work_items import get_item

ROLE_TRANSITIONS: dict[AgentRole, dict[WorkStatus, set[WorkStatus]]] = {
    AgentRole.agent: {
        WorkStatus.backlog: {WorkStatus.ready},
        WorkStatus.ready: {WorkStatus.in_progress, WorkStatus.blocked},
        WorkStatus.in_progress: {WorkStatus.review, WorkStatus.blocked},
        WorkStatus.blocked: {WorkStatus.in_progress},
        WorkStatus.review: set(),
        WorkStatus.done: set(),
        WorkStatus.cancelled: set(),
        WorkStatus.archived: set(),
    },
    AgentRole.reviewer: {
        WorkStatus.in_progress: {WorkStatus.review},
        WorkStatus.review: {WorkStatus.done, WorkStatus.in_progress},
        WorkStatus.done: {WorkStatus.archived},
        WorkStatus.archived: {WorkStatus.ready},
        WorkStatus.backlog: set(),
        WorkStatus.ready: set(),
        WorkStatus.blocked: set(),
        WorkStatus.cancelled: set(),
    },
    AgentRole.human: {
        WorkStatus.backlog: {WorkStatus.ready, WorkStatus.cancelled},
        WorkStatus.ready: {
            WorkStatus.in_progress,
            WorkStatus.blocked,
            WorkStatus.backlog,
            WorkStatus.cancelled,
        },
        WorkStatus.in_progress: {
            WorkStatus.review,
            WorkStatus.blocked,
            WorkStatus.cancelled,
        },
        WorkStatus.blocked: {WorkStatus.in_progress, WorkStatus.cancelled},
        WorkStatus.review: {
            WorkStatus.done,
            WorkStatus.in_progress,
            WorkStatus.cancelled,
        },
        WorkStatus.done: {
            WorkStatus.archived,
            WorkStatus.ready,
            WorkStatus.in_progress,
        },
        WorkStatus.cancelled: {WorkStatus.archived, WorkStatus.backlog},
        WorkStatus.archived: {WorkStatus.ready, WorkStatus.backlog},
    },
}


class FlowError(ValueError):
    """Raised when a transition or gate fails."""


@dataclass(frozen=True)
class StatusChange:
    item_id: str
    from_status: str
    to_status: str
    note_id: str | None


def _flow_config(project: Project) -> dict[str, Any]:
    config = project.config or {}
    flow = config.get("flow")
    return flow if isinstance(flow, dict) else {}


def allowed_targets(
    project: Project, role: AgentRole, current: WorkStatus
) -> set[WorkStatus]:
    """Return roles' allowed target statuses, with project overrides."""

    roles = _flow_config(project).get("roles")
    if isinstance(roles, dict):
        raw = roles.get(role.value)
        if isinstance(raw, list):
            result: set[WorkStatus] = set()
            for pair in raw:
                if not isinstance(pair, str) or "->" not in pair:
                    continue
                source, target = pair.split("->", 1)
                if source.strip() != current.value:
                    continue
                try:
                    result.add(WorkStatus(target.strip()))
                except ValueError:
                    continue
            return result
    return ROLE_TRANSITIONS.get(role, {}).get(current, set())


async def blocking_items(session: AsyncSession, item_id: str) -> list[str]:
    """Return ids of unfinished items that block ``item_id``."""

    stmt = (
        select(Dependency.from_id)
        .join(WorkItem, WorkItem.id == Dependency.from_id)
        .where(
            Dependency.to_id == item_id,
            Dependency.type.in_([DependencyType.blocks, DependencyType.blocked_by]),
            WorkItem.status != WorkStatus.done,
        )
    )
    return list((await session.execute(stmt)).scalars().all())


async def gate_errors(
    session: AsyncSession, item: WorkItem, target: WorkStatus
) -> list[str]:
    """Return human-readable gate failures for a target status."""

    errors: list[str] = []
    if target in (
        WorkStatus.ready,
        WorkStatus.in_progress,
        WorkStatus.review,
        WorkStatus.done,
    ):
        blocked = await blocking_items(session, item.id)
        if blocked:
            errors.append(f"blocked by: {', '.join(blocked)}")
    if target is WorkStatus.ready:
        if item.estimate_hours is None or item.estimate_hours <= 0:
            errors.append("estimate_hours must be set before moving to ready")
        if not (item.meta or {}).get("rice"):
            errors.append("meta.rice must be set before moving to ready")
    if target is WorkStatus.review and item.started_at is None:
        errors.append("item must be started before review")
    if target is WorkStatus.done and not item.acceptance_criteria:
        errors.append("acceptance_criteria must be set before done")
    return errors


async def check_ready_gate(session: AsyncSession, item: WorkItem) -> list[str]:
    """Backwards-compatible helper for the ready gate."""

    return await gate_errors(session, item, WorkStatus.ready)


async def check_done_gate(session: AsyncSession, item: WorkItem) -> list[str]:
    """Backwards-compatible helper for the done gate."""

    return await gate_errors(session, item, WorkStatus.done)


async def change_status(
    session: AsyncSession,
    item_id: str,
    target_status: str,
    *,
    role: str = "agent",
    actor: str = "mcp-client",
    note: str | None = None,
) -> StatusChange:
    """Validate a role-aware transition, apply gates and write an audit note."""

    item = await get_item(session, item_id)
    if item is None:
        raise FlowError(f"work item '{item_id}' not found")
    try:
        target = WorkStatus(target_status)
    except ValueError as exc:
        raise FlowError(f"unknown status: {target_status}") from exc
    try:
        role_enum = AgentRole(role)
    except ValueError as exc:
        raise FlowError(f"unknown role: {role}") from exc

    project = await session.get(Project, item.project_id)
    if project is None:
        raise FlowError(f"project '{item.project_id}' not found")

    if target is item.status:
        return StatusChange(
            item_id=item.id,
            from_status=item.status.value,
            to_status=target.value,
            note_id=None,
        )

    allowed = allowed_targets(project, role_enum, item.status)
    if target not in allowed:
        raise FlowError(
            f"transition {item.status.value} -> {target.value} "
            f"is not allowed for role {role_enum.value}"
        )

    errors = await gate_errors(session, item, target)
    if errors:
        raise FlowError("; ".join(errors))

    previous = item.status
    now = datetime.now(UTC).replace(tzinfo=None)
    item.status = target
    if target is WorkStatus.in_progress and item.started_at is None:
        item.started_at = now
    if target is WorkStatus.done:
        item.done_at = now
    elif target is not WorkStatus.archived:
        item.done_at = None
    if target is WorkStatus.archived:
        item.archived_at = now
    else:
        item.archived_at = None

    note_row = Note(
        project_id=item.project_id,
        work_item_id=item.id,
        kind=NoteKind.comment,
        content=note or f"status: {previous.value} -> {target.value}",
        author=actor,
        meta={
            "from": previous.value,
            "to": target.value,
            "role": role_enum.value,
        },
    )
    session.add(note_row)
    await session.flush()
    return StatusChange(
        item_id=item.id,
        from_status=previous.value,
        to_status=target.value,
        note_id=note_row.id,
    )


async def workflow_info(
    session: AsyncSession, item_id: str, role: str = "agent"
) -> dict[str, Any]:
    """Describe the current status, allowed targets and gate errors."""

    item = await get_item(session, item_id)
    if item is None:
        raise FlowError(f"work item '{item_id}' not found")
    try:
        role_enum = AgentRole(role)
    except ValueError as exc:
        raise FlowError(f"unknown role: {role}") from exc
    project = await session.get(Project, item.project_id)
    if project is None:
        raise FlowError(f"project '{item.project_id}' not found")

    allowed = allowed_targets(project, role_enum, item.status)
    gates: dict[str, list[str]] = {}
    for target in sorted(allowed, key=lambda status: status.value):
        gates[target.value] = await gate_errors(session, item, target)

    return {
        "item_id": item.id,
        "status": item.status.value,
        "role": role_enum.value,
        "allowed": sorted(target.value for target in allowed),
        "gates": gates,
    }

