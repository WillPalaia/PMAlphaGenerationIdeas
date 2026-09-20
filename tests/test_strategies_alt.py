from pm_alpha.models import MarketSnapshot
from pm_alpha.strategies import MeanReversionStrategy, MomentumStrategy


def test_momentum_uses_only_prior_snapshot():
    strategy = MomentumStrategy(lookback=3, minimum_move=0.05)
    first = MarketSnapshot(0, "kalshi", "m1", 0.39, 0.40, 1, 1)
    second = MarketSnapshot(1, "kalshi", "m1", 0.45, 0.46, 1, 1)
    assert list(strategy.on_snapshot(first)) == []
    orders = list(strategy.on_snapshot(second))
    assert len(orders) == 1
    assert orders[0].signal == "positive-momentum"


def test_mean_reversion_requires_lookback_and_caps_entries():
    strategy = MeanReversionStrategy(lookback=2, deviation=0.05, max_orders_per_market=1)
    for timestamp, price in enumerate((0.50, 0.50, 0.40, 0.35)):
        orders = list(
            strategy.on_snapshot(
                MarketSnapshot(timestamp, "kalshi", "m1", price - 0.01, price, 1, 1)
            )
        )
    assert len(orders) == 0
    assert strategy._orders[("kalshi", "m1")] == 1
