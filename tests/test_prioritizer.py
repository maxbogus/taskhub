"""Tests for RICE prioritisation (pure functions)."""

from __future__ import annotations

from taskhub.services.prioritizer import rice_category, rice_recommendation, rice_score


def test_rice_score_formula() -> None:
    assert rice_score(100, 2, 0.75, 3) == 50.0


def test_rice_score_zero_effort_returns_zero() -> None:
    assert rice_score(100, 2, 0.75, 0) == 0.0
    assert rice_score(100, 2, 0.75, -1) == 0.0


def test_rice_categories() -> None:
    assert rice_category(50) == "HIGH"
    assert rice_category(20) == "MEDIUM"
    assert rice_category(19.99) == "LOW"


def test_rice_recommendations() -> None:
    assert rice_recommendation(50) == "Do first"
    assert rice_recommendation(20) == "Schedule"
    assert rice_recommendation(1) == "Later"
