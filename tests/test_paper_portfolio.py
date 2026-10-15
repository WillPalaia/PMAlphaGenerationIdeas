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


def test_portfolio_tracks_per_strategy_attribution(tmp_path):
    store = SnapshotStore(tmp_path / "paper.sqlite")
    portfolio = PaperPortfolio(store, starting_cash=100.0, fee_rate=0.01)
    snapshot = MarketSnapshot(1, "kalshi", "m1", 0.40, 0.50, 10, 10)

    intent_a = OrderIntent(1, "kalshi", "m1", Side.BUY, 2, 0.50, "order-a", strategy="strat-alpha")
    intent_b = OrderIntent(1, "kalshi", "m1", Side.BUY, 1, 0.50, "order-b", strategy="strat-beta")

    assert portfolio.submit(intent_a, snapshot) is True
    assert portfolio.submit(intent_b, snapshot) is True

    # Check that strategy states exist and are tracked independently
    assert "strat-alpha" in portfolio.strategy_states
    assert "strat-beta" in portfolio.strategy_states
    assert portfolio.strategy_states["strat-alpha"].positions[("kalshi", "m1")].quantity == 2
    assert portfolio.strategy_states["strat-beta"].positions[("kalshi", "m1")].quantity == 1

    # Check persistence in SQLite
    with store.connection() as conn:
        orders = [tuple(r) for r in conn.execute("SELECT strategy, status, filled_quantity FROM paper_orders ORDER BY client_order_id").fetchall()]
        assert orders == [("strat-alpha", "filled", 2.0), ("strat-beta", "filled", 1.0)]
        strategy_equity = [tuple(r) for r in conn.execute("SELECT strategy, equity FROM paper_strategy_equity ORDER BY id").fetchall()]
        assert len(strategy_equity) == 2
        assert strategy_equity[0][0] == "strat-alpha"
        assert strategy_equity[1][0] == "strat-beta"


def test_isolated_strategy_sub_portfolios_cannot_cannibalize_inventory_or_cash(tmp_path):
    store = SnapshotStore(tmp_path / "paper.sqlite")
    portfolio = PaperPortfolio(store, starting_cash=1.50, fee_rate=0.01)
    snapshot = MarketSnapshot(1, "kalshi", "m1", 0.40, 0.50, 10, 10)

    # Strategy Alpha buys 2 contracts for 2 * $0.50 + fee = $1.01
    intent_a = OrderIntent(1, "kalshi", "m1", Side.BUY, 2, 0.50, "a-buy-1", strategy="strat-alpha")
    assert portfolio.submit(intent_a, snapshot) is True
    assert portfolio.strategy_states["strat-alpha"].positions[("kalshi", "m1")].quantity == 2.0
    assert portfolio.strategy_states["strat-alpha"].cash == pytest.approx(1.50 - 1.01)

    # Strategy Beta (starts with $1.50 cash, 0 inventory) tries to sell: should be rejected for inventory!
    intent_b_sell = OrderIntent(2, "kalshi", "m1", Side.SELL, 1, 0.40, "b-sell-1", strategy="strat-beta")
    assert portfolio.submit(intent_b_sell, snapshot) is False
    with store.connection() as conn:
        reason = conn.execute("SELECT reject_reason FROM paper_orders WHERE client_order_id='b-sell-1'").fetchone()[0]
        assert reason == "inventory"

    # Strategy Alpha tries to buy 2 more ($1.01), but only has ~$0.49 cash left: rejected for insufficient-cash!
    intent_a_buy2 = OrderIntent(3, "kalshi", "m1", Side.BUY, 2, 0.50, "a-buy-2", strategy="strat-alpha")
    assert portfolio.submit(intent_a_buy2, snapshot) is False
    with store.connection() as conn:
        reason = conn.execute("SELECT reject_reason FROM paper_orders WHERE client_order_id='a-buy-2'").fetchone()[0]
        assert reason == "insufficient-cash"

    # But Strategy Beta still has its full $1.50 cash: it can buy!
    intent_b_buy = OrderIntent(4, "kalshi", "m1", Side.BUY, 2, 0.50, "b-buy-1", strategy="strat-beta")
    assert portfolio.submit(intent_b_buy, snapshot) is True
    assert portfolio.strategy_states["strat-beta"].positions[("kalshi", "m1")].quantity == 2.0


def test_submit_bundle_atomic_fill_and_orphan_protection(tmp_path):
    store = SnapshotStore(tmp_path / "paper_bundle.sqlite")
    portfolio = PaperPortfolio(store, starting_cash=100.0, fee_rate=0.01)

    snap_a = MarketSnapshot(1000, "kalshi", "LEG-A", 0.40, 0.42, 10, 10)
    snap_b = MarketSnapshot(1000, "kalshi", "LEG-B", 0.50, 0.52, 10, 10)
    portfolio.process_snapshot(snap_a)
    portfolio.process_snapshot(snap_b)

    # 1. Successful atomic execution of both legs
    intents = [
        OrderIntent(1000, "kalshi", "LEG-A", Side.BUY, 1.0, 0.42, "bundle-1-A", signal="event-parity-arbitrage"),
        OrderIntent(1000, "kalshi", "LEG-B", Side.BUY, 1.0, 0.52, "bundle-1-B", signal="event-parity-arbitrage"),
    ]
    results = portfolio.submit_bundle(intents, snap_b)
    assert results == [True, True]
    assert portfolio.positions[("kalshi", "LEG-A")].quantity == 1.0
    assert portfolio.positions[("kalshi", "LEG-B")].quantity == 1.0

    # 2. Price moved on Leg A: Leg A ask jumped to 0.45 > limit_price 0.42
    # submit_bundle must reject BOTH legs to prevent orphan unhedged position!
    snap_a_moved = MarketSnapshot(2000, "kalshi", "LEG-A", 0.43, 0.45, 10, 10)
    portfolio.process_snapshot(snap_a_moved)

    stale_intents = [
        OrderIntent(2000, "kalshi", "LEG-A", Side.BUY, 1.0, 0.42, "bundle-2-A", signal="event-parity-arbitrage"),
        OrderIntent(2000, "kalshi", "LEG-B", Side.BUY, 1.0, 0.52, "bundle-2-B", signal="event-parity-arbitrage"),
    ]
    fail_results = portfolio.submit_bundle(stale_intents, snap_b)
    assert fail_results == [False, False]
    # Verify no orphan order or inventory fill occurred!
    assert portfolio.positions[("kalshi", "LEG-A")].quantity == 1.0
    assert portfolio.positions[("kalshi", "LEG-B")].quantity == 1.0

    # 3. Insufficient cash for bundle
    poor_portfolio = PaperPortfolio(SnapshotStore(tmp_path / "poor.sqlite"), starting_cash=0.50, fee_rate=0.01)
    poor_portfolio.process_snapshot(snap_a)
    poor_portfolio.process_snapshot(snap_b)
    # Total cost = 0.42 + 0.52 = 0.94 + fee > 0.50 cash
    cash_intents = [
        OrderIntent(1000, "kalshi", "LEG-A", Side.BUY, 1.0, 0.42, "poor-1-A", signal="event-parity-arbitrage"),
        OrderIntent(1000, "kalshi", "LEG-B", Side.BUY, 1.0, 0.52, "poor-1-B", signal="event-parity-arbitrage"),
    ]
    cash_results = poor_portfolio.submit_bundle(cash_intents, snap_b)
    assert cash_results == [False, False]
    assert len(poor_portfolio.positions) == 0
