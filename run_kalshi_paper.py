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
    OrderFlowImbalanceStrategy,
    PennyContrarianStrategy,
    RangeBreakoutStrategy,
    SpreadHarvestingMarketMaker,
    StableHighProbabilityStrategy,
    TimeDecayYieldStrategy,
    VolumeWeightedMeanReversionStrategy,
    VwapPullbackStrategy,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="*", default=[], help="Kalshi market tickers (optional if --discover)")
    parser.add_argument("--db", default="data/market_data.sqlite")
    parser.add_argument("--interval", type=float, default=2.5)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--discovery-interval", type=float, default=300.0)
    parser.add_argument("--market-limit", type=int, default=140, help="Maximum number of active markets to discover")
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

    discovered_markets_meta = {}
    initial_tickers = list(args.tickers)
    if not initial_tickers and args.discover:
        log.info("Discovering initial binary markets across diversified sectors (limit %d)...", args.market_limit)
        discovered = await discovery.discover(limit=args.market_limit)
        initial_tickers = [market.ticker for market in discovered]
        discovered_markets_meta = {m.ticker: m for m in discovered}
        log.info("Discovered %d active markets across diversified sectors", len(initial_tickers))

    remaining_open = [
        market_id for (venue, market_id), pos in portfolio.positions.items()
        if pos.quantity > 0 and not pos.settled
    ]
    active_watchlist = list(dict.fromkeys(initial_tickers + remaining_open))
    source = KalshiPublicSource(active_watchlist or ["KXNFLRSHYDS-26SEP24ATLGB-ATLBROBINSON7-175"], concurrency=8)
    next_discovery = asyncio.get_running_loop().time() + args.discovery_interval

    strategy_definitions = {
        # 1. Spread-Capturing Market Maker (Strict 1-Lot Inventory to Prevent One-Sided Risk)
        # Captures wide prediction market spreads (4¢-10¢) using micro-price skew,
        # with strict 1-contract max inventory per market so it never accumulates toxic inventory.
        "spread-harvesting": SpreadHarvestingMarketMaker(
            min_spread=0.04,
            max_spread=0.10,
            inventory_skew=0.02,
            max_inventory=1.0,
            quantity=1.0,
            aggressiveness=0.40,
            min_profit=0.02,
            min_price=0.20,
            max_price=0.80,
            prefix="mm-harvest",
            strategy_name="spread-harvesting",
        ),

        # 2. Pure Hold-to-Maturity Favorite Harvester (Zero Naive Stop-Losses)
        # Exploits the empirical favorite-longshot bias (85¢-94¢ contracts settle YES >90% of the time).
        # Eliminates false stop-outs: harvests early if bid reaches 98¢, otherwise holds to expiration.
        "conservative-yield": StableHighProbabilityStrategy(
            lower_price=0.85,
            upper_price=0.94,
            lookback=6,
            max_range=0.03,
            min_depth=3.0,
            take_profit_price=0.98,
            stop_loss_price=None,
            max_spread=0.025,
            prefix="conservative-yield",
            strategy_name="conservative-yield",
        ),

        # 3. Extreme Statistical Reversion (Selective Panic Buyer with Mandatory Time-Decay Pre-Expiry Square-Off)
        # Strictly buys extreme dislocations (Z <= -2.5) with tight spread, exiting on profitable mean reversion.
        # Enforces a mandatory 120-tick / 2-hour holding bound so un-reverted positions are never held into binary expiration.
        "deep-oversold": BollingerReversionStrategy(
            lookback=15,
            entry_z=-2.5,
            exit_z=-0.5,
            prefix="deep-oversold",
            strategy_name="deep-oversold",
            min_price=0.20,
            max_price=0.75,
            min_std=0.02,
            min_stop_loss_bid=0.15,
            max_spread=0.035,
            min_profit_target=0.02,
            max_holding_ticks=120,
            max_holding_ms=7200000,
        ),

        # 4. Parity & Dutch Book Arbitrage (Risk-Free Structural Mispricing)
        # Automatically detects crossed/locked books (bid >= ask) and structural Dutch book anomalies.
        "complement-arbitrage": ComplementArbitrageStrategy(
            min_edge=0.0,
            quantity=1.0,
        ),
    }

    strategy_baskets = {
        "spread-harvesting": {"sports", "macro_finance"},
        "conservative-yield": {"macro_finance", "weather"},
        "deep-oversold": {"macro_finance", "sports"},
        "complement-arbitrage": {"macro_finance", "sports", "weather", "politics"},
    }

    strategy = MultiStrategy(
        strategy_definitions,
        baskets=strategy_baskets,
        market_metadata=discovered_markets_meta,
    )

    def log_strategy_basket_summary() -> None:
        log.info("--- Strategy Market Basket Allocation Summary ---")
        for name in strategy_definitions:
            spec = strategy_baskets.get(name, {"all"})
            count = sum(
                1 for t in active_watchlist
                if strategy.is_market_allowed(name, MarketSnapshot(0, "kalshi", t))
            )
            spec_str = ",".join(spec) if isinstance(spec, set) else str(spec)
            log.info("  [%-24s] Basket: %-32s -> Monitoring %3d markets", name, spec_str, count)
        log.info("-------------------------------------------------")

    log_strategy_basket_summary()

    async def refresh() -> None:
        nonlocal next_discovery, active_watchlist
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
        new_meta = {m.ticker: m for m in discovered}
        strategy.set_market_metadata(new_meta)

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
        log_strategy_basket_summary()
        next_discovery = now + args.discovery_interval
    log.info(
        "PAPER MODE ONLY: Running 3 focused institutional-grade paper strategies concurrently in isolated sub-portfolios"
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
