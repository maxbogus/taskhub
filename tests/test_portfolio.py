"""Tests for the portfolio pseudo-project (M14).

The files at the root of the repositories tree (INDEX.md, STATUS.md,
SUMMARY.md, ...) describe the whole portfolio rather than a single
project. They are collected into one pseudo-project so they are
reachable from the same tools as everything else.

The pseudo-project is **not** a real directory: it must not show up as
a project in the ordinary listing, or the dashboard would show a fake
entry among the real ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from taskhub.models import Project
from taskhub.repo.projects import get_project_by_slug, list_projects
from taskhub.services.portfolio import (
    PORTFOLIO_SLUG,
    ensure_portfolio,
    is_portfolio,
    portfolio_summary,
)


def test_portfolio_slug_is_reserved() -> None:
    """The slug must not collide with a real project name."""
    assert PORTFOLIO_SLUG == "portfolio"
    assert is_portfolio(PORTFOLIO_SLUG) is True
    assert is_portfolio("taskhub") is False


async def test_ensure_portfolio_creates_the_project(session: AsyncSession) -> None:
    """The pseudo-project is created on demand and is idempotent."""
    created = await ensure_portfolio(session)
    await session.commit()

    assert created.slug == PORTFOLIO_SLUG
    assert created.root_path == ""

    again = await ensure_portfolio(session)
    await session.commit()
    assert again.id == created.id


async def test_portfolio_is_hidden_from_the_project_list(
    session: AsyncSession,
) -> None:
    """A pseudo-project must not appear among the real projects."""
    from taskhub.repo.projects import create_project

    await create_project(session, slug="real", name="Real", root_path="/tmp/real")
    await ensure_portfolio(session)
    await session.commit()

    slugs = [project.slug for project in await list_projects(session)]

    assert "real" in slugs
    assert PORTFOLIO_SLUG not in slugs


async def test_portfolio_is_still_reachable_by_slug(session: AsyncSession) -> None:
    """It is hidden from the listing, not unreachable."""
    await ensure_portfolio(session)
    await session.commit()

    project = await get_project_by_slug(session, PORTFOLIO_SLUG)

    assert project is not None
    assert project.slug == PORTFOLIO_SLUG


async def test_portfolio_summary_counts_real_projects(
    session: AsyncSession,
) -> None:
    """The summary describes the whole portfolio."""
    from taskhub.models import WorkStatus
    from taskhub.repo.projects import create_project
    from taskhub.repo.work_items import create_item

    project = await create_project(session, slug="real", name="Real", root_path="/tmp/real")
    await create_item(session, project_id=project.id, title="Done", status=WorkStatus.done)
    await create_item(session, project_id=project.id, title="Open")
    await ensure_portfolio(session)
    await session.commit()

    summary = await portfolio_summary(session)

    assert summary["projects"] == 1
    assert summary["items"] == 2
    assert summary["by_status"]["done"] == 1
    assert summary["by_status"]["backlog"] == 1
    # One item is done, the other is still open.
    assert summary["done"] == 1
    assert summary["open"] == 1


async def test_portfolio_slug_cannot_be_upserted_as_a_real_project(
    session: AsyncSession,
) -> None:
    """Registering a real project under the reserved slug is refused."""
    from taskhub.repo.projects import upsert_project

    with pytest.raises(ValueError, match="reserved"):
        await upsert_project(session, slug=PORTFOLIO_SLUG, name="Fake", root_path="/tmp/fake")


async def test_portfolio_is_marked_in_the_database(session: AsyncSession) -> None:
    """The pseudo-project is recognisable by its marker, not by its name."""
    project = await ensure_portfolio(session)
    await session.commit()

    assert project.config.get("pseudo") is True
    assert isinstance(project, Project)


async def test_portfolio_source_files_are_declared() -> None:
    """The root portfolio files must be declared in sources.yaml."""
    from taskhub import sources

    config = sources.load_sources()
    names = {spec.name for spec in config.sources}

    assert "portfolio-root" in names

    spec = next(s for s in config.sources if s.name == "portfolio-root")
    assert spec.kind == "note"
    assert spec.root == "project"
    assert spec.register_project is False


async def test_portfolio_summary_tool_is_registered() -> None:
    """The portfolio summary must be reachable from MCP."""
    from taskhub.mcp import server

    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}

    assert "portfolio_summary" in names


async def test_register_projects_skips_the_root(tmp_path: Path) -> None:
    """The repositories root is not a project: it holds the portfolio."""
    from taskhub.services.projects import discover_projects

    (tmp_path / "child").mkdir()
    (tmp_path / "child" / "BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")

    candidates = discover_projects(tmp_path, max_depth=1)

    assert all(candidate.slug != "portfolio" for candidate in candidates)
