from pm_alpha.models import MarketSnapshot
from pm_alpha.storage import SnapshotStore


def test_snapshot_store_is_idempotent_and_replayable(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite")
    snapshot = MarketSnapshot(100, "kalshi", "m1", 0.40, 0.45, 2, 3)

    assert store.append(snapshot)
    assert not store.append(snapshot)
    assert store.append_many([MarketSnapshot(200, "kalshi", "m1", 0.41, 0.46, 1, 2)]) == 1
    assert store.load("kalshi", "m1") == [snapshot, MarketSnapshot(200, "kalshi", "m1", 0.41, 0.46, 1, 2)]
    assert store.load("kalshi", "m1", start_ms=150)[0].timestamp_ms == 200
    assert store.markets() == [("kalshi", "m1")]
