"""Transform for promptfoo: turn a contract case into an MCP tool call.

The MCP provider of promptfoo expects the prompt to be a JSON payload
with a tool name and its arguments::

    {"tool": "economics_summary", "args": {"project_slug": "demo"}}

The case file keeps the contract in a nested ``expect`` block, so this
transform flattens the case into the two variables the provider needs:
``tool_call`` (the JSON payload) and ``expect`` (the contract to check).

The same file also backs the deterministic replay
(``scripts/mock_eval_provider.py``), so the case set stays the single
source of truth for both layers.
"""

from __future__ import annotations

import json
from typing import Any


def _case(vars_block: dict[str, Any]) -> dict[str, Any]:
    """Return the case contract from the test variables.

    The contract travels as one JSON string in ``vars.contract``: promptfoo
    flattens arrays inside ``vars``, so a structured contract would lose
    its lists.

    Args:
        vars_block: The variables of the test case.

    Returns:
        The case contract (empty when it cannot be read).
    """
    raw = vars_block.get("contract")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def transform_vars(vars_block: dict[str, Any]) -> dict[str, Any]:
    """Build the prompt variables for one case.

    Args:
        vars_block: The variables of the test case as promptfoo sees them.

    Returns:
        The variables to substitute into the prompt.
    """
    case = _case(vars_block)
    if not case:
        return {}

    tool = str(case.get("tool", ""))
    args = case.get("args") or {}
    expect = case.get("expect") or {}

    result: dict[str, Any] = {
        "tool_call": json.dumps({"tool": tool, "args": args}, ensure_ascii=False),
        "expect": json.dumps(expect, ensure_ascii=False),
    }
    if case.get("id"):
        result["case_id"] = str(case["id"])
    if case.get("description"):
        result["description"] = str(case["description"])
    return result


def get_transform(vars_block: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Entry point for promptfoo (``transformVars``).

    Args:
        vars_block: The variables of the test case.
        context: The promptfoo context (unused).

    Returns:
        The variables to substitute into the prompt.
    """
    return transform_vars(vars_block)


if __name__ == "__main__":
    print(json.dumps(transform_vars({}), ensure_ascii=False))
