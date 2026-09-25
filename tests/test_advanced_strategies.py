import pytest

from pm_alpha.models import MarketSnapshot, Side
from pm_alpha.strategies import (
    BollingerReversionStrategy,
    ComplementArbitrageStrategy,
    EmaCrossoverStrategy,
    FavoriteYieldStrategy,
    JumpFollowingStrategy,
    OrderBookImbalanceStrategy,
    RangeBreakoutStrategy,
    SpreadHarvestingMarketMaker,
    TimeDecayYieldStrategy,
    VwapPullbackStrategy,
)


def test_favorite_yield_strategy_triggers_on_stable_favorites():
    strategy = FavoriteYieldStrategy(min_probability=0.85, max_probability=0.95, lookback=3, max_range=0.03)
    # First 2 snapshots inside range: no order yet (needs lookback=3)
    assert list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.89, 0.90, 10, 10))) == []
    assert list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.90, 0.91, 10, 10))) == []
    # 3rd snapshot inside range: triggers buy
    orders = list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.90, 0.91, 10, 10)))
    assert len(orders) == 1
    assert orders[0].side == Side.BUY
    assert orders[0].limit_price == 0.91
    assert orders[0].signal == "favorite-yield"
    assert orders[0].strategy == "favorite-yield"

    # Subsequent snapshot respects max_orders_per_market=1
    assert list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.90, 0.91, 10, 10))) == []


def test_order_book_imbalance_triggers_on_heavy_bid_depth():
    strategy = OrderBookImbalanceStrategy(imbalance_threshold=0.5, max_spread=0.04, min_depth=5.0)
    # Balanced book (10 vs 10): OBI = 0 -> no order
    assert list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.50, 0.52, 10, 10))) == []
    # Imbalanced book: bid=40, ask=5 -> OBI = (40-5)/45 = 0.777 >= 0.5 -> triggers buy
    orders = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.50, 0.52, 40, 5)))
    assert len(orders) == 1
    assert orders[0].side == Side.BUY
    assert orders[0].limit_price == 0.52
    assert orders[0].signal == "orderbook-imbalance"
    assert orders[0].strategy == "orderbook-imbalance"

    # Wide spread (0.10 > max_spread 0.04) -> rejected
    strategy2 = OrderBookImbalanceStrategy(imbalance_threshold=0.5, max_spread=0.04)
    assert list(strategy2.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.40, 0.55, 100, 2))) == []


def test_bollinger_reversion_triggers_oversold_buy_and_exit_sell():
    strategy = BollingerReversionStrategy(lookback=5, entry_z=-1.5, exit_z=0.0, max_orders_per_market=1)
    # Establish flat baseline around 0.50
    for t in range(1, 5):
        assert list(strategy.on_snapshot(MarketSnapshot(t, "kalshi", "m1", 0.49, 0.50, 10, 10))) == []
    # Sharp drop to 0.40 -> large negative z-score -> triggers oversold buy
    buy_orders = list(strategy.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.39, 0.40, 10, 10)))
    assert len(buy_orders) == 1
    assert buy_orders[0].side == Side.BUY
    assert buy_orders[0].signal == "bollinger-oversold"

    # Price rebounds above mean to 0.55 -> z >= 0 and position > 0 -> triggers exit sell
    sell_orders = list(strategy.on_snapshot(MarketSnapshot(6, "kalshi", "m1", 0.54, 0.55, 10, 10)))
    assert len(sell_orders) == 1
    assert sell_orders[0].side == Side.SELL
    assert sell_orders[0].limit_price == 0.54
    assert sell_orders[0].signal == "bollinger-exit"


def test_range_breakout_triggers_on_channel_break():
    strategy = RangeBreakoutStrategy(lookback=4, breakout_margin=0.02)
    # Flat range between 0.45 and 0.48
    for t, p in enumerate((0.45, 0.46, 0.47, 0.48), start=1):
        assert list(strategy.on_snapshot(MarketSnapshot(t, "kalshi", "m1", p - 0.01, p, 10, 10))) == []
    # Price breaks out to 0.52 (channel high 0.48 + 0.02 margin = 0.50) -> triggers buy
    orders = list(strategy.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.51, 0.52, 10, 10)))
    assert len(orders) == 1
    assert orders[0].side == Side.BUY
    assert orders[0].limit_price == 0.52
    assert orders[0].signal == "range-breakout"


def test_ema_crossover_triggers_on_golden_cross():
    strategy = EmaCrossoverStrategy(fast_span=3, slow_span=6, min_cross_diff=0.01)
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.49, 0.50, 10, 10)))
    list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.47, 0.48, 10, 10)))
    list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.46, 0.47, 10, 10)))
    orders = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.55, 0.56, 10, 10)))
    assert len(orders) >= 1
    assert orders[0].side == Side.BUY
    assert orders[0].signal == "ema-crossover"


def test_spread_harvesting_market_maker_quotes_both_sides():
    mm = SpreadHarvestingMarketMaker(min_spread=0.06, inventory_skew=0.01, max_inventory=5.0, aggressiveness=0.50)
    buy_orders = list(mm.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.40, 0.50, 10, 10)))
    assert len(buy_orders) == 1
    assert buy_orders[0].side == Side.BUY
    assert buy_orders[0].signal == "mm-harvest-buy"
    assert mm._inventory[("kalshi", "m1")] == 1.0

    sell_orders = list(mm.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.48, 0.56, 10, 10)))
    assert len(sell_orders) == 1
    assert sell_orders[0].side == Side.SELL
    assert sell_orders[0].signal == "mm-harvest-sell"


def test_complement_arbitrage_triggers_on_locked_book():
    strategy = ComplementArbitrageStrategy()
    assert list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.40, 0.45, 10, 10))) == []
    orders = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.50, 0.50, 10, 10)))
    assert len(orders) == 1
    assert orders[0].signal == "locked-book-arbitrage"


def test_vwap_pullback_triggers_in_uptrend():
    strategy = VwapPullbackStrategy(lookback=4, pullback_threshold=0.02, trend_lookback=4)
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.49, 0.50, 10, 10)))
    list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.54, 0.55, 10, 10)))
    list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.59, 0.60, 20, 20)))
    orders = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.51, 0.52, 5, 5)))
    assert len(orders) == 1
    assert orders[0].signal == "vwap-pullback"


def test_jump_following_triggers_on_price_spike():
    strategy = JumpFollowingStrategy(jump_threshold=0.08, lookback=2)
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.39, 0.40, 10, 10)))
    orders = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.49, 0.50, 10, 10)))
    assert len(orders) == 1
    assert orders[0].signal == "jump-momentum"


def test_time_decay_yield_triggers_on_stable_near_parity():
    strategy = TimeDecayYieldStrategy(target_min_price=0.85, target_max_price=0.95, lookback=3, max_volatility=0.02)
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.89, 0.90, 10, 10)))
    list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.90, 0.91, 10, 10)))
    orders = list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.90, 0.91, 10, 10)))
    assert len(orders) == 1
    assert orders[0].signal == "time-decay-yield"


def test_momentum_strategy_take_profit_and_stop_loss():
    from pm_alpha.strategies import MomentumStrategy
    strategy = MomentumStrategy(lookback=2, minimum_move=0.03, take_profit=0.08, stop_loss=0.05)
    # 1. Warmup
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.49, 0.50, 10, 10)))
    # 2. Buy on momentum
    buys = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.54, 0.55, 10, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY

    # 3. Take profit when bid jumps to 0.64 (+0.09 >= +0.08)
    sells = list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.64, 0.65, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "momentum-take-profit"
    assert ("kalshi", "m1") not in strategy._positions


def test_mean_reversion_sells_when_reverting_to_mean():
    from pm_alpha.strategies import MeanReversionStrategy
    strategy = MeanReversionStrategy(lookback=3, deviation=0.05)
    # History: 0.50, 0.50, 0.50 -> mean = 0.50
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.49, 0.50, 10, 10)))
    list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.49, 0.50, 10, 10)))
    list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.49, 0.50, 10, 10)))
    # Dip to 0.42 (<= 0.50 - 0.05) -> triggers buy
    buys = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.41, 0.42, 10, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY

    # Rebound to 0.50 (>= mean) -> triggers exit sell to lock in profit!
    sells = list(strategy.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.50, 0.51, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "mean-reverted-exit"
    assert ("kalshi", "m1") not in strategy._positions


def test_favorite_yield_harvests_at_near_parity():
    strategy = FavoriteYieldStrategy(min_probability=0.85, max_probability=0.95, lookback=2)
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.89, 0.90, 10, 10)))
    buys = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.89, 0.90, 10, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY

    # Price approaches 0.97 -> harvest yield early and sell!
    sells = list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.97, 0.98, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "favorite-yield-harvested"
    assert ("kalshi", "m1") not in strategy._positions


def test_ema_crossover_sells_on_death_cross():
    strategy = EmaCrossoverStrategy(fast_span=2, slow_span=5, min_cross_diff=0.01)
    # Warmup
    for t in range(1, 4):
        list(strategy.on_snapshot(MarketSnapshot(t, "kalshi", "m1", 0.45, 0.46, 10, 10)))
    # Golden cross buy
    buys = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.55, 0.56, 10, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY

    # Sharp reversal -> death cross triggers sell
    sells = list(strategy.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.39, 0.40, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "ema-death-cross-exit"
    assert ("kalshi", "m1") not in strategy._positions

