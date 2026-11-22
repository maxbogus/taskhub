"""The portfolio pseudo-project (M14).

The files at the root of the repositories tree (INDEX.md, STATUS.md,
SUMMARY.md, ...) describe the whole portfolio rather than one project.
They are collected into a single pseudo-project so they are reachable
through the same tools as everything else.

Two rules keep the pseudo-project from confusing the rest of the system:

* it is **hidden** from the project listing — the dashboard must show
  real projects, not a fake entry;
* its slug is **reserved** — a real project cannot take the name.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Project, WorkItem, WorkStatus

#: The slug of the pseudo-project.
PORTFOLIO_SLUG = "portfolio"

#: Marker in ``Project.config`` that identifies the pseudo-project.
PSEUDO_MARKER = "pseudo"


def is_portfolio(slug: str | None) -> bool:
    """True when the slug belongs to the portfolio pseudo-project."""
    return slug == PORTFOLIO_SLUG


async def ensure_portfolio(session: AsyncSession) -> Project:
    """Return the portfolio pseudo-project, creating it when needed.

    Args:
        session: Database session.

    Returns:
        The pseudo-project.
    """
    existing = (
        await session.execute(select(Project).where(Project.slug == PORTFOLIO_SLUG))
    ).scalar_one_or_none()
    if existing is not None:
        existing.name = "Portfolio"
        existing.description = "Root-level files of the repositories tree."
        existing.config = {**existing.config, PSEUDO_MARKER: True}
        await session.flush()
        return existing

    project = Project(
        slug=PORTFOLIO_SLUG,
        name="Portfolio",
        root_path="",
        description="Root-level files of the repositories tree.",
        config={PSEUDO_MARKER: True},
    )
    session.add(project)
    await session.flush()
    return project


async def portfolio_summary(session: AsyncSession) -> dict[str, Any]:
    """Summarise the whole portfolio: projects, items and statuses.

    The pseudo-project itself is excluded from the counts.

    Args:
        session: Database session.

    Returns:
        The portfolio counters.
    """
    projects = list(
        (
            await session.execute(
                select(Project).where(
                    Project.archived_at.is_(None),
                    Project.slug != PORTFOLIO_SLUG,
                )
            )
        )
        .scalars()
        .all()
    )
    project_ids = [project.id for project in projects]

    by_status: dict[str, int] = {}
    items = 0
    if project_ids:
        rows = (
            await session.execute(
                select(WorkItem.status, func.count(WorkItem.id))
                .where(WorkItem.project_id.in_(project_ids))
                .group_by(WorkItem.status)
            )
        ).all()
        by_status = {status.value: int(count) for status, count in rows}
        items = sum(by_status.values())

    return {
        "projects": len(projects),
        "items": items,
        "by_status": by_status,
        "done": by_status.get(WorkStatus.done.value, 0),
        "open": sum(
            count
            for status, count in by_status.items()
            if status
            not in (
                WorkStatus.done.value,
                WorkStatus.cancelled.value,
                WorkStatus.archived.value,
            )
        ),
        "slugs": sorted(project.slug for project in projects),
    }
