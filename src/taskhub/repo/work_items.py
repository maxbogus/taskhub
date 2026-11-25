"""Work item and dependency repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import (
    Claim,
    Dependency,
    DependencyType,
    WorkItem,
    WorkItemType,
    WorkStatus,
)


async def create_item(
    session: AsyncSession,
    *,
    project_id: str,
    title: str,
    type: WorkItemType = WorkItemType.task,
    description: str | None = None,
    parent_id: str | None = None,
    priority: int = 3,
    estimate_hours: float | None = None,
    due_at: datetime | None = None,
    required_skill: str | None = None,
    acceptance_criteria: str | None = None,
    external_ref: str | None = None,
    meta: dict[str, Any] | None = None,
    status: WorkStatus = WorkStatus.backlog,
    order_index: float = 1000.0,
) -> WorkItem:
    item = WorkItem(
        project_id=project_id,
        title=title,
        type=type,
        description=description,
        parent_id=parent_id,
        priority=priority,
        estimate_hours=estimate_hours,
        due_at=due_at,
        required_skill=required_skill,
        acceptance_criteria=acceptance_criteria,
        external_ref=external_ref,
        meta=meta or {},
        status=status,
        order_index=order_index,
    )
    session.add(item)
    await session.flush()
    return item


async def get_item(session: AsyncSession, item_id: str) -> WorkItem | None:
    return await session.get(WorkItem, item_id)


async def list_items(
    session: AsyncSession,
    project_id: str,
    *,
    status: WorkStatus | None = None,
    limit: int = 100,
) -> list[WorkItem]:
    stmt = select(WorkItem).where(WorkItem.project_id == project_id, WorkItem.archived_at.is_(None))
    if status is not None:
        stmt = stmt.where(WorkItem.status == status)
    stmt = stmt.order_by(
        WorkItem.priority.asc(),
        WorkItem.order_index.asc(),
        WorkItem.created_at.asc(),
    ).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def next_item(
    session: AsyncSession,
    project_id: str,
    *,
    required_skill: str | None = None,
    now: datetime | None = None,
) -> WorkItem | None:
    """Return the highest-priority unclaimed, unblocked item.

    Filtering is intentionally done in Python for M1 clarity; a SQL-only
    query with NOT EXISTS is a follow-up optimisation for the 10k-item target.
    """

    now = now or datetime.now(UTC).replace(tzinfo=None)
    stmt = select(WorkItem).where(
        WorkItem.project_id == project_id,
        WorkItem.archived_at.is_(None),
        WorkItem.status.in_([WorkStatus.ready, WorkStatus.backlog]),
    )
    if required_skill:
        stmt = stmt.where(WorkItem.required_skill == required_skill)
    stmt = stmt.order_by(
        WorkItem.priority.asc(),
        WorkItem.order_index.asc(),
        WorkItem.created_at.asc(),
    )
    candidates = list((await session.execute(stmt)).scalars().all())
    if not candidates:
        return None

    claimed_stmt = select(Claim.work_item_id).where(
        Claim.released_at.is_(None), Claim.expires_at > now
    )
    claimed = set((await session.execute(claimed_stmt)).scalars().all())

    blocked_stmt = (
        select(Dependency.to_id)
        .join(WorkItem, WorkItem.id == Dependency.from_id)
        .where(
            Dependency.type.in_([DependencyType.blocks, DependencyType.blocked_by]),
            WorkItem.status != WorkStatus.done,
        )
    )
    blocked = set((await session.execute(blocked_stmt)).scalars().all())

    for item in candidates:
        if item.id in claimed or item.id in blocked:
            continue
        return item
    return None


async def add_dependency(
    session: AsyncSession,
    *,
    from_id: str,
    to_id: str,
    type: DependencyType = DependencyType.blocked_by,
) -> Dependency:
    if from_id == to_id:
        raise ValueError("an item cannot depend on itself")
    dependency = Dependency(from_id=from_id, to_id=to_id, type=type)
    session.add(dependency)
    await session.flush()
    return dependency
