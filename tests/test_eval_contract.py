"""Contract tests for the MCP tool surface (M11).

Every case in ``eval/cases/tool_contract.jsonl`` is executed as a **real**
MCP tool call and checked against the expected contract. The cases live in
one file so the same set drives the pytest contract (layer 1) and the
promptfoo replay (layer 2).

These tests are deterministic and need no model, no network and no npm —
they belong in CI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from taskhub.mcp import server

CASES_FILE = Path(__file__).resolve().parent.parent / "eval" / "cases" / "tool_contract.jsonl"


def load_cases() -> list[dict[str, Any]]:
    """Load the contract cases."""
    if not CASES_FILE.is_file():
        return []
    return [
        json.loads(line)
        for line in CASES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


CASES = load_cases()


async def call_tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool and return its structured result or the error."""
    try:
        result = await server.mcp.call_tool(tool, arguments)
    except Exception as exc:
        return {"__error__": str(exc)}
    payload = result[1] if isinstance(result, tuple) else result
    if isinstance(payload, dict) and "result" in payload:
        return {"__list__": payload["result"]}
    return payload if isinstance(payload, dict) else {"__value__": payload}


@pytest.fixture(autouse=True)
async def _seeded_project(tmp_path: Path, monkeypatch):
    """Run every case against a temporary database with one project."""
    db_file = tmp_path / "eval-contract.db"
    monkeypatch.setenv("TASKHUB_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    from taskhub.db import dispose_engine, init_db, session_scope
    from taskhub.repo.projects import create_project

    await dispose_engine()
    await init_db()
    async with session_scope() as session:
        await create_project(session, slug="demo", name="Demo", root_path="/tmp/demo")
        await session.commit()
    yield
    await dispose_engine()


def test_cases_are_present() -> None:
    """The contract set must not be empty."""
    assert CASES, f"no cases found in {CASES_FILE}"


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
async def test_tool_contract(case: dict[str, Any]) -> None:
    """Each case calls a real tool and checks the expected outcome."""
    expect = case["expect"]
    payload = await call_tool(case["tool"], case.get("args", {}))

    if expect["status"] == "error":
        error = payload.get("__error__", "")
        assert error, f"expected an error, got {payload}"
        assert expect["error_contains"] in error, (
            f"error {error!r} does not contain {expect['error_contains']!r}"
        )
        return

    assert "__error__" not in payload, f"unexpected error: {payload}"

    for key, value in expect.get("json_contains", {}).items():
        assert payload.get(key) == value, f"{key} is {payload.get(key)!r}, expected {value!r}"

    for key, value in expect.get("json_path_equals", {}).items():
        assert payload.get(key) == value, f"{key} is {payload.get(key)!r}, expected {value!r}"


async def test_economics_tools_are_registered() -> None:
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


# ---------------------------------------------------------------------------
# Agentic layer (M12): a live model drives the real MCP tools.
# ---------------------------------------------------------------------------

AGENT_SCENARIOS_FILE = (
    Path(__file__).resolve().parent.parent / "eval" / "cases" / "agent_scenarios.jsonl"
)

#: Scenarios the local model is known to fail.
#:
#: A small local model is not deterministic enough to be a gate, so its
#: failures are recorded rather than hidden. The convention is the
#: hh-matcher one used by the donor harnesses: an unexpected **success**
#: turns into an XPASS error, which is the signal to revisit this list
#: instead of quietly dropping the check.
KNOWN_MODEL_FAILURES: set[str] = set()


def load_agent_scenarios() -> list[dict[str, Any]]:
    """Load the agentic scenarios."""
    if not AGENT_SCENARIOS_FILE.is_file():
        return []
    return [
        json.loads(line)
        for line in AGENT_SCENARIOS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_agent_scenarios_are_present() -> None:
    """The agentic scenario set must not be empty."""
    assert load_agent_scenarios(), f"no agent scenarios found in {AGENT_SCENARIOS_FILE}"


def test_agent_scenarios_use_allowed_tools_only() -> None:
    """A scenario may only expect tools the agentic runner exposes."""
    from scripts.agent_eval import ALLOWED_TOOLS

    for scenario in load_agent_scenarios():
        for tool in scenario.get("expect_tools", []):
            assert tool in ALLOWED_TOOLS, f"{scenario['id']} expects unknown tool {tool}"


@pytest.mark.slow
@pytest.mark.parametrize(
    "scenario",
    load_agent_scenarios(),
    ids=[s["id"] for s in load_agent_scenarios()],
)
async def test_agent_scenario(scenario: dict[str, Any], tmp_path: Path, monkeypatch) -> None:
    """Run one scenario against the local model and the real MCP server.

    Marked ``slow``: it needs Ollama and takes tens of seconds. It is not
    part of CI; run it with ``make eval-agent``.

    The test seeds its own database, so it does not depend on the
    contract fixture (which creates an empty one).
    """
    from scripts.agent_eval import run_scenario, tool_schemas
    from scripts.eval_seed import seed_demo

    db_file = tmp_path / "agent-eval.db"
    monkeypatch.setenv("TASKHUB_DB_URL", f"sqlite+aiosqlite:///{db_file}")
    from taskhub.db import dispose_engine, init_db, session_scope

    await dispose_engine()
    await init_db()
    async with session_scope() as session:
        await seed_demo(session)

    model = os.environ.get("TASKHUB_EVAL_MODEL", "qwen2.5:7b-instruct-q4_K_M")
    schemas = await tool_schemas()
    verdict = await run_scenario(scenario, model, schemas)
    await dispose_engine()

    if scenario["id"] in KNOWN_MODEL_FAILURES:
        if verdict["passed"]:
            pytest.xfail(f"{scenario['id']} unexpectedly passed — revisit the list")
        return

    assert verdict["passed"], (
        f"{scenario['id']} failed: {verdict['problems']} "
        f"(tools: {verdict['tools_called']}, answer: {verdict['answer'][:200]!r})"
    )
