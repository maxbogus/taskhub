"""Tests for the agent economics MCP tools (M11).

The tools are exercised the way a client exercises them: through
``FastMCP.call_tool``, so argument names and the registered schema are
covered too — a renamed argument would break every agent that calls it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from taskhub.api.main import app
from taskhub.db import dispose_engine, init_db, session_scope
from taskhub.mcp import server
from taskhub.repo.projects import create_project


@pytest.fixture
async def client(tmp_path: Path) -> AsyncClient:
    """An API client bound to a temporary database."""
    db_file = tmp_path / "mcp-econ.db"
    import os

    os.environ["TASKHUB_DB_URL"] = f"sqlite+aiosqlite:///{db_file}"
    await dispose_engine()
    await init_db()
    async with session_scope() as session:
        await create_project(session, slug="demo", name="Demo", root_path="/tmp/demo")
        await session.commit()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await dispose_engine()


async def _call(tool: str, arguments: dict[str, Any]) -> Any:
    """Call an MCP tool and return its structured result."""
    result = await server.mcp.call_tool(tool, arguments)
    return result[1] if isinstance(result, tuple) else result


async def _call_list(tool: str, arguments: dict[str, Any]) -> list[Any]:
    """Call an MCP tool that returns a list and unwrap it."""
    payload = await _call(tool, arguments)
    if isinstance(payload, dict) and "result" in payload:
        return list(payload["result"])
    return list(payload)


async def test_economics_summary_tool_reports_rate(client: AsyncClient) -> None:
    """The summary tool returns the human rate in force."""
    payload = await _call("economics_summary", {"project_slug": "demo"})

    assert payload["project"] == "demo"
    assert payload["human_rate_usd_per_hour"] > 0
    assert payload["currency"] == "USD"
    assert payload["roi"] is None


async def test_economics_summary_tool_rejects_unknown_project(
    client: AsyncClient,
) -> None:
    """An unknown project is an error, not an empty report."""
    with pytest.raises(Exception, match="not found"):
        await _call("economics_summary", {"project_slug": "absent"})


async def test_set_project_economics_tool_sets_rate(client: AsyncClient) -> None:
    """The rate can be written and is then visible in the summary."""
    written = await _call(
        "set_project_economics",
        {"project_slug": "demo", "human_rate_usd_per_hour": 75.0},
    )

    assert written["human_rate_usd_per_hour"] == 75.0

    summary = await _call("economics_summary", {"project_slug": "demo"})
    assert summary["human_rate_usd_per_hour"] == 75.0


async def test_set_project_economics_tool_writes_audit_note(
    client: AsyncClient,
) -> None:
    """A rate change leaves a decision note in the project."""
    await _call(
        "set_project_economics",
        {"project_slug": "demo", "human_rate_usd_per_hour": 80.0},
    )

    notes = await _call_list("search_notes", {"project_slug": "demo", "query": "economics"})
    assert any("economics" in note["content"] for note in notes)


async def test_set_model_price_tool_writes_and_lists(client: AsyncClient) -> None:
    """A price written through the tool shows up in the price list."""
    written = await _call(
        "set_model_price",
        {"pattern": "demo-*", "input_usd_per_mtok": 2.0},
    )

    assert written["pattern"] == "demo-*"
    assert written["created"] is True
    assert written["input_usd_per_mtok"] == pytest.approx(2.0)

    prices = await _call_list("list_prices", {"model": "demo"})
    assert any(price["pattern"] == "demo-*" for price in prices)
    assert any(price["source"] == "db" for price in prices)


async def test_sync_prices_tool_is_dry_run_by_default(client: AsyncClient) -> None:
    """The sync must not write unless it is asked to."""
    report = await _call("sync_prices", {})

    assert report["dry_run"] is True
    assert report["applied"] > 0

    # The bundled YAML prices are still visible — nothing was written to
    # the database, which is what the dry run protects.
    prices = await _call_list("list_prices", {"model": "claude-*"})
    assert prices
    assert all(price["source"] == "yaml" for price in prices)


async def test_sync_prices_tool_writes_when_asked(client: AsyncClient) -> None:
    """With ``dry_run=False`` the price list lands in the database."""
    report = await _call("sync_prices", {"dry_run": False})

    assert report["dry_run"] is False
    assert report["created"] > 0

    prices = await _call_list("list_prices", {"model": "claude-*"})
    assert any(price["source"] == "db" for price in prices)


async def test_cost_series_tool_rejects_unknown_bucket(client: AsyncClient) -> None:
    """An unsupported bucket is a clear error."""
    with pytest.raises(Exception, match="bucket"):
        await _call("cost_series", {"bucket": "fortnight"})


async def test_economics_report_tool_rejects_unknown_group(client: AsyncClient) -> None:
    """An unsupported grouping is a clear error."""
    with pytest.raises(Exception, match="group_by"):
        await _call("economics_report", {"group_by": "phase"})


async def test_new_tools_are_registered() -> None:
    """Every economics tool must be exposed by the MCP server."""
    tools = await server.mcp.list_tools()
    names = {tool.name for tool in tools}

    assert {
        "economics_report",
        "economics_summary",
        "cost_series",
        "list_prices",
        "sync_prices",
        "set_model_price",
        "close_model_price",
        "set_project_economics",
    } <= names
