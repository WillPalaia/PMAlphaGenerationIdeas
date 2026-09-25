from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from .backtest import BacktestConfig, EventDrivenBacktester, Strategy
from .metrics import summarize
from .models import MarketSnapshot
from .strategies import (
    BollingerReversionStrategy,
    BuyBelowThreshold,
    ComplementArbitrageStrategy,
    EmaCrossoverStrategy,
    FavoriteYieldStrategy,
    JumpFollowingStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    OrderBookImbalanceStrategy,
    RangeBreakoutStrategy,
    SpreadHarvestingMarketMaker,
    StableHighProbabilityStrategy,
    TimeDecayYieldStrategy,
    VwapPullbackStrategy,
)


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
    include_advanced: bool = False,
) -> tuple[SweepRow, ...]:
    """Run predeclared baseline grids independently on each market."""

    factories: list[tuple[str, str, Callable[[], Strategy]]] = []
    for threshold in (0.10, 0.25, 0.50, 0.75, 0.90):
        factories.append(("threshold", json.dumps({"threshold": threshold}), lambda t=threshold: BuyBelowThreshold(t)))
    for move in (0.02, 0.05, 0.10):
        factories.append(("momentum", json.dumps({"minimum_move": move}), lambda m=move: MomentumStrategy(minimum_move=m)))
    for deviation in (0.03, 0.05, 0.10):
        factories.append(("mean_reversion", json.dumps({"deviation": deviation}), lambda d=deviation: MeanReversionStrategy(deviation=d)))
    for band in ((0.65, 0.75), (0.68, 0.72), (0.70, 0.80)):
        factories.append(
            (
                "stable_high_probability",
                json.dumps({"lower_price": band[0], "upper_price": band[1]}),
                lambda lower=band[0], upper=band[1]: StableHighProbabilityStrategy(
                    lower_price=lower, upper_price=upper
                ),
            )
        )

    if include_advanced:
        for min_p, max_p in ((0.80, 0.95), (0.85, 0.96)):
            factories.append(
                (
                    "favorite_yield",
                    json.dumps({"min_prob": min_p, "max_prob": max_p}),
                    lambda mi=min_p, ma=max_p: FavoriteYieldStrategy(min_probability=mi, max_probability=ma),
                )
            )
        for threshold in (0.40, 0.60):
            factories.append(
                (
                    "orderbook_imbalance",
                    json.dumps({"threshold": threshold}),
                    lambda th=threshold: OrderBookImbalanceStrategy(imbalance_threshold=th),
                )
            )
        for z in (-1.5, -2.0):
            factories.append(
                (
                    "bollinger_reversion",
                    json.dumps({"entry_z": z}),
                    lambda ez=z: BollingerReversionStrategy(entry_z=ez),
                )
            )
        for margin in (0.02, 0.04):
            factories.append(
                (
                    "range_breakout",
                    json.dumps({"margin": margin}),
                    lambda mg=margin: RangeBreakoutStrategy(breakout_margin=mg),
                )
            )
        for min_diff in (0.01, 0.02):
            factories.append(
                (
                    "ema_crossover",
                    json.dumps({"min_cross_diff": min_diff}),
                    lambda md=min_diff: EmaCrossoverStrategy(min_cross_diff=md),
                )
            )
        factories.append(
            (
                "spread_harvesting",
                json.dumps({"min_spread": 0.04}),
                lambda: SpreadHarvestingMarketMaker(min_spread=0.04),
            )
        )
        factories.append(
            (
                "complement_arbitrage",
                "{}",
                lambda: ComplementArbitrageStrategy(),
            )
        )
        factories.append(
            (
                "vwap_pullback",
                json.dumps({"pullback": 0.02}),
                lambda: VwapPullbackStrategy(pullback_threshold=0.02),
            )
        )
        for jump in (0.05, 0.08):
            factories.append(
                (
                    "jump_following",
                    json.dumps({"jump": jump}),
                    lambda j=jump: JumpFollowingStrategy(jump_threshold=j),
                )
            )
        factories.append(
            (
                "time_decay_yield",
                json.dumps({"min_p": 0.80, "max_p": 0.95}),
                lambda: TimeDecayYieldStrategy(target_min_price=0.80, target_max_price=0.95),
            )
        )

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
