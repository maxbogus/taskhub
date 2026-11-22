"""Tests for token cost estimation (M11).

All monetary results stay exact: prices are integers in micro-USD and the
arithmetic runs in ``Decimal``, never in binary floats.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from taskhub.prices import PriceSpec
from taskhub.services.cost_estimator import (
    TokenUsage,
    error_cost_usd,
    estimate_token_cost_micro,
    estimate_token_cost_usd,
    human_equivalent_cost_usd,
    match_price,
    micro_usd_to_usd,
)


def _price(
    pattern: str,
    *,
    input_micro: int = 0,
    output_micro: int = 0,
    cache_read_micro: int = 0,
    cache_write_micro: int = 0,
    valid_from: datetime = datetime(2026, 1, 1),
    valid_to: datetime | None = None,
) -> PriceSpec:
    """Build a price spec with micro-USD rates."""
    return PriceSpec(
        pattern=pattern,
        provider=None,
        currency="USD",
        input_usd_micro=input_micro,
        output_usd_micro=output_micro,
        cache_read_usd_micro=cache_read_micro,
        cache_write_usd_micro=cache_write_micro,
        valid_from=valid_from,
        valid_to=valid_to,
    )


def test_match_price_prefers_most_specific_pattern() -> None:
    """A longer glob beats a shorter one for the same model."""
    prices = [
        _price("claude-*", input_micro=3_000_000),
        _price("claude-opus-*", input_micro=15_000_000),
    ]

    matched = match_price("claude-opus-4", prices, datetime(2026, 6, 1))

    assert matched is not None
    assert matched.pattern == "claude-opus-*"


def test_match_price_prefers_exact_name_over_glob() -> None:
    """An exact model name wins over a same-length glob."""
    prices = [
        _price("gpt-*", input_micro=1_000_000),
        _price("gpt-5", input_micro=2_000_000),
    ]

    matched = match_price("gpt-5", prices, datetime(2026, 6, 1))

    assert matched is not None
    assert matched.pattern == "gpt-5"


def test_match_price_prefers_newer_valid_from_on_tie() -> None:
    """Two rows with the same pattern: the newer price wins."""
    prices = [
        _price("claude-*", input_micro=3_000_000, valid_from=datetime(2026, 1, 1)),
        _price("claude-*", input_micro=4_000_000, valid_from=datetime(2026, 5, 1)),
    ]

    matched = match_price("claude-x", prices, datetime(2026, 6, 1))

    assert matched is not None
    assert matched.input_usd_micro == 4_000_000


def test_match_price_respects_validity_window() -> None:
    """A price outside its window does not apply."""
    prices = [
        _price(
            "claude-*",
            input_micro=3_000_000,
            valid_from=datetime(2026, 1, 1),
            valid_to=datetime(2026, 4, 1),
        ),
    ]

    assert match_price("claude-x", prices, datetime(2026, 2, 1)) is not None
    assert match_price("claude-x", prices, datetime(2026, 5, 1)) is None


def test_match_price_returns_none_for_unknown_model() -> None:
    """An unknown model has no price — it must not fall back to zero."""
    prices = [_price("claude-*", input_micro=3_000_000)]

    assert match_price("mystery-model", prices, datetime(2026, 6, 1)) is None


def test_match_price_is_case_insensitive() -> None:
    """Model names arrive in mixed case; pricing must not depend on it."""
    prices = [_price("claude-*", input_micro=3_000_000)]

    matched = match_price("Claude-Opus-4", prices, datetime(2026, 6, 1))

    assert matched is not None
    assert matched.pattern == "claude-*"


def test_estimate_token_cost_uses_separate_rates() -> None:
    """Input, output and cache tokens are billed at their own rates."""
    price = _price(
        "claude-*",
        input_micro=3_000_000,
        output_micro=15_000_000,
        cache_read_micro=300_000,
        cache_write_micro=3_750_000,
    )
    usage = TokenUsage(
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )

    micro = estimate_token_cost_micro(usage, price)

    # 3 + 15 + 0.3 + 3.75 USD
    assert micro == 22_050_000
    assert estimate_token_cost_usd(usage, price) == Decimal("22.05")


def test_estimate_token_cost_is_exact_for_odd_token_counts() -> None:
    """Sub-cent costs must not be rounded away."""
    price = _price("claude-*", input_micro=3_000_000)
    usage = TokenUsage(prompt_tokens=1, completion_tokens=0)

    usd = estimate_token_cost_usd(usage, price)

    assert usd == Decimal("0.000003")


def test_estimate_token_cost_zero_usage_is_zero() -> None:
    """No tokens cost nothing."""
    price = _price("claude-*", input_micro=3_000_000)

    assert estimate_token_cost_micro(TokenUsage(), price) == 0


def test_micro_usd_to_usd_converts_exactly() -> None:
    """Micro-USD convert to USD without float drift."""
    assert micro_usd_to_usd(3_000_000) == Decimal("3")
    assert micro_usd_to_usd(1) == Decimal("0.000001")


def test_human_equivalent_cost_multiplies_hours_by_rate() -> None:
    """The human baseline is hours times the hourly rate."""
    assert human_equivalent_cost_usd(2.5, 50.0) == Decimal("125")


def test_error_cost_multiplies_failures_by_price() -> None:
    """Each failed run costs the configured penalty."""
    assert error_cost_usd(3, 10.0) == Decimal("30")
    assert error_cost_usd(0, 10.0) == Decimal("0")
