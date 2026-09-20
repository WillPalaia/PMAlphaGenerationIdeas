from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .backtest import BacktestConfig, EventDrivenBacktester, Strategy
from .metrics import BacktestMetrics, summarize
from .models import MarketSnapshot


@dataclass(frozen=True)
class ExperimentResult:
    name: str
    metrics: BacktestMetrics


def compare_strategies(
    snapshots: Iterable[MarketSnapshot],
    strategies: dict[str, Callable[[], Strategy]],
    config: BacktestConfig | None = None,
) -> tuple[ExperimentResult, ...]:
    """Run independent strategy instances on identical data and assumptions."""

    data = tuple(snapshots)
    results = []
    for name, factory in strategies.items():
        result = EventDrivenBacktester(config).run(data, factory())
        results.append(ExperimentResult(name, summarize(result)))
    return tuple(results)
