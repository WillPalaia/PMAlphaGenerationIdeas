from pm_alpha.models import MarketSnapshot
from pm_alpha.sweep import run_sweep


def test_sweep_runs_independent_strategy_grid():
    snapshots = [
        MarketSnapshot(i, "kalshi", "m1", 0.39 + i * 0.01, 0.40 + i * 0.01, 10, 10)
        for i in range(5)
    ]
    rows = run_sweep({"m1": snapshots}, fee_rates=(0.0, 0.01))
    assert len(rows) == 11 * 2
    assert {row.strategy for row in rows} == {"threshold", "momentum", "mean_reversion"}
