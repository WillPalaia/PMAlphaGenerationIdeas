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


def test_bollinger_reversion_time_decay_exit():
    from pm_alpha.strategies import BollingerReversionStrategy
    strat = BollingerReversionStrategy(
        lookback=5,
        entry_z=-1.0,
        exit_z=0.0,
        min_std=0.01,
        max_spread=0.05,
        max_holding_ticks=3,
        min_stop_loss_bid=0.20,
    )
    for t in range(4):
        list(strat.on_snapshot(MarketSnapshot(t * 1000, "kalshi", "m1", 0.49, 0.50, 10, 10)))

    # Drop to 0.40 -> triggers oversold buy at ask=0.40
    buys = list(strat.on_snapshot(MarketSnapshot(4000, "kalshi", "m1", 0.38, 0.40, 10, 10)))
    assert len(buys) == 1
    assert buys[0].side == Side.BUY

    # Price stays flat at 0.38 / 0.40 without reverting (z stays negative)
    # Tick 1 after buy
    assert list(strat.on_snapshot(MarketSnapshot(5000, "kalshi", "m1", 0.38, 0.40, 10, 10))) == []
    # Tick 2 after buy
    assert list(strat.on_snapshot(MarketSnapshot(6000, "kalshi", "m1", 0.38, 0.40, 10, 10))) == []
    # Tick 3 after buy -> hits max_holding_ticks (3) -> triggers time-decay exit!
    decays = list(strat.on_snapshot(MarketSnapshot(7000, "kalshi", "m1", 0.38, 0.40, 10, 10)))
    assert len(decays) == 1
    assert decays[0].side == Side.SELL
    assert decays[0].limit_price == 0.38
    assert decays[0].signal == "bollinger-time-decay-exit"


def test_spread_harvesting_market_maker_resolution():
    from pm_alpha.strategies import SpreadHarvestingMarketMaker
    mm = SpreadHarvestingMarketMaker(
        min_spread=0.04,
        max_spread=0.15,
        prefix="mm-harvest",
        strategy_name="spread-harvesting",
    )
    # Buy when spread is wide (0.45 bid, 0.55 ask)
    buys = list(mm.on_snapshot(MarketSnapshot(1000, "kalshi", "m1", 0.45, 0.55, 10, 10)))
    assert len(buys) == 1
    assert buys[0].signal == "mm-harvest-buy"
    assert buys[0].strategy == "spread-harvesting"
    assert ("kalshi", "m1") in mm._inventory

    # Resolution snapshot clears inventory
    res = list(mm.on_snapshot(MarketSnapshot(2000, "kalshi", "m1", None, None, 0, 0, resolved=True, settlement_yes=1.0)))
    assert len(res) == 0
    assert ("kalshi", "m1") not in mm._inventory


def test_spread_harvesting_anti_courtsiding_volatility_filter():
    from pm_alpha.strategies import SpreadHarvestingMarketMaker
    mm = SpreadHarvestingMarketMaker(
        min_spread=0.04,
        max_spread=0.15,
        max_price_jump=0.01,
        max_rolling_range=0.02,
        volatility_lookback=5,
    )
    # Tick 1: Stable market (mid = 0.50)
    intents1 = list(mm.on_snapshot(MarketSnapshot(1000, "kalshi", "m1", 0.45, 0.55, 10, 10)))
    assert len(intents1) == 1

    # Tick 2: Sudden jump from news/courtsiding (mid jumps from 0.50 to 0.55, jump = 0.05 > 0.01)
    intents_jump = list(mm.on_snapshot(MarketSnapshot(2000, "kalshi", "m1", 0.50, 0.60, 10, 10)))
    assert len(intents_jump) == 0  # Market maker freezes and refuses to quote!


def test_spread_harvesting_time_decay_square_off():
    from pm_alpha.strategies import SpreadHarvestingMarketMaker
    mm = SpreadHarvestingMarketMaker(
        min_spread=0.04,
        max_spread=0.15,
        max_holding_ticks=3,
        stop_loss_margin=None,
        salvage_floor=0.03,
    )
    # Tick 1: Buys at ask 0.50
    b1 = list(mm.on_snapshot(MarketSnapshot(1000, "kalshi", "m1", 0.40, 0.50, 10, 10)))
    assert len(b1) == 1
    assert b1[0].side == Side.BUY

    # Ticks 2, 3: Market moves sideways, no exit
    list(mm.on_snapshot(MarketSnapshot(2000, "kalshi", "m1", 0.40, 0.50, 10, 10)))
    list(mm.on_snapshot(MarketSnapshot(3000, "kalshi", "m1", 0.40, 0.50, 10, 10)))

    # Tick 4: Holding ticks >= 3 (max_holding_ticks exceeded). Emits emergency liquidation SELL at bid
    sq = list(mm.on_snapshot(MarketSnapshot(4000, "kalshi", "m1", 0.40, 0.50, 10, 10)))
    assert len(sq) == 1
    assert sq[0].side == Side.SELL
    assert sq[0].signal == "mm-square-off-time"
    assert sq[0].limit_price == 0.40
    assert mm._inventory.get(("kalshi", "m1"), 0.0) == 0.0


def test_spread_harvesting_toxic_stop_loss_square_off():
    from pm_alpha.strategies import SpreadHarvestingMarketMaker
    mm = SpreadHarvestingMarketMaker(
        min_spread=0.04,
        max_spread=0.15,
        stop_loss_margin=0.06,
        salvage_floor=0.03,
    )
    # Tick 1: Buys at ask 0.50 (avg_cost = 0.50)
    list(mm.on_snapshot(MarketSnapshot(1000, "kalshi", "m1", 0.40, 0.50, 10, 10)))

    # Tick 2: Bid plunges to 0.43 (0.50 - 0.43 = 0.07 >= stop_loss_margin 0.06)
    toxic = list(mm.on_snapshot(MarketSnapshot(2000, "kalshi", "m1", 0.43, 0.55, 10, 10)))
    assert len(toxic) == 1
    assert toxic[0].side == Side.SELL
    assert toxic[0].signal == "mm-toxic-flow-stop"
    assert toxic[0].limit_price == 0.43
    assert mm._inventory.get(("kalshi", "m1"), 0.0) == 0.0


def test_microstructure_retail_herd_fader():
    from pm_alpha.strategies import MicrostructureRetailHerdFader
    fader = MicrostructureRetailHerdFader(
        lookback_snapshots=3,
        max_target_price=0.30,
        min_price_runup=0.04,
        min_depth_absorption=2.0,
        min_microprice_discount=0.015,
        max_spread=0.03,
    )
    # Ticks 1-3: baseline at ask 0.20
    list(fader.on_snapshot(MarketSnapshot(1000, "kalshi", "m1", 0.18, 0.20, 10, 10)))
    list(fader.on_snapshot(MarketSnapshot(2000, "kalshi", "m1", 0.18, 0.20, 10, 10)))
    list(fader.on_snapshot(MarketSnapshot(3000, "kalshi", "m1", 0.18, 0.20, 10, 10)))

    # Tick 4: retail runs price up to 0.25 (runup = +0.05). Bid 0.23, Ask 0.25.
    # Institutional ask wall: bid_size=10, ask_size=30 (depth ratio 3.0 >= 2.0).
    # Micro-price = (10*0.25 + 30*0.23)/40 = (2.5 + 6.9)/40 = 0.235. Micro-discount = 0.25 - 0.235 = 0.015.
    fade = list(fader.on_snapshot(MarketSnapshot(4000, "kalshi", "m1", 0.23, 0.25, 10, 30)))
    assert len(fade) == 1
    assert fade[0].side == Side.SELL
    assert fade[0].signal == "retail-herd-exhaustion-fade"
    assert fade[0].limit_price == 0.23


def test_atmospheric_temperature_harvester():
    from pm_alpha.strategies import AtmosphericTemperatureHarvester
    harvester = AtmosphericTemperatureHarvester(
        min_price=0.88,
        max_price=0.95,
        lookback=3,
        max_range=0.02,
        min_depth=2.0,
        max_spread=0.02,
    )
    # Ticks 1-3: Stable high price in favorite weather zone
    list(harvester.on_snapshot(MarketSnapshot(1000, "kalshi", "KXTEMPCHIHS-26OCT08", 0.92, 0.93, 10, 10)))
    list(harvester.on_snapshot(MarketSnapshot(2000, "kalshi", "KXTEMPCHIHS-26OCT08", 0.92, 0.93, 10, 10)))
    orders = list(harvester.on_snapshot(MarketSnapshot(3000, "kalshi", "KXTEMPCHIHS-26OCT08", 0.92, 0.93, 10, 10)))
    assert len(orders) == 1
    assert orders[0].side == Side.BUY
    assert orders[0].signal == "atmospheric-determinism-entry"
    assert orders[0].limit_price == 0.93


def test_event_parity_arbitrage_executes_on_lol_worlds():
    from pm_alpha.strategies import EventParityArbitrageStrategy

    strategy = EventParityArbitrageStrategy(min_edge=0.01, fee_rate=0.01, quantity=5.0)

    # Leg 1: BLG wins game
    snap_blg = MarketSnapshot(
        timestamp_ms=1000,
        venue="kalshi",
        market_id="KXLOLGAME-26OCT15BLGT1-BLG",
        yes_bid=0.40,
        yes_ask=0.42,
        bid_size=10.0,
        ask_size=10.0,
    )
    orders = list(strategy.on_snapshot(snap_blg))
    assert orders == []  # Only one leg observed so far

    # Leg 2: T1 wins game (sum of asks = 0.42 + 0.52 = 0.94 < 1.00 - fees)
    snap_t1 = MarketSnapshot(
        timestamp_ms=1050,
        venue="kalshi",
        market_id="KXLOLGAME-26OCT15BLGT1-T1",
        yes_bid=0.50,
        yes_ask=0.52,
        bid_size=15.0,
        ask_size=8.0,
    )
    orders = list(strategy.on_snapshot(snap_t1))
    assert len(orders) == 2
    assert all(o.side == Side.BUY for o in orders)
    assert all(o.signal == "event-parity-arbitrage" for o in orders)
    assert all(o.strategy == "event-parity-arbitrage" for o in orders)

    tickers = {o.market_id: o for o in orders}
    assert "KXLOLGAME-26OCT15BLGT1-BLG" in tickers
    assert "KXLOLGAME-26OCT15BLGT1-T1" in tickers
    assert tickers["KXLOLGAME-26OCT15BLGT1-BLG"].limit_price == 0.42
    assert tickers["KXLOLGAME-26OCT15BLGT1-T1"].limit_price == 0.52
    # Executable quantity capped by strategy quantity (5.0) and min depth (8.0)
    assert tickers["KXLOLGAME-26OCT15BLGT1-BLG"].quantity == 5.0
    assert tickers["KXLOLGAME-26OCT15BLGT1-T1"].quantity == 5.0


def test_event_parity_arbitrage_rejects_cumulative_ladder_trap():
    from pm_alpha.strategies import EventParityArbitrageStrategy

    strategy = EventParityArbitrageStrategy(min_edge=0.01, fee_rate=0.01)

    # Treasury yield strike ladder (NOT mutually exclusive - multiple legs settle YES!)
    snap1 = MarketSnapshot(
        timestamp_ms=1000,
        venue="kalshi",
        market_id="KXTREASURY10Y-26OCT-T4.25",
        yes_bid=0.35,
        yes_ask=0.38,
        bid_size=10.0,
        ask_size=10.0,
    )
    snap2 = MarketSnapshot(
        timestamp_ms=1050,
        venue="kalshi",
        market_id="KXTREASURY10Y-26OCT-T4.50",
        yes_bid=0.35,
        yes_ask=0.38,
        bid_size=10.0,
        ask_size=10.0,
    )
    list(strategy.on_snapshot(snap1))
    orders = list(strategy.on_snapshot(snap2))
    # Cumulative ladder trap must be detected and rejected
    assert orders == []

    # Temperature strike ladder (also cumulative)
    snap_temp1 = MarketSnapshot(1000, "kalshi", "KXTEMPCHI-26OCT-T70.99", 0.40, 0.45, 10, 10)
    snap_temp2 = MarketSnapshot(1050, "kalshi", "KXTEMPCHI-26OCT-T71.99", 0.40, 0.45, 10, 10)
    list(strategy.on_snapshot(snap_temp1))
    assert list(strategy.on_snapshot(snap_temp2)) == []


def test_event_parity_arbitrage_prevents_partial_fill_legging_risk():
    from pm_alpha.strategies import EventParityArbitrageStrategy

    strategy = EventParityArbitrageStrategy(min_order_size=1.0)
    # Leg 1 has ample liquidity
    snap1 = MarketSnapshot(1000, "kalshi", "KXCS2MAP-26SEP25-AIM", 0.40, 0.42, 10.0, 10.0)
    # Leg 2 has microscopic liquidity (0.4 contracts < min_order_size 1.0)
    snap2 = MarketSnapshot(1050, "kalshi", "KXCS2MAP-26SEP25-INN", 0.50, 0.52, 10.0, 0.4)

    list(strategy.on_snapshot(snap1))
    orders = list(strategy.on_snapshot(snap2))
    # Must reject to avoid partial-fill legging risk
    assert orders == []


def test_atmospheric_temperature_harvester_quarter_kelly_sizing():
    from pm_alpha.strategies import AtmosphericTemperatureHarvester

    # Initialize harvester with $100 cash
    harvester = AtmosphericTemperatureHarvester(
        min_price=0.88,
        max_price=0.95,
        allocated_cash=100.0,
        max_contracts=50.0,
        dynamic_sizing=True,
    )
    # At price 0.92, quarter-kelly fraction is ~0.21, clamped to max_cash_pct (0.10)
    # Target dollars = $100 * 0.10 = $10.00 -> 10 contracts
    qty_100 = harvester.calculate_sizing(0.92)
    assert qty_100 == 10.0

    # Compounding: Scale cash up to $500
    harvester.set_allocated_cash(500.0)
    # Target dollars = $500 * 0.10 = $50.00 -> 54 contracts, clamped to max_contracts (50.0)
    qty_500 = harvester.calculate_sizing(0.92)
    assert qty_500 == 50.0

    # Small bankroll: $20 cash
    harvester.set_allocated_cash(20.0)
    # Target dollars = $20 * 0.10 = $2.00 -> 2 contracts
    qty_20 = harvester.calculate_sizing(0.92)
    assert qty_20 == 2.0


def test_atmospheric_temperature_harvester_dynamic_time_scaling_margin():
    from pm_alpha.strategies import AtmosphericTemperatureHarvester
    from pm_alpha.weather_oracle import WeatherOracle, NwsObservation
    from unittest.mock import patch

    oracle = WeatherOracle()
    # Mock observation: station is 73.2°F, strike is 71.0°F -> margin = +2.2°F
    mock_obs = NwsObservation(
        station_id="KLAX",
        timestamp_iso="2026-10-15T05:00:00Z",
        temp_f=73.2,
        temp_c=22.89,
        dewpoint_f=55.0,
        wind_speed_mph=5.0,
        description="Clear",
        fetched_at_ms=1700000000000,
    )

    with patch.object(oracle, "get_latest_observation", return_value=mock_obs):
        # Scenario A: 4 hours to close -> required_margin = max(1.0, 0.8 + 0.5 * 4.0) = 2.8°F
        # Current margin is 2.2°F < 2.8°F -> MARGIN_TOO_CLOSE!
        meta_4h = {"KXTEMPLAXHS-26OCT1502-T71.00": type("Meta", (), {"hours_to_close": 4.0})()}
        harvester_4h = AtmosphericTemperatureHarvester(
            lookback=2,
            oracle=oracle,
            market_metadata=meta_4h,
        )
        list(harvester_4h.on_snapshot(MarketSnapshot(1000, "kalshi", "KXTEMPLAXHS-26OCT1502-T71.00", 0.92, 0.93, 10, 10)))
        orders_4h = list(harvester_4h.on_snapshot(MarketSnapshot(2000, "kalshi", "KXTEMPLAXHS-26OCT1502-T71.00", 0.92, 0.93, 10, 10)))
        assert orders_4h == []

        # Scenario B: 0.5 hours to close -> required_margin = max(1.0, 0.8 + 0.5 * 0.5) = 1.05°F
        # Current margin is 2.2°F >= 1.05°F -> Safe! Generates order.
        meta_05h = {"KXTEMPLAXHS-26OCT1502-T71.00": type("Meta", (), {"hours_to_close": 0.5})()}
        harvester_05h = AtmosphericTemperatureHarvester(
            lookback=2,
            oracle=oracle,
            market_metadata=meta_05h,
        )
        list(harvester_05h.on_snapshot(MarketSnapshot(1000, "kalshi", "KXTEMPLAXHS-26OCT1502-T71.00", 0.92, 0.93, 10, 10)))
        orders_05h = list(harvester_05h.on_snapshot(MarketSnapshot(2000, "kalshi", "KXTEMPLAXHS-26OCT1502-T71.00", 0.92, 0.93, 10, 10)))
        assert len(orders_05h) == 1
        assert orders_05h[0].signal == "atmospheric-determinism-entry"


def test_event_parity_arbitrage_defers_incomplete_3way_partition_until_all_legs_present():
    from pm_alpha.strategies import EventParityArbitrageStrategy
    from pm_alpha.discovery import DiscoveredMarket

    # A 3-way soccer event with 3 mutually exclusive markets
    meta = {
        "KXSOCCERGAME-26OCT15-MAN": DiscoveredMarket(
            ticker="KXSOCCERGAME-26OCT15-MAN", title="Man", close_time="", liquidity_dollars=100,
            volume=100, open_interest=100, category="sports", event_ticker="KXSOCCERGAME-26OCT15", mutually_exclusive=True,
        ),
        "KXSOCCERGAME-26OCT15-CHEL": DiscoveredMarket(
            ticker="KXSOCCERGAME-26OCT15-CHEL", title="Chel", close_time="", liquidity_dollars=100,
            volume=100, open_interest=100, category="sports", event_ticker="KXSOCCERGAME-26OCT15", mutually_exclusive=True,
        ),
        "KXSOCCERGAME-26OCT15-DRAW": DiscoveredMarket(
            ticker="KXSOCCERGAME-26OCT15-DRAW", title="Draw", close_time="", liquidity_dollars=100,
            volume=100, open_interest=100, category="sports", event_ticker="KXSOCCERGAME-26OCT15", mutually_exclusive=True,
        ),
    }

    strategy = EventParityArbitrageStrategy(min_edge=0.01, fee_rate=0.01, market_metadata=meta)

    # Leg 1 arrives
    snap1 = MarketSnapshot(1000, "kalshi", "KXSOCCERGAME-26OCT15-MAN", 0.30, 0.35, 10, 10)
    assert list(strategy.on_snapshot(snap1)) == []

    # Leg 2 arrives: Sum of asks is 0.35 + 0.35 = 0.70 < 1.00, BUT partition is NOT exhaustive (missing DRAW)!
    # Must defer and NOT emit orders!
    snap2 = MarketSnapshot(1050, "kalshi", "KXSOCCERGAME-26OCT15-CHEL", 0.30, 0.35, 10, 10)
    assert list(strategy.on_snapshot(snap2)) == []

    # Leg 3 arrives: All 3 legs now present! Total ask = 0.35 + 0.35 + 0.20 = 0.90 < 1.00 - fees.
    # Now all legs are present, so Dutch book executes across all 3 legs!
    snap3 = MarketSnapshot(1100, "kalshi", "KXSOCCERGAME-26OCT15-DRAW", 0.18, 0.20, 10, 10)
    orders = list(strategy.on_snapshot(snap3))
    assert len(orders) == 3
    assert {o.market_id for o in orders} == {
        "KXSOCCERGAME-26OCT15-MAN", "KXSOCCERGAME-26OCT15-CHEL", "KXSOCCERGAME-26OCT15-DRAW"
    }


def test_event_parity_arbitrage_budget_constraint():
    from pm_alpha.strategies import EventParityArbitrageStrategy

    # Basket cost per contract ~ 0.42 + 0.52 = 0.94 + fee = $0.9494
    # Allocated cash is only $0.50 -> Cannot afford 1.0 contract!
    strategy = EventParityArbitrageStrategy(
        min_edge=0.01, fee_rate=0.01, quantity=1.0, min_order_size=1.0, allocated_cash=0.50
    )
    snap1 = MarketSnapshot(1000, "kalshi", "KXLOLGAME-26OCT15BLGT1-BLG", 0.40, 0.42, 10.0, 10.0)
    snap2 = MarketSnapshot(1050, "kalshi", "KXLOLGAME-26OCT15BLGT1-T1", 0.50, 0.52, 10.0, 8.0)

    list(strategy.on_snapshot(snap1))
    orders = list(strategy.on_snapshot(snap2))
    assert orders == []  # Rejected due to budget constraint


def test_atmospheric_temperature_harvester_zero_cash_prevents_trading():
    from pm_alpha.strategies import AtmosphericTemperatureHarvester

    harvester = AtmosphericTemperatureHarvester(
        min_price=0.88,
        max_price=0.95,
        allocated_cash=0.0,
        dynamic_sizing=True,
    )
    assert harvester.calculate_sizing(0.92) == 0.0


def test_atmospheric_temperature_harvester_dynamic_hours_with_timestamp():
    from pm_alpha.strategies import AtmosphericTemperatureHarvester
    from pm_alpha.discovery import DiscoveredMarket

    meta = {
        "KXTEMP-TEST": DiscoveredMarket(
            ticker="KXTEMP-TEST", title="Test", close_time="2026-10-15T08:00:00Z",
            liquidity_dollars=100, volume=100, open_interest=100,
        )
    }
    harvester = AtmosphericTemperatureHarvester(market_metadata=meta)
    # 2 hours before close: 2026-10-15T06:00:00Z in ms
    t_2h_before_ms = 1792044000000  # matches 06:00 UTC
    import datetime
    ct = datetime.datetime.fromisoformat("2026-10-15T08:00:00+00:00")
    t_ms = int((ct.timestamp() - 7200) * 1000)
    hours = harvester.get_hours_to_close("KXTEMP-TEST", timestamp_ms=t_ms)
    assert round(hours, 2) == 2.0


def test_event_parity_arbitrage_rejects_spreads_and_ladders():
    from pm_alpha.strategies import EventParityArbitrageStrategy

    strategy = EventParityArbitrageStrategy(min_edge=0.01, fee_rate=0.01)

    # 1. Goal spread line with trailing digits e.g. TOR4 and TOR5
    snap_spread1 = MarketSnapshot(1000, "kalshi", "KXNHLSPREAD-26OCT15BUFTOR-TOR4", 0.10, 0.12, 10.0, 10.0)
    snap_spread2 = MarketSnapshot(1050, "kalshi", "KXNHLSPREAD-26OCT15BUFTOR-TOR5", 0.05, 0.07, 10.0, 10.0)
    assert not strategy.is_mutually_exclusive_partition("KXNHLSPREAD-26OCT15BUFTOR", [snap_spread1, snap_spread2])

    list(strategy.on_snapshot(snap_spread1))
    orders = list(strategy.on_snapshot(snap_spread2))
    assert orders == []

    # 2. Player prop rushing ladder
    snap_ladder1 = MarketSnapshot(1100, "kalshi", "KXNFLLADDERRSHYDS-26OCT18BUFLV-BUFJALLEN17", 0.10, 0.13, 10.0, 10.0)
    snap_ladder2 = MarketSnapshot(1150, "kalshi", "KXNFLLADDERRSHYDS-26OCT18BUFLV-BUFJCOOK4", 0.25, 0.27, 10.0, 10.0)
    assert not strategy.is_mutually_exclusive_partition("KXNFLLADDERRSHYDS-26OCT18BUFLV", [snap_ladder1, snap_ladder2])

    list(strategy.on_snapshot(snap_ladder1))
    orders_ladder = list(strategy.on_snapshot(snap_ladder2))
    assert orders_ladder == []


def test_paper_config_supports_quarter_kelly_order_notional():
    from pm_alpha.paper import PaperConfig, PaperRunner
    from pm_alpha.models import OrderIntent, Side

    cfg = PaperConfig()
    assert cfg.max_order_notional >= 15.0

    # 10 contracts @ $0.92 = $9.20 notional
    intent = OrderIntent(
        timestamp_ms=1000,
        venue="kalshi",
        market_id="KXTEMP-TEST",
        side=Side.BUY,
        quantity=10.0,
        limit_price=0.92,
        client_order_id="test-order",
        signal="test",
    )
    runner = PaperRunner(None, None, None, cfg)
    assert runner._is_allowed(intent)

