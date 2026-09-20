from __future__ import annotations

from dataclasses import dataclass

from .models import BacktestResult


@dataclass(frozen=True)
class BacktestMetrics:
    return_fraction: float
    fee_fraction: float
    fill_rate: float
    has_unresolved_inventory: bool


def summarize(result: BacktestResult) -> BacktestMetrics:
    return BacktestMetrics(
        return_fraction=(result.ending_equity - result.starting_cash) / result.starting_cash,
        fee_fraction=result.fees / result.starting_cash,
        fill_rate=(
            result.filled_quantity / (result.filled_quantity + result.rejected_orders)
            if result.filled_quantity + result.rejected_orders
            else 0.0
        ),
        has_unresolved_inventory=bool(result.unresolved_markets),
    )
