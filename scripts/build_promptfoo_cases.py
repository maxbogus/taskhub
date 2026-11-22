#!/usr/bin/env python3
"""Convert the MCP contract cases into a promptfoo test file.

``eval/cases/tool_contract.jsonl`` is the single source of truth: the
pytest contract (``tests/test_eval_contract.py``) reads it directly.
Promptfoo, however, expects each test case to carry a ``vars`` block and
treats unknown top-level keys as an error, so the cases are converted
here into the shape promptfoo understands.

The whole contract travels in one JSON string (``vars.contract``)
because promptfoo flattens arrays in ``vars`` — the same reason the
donor harnesses (``school21intensive3``, ``architectureKata``) keep their
contract in a single string.

Usage:
    python scripts/build_promptfoo_cases.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "eval" / "cases" / "tool_contract.jsonl"
TARGET = ROOT / "eval" / "cases" / "tool_contract.promptfoo.jsonl"


def convert(case: dict[str, Any]) -> dict[str, Any]:
    """Convert one contract case into a promptfoo test case.

    Args:
        case: The contract case.

    Returns:
        The promptfoo test case.
    """
    return {
        "description": case.get("description", case.get("id", "")),
        "vars": {
            "case_id": str(case.get("id", "")),
            "contract": json.dumps(case, ensure_ascii=False),
        },
    }


def main(argv: list[str]) -> int:
    """Write the promptfoo test file.

    Args:
        argv: Unused.

    Returns:
        0 on success, 1 when the source file is missing.
    """
    if not SOURCE.is_file():
        print(f"source not found: {SOURCE}", file=sys.stderr)
        return 1
    cases = [
        json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    lines = [json.dumps(convert(case), ensure_ascii=False) for case in cases]
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} cases to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
