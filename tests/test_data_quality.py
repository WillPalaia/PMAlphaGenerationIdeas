import pytest

from pm_alpha.models import MarketSnapshot


def test_snapshot_rejects_crossed_book():
    with pytest.raises(ValueError, match="yes_bid"):
        MarketSnapshot(0, "kalshi", "m1", 0.60, 0.50, 1, 1)


def test_snapshot_rejects_live_quotes_on_resolution():
    with pytest.raises(ValueError, match="resolved"):
        MarketSnapshot(0, "kalshi", "m1", 0.40, None, 1, 0, True, True)
