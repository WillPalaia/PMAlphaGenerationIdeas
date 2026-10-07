from pm_alpha.models import MarketSnapshot
from pm_alpha.strategies import StableHighProbabilityStrategy


def test_stable_high_probability_strategy_requires_stable_price_band():
    strategy = StableHighProbabilityStrategy(lookback=3, max_range=0.02)
    snapshots = [
        MarketSnapshot(1, "kalshi", "m1", 0.68, 0.70, 1, 1),
        MarketSnapshot(2, "kalshi", "m1", 0.69, 0.70, 1, 1),
        MarketSnapshot(3, "kalshi", "m1", 0.68, 0.70, 1, 1),
    ]
    intents = [intent for snapshot in snapshots for intent in strategy.on_snapshot(snapshot)]
    assert len(intents) == 1
    assert intents[0].signal == "stable-high-probability"


def test_stable_strategy_stop_loss_confirmation():
    strategy = StableHighProbabilityStrategy(
        lookback=3,
        max_range=0.03,
        take_profit_price=0.98,
        stop_loss_price=0.55,
        min_stop_loss_bid=0.40,
        stop_loss_confirmations=3,
    )
    # Buy entry
    for t in (1, 2, 3):
        list(strategy.on_snapshot(MarketSnapshot(t, "kalshi", "m1", 0.69, 0.70, 10, 10)))

    # Tick 1 with bid dropping into stop-loss zone (0.50) -> should NOT trigger stop loss yet (confirmation 1/3)
    orders1 = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.50, 0.54, 10, 10)))
    assert len(orders1) == 0

    # Bid bounces back to 0.65 -> counter resets
    orders_bounce = list(strategy.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.65, 0.66, 10, 10)))
    assert len(orders_bounce) == 0

    # Now bid drops again for 2 ticks -> still not 3 confirmations
    assert list(strategy.on_snapshot(MarketSnapshot(6, "kalshi", "m1", 0.50, 0.54, 10, 10))) == []
    assert list(strategy.on_snapshot(MarketSnapshot(7, "kalshi", "m1", 0.50, 0.54, 10, 10))) == []

    # 3rd consecutive tick in stop-loss zone -> triggers verified stop loss!
    orders_sl = list(strategy.on_snapshot(MarketSnapshot(8, "kalshi", "m1", 0.50, 0.54, 10, 10)))
    assert len(orders_sl) == 1
    assert orders_sl[0].side.value == "sell"
    assert orders_sl[0].signal == "stable-high-stop-loss"

