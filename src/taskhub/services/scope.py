"""Resolve the active project scope from an explicit slug or the agent cwd."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Project
from taskhub.repo.projects import find_project_by_path, get_project_by_slug


@dataclass(frozen=True)
class Scope:
    """Resolved scope for a tool call."""

    project: Project | None
    kind: str  # "project" | "global"


async def resolve_scope(
    session: AsyncSession,
    *,
    cwd: str | None = None,
    project_slug: str | None = None,
) -> Scope:
    """Resolve scope: explicit slug → longest cwd prefix → global.

    ``repos_touched`` fallback from agent context belongs to a later milestone
    once session metadata is stored; the cwd path is the primary signal.
    """

    if project_slug:
        project = await get_project_by_slug(session, project_slug)
        if project is None:
            raise ValueError(f"project '{project_slug}' not found")
        return Scope(project=project, kind="project")
    if cwd:
        project = await find_project_by_path(session, cwd)
        if project is not None:
            return Scope(project=project, kind="project")
    return Scope(project=None, kind="global")
