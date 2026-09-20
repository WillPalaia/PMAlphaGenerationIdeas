from pm_alpha.models import MarketSnapshot
from pm_alpha.report import inspect_database
from pm_alpha.storage import SnapshotStore
import pytest


def test_database_report_summarizes_coverage(tmp_path):
    store = SnapshotStore(tmp_path / "data.sqlite")
    store.append_many(
        [
            MarketSnapshot(100, "kalshi", "m1", 0.40, 0.50, 2, 3),
            MarketSnapshot(200, "kalshi", "m1", None, None, 0, 0),
        ]
    )
    report = inspect_database(tmp_path / "data.sqlite")
    assert len(report) == 1
    assert report[0].snapshots == 2
    assert report[0].quote_coverage == 0.5
    assert report[0].average_spread == pytest.approx(0.1)
