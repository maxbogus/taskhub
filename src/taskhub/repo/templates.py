"""Task template repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import TaskTemplate


async def get_template(
    session: AsyncSession, name: str, *, project_id: str | None = None
) -> TaskTemplate | None:
    """Return a template by name, preferring a project-scoped copy."""

    stmt = select(TaskTemplate).where(TaskTemplate.name == name)
    if project_id is not None:
        stmt = stmt.where(
            (TaskTemplate.project_id == project_id)
            | (TaskTemplate.project_id.is_(None))
        )
        stmt = stmt.order_by(TaskTemplate.project_id.is_(None).asc())
    else:
        stmt = stmt.where(TaskTemplate.project_id.is_(None))
    stmt = stmt.order_by(TaskTemplate.created_at.asc())
    return (await session.execute(stmt)).scalars().first()


async def upsert_template(
    session: AsyncSession,
    *,
    name: str,
    body: str,
    type: str = "task",
    project_id: str | None = None,
) -> tuple[TaskTemplate, bool]:
    """Create or update a template; returns ``(template, created)``."""

    stmt = select(TaskTemplate).where(TaskTemplate.name == name)
    if project_id is None:
        stmt = stmt.where(TaskTemplate.project_id.is_(None))
    else:
        stmt = stmt.where(TaskTemplate.project_id == project_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        template = TaskTemplate(
            project_id=project_id, name=name, body=body, type=type
        )
        session.add(template)
        await session.flush()
        return template, True
    existing.body = body
    existing.type = type
    await session.flush()
    return existing, False


async def list_templates(
    session: AsyncSession, *, project_id: str | None = None
) -> list[TaskTemplate]:
    """List templates, including global ones when a project is scoped."""

    stmt = select(TaskTemplate)
    if project_id is not None:
        stmt = stmt.where(
            (TaskTemplate.project_id == project_id)
            | (TaskTemplate.project_id.is_(None))
        )
    stmt = stmt.order_by(TaskTemplate.name.asc())
    return list((await session.execute(stmt)).scalars().all())
