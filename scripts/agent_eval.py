#!/usr/bin/env python3
"""Agentic eval: a live model drives the real MCP tools (M12, layer 3).

The model gets the **real** MCP tool schemas (read from the running
TaskHub server) and a goal in plain Russian. It decides which tool to
call; the harness executes it against the real server and feeds the
result back, until the model answers.

Why a custom runner instead of promptfoo: promptfoo's ``config.mcp``
integration (which hands MCP tools to a model) supports Gemini, OpenAI
and Anthropic — not Ollama. The standalone ``mcp`` provider calls tools
but does not let a model choose them. Since Ollama is the only local
model available here, the agentic loop lives in this script.

The run is **not** part of CI: a small local model is not deterministic
enough to be a gate. Its failures are recorded in ``KNOWN_MODEL_FAILURES``
and asserted with ``xfail(strict=True)`` in the pytest contract — an
unexpected success is an error there, so the list cannot rot silently.

Usage:
    python scripts/agent_eval.py --model qwen2.5:7b-instruct-q4_K_M
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

DEFAULT_MODEL = "qwen2.5:7b-instruct-q4_K_M"
OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
SCENARIOS_FILE = PROJECT_ROOT / "eval" / "cases" / "agent_scenarios.jsonl"

#: The system prompt lives in a file: it is content, and a Python string
#: with Cyrillic letters next to Latin ones trips the homoglyph lint.
SYSTEM_PROMPT_FILE = PROJECT_ROOT / "eval" / "agent_system_prompt.txt"

#: Tools the model may use. Read-only plus the two harmless writes.
ALLOWED_TOOLS = (
    "economics_summary",
    "economics_report",
    "cost_series",
    "list_prices",
)

#: How many tool-calling rounds one scenario may take.
MAX_ROUNDS = 4


def system_prompt() -> str:
    """Read the system prompt from its file."""
    return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()


def load_scenarios() -> list[dict[str, Any]]:
    """Load the agentic scenarios."""
    if not SCENARIOS_FILE.is_file():
        return []
    return [
        json.loads(line)
        for line in SCENARIOS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


async def tool_schemas() -> list[dict[str, Any]]:
    """Build the Ollama tool schemas from the real MCP server.

    The MCP schemas carry ``$schema`` and ``title`` metadata that Ollama
    rejects, so the parameters are stripped down to what it needs.

    Returns:
        The tool definitions in Ollama's format.
    """
    from taskhub.mcp import server

    tools = await server.mcp.list_tools()
    schemas: list[dict[str, Any]] = []
    for tool in tools:
        if tool.name not in ALLOWED_TOOLS:
            continue
        schema = tool.inputSchema or {}
        properties = schema.get("properties") or {}
        cleaned = {
            name: {
                key: value for key, value in (spec or {}).items() if key not in ("$schema", "title")
            }
            for name, spec in properties.items()
        }
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": {
                        "type": "object",
                        "properties": cleaned,
                        "required": schema.get("required", []),
                    },
                },
            }
        )
    return schemas


def call_ollama(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Send one chat request to Ollama.

    Args:
        model: Model name.
        messages: Conversation so far.
        tools: Tool definitions.
        timeout: Request timeout in seconds.

    Returns:
        The parsed response.

    Raises:
        RuntimeError: Ollama is unreachable or answered with an error.
    """
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 512},
        }
    ).encode()
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload: dict[str, Any] = json.loads(response.read())
        return payload
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama is unreachable at {OLLAMA_URL}: {exc}") from exc


async def run_tool(name: str, arguments: dict[str, Any]) -> str:
    """Call a real MCP tool and return its textual result.

    Args:
        name: Tool name.
        arguments: Tool arguments.

    Returns:
        The tool output as text (the error message on failure).
    """
    from taskhub.mcp import server

    try:
        result = await server.mcp.call_tool(name, arguments)
    except Exception as exc:
        return f"ERROR: {exc}"
    payload = result[1] if isinstance(result, tuple) else result
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    if isinstance(payload, dict) and "__error__" in payload:
        return str(payload["__error__"])
    return json.dumps(payload, ensure_ascii=False)


async def run_scenario(
    scenario: dict[str, Any], model: str, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    """Run one scenario against the model and the MCP server.

    Args:
        scenario: The scenario contract.
        model: Model name.
        tools: Tool definitions.

    Returns:
        The verdict: the tools called, the final answer and the problems.
    """
    system = system_prompt()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": str(scenario.get("goal", ""))},
    ]

    called: list[str] = []
    answer = ""
    for _round in range(MAX_ROUNDS):
        response = call_ollama(model, messages, tools)
        message = response.get("message", {})
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            answer = str(message.get("content") or "")
            break
        for call in tool_calls:
            function = call.get("function", {})
            name = str(function.get("name", ""))
            raw_args = function.get("arguments") or {}
            if isinstance(raw_args, str):
                try:
                    raw_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    raw_args = {}
            called.append(name)
            output = await run_tool(name, raw_args if isinstance(raw_args, dict) else {})
            messages.append({"role": "assistant", "content": message.get("content") or ""})
            messages.append({"role": "tool", "content": output, "name": name})
    else:
        answer = str(response.get("message", {}).get("content") or "")

    problems = check_scenario(scenario, called, answer)
    return {
        "id": scenario.get("id", ""),
        "passed": not problems,
        "tools_called": called,
        "answer": answer,
        "problems": problems,
    }


def check_scenario(scenario: dict[str, Any], called: list[str], answer: str) -> list[str]:
    """Check the outcome of one scenario against its contract.

    Args:
        scenario: The scenario contract.
        called: The tools the model called.
        answer: The final answer of the model.

    Returns:
        The list of problems (empty when the scenario passed).
    """
    problems: list[str] = []
    expected_tools = scenario.get("expect_tools") or []
    for tool in expected_tools:
        if tool not in called:
            problems.append(f"ожидался вызов {tool}, вызваны: {called}")
    forbidden = scenario.get("forbid_tools") or []
    for tool in forbidden:
        if tool in called:
            problems.append(f"запрещённый вызов {tool}")
    lower = answer.lower()
    for needle in scenario.get("must_contain") or []:
        if str(needle).lower() not in lower:
            problems.append(f"в ответе нет «{needle}»")
    for needle in scenario.get("must_not_contain") or []:
        if str(needle).lower() in lower:
            problems.append(f"в ответе есть «{needle}»")
    return problems


async def run_all(model: str) -> list[dict[str, Any]]:
    """Run every scenario.

    Args:
        model: Model name.

    Returns:
        One verdict per scenario.
    """
    tools = await tool_schemas()
    results: list[dict[str, Any]] = []
    for scenario in load_scenarios():
        results.append(await run_scenario(scenario, model, tools))
    return results


def main(argv: list[str]) -> int:
    """Run the agentic eval and print the verdicts.

    Args:
        argv: The command line arguments.

    Returns:
        0 when every scenario passed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--report", type=Path, default=None, help="write the verdicts as JSON")
    args = parser.parse_args(argv)

    try:
        results = asyncio.run(run_all(args.model))
    except RuntimeError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2

    passed = 0
    for index, result in enumerate(results, start=1):
        mark = "✓" if result["passed"] else "✗"
        if result["passed"]:
            passed += 1
        print(f"{mark} [{index:2d}/{len(results)}] {result['id']}")
        if result["tools_called"]:
            print(f"      тулы: {', '.join(result['tools_called'])}")
        for problem in result["problems"]:
            print(f"      · {problem}")

    print()
    print("─" * 60)
    print(f"Сценариев: {len(results)}  прошло: {passed}")

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps({"results": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Отчёт: {args.report}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
