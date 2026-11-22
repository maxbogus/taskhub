"""Declarative model price list, loaded from YAML.

All model prices live in ``prices.yaml``; this module only reads and
interprets that file. Override it with ``TASKHUB_PRICES_FILE``.

Prices are published per 1M tokens, but stored as **micro-USD**
(1 USD = 1 000 000) so that every amount stays an integer: the project
forbids floats in monetary arithmetic (``services/money.py``) and SQLite
does not store ``Decimal`` natively. Conversion happens once, at load
time, and the resulting integer is exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from taskhub.config import get_settings

PACKAGE = "taskhub"
DEFAULT_CONFIG_NAME = "prices.yaml"

#: Micro-USD in one USD. Prices are stored as integers in these units.
MICRO_USD_PER_USD = 1_000_000

#: Tokens in the unit the vendors publish their prices in.
TOKENS_PER_MTOK = 1_000_000


def to_micro_usd(usd_per_mtok: Decimal | float | int | str) -> int:
    """Convert a price per 1M tokens to micro-USD per 1M tokens.

    The conversion is exact: the input is read as a decimal string, never
    as a binary float, so ``0.1`` becomes ``100_000`` and not
    ``99_999``.

    Args:
        usd_per_mtok: Price in USD per 1M tokens.

    Returns:
        The price in micro-USD per 1M tokens.

    Raises:
        ValueError: The value is negative or not a number.
    """
    try:
        amount = Decimal(str(usd_per_mtok))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid price: {usd_per_mtok!r}") from exc
    if amount < 0:
        raise ValueError(f"price must not be negative: {usd_per_mtok!r}")
    return int(amount * MICRO_USD_PER_USD)


def to_micro_usd_per_token(usd_per_mtok: Decimal | float | int | str) -> int:
    """Convert a price per 1M tokens to micro-USD per single token.

    Used by the estimator, which multiplies token counts. The result is
    rounded to the nearest micro-USD so that a single token always has a
    non-zero price instead of being rounded away.

    Args:
        usd_per_mtok: Price in USD per 1M tokens.

    Returns:
        The price in micro-USD per token (rounded, at least 1 if non-zero).
    """
    micro_per_mtok = to_micro_usd(usd_per_mtok)
    per_token = micro_per_mtok / TOKENS_PER_MTOK
    if per_token == 0:
        return 0
    return max(1, round(per_token))


@dataclass(frozen=True)
class PriceSpec:
    """One model price: a glob pattern plus its rates and validity window."""

    pattern: str
    provider: str | None
    currency: str
    input_usd_micro: int
    output_usd_micro: int
    cache_read_usd_micro: int
    cache_write_usd_micro: int
    valid_from: datetime
    valid_to: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view with human-readable prices."""
        return {
            "pattern": self.pattern,
            "provider": self.provider,
            "currency": self.currency,
            "input_usd_per_mtok": self.input_usd_micro / MICRO_USD_PER_USD,
            "output_usd_per_mtok": self.output_usd_micro / MICRO_USD_PER_USD,
            "cache_read_usd_per_mtok": self.cache_read_usd_micro / MICRO_USD_PER_USD,
            "cache_write_usd_per_mtok": self.cache_write_usd_micro / MICRO_USD_PER_USD,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
        }


class PriceDefaults(BaseModel):
    """Defaults shared by every price row."""

    currency: str = "USD"
    unit: str = "usd_per_mtok"


class PriceRow(BaseModel):
    """One raw price row as written in ``prices.yaml``."""

    pattern: str
    provider: str | None = None
    currency: str | None = None
    input_usd_per_mtok: Decimal = Decimal("0")
    output_usd_per_mtok: Decimal = Decimal("0")
    cache_read_usd_per_mtok: Decimal = Decimal("0")
    cache_write_usd_per_mtok: Decimal = Decimal("0")
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("pattern")
    @classmethod
    def _check_pattern(cls, value: str) -> str:
        """Reject blank globs — an empty pattern would match every model."""
        if not value.strip():
            raise ValueError("price pattern must not be empty")
        return value.strip()

    @field_validator(
        "input_usd_per_mtok",
        "output_usd_per_mtok",
        "cache_read_usd_per_mtok",
        "cache_write_usd_per_mtok",
    )
    @classmethod
    def _check_non_negative(cls, value: Decimal) -> Decimal:
        """Reject negative rates — they would invert the cost."""
        if value < 0:
            raise ValueError(f"price must not be negative: {value}")
        return value


class PricesConfig(BaseModel):
    """The full parsed price configuration."""

    defaults: PriceDefaults = Field(default_factory=PriceDefaults)
    prices: list[PriceRow] = Field(default_factory=list)

    def to_specs(self) -> list[PriceSpec]:
        """Convert raw rows into specs with micro-USD rates.

        A row without ``valid_from`` becomes valid from the beginning of
        time, so a price added without a date still applies to metrics
        recorded earlier.

        Returns:
            The price specs in file order.
        """
        specs: list[PriceSpec] = []
        for row in self.prices:
            currency = row.currency or self.defaults.currency
            specs.append(
                PriceSpec(
                    pattern=row.pattern,
                    provider=row.provider,
                    currency=currency,
                    input_usd_micro=to_micro_usd(row.input_usd_per_mtok),
                    output_usd_micro=to_micro_usd(row.output_usd_per_mtok),
                    cache_read_usd_micro=to_micro_usd(row.cache_read_usd_per_mtok),
                    cache_write_usd_micro=to_micro_usd(row.cache_write_usd_per_mtok),
                    valid_from=row.valid_from or datetime(1970, 1, 1),
                    valid_to=row.valid_to,
                )
            )
        return specs


def default_config_path() -> Path:
    """Path of the bundled ``prices.yaml``."""
    bundled = resources.files(PACKAGE).joinpath(DEFAULT_CONFIG_NAME)
    return Path(str(bundled))


@lru_cache(maxsize=8)
def load_prices(path: str | None = None) -> PricesConfig:
    """Load the price config; ``path=None`` uses the env override or the bundle.

    Args:
        path: Explicit path to a price file.

    Returns:
        The parsed configuration.

    Raises:
        FileNotFoundError: The price file does not exist.
        ValueError: The file is not valid or contains an invalid row.
    """
    if path is None:
        override = get_settings().prices_file
        file_path = Path(override).expanduser() if override else default_config_path()
    else:
        file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise FileNotFoundError(f"prices config not found: {file_path}")

    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"prices config must be a mapping: {file_path}")
    return PricesConfig.model_validate(data)


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO timestamp into a naive UTC datetime.

    The database stores naive UTC timestamps, so prices must use the same
    convention or a price would silently never match.

    Args:
        value: ISO-8601 timestamp.

    Returns:
        The timestamp as a naive datetime.

    Raises:
        ValueError: The value is not a valid timestamp.
    """
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid datetime: {value!r}") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed
