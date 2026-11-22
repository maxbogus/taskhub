"""Tests for the declarative model price list (M11)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from taskhub.prices import (
    MICRO_USD_PER_USD,
    load_prices,
    to_micro_usd,
)


def _write_prices(tmp_path: Path, body: str) -> Path:
    """Write a prices.yaml body and return its path."""
    path = tmp_path / "prices.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_to_micro_usd_is_exact() -> None:
    """USD per 1M tokens converts to micro-USD without float drift."""
    assert to_micro_usd(3.0) == 3 * MICRO_USD_PER_USD
    assert to_micro_usd(Decimal("0.3")) == 300_000
    assert to_micro_usd(0) == 0


def test_load_prices_reads_patterns_and_rates(tmp_path: Path) -> None:
    """A price row becomes a spec with micro-USD rates."""
    path = _write_prices(
        tmp_path,
        """
prices:
  - pattern: "claude-*"
    provider: anthropic
    input_usd_per_mtok: 3.0
    output_usd_per_mtok: 15.0
    cache_read_usd_per_mtok: 0.3
    cache_write_usd_per_mtok: 3.75
    valid_from: "2026-01-01T00:00:00"
""",
    )

    config = load_prices(str(path))
    specs = config.to_specs()

    assert len(specs) == 1
    spec = specs[0]
    assert spec.pattern == "claude-*"
    assert spec.provider == "anthropic"
    assert spec.currency == "USD"
    assert spec.input_usd_micro == 3_000_000
    assert spec.output_usd_micro == 15_000_000
    assert spec.cache_read_usd_micro == 300_000
    assert spec.cache_write_usd_micro == 3_750_000
    assert spec.valid_from == datetime(2026, 1, 1)


def test_defaults_apply_currency_to_every_row(tmp_path: Path) -> None:
    """``defaults.currency`` fills rows that do not set their own."""
    path = _write_prices(
        tmp_path,
        """
defaults:
  currency: EUR
prices:
  - pattern: "a-*"
""",
    )

    config = load_prices(str(path))

    assert config.to_specs()[0].currency == "EUR"


def test_missing_file_raises(tmp_path: Path) -> None:
    """A missing price file fails loudly instead of silently pricing nothing."""
    with pytest.raises(FileNotFoundError):
        load_prices(str(tmp_path / "absent.yaml"))


def test_blank_pattern_is_rejected(tmp_path: Path) -> None:
    """An empty glob would match everything — it must not be accepted."""
    path = _write_prices(
        tmp_path,
        """
prices:
  - pattern: "   "
""",
    )

    with pytest.raises(ValueError, match="pattern"):
        load_prices(str(path))


def test_negative_price_is_rejected(tmp_path: Path) -> None:
    """Negative rates would invert the cost."""
    path = _write_prices(
        tmp_path,
        """
prices:
  - pattern: "a-*"
    input_usd_per_mtok: -1.0
""",
    )

    with pytest.raises(ValueError):
        load_prices(str(path))


def test_env_override_selects_file(monkeypatch, tmp_path: Path) -> None:
    """TASKHUB_PRICES_FILE wins over the bundled list."""
    path = _write_prices(
        tmp_path,
        """
prices:
  - pattern: "env-*"
    input_usd_per_mtok: 1.0
""",
    )
    load_prices.cache_clear()
    monkeypatch.setenv("TASKHUB_PRICES_FILE", str(path))

    config = load_prices()

    assert config.to_specs()[0].pattern == "env-*"
    load_prices.cache_clear()


def test_bundled_prices_load() -> None:
    """The bundled list is valid and carries no negative rates."""
    load_prices.cache_clear()
    config = load_prices()
    specs = config.to_specs()

    assert specs
    assert all(spec.input_usd_micro >= 0 for spec in specs)
    assert all(spec.output_usd_micro >= 0 for spec in specs)
    assert all(spec.pattern.strip() for spec in specs)
