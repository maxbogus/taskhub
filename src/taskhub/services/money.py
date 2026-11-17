"""Decimal helpers for money-like values.

Ported from ``vkrMgimo/calc/money.py``. The rule stays the same: no floats
for monetary or EVM arithmetic, because accumulated rounding changes the
decision, not just the last digit.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

MONEY_QUANTUM: Final[Decimal] = Decimal("0.01")
MONEY_ROUNDING: Final[str] = ROUND_HALF_EVEN
ZERO: Final[Decimal] = Decimal("0.00")


def to_decimal(value: Decimal | float | int | str) -> Decimal:
    """Convert to Decimal without importing binary float error."""

    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool is not a valid money value")
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(f"unsupported money value type: {type(value).__name__}")


def quantize_money(value: Decimal | float | int | str) -> Decimal:
    """Round a monetary value to kopecks (bankers' rounding)."""

    return to_decimal(value).quantize(MONEY_QUANTUM, rounding=MONEY_ROUNDING)


def safe_divide(
    numerator: Decimal | float | int | str,
    denominator: Decimal | float | int | str,
) -> Decimal:
    """Divide, returning ZERO for a zero denominator."""

    den = to_decimal(denominator)
    if den == 0:
        return ZERO
    return to_decimal(numerator) / den


def round_to(value: Decimal | float | int | str, digits: int = 2) -> Decimal:
    """Round to ``digits`` decimal places."""

    exponent = Decimal(1).scaleb(-digits)
    return to_decimal(value).quantize(exponent, rounding=MONEY_ROUNDING)
