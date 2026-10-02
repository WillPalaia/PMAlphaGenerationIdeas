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


def test_time_decay_holds_and_harvests_at_near_parity():
    strategy = TimeDecayYieldStrategy(target_min_price=0.80, target_max_price=0.95, lookback=2)
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.84, 0.85, 10, 10)))
    buys = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.84, 0.85, 10, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY

    # Price moves to 0.93 (+0.08 from entry): Should NOT sell, because yield holds for convergence!
    no_sells = list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.93, 0.94, 10, 10)))
    assert len(no_sells) == 0
    assert ("kalshi", "m1") in strategy._positions

    # Price reaches near-parity 0.97 (>= harvest_price): Now harvests yield!
    sells = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.97, 0.98, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "theta-decay-harvested"
    assert ("kalshi", "m1") not in strategy._positions


def test_range_breakout_rides_past_small_gains_to_parity():
    strategy = RangeBreakoutStrategy(lookback=3, breakout_margin=0.02)
    # Channel high is 0.50
    for t in (1, 2, 3):
        list(strategy.on_snapshot(MarketSnapshot(t, "kalshi", "m1", 0.49, 0.50, 10, 10)))
    # Breakout to 0.53
    buys = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.52, 0.53, 10, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY

    # Price at 0.65 (+0.12 gain): Does NOT sell for small gain, lets trend expand!
    no_sells = list(strategy.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.65, 0.66, 10, 10)))
    assert len(no_sells) == 0
    assert ("kalshi", "m1") in strategy._positions

    # Price reaches parity target 0.94: Takes profit near certainty
    sells = list(strategy.on_snapshot(MarketSnapshot(6, "kalshi", "m1", 0.94, 0.95, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "breakout-take-profit"
    assert ("kalshi", "m1") not in strategy._positions


def test_order_book_imbalance_cuts_on_queue_flip():
    strategy = OrderBookImbalanceStrategy(imbalance_threshold=0.5, max_spread=0.04, min_depth=5.0)
    # Enter on positive imbalance
    buys = list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.50, 0.52, 40, 5)))
    assert len(buys) == 1

    # Book flips to heavy selling pressure (bid=5, ask=40 -> OBI = -0.77 <= -0.30)
    sells = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.50, 0.52, 5, 40)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "imbalance-flip-exit"
    assert ("kalshi", "m1") not in strategy._positions


def test_order_flow_imbalance_triggers_on_persistent_accumulating_pressure():
    from pm_alpha.strategies import OrderFlowImbalanceStrategy
    strategy = OrderFlowImbalanceStrategy(lookback=3, min_cumulative_ofi=6.0, max_spread=0.04)

    # Base snapshot
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.50, 0.52, 10, 10)))
    # Bid increases by +4, ask decreases by -2 (step OFI = 4 - (-2) = 6)
    list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.50, 0.52, 14, 8)))
    # Bid increases by +3 (step OFI = 3)
    list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.50, 0.52, 17, 8)))
    # Cumulative OFI is >= 6 -> triggers buy
    buys = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.50, 0.52, 18, 7)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY
    assert buys[0].signal == "order-flow-imbalance"

    # Price rises to 0.57 (+0.05 gain) -> triggers take profit
    sells = list(strategy.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.57, 0.58, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "ofi-take-profit"


def test_volume_mean_reversion_triggers_on_deep_book_discount():
    from pm_alpha.strategies import VolumeWeightedMeanReversionStrategy
    strategy = VolumeWeightedMeanReversionStrategy(lookback=4, deviation=0.04, min_depth=3.0, max_spread=0.04)

    # Establish mean around 0.50 with deep books
    for t in range(1, 5):
        list(strategy.on_snapshot(MarketSnapshot(t, "kalshi", "m1", 0.49, 0.50, 10, 10)))

    # Dip to 0.45 with thin depth (ask_size=1 < 3.0) -> rejected by liquidity filter
    assert list(strategy.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.43, 0.45, 10, 1))) == []

    # Dip to 0.44 with verified depth (ask_size=5, bid_size=5) -> triggers buy
    buys = list(strategy.on_snapshot(MarketSnapshot(6, "kalshi", "m1", 0.43, 0.44, 5, 5)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY
    assert buys[0].signal == "volume-mean-reversion"

    # Rebound back to mean 0.50 -> triggers reverted exit
    sells = list(strategy.on_snapshot(MarketSnapshot(7, "kalshi", "m1", 0.50, 0.51, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "vmr-reverted-exit"


def test_penny_contrarian_strategy_triggers_on_liquid_support_and_exits():
    from pm_alpha.strategies import PennyContrarianStrategy
    strategy = PennyContrarianStrategy(min_price=0.02, max_price=0.10, min_bid_depth=5.0, take_profit=0.08)

    # Penny contract with thin bid (bid_size=1 < 5.0) -> no order
    assert list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.04, 0.06, 1, 10))) == []

    # Penny contract with strong bid support (bid_size=20 >= 5.0, ask=0.06 within range) -> triggers buy
    buys = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.04, 0.06, 20, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY
    assert buys[0].signal == "penny-contrarian-entry"

    # Price doubles to bid=0.14 -> triggers take profit
    sells = list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.14, 0.16, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL
    assert sells[0].signal == "penny-take-profit"


def test_momentum_strategy_with_trailing_stop_and_price_filter():
    from pm_alpha.strategies import MomentumStrategy
    strategy = MomentumStrategy(
        lookback=2,
        minimum_move=0.03,
        take_profit=0.20,
        stop_loss=0.08,
        min_price=0.20,
        max_price=0.75,
        trailing_stop_activation=0.08,
        min_stop_loss_bid=0.15,
        strategy_name="momentum",
    )
    # 1. Warmup
    list(strategy.on_snapshot(MarketSnapshot(1, "kalshi", "m1", 0.39, 0.40, 10, 10)))

    # 2. Buy on momentum at ask=0.45
    buys = list(strategy.on_snapshot(MarketSnapshot(2, "kalshi", "m1", 0.44, 0.45, 10, 10)))
    assert len(buys) == 1
    assert buys[0].strategy == "momentum"

    # 3. Price peaks at bid=0.54 (gain = +0.09 >= trailing_stop_activation of 0.08) -> locks in breakeven (stop=0.46)
    list(strategy.on_snapshot(MarketSnapshot(3, "kalshi", "m1", 0.54, 0.55, 10, 10)))

    # 4. Price retraces to bid=0.45 (<= eff_stop 0.46) -> exits with preserved profit/breakeven, NOT loss!
    sells = list(strategy.on_snapshot(MarketSnapshot(4, "kalshi", "m1", 0.45, 0.46, 10, 10)))
    assert len(sells) == 1
    assert sells[0].signal == "momentum-stop-loss"
    assert sells[0].limit_price == 0.45


def test_bollinger_reversion_with_min_profit_target():
    from pm_alpha.strategies import BollingerReversionStrategy
    strat = BollingerReversionStrategy(
        lookback=5,
        entry_z=-1.0,
        exit_z=0.0,
        min_profit_target=0.03,
        min_std=0.01,
        max_spread=0.05,
    )
    # Warmup flat history: 0.50, 0.50, 0.50, 0.50
    for t in range(4):
        list(strat.on_snapshot(MarketSnapshot(t, "kalshi", "m1", 0.49, 0.50, 10, 10)))

    # Drop to 0.40 -> triggers oversold buy at ask=0.40
    buys = list(strat.on_snapshot(MarketSnapshot(5, "kalshi", "m1", 0.38, 0.40, 10, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY

    # Mean is now ~0.48. If bid reverts to 0.41, z >= 0.0, but gain is only 0.01 < min_profit_target (0.03) -> NO exit sell
    assert list(strat.on_snapshot(MarketSnapshot(6, "kalshi", "m1", 0.41, 0.42, 10, 10))) == []

    # If bid reverts to 0.45 (gain = 0.05 >= 0.03) -> triggers profitable exit sell!
    sells = list(strat.on_snapshot(MarketSnapshot(7, "kalshi", "m1", 0.45, 0.46, 10, 10)))
    assert len(sells) == 1
    assert sells[0].side == Side.SELL




