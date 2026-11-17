"""Monte Carlo forecast for project duration.

Ported from ``telemetry/scripts/montecarlo/{simulator,report}.py``. The model
keeps the donor semantics: triangular total effort, gaussian weekly velocity
noise, Poisson blockers and Bernoulli sick-day risk. Results are deterministic
for a fixed seed, which the tests rely on.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray

WORK_DAYS_PER_WEEK = 5
OVERHEAD_DAYS_PER_WEEK = 0.5  # planning, meetings, review
DEFAULT_PERCENTILES = (10, 25, 50, 75, 90, 95)


def simulate_duration(
    sp_total_min: float,
    sp_total_mode: float,
    sp_total_max: float,
    velocity_per_fte_per_week: float,
    fte_count: float,
    velocity_noise_pct: float,
    blocker_lambda: float,
    blocker_days_lost: float,
    sick_day_prob: float,
    max_weeks: int = 52,
    n_iterations: int = 5000,
    random_seed: int | None = None,
) -> dict[str, Any]:
    """Simulate completion duration in working weeks."""

    if random_seed is not None:
        np.random.seed(random_seed)

    sp_totals = np.random.triangular(
        sp_total_min, sp_total_mode, sp_total_max, n_iterations
    )
    team_velocity_per_week = velocity_per_fte_per_week * fte_count

    durations_weeks: list[int] = []
    completed_flags: list[bool] = []

    for i in range(n_iterations):
        target_sp = float(sp_totals[i])
        sp_done = 0.0
        weeks_elapsed = 0

        n_blockers = int(np.random.poisson(blocker_lambda))
        blocker_weeks: set[int] = set()
        if n_blockers > 0 and max_weeks > 0:
            blocker_starts = np.random.randint(0, max(1, max_weeks), n_blockers)
            for start in blocker_starts:
                blocker_weeks.add(int(start))

        while sp_done < target_sp and weeks_elapsed < max_weeks:
            weeks_elapsed += 1
            if weeks_elapsed in blocker_weeks:
                continue

            effective_days = WORK_DAYS_PER_WEEK - OVERHEAD_DAYS_PER_WEEK
            sick_factor = 1.0 - sick_day_prob * 0.5
            effective_days *= sick_factor

            noise = 1.0 + np.random.normal(0, velocity_noise_pct)
            weekly_sp = max(
                0.1,
                team_velocity_per_week
                * noise
                * (effective_days / WORK_DAYS_PER_WEEK),
            )
            sp_done += weekly_sp

        durations_weeks.append(weeks_elapsed)
        completed_flags.append(sp_done >= target_sp)

    durations_arr = np.array(durations_weeks)
    completed_arr = np.array(completed_flags)

    return {
        "durations_weeks": durations_arr,
        "completed": completed_arr,
        "sp_target_mean": float(np.mean(sp_totals)),
        "velocity_per_fte_per_week": velocity_per_fte_per_week,
        "fte_count": fte_count,
        "team_velocity_per_week": team_velocity_per_week,
        "n_iterations": n_iterations,
        "n_completed": int(np.sum(completed_arr)),
        "pct_completed": float(np.mean(completed_arr)),
    }


def calc_probability_by_target(
    durations: NDArray[Any], targets: list[int]
) -> dict[int, float]:
    """Probability of finishing within each target week count."""

    return {target: float(np.mean(durations <= target)) for target in targets}


def calc_percentiles(
    durations: NDArray[Any],
    percentiles: tuple[int, ...] = DEFAULT_PERCENTILES,
) -> dict[str, float]:
    """Percentile statistics for the duration distribution."""

    return {f"p{value}": float(np.percentile(durations, value)) for value in percentiles}


def summarize_forecast(
    result: dict[str, Any],
    targets_weeks: list[int],
) -> dict[str, Any]:
    """Convert a simulation result into a compact, JSON-safe summary."""

    durations = result["durations_weeks"]
    percentiles = calc_percentiles(durations)
    probabilities = calc_probability_by_target(durations, targets_weeks)
    return {
        **percentiles,
        "probability_by_target": {
            str(target): probability for target, probability in probabilities.items()
        },
        "n_iterations": result["n_iterations"],
        "n_completed": result["n_completed"],
        "pct_completed": result["pct_completed"],
        "sp_target_mean": result["sp_target_mean"],
        "team_velocity_per_week": result["team_velocity_per_week"],
    }
