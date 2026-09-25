import pytest

from pm_alpha.models import MarketSnapshot, OrderIntent, Side
from pm_alpha.paper import MultiStrategy, PaperPortfolio
from pm_alpha.storage import SnapshotStore


def test_portfolio_fills_marks_and_settles_persistently(tmp_path):
    store = SnapshotStore(tmp_path / "paper.sqlite")
    portfolio = PaperPortfolio(store, starting_cash=10.0, fee_rate=0.1)
    live = MarketSnapshot(1, "kalshi", "m1", 0.4, 0.5, 2, 3)
    intent = OrderIntent(1, "kalshi", "m1", Side.BUY, 2, 0.5, "buy-1")

    assert portfolio.submit(intent, live)
    assert portfolio.cash == pytest.approx(8.9)
    assert portfolio.positions[("kalshi", "m1")].quantity == 2
    assert portfolio.process_snapshot(live).equity == pytest.approx(9.8)

    resolved = MarketSnapshot(2, "kalshi", "m1", None, None, resolved=True, settlement_yes=True)
    portfolio.process_snapshot(resolved)
    assert portfolio.cash == pytest.approx(10.9)
    assert portfolio.positions[("kalshi", "m1")].quantity == 0
    restored = PaperPortfolio(store, starting_cash=0.0, fee_rate=0.1)
    assert restored.cash == pytest.approx(10.9)


def test_portfolio_rejects_inventory_and_does_not_place_live_orders(tmp_path):
    store = SnapshotStore(tmp_path / "paper.sqlite")
    portfolio = PaperPortfolio(store, max_inventory_per_market=1)
    snapshot = MarketSnapshot(1, "kalshi", "m1", 0.4, 0.5, 1, 1)
    assert portfolio.submit(OrderIntent(1, "kalshi", "m1", Side.BUY, 2, 0.5, "too-big"), snapshot) is True
    assert portfolio.positions[("kalshi", "m1")].quantity == 1
    assert portfolio.submit(OrderIntent(1, "kalshi", "m1", Side.BUY, 1, 0.4, "not-marketable"), snapshot) is False


def test_multi_strategy_combines_intents():
    class Strategy:
        def on_snapshot(self, snapshot):
            return [OrderIntent(snapshot.timestamp_ms, "v", "m", Side.BUY, 1, 0.5, "x")]

    assert len(MultiStrategy({"a": Strategy(), "b": Strategy()}).on_snapshot(
        MarketSnapshot(1, "v", "m", 0.4, 0.5, 1, 1)
    )) == 2
