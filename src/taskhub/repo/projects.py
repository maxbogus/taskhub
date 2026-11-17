"""Project repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Project


async def create_project(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    root_path: str,
    description: str | None = None,
    config: dict[str, Any] | None = None,
) -> Project:
    project = Project(
        slug=slug,
        name=name,
        root_path=root_path,
        description=description,
        config=config or {},
    )
    session.add(project)
    await session.flush()
    return project


async def upsert_project(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    root_path: str,
    description: str | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[Project, bool]:
    """Create a project or refresh its metadata; returns ``(project, created)``."""

    stmt = select(Project).where(Project.slug == slug)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        project = Project(
            slug=slug,
            name=name,
            root_path=root_path,
            description=description,
            config=config or {},
        )
        session.add(project)
        await session.flush()
        return project, True
    existing.name = name
    existing.root_path = root_path
    if description is not None:
        existing.description = description
    if existing.archived_at is not None:
        existing.archived_at = None
    await session.flush()
    return existing, False


async def get_project_by_slug(session: AsyncSession, slug: str) -> Project | None:
    stmt = select(Project).where(
        Project.slug == slug, Project.archived_at.is_(None)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_projects(session: AsyncSession) -> list[Project]:
    stmt = (
        select(Project)
        .where(Project.archived_at.is_(None))
        .order_by(Project.slug.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def find_project_by_path(session: AsyncSession, path: str) -> Project | None:
    """Return the project whose root_path is the longest prefix of ``path``."""

    target = Path(path).expanduser().resolve()
    best: Project | None = None
    best_len = -1
    for project in await list_projects(session):
        root = Path(project.root_path).expanduser().resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if len(str(root)) > best_len:
            best = project
            best_len = len(str(root))
    return best
