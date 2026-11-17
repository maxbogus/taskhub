"""Tests for the Monte Carlo forecast."""

from __future__ import annotations

from taskhub.services.forecast import (
    calc_percentiles,
    calc_probability_by_target,
    simulate_duration,
    summarize_forecast,
)


def _run(seed: int = 42) -> dict:
    return simulate_duration(
        sp_total_min=10,
        sp_total_mode=12,
        sp_total_max=20,
        velocity_per_fte_per_week=5,
        fte_count=1,
        velocity_noise_pct=0.2,
        blocker_lambda=1,
        blocker_days_lost=3,
        sick_day_prob=0.05,
        n_iterations=300,
        random_seed=seed,
    )


def test_simulation_is_deterministic_with_seed() -> None:
    first = _run(seed=42)
    second = _run(seed=42)
    assert list(first["durations_weeks"]) == list(second["durations_weeks"])


def test_percentiles_are_ordered() -> None:
    result = _run()
    percentiles = calc_percentiles(result["durations_weeks"])
    assert percentiles["p10"] <= percentiles["p50"]
    assert percentiles["p50"] <= percentiles["p90"]
    assert percentiles["p90"] <= percentiles["p95"]


def test_probability_is_monotonic_in_target() -> None:
    result = _run()
    targets = [4, 8, 12, 16, 24]
    probabilities = calc_probability_by_target(result["durations_weeks"], targets)
    values = [probabilities[target] for target in targets]
    assert values == sorted(values)


def test_summary_has_json_safe_target_keys() -> None:
    summary = summarize_forecast(_run(), [4, 8, 12])
    assert set(summary["probability_by_target"]) == {"4", "8", "12"}
    assert 0.0 <= summary["pct_completed"] <= 1.0
    assert summary["n_iterations"] == 300
