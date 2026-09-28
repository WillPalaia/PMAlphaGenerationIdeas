"""Continuously capture Kalshi books and run a no-order paper strategy."""

import argparse
import asyncio
import logging
import time

from pm_alpha.kalshi_source import KalshiPublicSource
from pm_alpha.discovery import KalshiMarketDiscovery
from pm_alpha.models import MarketSnapshot
from pm_alpha.paper import MultiStrategy, PaperConfig, PaperPortfolio, PaperRunner
from pm_alpha.storage import SnapshotStore
from pm_alpha.strategies import (
    BollingerReversionStrategy,
    BuyBelowThreshold,
    ComplementArbitrageStrategy,
    EmaCrossoverStrategy,
    FavoriteYieldStrategy,
    JumpFollowingStrategy,
    MeanReversionStrategy,
    MomentumStrategy,
    OrderBookImbalanceStrategy,
    RangeBreakoutStrategy,
    SpreadHarvestingMarketMaker,
    StableHighProbabilityStrategy,
    TimeDecayYieldStrategy,
    VwapPullbackStrategy,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*", default=[], help="Kalshi market tickers (optional if --discover)")
    parser.add_argument("--db", default="data/market_data.sqlite")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--discovery-interval", type=float, default=300.0)
    parser.add_argument("--market-limit", type=int, default=30, help="Maximum number of active markets to discover")
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--fee-rate", type=float, default=0.01)
    parser.add_argument("--reset-cash", action="store_true", help="Reset all strategy sub-portfolio cash to starting-cash")
    args = parser.parse_args()

    if not args.tickers and not args.discover:
        parser.error("provide at least one ticker or enable --discover")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log = logging.getLogger(__name__)

    store = SnapshotStore(args.db)
    portfolio = PaperPortfolio(
        store,
        starting_cash=args.starting_cash,
        fee_rate=args.fee_rate,
        reset_cash=args.reset_cash,
    )
    discovery = KalshiMarketDiscovery()

    # Settle any previously opened positions that have finalized
    unsettled_tickers = [
        market_id for (venue, market_id), pos in portfolio.positions.items()
        if pos.quantity > 0 and not pos.settled
    ]
    if unsettled_tickers:
        log.info("Checking settlements for %d existing open positions...", len(unsettled_tickers))
        settlements = await discovery.check_settlements(unsettled_tickers)
        for ticker, settlement_yes in settlements:
            log.info("Settling %s: result=%s", ticker, "YES" if settlement_yes else "NO")
            snap = MarketSnapshot(
                timestamp_ms=int(time.time() * 1000),
                venue="kalshi",
                market_id=ticker,
                yes_bid=None,
                yes_ask=None,
                resolved=True,
                settlement_yes=settlement_yes,
            )
            portfolio.process_snapshot(snap)

    initial_tickers = list(args.tickers)
    if not initial_tickers and args.discover:
        log.info("Discovering initial binary markets (limit %d)...", args.market_limit)
        discovered = await discovery.discover(limit=args.market_limit)
        initial_tickers = [market.ticker for market in discovered]
        log.info("Discovered %d active markets", len(initial_tickers))

    remaining_open = [
        market_id for (venue, market_id), pos in portfolio.positions.items()
        if pos.quantity > 0 and not pos.settled
    ]
    initial_watchlist = list(dict.fromkeys(initial_tickers + remaining_open))
    source = KalshiPublicSource(initial_watchlist or ["KXNFLRSHYDS-26SEP24ATLGB-ATLBROBINSON7-175"])
    next_discovery = asyncio.get_running_loop().time() + args.discovery_interval

    async def refresh() -> None:
        nonlocal next_discovery
        now = asyncio.get_running_loop().time()
        if not args.discover or now < next_discovery:
            return

        # 1. Settle any open positions that have resolved
        open_tickers = [
            market_id for (venue, market_id), pos in portfolio.positions.items()
            if pos.quantity > 0 and not pos.settled
        ]
        if open_tickers:
            settlements = await discovery.check_settlements(open_tickers)
            for ticker, settlement_yes in settlements:
                log.info(
                    "Market %s settled! Result=%s. Payout credited to cash.",
                    ticker,
                    "YES" if settlement_yes else "NO",
                )
                snap = MarketSnapshot(
                    timestamp_ms=int(time.time() * 1000),
                    venue="kalshi",
                    market_id=ticker,
                    yes_bid=None,
                    yes_ask=None,
                    resolved=True,
                    settlement_yes=settlement_yes,
                )
                portfolio.process_snapshot(snap)

        # 2. Discover new open markets
        discovered = await discovery.discover(limit=args.market_limit)
        discovered_tickers = [market.ticker for market in discovered]

        # 3. Maintain watchlist: newly discovered markets + any unsettled open positions
        still_open = [
            market_id for (venue, market_id), pos in portfolio.positions.items()
            if pos.quantity > 0 and not pos.settled
        ]
        active_watchlist = list(dict.fromkeys(discovered_tickers + still_open))
        if active_watchlist:
            source.set_market_tickers(active_watchlist)

        log.info(
            "Watchlist recycled: %d markets (%d active discovered, %d monitored open positions). Cash: $%.2f",
            len(active_watchlist),
            len(discovered_tickers),
            len(still_open),
            portfolio.cash,
        )
        next_discovery = now + args.discovery_interval

    strategy = MultiStrategy({
        # 1. Statistical Mean Reversion & Deep Oversold (Top Performers)
        "bollinger-reversion": BollingerReversionStrategy(lookback=15, entry_z=-2.0, exit_z=0.0),
        "bollinger-deep-oversold": BollingerReversionStrategy(lookback=15, entry_z=-2.5, exit_z=-0.5),
        "mean-reversion": MeanReversionStrategy(lookback=10, deviation=0.04, max_spread=0.08),

        # 2. Probability Calibration & Low-Volatility Anchoring (Top Performers)
        "stable-high-probability": StableHighProbabilityStrategy(lower_price=0.68, upper_price=0.72, lookback=5, max_range=0.03),
        "stable-conservative-80": StableHighProbabilityStrategy(lower_price=0.78, upper_price=0.85, lookback=6, max_range=0.03),
        "threshold": BuyBelowThreshold(0.40),

        # 3. Order Book Microstructure Depth & Jump Dynamics (Top Performers)
        "orderbook-imbalance": OrderBookImbalanceStrategy(imbalance_threshold=0.50, max_spread=0.05),
        "jump-following": JumpFollowingStrategy(jump_threshold=0.06, lookback=3, take_profit=0.15, stop_loss=0.10),

        # 4. Trend & Breakout Momentum (Fixed with Liquid Spread Filters)
        "ema-crossover": EmaCrossoverStrategy(fast_span=5, slow_span=20, min_cross_diff=0.015),
        "range-breakout": RangeBreakoutStrategy(lookback=20, breakout_margin=0.02),
        "vwap-pullback": VwapPullbackStrategy(lookback=15, pullback_threshold=0.02),
        "momentum": MomentumStrategy(lookback=3, minimum_move=0.03, take_profit=0.20, stop_loss=0.10, max_spread=0.08),

        # 5. Yield Harvesting & Arbitrage (Protected against Illiquid Book Traps)
        "favorite-yield": FavoriteYieldStrategy(min_probability=0.85, max_probability=0.96, max_spread=0.06, harvest_price=0.98, stop_loss_price=0.50),
        "time-decay-yield": TimeDecayYieldStrategy(target_min_price=0.80, target_max_price=0.95, max_spread=0.06, harvest_price=0.98, stop_loss_price=0.50),
        "spread-harvesting": SpreadHarvestingMarketMaker(min_spread=0.04, max_spread=0.15),
        "complement-arbitrage": ComplementArbitrageStrategy(min_edge=0.01),
    })
    log.info(
        "PAPER MODE ONLY: Running 16 diverse quantitative paper strategies concurrently in isolated sub-portfolios"
    )

    runner = PaperRunner(
        source,
        strategy,
        store,
        PaperConfig(poll_interval_seconds=args.interval),
        refresh=refresh,
        portfolio=portfolio,
    )
    try:
        await runner.run()
    except KeyboardInterrupt:
        runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
