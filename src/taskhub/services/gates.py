"""Workflow gates for status transitions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Dependency, DependencyType, WorkItem, WorkStatus


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


async def check_ready_gate(session: AsyncSession, item: WorkItem) -> list[str]:
    """Return human-readable gate failures for moving an item to ready."""

    errors: list[str] = []
    if item.estimate_hours is None or item.estimate_hours <= 0:
        errors.append("estimate_hours must be set before moving to ready")
    if not (item.meta or {}).get("rice"):
        errors.append("meta.rice must be set before moving to ready")
    blocked = await blocking_items(session, item.id)
    if blocked:
        errors.append(f"blocked by: {', '.join(blocked)}")
    return errors


async def check_done_gate(session: AsyncSession, item: WorkItem) -> list[str]:
    """Return human-readable gate failures for moving an item to done."""

    errors: list[str] = []
    if not item.acceptance_criteria:
        errors.append("acceptance_criteria must be set before done")
    return errors
