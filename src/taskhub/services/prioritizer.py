"""RICE prioritisation, ported from mcp-servers/unified task_prioritize_tasks."""

from __future__ import annotations


def rice_score(
    reach: float,
    impact: float,
    confidence: float,
    effort: float,
) -> float:
    """Return RICE = (Reach x Impact x Confidence) / Effort.

    Effort is expressed in hours. Zero or negative effort yields 0.0 instead
    of a division error, matching the donor implementation.
    """

    if effort <= 0:
        return 0.0
    return round((reach * impact * confidence) / effort, 2)


def rice_category(score: float) -> str:
    if score >= 50:
        return "HIGH"
    if score >= 20:
        return "MEDIUM"
    return "LOW"


def rice_recommendation(score: float) -> str:
    if score >= 50:
        return "Do first"
    if score >= 20:
        return "Schedule"
    return "Later"
