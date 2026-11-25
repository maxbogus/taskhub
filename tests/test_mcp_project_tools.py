"""Contract tests for the project admin MCP tools (M16).

The tools are called through ``FastMCP.call_tool`` so the registered schema and
the argument names are covered — an agent calling ``sync_projects`` must get the
same behaviour as the CLI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from taskhub.db import dispose_engine, init_db, session_scope
from taskhub.mcp import server
from taskhub.repo.projects import create_project
from taskhub.repo.work_items import create_item


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest_asyncio.fixture
async def repositories(tmp_path: Path, monkeypatch) -> AsyncIterator[Path]:
    """A temporary database plus a temporary repositories root."""

    db_file = tmp_path / "mcp-projects.db"
    monkeypatch.setenv("TASKHUB_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    await dispose_engine()
    await init_db()
    root = tmp_path / "Repositories"
    root.mkdir()
    yield root
    await dispose_engine()


async def _call(tool: str, arguments: dict[str, Any]) -> Any:
    result = await server.mcp.call_tool(tool, arguments)
    return result[1] if isinstance(result, tuple) else result


async def test_sync_projects_tool_reports_registered(repositories: Path) -> None:
    _write(repositories / "alpha" / "docs" / "backlog" / "T-1.md", "# T-1\n")

    payload = await _call(
        "sync_projects", {"root": str(repositories), "dry_run": True, "max_depth": 2}
    )

    assert payload["root"] == str(repositories)
    assert payload["dry_run"] is True
    assert payload["registered"] == 1
    assert [entry["slug"] for entry in payload["projects"]] == ["alpha"]


async def test_project_audit_tool_lists_stale(repositories: Path) -> None:
    async with session_scope() as session:
        await create_project(
            session,
            slug="chem-trial",
            name="chem_trial",
            root_path=str(repositories / "chem_trial"),
        )
        await session.commit()

    payload = await _call("project_audit", {"root": str(repositories), "max_depth": 2})

    assert payload["active"] == 1
    assert payload["orphan_items"] == 0
    assert [entry["slug"] for entry in payload["stale"]] == ["chem-trial"]


async def test_move_project_tool_keeps_the_slug(repositories: Path) -> None:
    target = repositories / "career" / "hh-matcher"
    target.mkdir(parents=True)
    async with session_scope() as session:
        await create_project(
            session, slug="hh-matcher", name="hh-matcher", root_path=str(repositories / "hh")
        )
        await session.commit()

    payload = await _call("move_project", {"slug": "hh-matcher", "root": str(target)})

    assert payload["moved"] is True
    assert payload["slug"] == "hh-matcher"
    assert payload["to"] == str(target)


async def test_archive_project_tool_refuses_data_without_force(repositories: Path) -> None:
    async with session_scope() as session:
        project = await create_project(
            session, slug="busy", name="busy", root_path=str(repositories / "busy")
        )
        await create_item(session, project_id=project.id, title="T-1")
        await session.commit()

    with pytest.raises(Exception, match="work items"):
        await _call("archive_project", {"slug": "busy"})

    payload = await _call("archive_project", {"slug": "busy", "force": True})
    assert payload["archived"] is True
    assert payload["work_items"] == 1
