"""Tests for cwd/project scope resolution."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.repo.projects import create_project
from taskhub.services.scope import resolve_scope


async def test_resolve_scope_by_slug(session: AsyncSession) -> None:
    await create_project(session, slug="alpha", name="Alpha", root_path="/tmp/alpha")
    await session.commit()

    scope = await resolve_scope(session, project_slug="alpha")

    assert scope.kind == "project"
    assert scope.project is not None
    assert scope.project.slug == "alpha"


async def test_resolve_scope_by_cwd_longest_prefix(session: AsyncSession) -> None:
    await create_project(session, slug="root", name="Root", root_path="/home/user/repos")
    await create_project(
        session, slug="nested", name="Nested", root_path="/home/user/repos/nested"
    )
    await session.commit()

    scope = await resolve_scope(session, cwd="/home/user/repos/nested/src")

    assert scope.project is not None
    assert scope.project.slug == "nested"


async def test_resolve_scope_global_when_no_match(session: AsyncSession) -> None:
    scope = await resolve_scope(session, cwd="/unknown/path")

    assert scope.kind == "global"
    assert scope.project is None


async def test_resolve_scope_unknown_slug_raises(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not found"):
        await resolve_scope(session, project_slug="missing")
