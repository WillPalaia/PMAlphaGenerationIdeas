from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from .backtest import BacktestConfig, EventDrivenBacktester, Strategy
from .metrics import summarize
from .models import MarketSnapshot
from .strategies import BuyBelowThreshold, MeanReversionStrategy, MomentumStrategy


@dataclass(frozen=True)
class SweepRow:
    market_id: str
    strategy: str
    parameters: str
    starting_cash: float
    ending_equity: float
    return_fraction: float
    fees: float
    filled_quantity: float
    rejected_orders: int
    unresolved: bool


def run_sweep(
    markets: dict[str, Iterable[MarketSnapshot]],
    *,
    fee_rates: Iterable[float] = (0.0, 0.01, 0.02),
    starting_cash: float = 1_000.0,
) -> tuple[SweepRow, ...]:
    """Run predeclared baseline grids independently on each market."""

    factories: list[tuple[str, str, Callable[[], Strategy]]] = []
    for threshold in (0.10, 0.25, 0.50, 0.75, 0.90):
        factories.append(("threshold", json.dumps({"threshold": threshold}), lambda t=threshold: BuyBelowThreshold(t)))
    for move in (0.02, 0.05, 0.10):
        factories.append(("momentum", json.dumps({"minimum_move": move}), lambda m=move: MomentumStrategy(minimum_move=m)))
    for deviation in (0.03, 0.05, 0.10):
        factories.append(("mean_reversion", json.dumps({"deviation": deviation}), lambda d=deviation: MeanReversionStrategy(deviation=d)))

    rows: list[SweepRow] = []
    for market_id, raw_snapshots in markets.items():
        snapshots = tuple(raw_snapshots)
        for fee_rate in fee_rates:
            for name, parameters, factory in factories:
                result = EventDrivenBacktester(
                    BacktestConfig(starting_cash=starting_cash, taker_fee_rate=fee_rate)
                ).run(snapshots, factory())
                metrics = summarize(result)
                rows.append(
                    SweepRow(
                        market_id=f"{market_id}@fee={fee_rate:g}",
                        strategy=name,
                        parameters=parameters,
                        starting_cash=starting_cash,
                        ending_equity=result.ending_equity,
                        return_fraction=metrics.return_fraction,
                        fees=result.fees,
                        filled_quantity=result.filled_quantity,
                        rejected_orders=result.rejected_orders,
                        unresolved=metrics.has_unresolved_inventory,
                    )
                )
    return tuple(rows)


def write_csv(rows: Iterable[SweepRow], path: str | Path) -> None:
    rows = tuple(rows)
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0])) if rows else list(SweepRow.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
