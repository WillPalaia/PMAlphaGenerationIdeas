from pm_alpha.evaluation import walk_forward
from pm_alpha.models import MarketSnapshot


def test_walk_forward_is_chronological_and_non_overlapping():
    snapshots = [
        MarketSnapshot(i, "kalshi", "m1", 0.4, 0.5, 1, 1)
        for i in range(8)
    ]
    windows = walk_forward(snapshots, train_size=3, test_size=2)
    assert len(windows) == 2
    assert windows[0].train[-1].timestamp_ms < windows[0].test[0].timestamp_ms
    assert windows[0].test[-1].timestamp_ms < windows[1].test[0].timestamp_ms
