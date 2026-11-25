"""Earned Value Management (EVM) metrics.

Ported from ``vkrMgimo/calc/evm.py`` and extended with SPI, which the source
project computed elsewhere. All arithmetic stays in ``Decimal``.

Definitions used by TaskHub:

* ``EV`` — estimate of completed work (sum of estimates of done items).
* ``AC`` — actual effort in hours (sum of metric ``active_ms`` / 3_600_000).
* ``BAC`` — budget at completion (sum of estimates still open).
* ``PV`` — planned value (sum of estimates whose ``due_at`` has passed).
* ``CPI = EV / AC``, ``SPI = EV / PV``.
* ``EAC = BAC / CPI``, ``ETC = EAC - AC``, ``VAC = BAC - EAC``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from taskhub.services.money import quantize_money, round_to, to_decimal


@dataclass(frozen=True)
class EVMMetrics:
    """EVM snapshot at a reporting date."""

    cpi: Decimal | None
    eac: Decimal
    etc: Decimal
    vac: Decimal
    spi: Decimal | None = None


class EVMCalculator:
    """Stateless EVM calculator."""

    def cost_performance_index(
        self,
        earned_value: Decimal | float | int | str,
        actual_cost: Decimal | float | int | str,
    ) -> Decimal | None:
        """CPI = EV / AC; ``None`` when AC is zero."""

        cost = to_decimal(actual_cost)
        if cost == 0:
            return None
        return round_to(to_decimal(earned_value) / cost, 4)

    def schedule_performance_index(
        self,
        earned_value: Decimal | float | int | str,
        planned_value: Decimal | float | int | str,
    ) -> Decimal | None:
        """SPI = EV / PV; ``None`` when PV is zero."""

        planned = to_decimal(planned_value)
        if planned == 0:
            return None
        return round_to(to_decimal(earned_value) / planned, 4)

    def estimate_at_completion(
        self,
        actual_cost: Decimal | float | int | str,
        estimate_to_complete: Decimal | float | int | str,
    ) -> Decimal:
        """Additive form: EAC = AC + ETC."""

        return quantize_money(to_decimal(actual_cost) + to_decimal(estimate_to_complete))

    def estimate_at_completion_from_cpi(
        self,
        budget_at_completion: Decimal | float | int | str,
        cpi: Decimal | float | int | str | None,
    ) -> Decimal | None:
        """Relative form: EAC = BAC / CPI."""

        budget = to_decimal(budget_at_completion)
        if cpi is None or budget == 0:
            return None
        index = to_decimal(cpi)
        if index == 0:
            return None
        return quantize_money(budget / index)

    def estimate_to_complete(
        self,
        estimate_at_completion: Decimal | float | int | str,
        actual_cost: Decimal | float | int | str,
    ) -> Decimal:
        """ETC = EAC - AC (negative values are a diagnosis, not an error)."""

        return quantize_money(to_decimal(estimate_at_completion) - to_decimal(actual_cost))

    def variance_at_completion(
        self,
        budget_at_completion: Decimal | float | int | str,
        estimate_at_completion: Decimal | float | int | str,
    ) -> Decimal:
        """VAC = BAC - EAC; negative means overrun."""

        return quantize_money(to_decimal(budget_at_completion) - to_decimal(estimate_at_completion))

    def evaluate(
        self,
        earned_value: Decimal | float | int | str,
        actual_cost: Decimal | float | int | str,
        budget_at_completion: Decimal | float | int | str,
        planned_value: Decimal | float | int | str | None = None,
    ) -> EVMMetrics:
        """Build a consistent EVM snapshot."""

        cpi = self.cost_performance_index(earned_value, actual_cost)
        spi = (
            self.schedule_performance_index(earned_value, planned_value)
            if planned_value is not None
            else None
        )
        forecast = self.estimate_at_completion_from_cpi(budget_at_completion, cpi)
        if forecast is None:
            forecast = quantize_money(budget_at_completion)
        return EVMMetrics(
            cpi=cpi,
            eac=forecast,
            etc=self.estimate_to_complete(forecast, actual_cost),
            vac=self.variance_at_completion(budget_at_completion, forecast),
            spi=spi,
        )
