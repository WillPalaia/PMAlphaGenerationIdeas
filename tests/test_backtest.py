import pytest

from pm_alpha.backtest import BacktestConfig, EventDrivenBacktester
from pm_alpha.models import MarketSnapshot, OrderIntent, Side


class BuyAtAsk:
    def on_snapshot(self, snapshot):
        if snapshot.timestamp_ms == 0:
            yield OrderIntent(
                timestamp_ms=0,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=3,
                limit_price=0.60,
                client_order_id="order-1",
                signal="test",
            )


def test_backtester_uses_latency_snapshot_and_partial_depth():
    snapshots = [
        MarketSnapshot(0, "kalshi", "m1", 0.49, 0.50, 2, 2),
        MarketSnapshot(10, "kalshi", "m1", 0.51, 0.55, 1, 1),
    ]
    result = EventDrivenBacktester(
        BacktestConfig(starting_cash=10, taker_fee_rate=0.10, fill_latency_ms=10)
    ).run(snapshots, BuyAtAsk())

    assert result.filled_quantity == 1
    assert result.fees == pytest.approx(0.055)
    assert result.ending_cash == pytest.approx(9.395)
    assert result.rejected_orders == 0


def test_backtester_rejects_order_that_is_not_marketable():
    snapshots = [MarketSnapshot(0, "kalshi", "m1", 0.49, 0.50, 2, 2)]

    class TooCheap:
        def on_snapshot(self, snapshot):
            yield OrderIntent(0, "kalshi", "m1", Side.BUY, 1, 0.49, "order-2")

    result = EventDrivenBacktester().run(snapshots, TooCheap())
    assert result.filled_quantity == 0
    assert result.rejected_orders == 1


def test_backtester_settles_binary_position():
    snapshots = [
        MarketSnapshot(0, "kalshi", "m1", 0.49, 0.50, 2, 2),
        MarketSnapshot(10, "kalshi", "m1", None, None, resolved=True, settlement_yes=True),
    ]
    result = EventDrivenBacktester().run(snapshots, BuyAtAsk())
    assert result.filled_quantity == 2
    assert result.ending_cash == 1001.0
    assert result.ending_equity == 1001.0
    assert result.unresolved_markets == ()


def test_backtester_consumes_snapshot_liquidity_once():
    snapshots = [MarketSnapshot(0, "kalshi", "m1", 0.49, 0.50, 2, 2)]

    class TwoOrders:
        def on_snapshot(self, snapshot):
            for number in range(2):
                yield OrderIntent(0, "kalshi", "m1", Side.BUY, 2, 0.50, f"order-{number}")

    result = EventDrivenBacktester().run(snapshots, TwoOrders())
    assert result.filled_quantity == 2
    assert result.rejected_orders == 1
