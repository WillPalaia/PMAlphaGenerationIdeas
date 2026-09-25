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
