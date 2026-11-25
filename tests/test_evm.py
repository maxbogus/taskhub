"""Tests for the EVM core (ported from vkrMgimo/tests/test_evm.py)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from taskhub.services.evm import EVMCalculator, EVMMetrics


@pytest.fixture
def calc() -> EVMCalculator:
    return EVMCalculator()


class TestCostPerformanceIndex:
    def test_unity_when_earned_equals_actual(self, calc: EVMCalculator) -> None:
        assert calc.cost_performance_index(1000, 1000) == Decimal("1.0000")

    def test_above_unity_when_earned_exceeds_actual(self, calc: EVMCalculator) -> None:
        assert calc.cost_performance_index(1200, 1000) == Decimal("1.2000")

    def test_below_unity_when_actual_exceeds_earned(self, calc: EVMCalculator) -> None:
        assert calc.cost_performance_index(800, 1000) == Decimal("0.8000")

    def test_none_when_actual_cost_is_zero(self, calc: EVMCalculator) -> None:
        assert calc.cost_performance_index(1000, 0) is None

    def test_zero_when_earned_value_is_zero(self, calc: EVMCalculator) -> None:
        assert calc.cost_performance_index(0, 1000) == Decimal("0.0000")

    def test_result_is_decimal(self, calc: EVMCalculator) -> None:
        assert isinstance(calc.cost_performance_index(1000, 900), Decimal)


class TestSchedulePerformanceIndex:
    def test_unity_when_earned_equals_planned(self, calc: EVMCalculator) -> None:
        assert calc.schedule_performance_index(800, 800) == Decimal("1.0000")

    def test_none_when_planned_value_is_zero(self, calc: EVMCalculator) -> None:
        assert calc.schedule_performance_index(800, 0) is None


class TestEstimateAtCompletion:
    def test_eac_is_actual_plus_etc(self, calc: EVMCalculator) -> None:
        assert calc.estimate_at_completion(400, 600) == Decimal("1000.00")

    def test_eac_from_cpi(self, calc: EVMCalculator) -> None:
        assert calc.estimate_at_completion_from_cpi(1000, Decimal("0.8")) == Decimal("1250.00")

    def test_eac_from_cpi_none_when_cpi_undefined(self, calc: EVMCalculator) -> None:
        assert calc.estimate_at_completion_from_cpi(1000, None) is None

    def test_eac_from_cpi_none_when_cpi_is_zero(self, calc: EVMCalculator) -> None:
        assert calc.estimate_at_completion_from_cpi(1000, 0) is None

    def test_eac_from_cpi_none_when_budget_is_zero(self, calc: EVMCalculator) -> None:
        assert calc.estimate_at_completion_from_cpi(0, Decimal("0.8")) is None


class TestEstimateToComplete:
    def test_etc_is_eac_minus_actual(self, calc: EVMCalculator) -> None:
        assert calc.estimate_to_complete(1000, 400) == Decimal("600.00")

    def test_etc_is_zero_when_work_is_complete(self, calc: EVMCalculator) -> None:
        assert calc.estimate_to_complete(1000, 1000) == Decimal("0.00")

    def test_etc_is_negative_when_actual_exceeds_eac(self, calc: EVMCalculator) -> None:
        assert calc.estimate_to_complete(900, 1000) == Decimal("-100.00")


class TestVarianceAtCompletion:
    def test_vac_is_budget_minus_eac(self, calc: EVMCalculator) -> None:
        assert calc.variance_at_completion(1000, 1200) == Decimal("-200.00")

    def test_vac_positive_when_forecast_below_budget(self, calc: EVMCalculator) -> None:
        assert calc.variance_at_completion(1000, 900) == Decimal("100.00")


class TestEvaluate:
    def test_returns_consistent_snapshot(self, calc: EVMCalculator) -> None:
        result = calc.evaluate(earned_value=800, actual_cost=1000, budget_at_completion=2000)
        assert isinstance(result, EVMMetrics)
        assert result.cpi == Decimal("0.8000")
        assert result.eac == Decimal("2500.00")
        assert result.etc == Decimal("1500.00")
        assert result.vac == Decimal("-500.00")

    def test_snapshot_with_undefined_cpi(self, calc: EVMCalculator) -> None:
        result = calc.evaluate(earned_value=0, actual_cost=0, budget_at_completion=2000)
        assert result.cpi is None
        assert result.eac == Decimal("2000.00")
        assert result.etc == Decimal("2000.00")

    def test_snapshot_includes_spi(self, calc: EVMCalculator) -> None:
        result = calc.evaluate(
            earned_value=800,
            actual_cost=1000,
            budget_at_completion=2000,
            planned_value=1000,
        )
        assert result.spi == Decimal("0.8000")
