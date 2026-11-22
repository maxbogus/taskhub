#!/usr/bin/env python3
"""Deterministic promptfoo provider for the MCP contract cases (M11).

The provider answers from a fixed table instead of calling the server,
so the replay is reproducible and needs neither a model nor a database.
It is the CI-safe counterpart of the real MCP run
(``eval/promptfooconfig.yaml``).

The answer is the verdict of the case contract, not the tool output: the
mock exists to prove that the case set is executable and self-consistent.
The real behaviour is checked by the pytest contract
(``tests/test_eval_contract.py``) and by the real MCP run.

Usage:
    exec:python3 ../scripts/mock_eval_provider.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CASES_FILE = Path(__file__).resolve().parent.parent / "eval" / "cases" / "tool_contract.jsonl"


def load_cases() -> dict[str, dict[str, Any]]:
    """Load the contract cases by their identifier."""
    if not CASES_FILE.is_file():
        return {}
    cases: dict[str, dict[str, Any]] = {}
    for line in CASES_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        cases[str(case.get("id", ""))] = case
    return cases


def build_answer(case: dict[str, Any]) -> str:
    """Build the answer for one case.

    Args:
        case: The case contract.

    Returns:
        The verdict string the promptfoo assertions check.
    """
    expect = case.get("expect") or {}
    status = expect.get("status", "ok")
    if status == "error":
        needle = expect.get("error_contains", "")
        return f"PASS expected error containing: {needle}"
    return "PASS contract satisfied"


def _context_vars(raw: str) -> dict[str, Any]:
    """Extract the case variables from the promptfoo context.

    Args:
        raw: The JSON-encoded context.

    Returns:
        The variables of the case (empty when they cannot be read).
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    block = parsed.get("vars")
    if not isinstance(block, dict):
        return {}
    return block


def main(argv: list[str]) -> int:
    """Entry point of the exec provider.

    Args:
        argv: The promptfoo arguments (prompt, options, context).

    Returns:
        Always 0 — the verdict is printed on stdout.
    """
    context = argv[3] if len(argv) > 3 else "{}"
    variables = _context_vars(context)
    case_id = str(variables.get("case_id") or variables.get("id") or "")
    cases = load_cases()
    case = cases.get(case_id)
    if case is None:
        print(f"FAIL unknown case: {case_id or '<empty>'}")
        return 0
    print(build_answer(case))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
