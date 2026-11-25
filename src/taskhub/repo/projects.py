"""Project repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Project, _utcnow


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
    """Create a project or refresh its metadata; returns ``(project, created)``.

    Raises:
        ValueError: The slug is reserved for the portfolio pseudo-project.
    """

    from taskhub.services.portfolio import is_portfolio

    if is_portfolio(slug):
        raise ValueError(f"slug '{slug}' is reserved for the portfolio pseudo-project")

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


async def get_project_by_slug(
    session: AsyncSession, slug: str, *, include_archived: bool = False
) -> Project | None:
    stmt = select(Project).where(Project.slug == slug)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_projects(session: AsyncSession, *, include_archived: bool = False) -> list[Project]:
    """Return real projects: active only unless ``include_archived`` is set.

    The portfolio pseudo-project is left out: it is reachable by slug,
    but it must not appear among the real projects (the dashboard would
    show a fake entry).
    """

    from taskhub.services.portfolio import PORTFOLIO_SLUG

    stmt = select(Project).where(Project.slug != PORTFOLIO_SLUG)
    if not include_archived:
        stmt = stmt.where(Project.archived_at.is_(None))
    stmt = stmt.order_by(Project.slug.asc())
    return list((await session.execute(stmt)).scalars().all())


async def find_by_root_path(session: AsyncSession, root_path: str) -> Project | None:
    """Return the active project registered exactly at ``root_path``."""

    target = Path(root_path).expanduser().resolve()
    for project in await list_projects(session):
        if Path(project.root_path).expanduser().resolve() == target:
            return project
    return None


async def set_root_path(session: AsyncSession, project: Project, root_path: str) -> Project:
    """Re-point a project at another checkout; the slug never changes."""

    project.root_path = str(Path(root_path).expanduser().resolve())
    await session.flush()
    return project


async def archive_project(
    session: AsyncSession, project: Project, *, restore: bool = False
) -> Project:
    """Soft-archive (or restore) a project — the row is never deleted."""

    project.archived_at = None if restore else _utcnow()
    await session.flush()
    return project


async def project_counts(session: AsyncSession, project_id: str) -> tuple[int, int]:
    """Return ``(work_items, notes)`` attached to a project."""

    from taskhub.models import Note, WorkItem

    items = (
        await session.execute(
            select(func.count()).select_from(WorkItem).where(WorkItem.project_id == project_id)
        )
    ).scalar_one()
    notes = (
        await session.execute(
            select(func.count()).select_from(Note).where(Note.project_id == project_id)
        )
    ).scalar_one()
    return int(items), int(notes)


async def note_signatures(session: AsyncSession, project_id: str) -> set[tuple[str, str]]:
    """Return ``(kind, content)`` pairs of a project's notes for copy detection."""

    from taskhub.models import Note

    rows = await session.execute(
        select(Note.kind, Note.content).where(Note.project_id == project_id)
    )
    return {(str(kind), str(content)) for kind, content in rows.all()}


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
