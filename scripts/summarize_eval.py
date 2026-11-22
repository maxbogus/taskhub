#!/usr/bin/env python3
"""Summarise a promptfoo report of the MCP contract run.

Reads the JSON report promptfoo writes and prints how many cases passed,
which ones failed and why. Exits non-zero when a case failed, so the
target can be used as a gate.

Usage:
    python scripts/summarize_eval.py docs/evaluation/eval-mcp.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the list of results from a promptfoo report.

    Args:
        payload: The parsed report.

    Returns:
        The result rows (empty when the structure is unexpected).
    """
    results = payload.get("results")
    if isinstance(results, dict):
        items = results.get("results")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    return []


def _case_id(result: dict[str, Any]) -> str:
    """Return the identifier of one result row."""
    variables = result.get("vars")
    if isinstance(variables, dict):
        case_id = variables.get("case_id")
        if case_id:
            return str(case_id)
    description = result.get("description") or result.get("testCase")
    if isinstance(description, dict):
        return str(description.get("description", ""))
    return str(description or "")


def summarize(report_path: Path) -> dict[str, Any]:
    """Compute the summary of a promptfoo report.

    Args:
        report_path: Path to the JSON report.

    Returns:
        The summary counters.

    Raises:
        FileNotFoundError: The report does not exist.
        ValueError: The report holds no results.
    """
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    results = _results(payload)
    if not results:
        raise ValueError(f"no results in report: {report_path}")

    passed = 0
    failed: list[dict[str, str]] = []
    for result in results:
        success = bool(result.get("success"))
        if success:
            passed += 1
            continue
        grading = result.get("gradingResult")
        reason = ""
        if isinstance(grading, dict):
            reason = str(grading.get("reason", ""))
        failed.append(
            {
                "case_id": _case_id(result),
                "reason": reason or str(result.get("error", "") or "unknown"),
            }
        )

    total = len(results)
    return {
        "total": total,
        "passed": passed,
        "failed": len(failed),
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "failures": failed,
    }


def main(argv: list[str]) -> int:
    """Print the summary of a promptfoo report.

    Args:
        argv: The report path.

    Returns:
        0 when every case passed, 1 otherwise.
    """
    if len(argv) < 2:
        print("usage: summarize_eval.py <report.json>", file=sys.stderr)
        return 1
    report_path = Path(argv[1])
    if not report_path.is_file():
        print(f"report not found: {report_path}", file=sys.stderr)
        return 1

    summary = summarize(report_path)
    print(
        f"Кейсов: {summary['total']}  прошло: {summary['passed']}  доля: {summary['pass_rate']:.0%}"
    )
    for failure in summary["failures"]:
        print(f"  ✗ {failure['case_id']}: {failure['reason']}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
