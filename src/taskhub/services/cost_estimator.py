"""Token cost estimation from a model price list (M11).

Pure functions, no database access: the estimator turns token counts and
a price into an exact monetary amount. All arithmetic stays in
``Decimal`` and prices are integers in micro-USD, so a result never
depends on binary float rounding (see ``services/money.py``).

Design rules that the rest of the project relies on:

* An unknown model has **no** price. The estimator returns ``None``
  instead of ``0`` — silently pricing everything at zero would
  understate the cost of the work.
* The reported ``Metric.cost_usd`` is never overwritten; the estimator
  only computes what the price list says the usage was worth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from fnmatch import fnmatch
from typing import Any

from taskhub.prices import MICRO_USD_PER_USD, TOKENS_PER_MTOK, PriceSpec

#: USD per micro-USD.
_MICRO_SCALE = Decimal(MICRO_USD_PER_USD)

#: USD precision kept in every returned amount.
_USD_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class TokenUsage:
    """Token counts of one metric, split by billing category."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        """Total tokens across all billing categories."""
        return (
            self.prompt_tokens
            + self.completion_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def to_dict(self) -> dict[str, int]:
        """Return the token counts as a plain dictionary."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_tokens": self.total_tokens,
        }


def _is_valid_at(price: PriceSpec, at: datetime) -> bool:
    """True when the price was in force at the given moment."""
    if price.valid_from > at:
        return False
    return price.valid_to is None or at < price.valid_to


def _specificity(pattern: str) -> tuple[int, int]:
    """Rank a glob pattern: exact names beat globs, longer patterns win.

    A pattern without wildcard characters is an exact model name and is
    always the most specific match.
    """
    has_wildcard = any(ch in pattern for ch in "*?[")
    return (0 if has_wildcard else 1, len(pattern))


def match_price(
    model: str | None,
    prices: list[PriceSpec],
    at: datetime,
) -> PriceSpec | None:
    """Find the price that applies to a model at a point in time.

    Resolution order:

    1. the pattern must match the model (case-insensitively);
    2. the price must be valid at ``at``;
    3. the most specific pattern wins (exact name over glob, then longer
       pattern);
    4. on a tie, the most recent ``valid_from`` wins.

    Args:
        model: Model name recorded with the metric.
        prices: Candidate prices.
        at: Moment the usage happened.

    Returns:
        The matching price, or ``None`` when the model has no price.
    """
    if not model:
        return None
    name = model.strip().lower()
    candidates = [
        price
        for price in prices
        if fnmatch(name, price.pattern.lower()) and _is_valid_at(price, at)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda price: (_specificity(price.pattern), price.valid_from),
    )


def _scaled(tokens: int, rate_micro_per_mtok: int) -> int:
    """Price ``tokens`` at a per-1M-token rate, in micro-USD.

    The result is rounded to the nearest micro-USD. A non-zero rate on a
    non-zero token count always yields at least one micro-USD, so cheap
    calls are not rounded down to nothing.
    """
    if tokens <= 0 or rate_micro_per_mtok <= 0:
        return 0
    rounded = round(tokens * rate_micro_per_mtok / TOKENS_PER_MTOK)
    return max(1, rounded)


def estimate_token_cost_micro(usage: TokenUsage, price: PriceSpec) -> int:
    """Estimate the cost of token usage in micro-USD.

    Args:
        usage: Token counts per billing category.
        price: Price to apply.

    Returns:
        The estimated cost in micro-USD.
    """
    return (
        _scaled(usage.prompt_tokens, price.input_usd_micro)
        + _scaled(usage.completion_tokens, price.output_usd_micro)
        + _scaled(usage.cache_read_tokens, price.cache_read_usd_micro)
        + _scaled(usage.cache_write_tokens, price.cache_write_usd_micro)
    )


def micro_usd_to_usd(micro_usd: int) -> Decimal:
    """Convert micro-USD to USD exactly.

    Args:
        micro_usd: Amount in micro-USD.

    Returns:
        The amount in USD.
    """
    return (Decimal(micro_usd) / _MICRO_SCALE).quantize(_USD_QUANTUM)


def estimate_token_cost_usd(usage: TokenUsage, price: PriceSpec) -> Decimal:
    """Estimate the cost of token usage in USD.

    Args:
        usage: Token counts per billing category.
        price: Price to apply.

    Returns:
        The estimated cost in USD.
    """
    return micro_usd_to_usd(estimate_token_cost_micro(usage, price))


def human_equivalent_cost_usd(hours: float, rate_usd_per_hour: float) -> Decimal:
    """Cost of the same amount of work done by a human.

    This is a **speed proxy**, not the cost of running an agent: agent
    time is not billed as human time. It answers "what would a person
    have charged for this work?".

    Args:
        hours: Amount of work in hours.
        rate_usd_per_hour: Human hourly rate.

    Returns:
        The human-equivalent cost in USD.
    """
    return (Decimal(str(hours)) * Decimal(str(rate_usd_per_hour))).quantize(_USD_QUANTUM)


def error_cost_usd(failures: int, cost_per_error_usd: float) -> Decimal:
    """Expected loss from failed runs.

    Args:
        failures: Number of failed runs.
        cost_per_error_usd: Penalty per failure.

    Returns:
        The expected loss in USD.
    """
    if failures <= 0:
        return Decimal("0")
    return (Decimal(failures) * Decimal(str(cost_per_error_usd))).quantize(_USD_QUANTUM)


def usage_from_metric(metric: Any) -> TokenUsage:
    """Build token usage from a ``Metric`` row.

    Args:
        metric: A ``Metric`` instance (duck-typed to keep this module
            free of ORM imports).

    Returns:
        The token usage of that metric.
    """
    return TokenUsage(
        prompt_tokens=int(getattr(metric, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(metric, "completion_tokens", 0) or 0),
        cache_read_tokens=int(getattr(metric, "cache_read_tokens", 0) or 0),
        cache_write_tokens=int(getattr(metric, "cache_write_tokens", 0) or 0),
    )
