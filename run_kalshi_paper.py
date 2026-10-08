"""Continuously capture Kalshi books and run a no-order paper strategy."""

import argparse
import asyncio
import logging
import time
from pathlib import Path

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
    MicrostructureRetailHerdFader,
    AtmosphericTemperatureHarvester,
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

    # Load dynamic auto-tuned strategy configuration if present
    config_file = Path("/var/lib/pm-alpha/strategy_config.json")
    if not config_file.exists():
        config_file = Path("/opt/pm-alpha/strategy_config.json")
    if not config_file.exists():
        config_file = Path("strategy_config.json")
    dyn_cfg: dict = {}
    if config_file.exists():
        try:
            import json
            with open(config_file, "r", encoding="utf-8") as f:
                dyn_cfg = json.load(f)
            log.info("Loaded dynamic strategy config from %s", config_file)
        except Exception as e:
            log.warning("Could not read dynamic config: %s", e)

    sh_cfg = dyn_cfg.get("spread-harvesting", {})
    cy_cfg = dyn_cfg.get("conservative-yield", {})
    mh_cfg = dyn_cfg.get("microstructure-herd-fader", {})
    ca_cfg = dyn_cfg.get("complement-arbitrage", {})

    strategy_definitions = {
        # 1. Spread-Capturing Market Maker with Pre-Settlement & Adverse Flow Square-Off
        # Quoting ONLY in slow, non-volatile sectors (Weather & Macro/Finance; NO live sports).
        # Equipped with price-jump detection (max_price_jump=0.01) and rolling-range bounds (max_rolling_range=0.02).
        # Crucially includes max_holding_ticks, max_holding_ms, and stop_loss_margin to
        # liquidate stale/toxic inventory before binary expiration ($0.00 wipeout protection).
        "spread-harvesting": SpreadHarvestingMarketMaker(
            min_spread=sh_cfg.get("min_spread", 0.04),
            max_spread=sh_cfg.get("max_spread", 0.10),
            inventory_skew=sh_cfg.get("inventory_skew", 0.02),
            max_inventory=sh_cfg.get("max_inventory", 1.0),
            quantity=1.0,
            aggressiveness=sh_cfg.get("aggressiveness", 0.40),
            min_profit=sh_cfg.get("min_profit", 0.02),
            min_price=sh_cfg.get("min_price", 0.20),
            max_price=sh_cfg.get("max_price", 0.80),
            max_price_jump=0.01,
            max_rolling_range=0.02,
            volatility_lookback=15,
            max_holding_ticks=sh_cfg.get("max_holding_ticks", 60),
            max_holding_ms=sh_cfg.get("max_holding_ms", 3600000),
            stop_loss_margin=sh_cfg.get("stop_loss_margin", 0.06),
            salvage_floor=0.03,
            prefix="mm-harvest",
            strategy_name="spread-harvesting",
        ),

        # 2. Pure Hold-to-Maturity Favorite Harvester (Weather & Macro Determinism)
        # NEVER sells early: zero stop-loss and zero take-profit. Holds 100% of contracts strictly to maturity.
        # Targets high-certainty contracts with tight spread in bounded physical sectors.
        "conservative-yield": AtmosphericTemperatureHarvester(
            min_price=cy_cfg.get("min_price", 0.88),
            max_price=cy_cfg.get("max_price", 0.95),
            lookback=cy_cfg.get("lookback", 6),
            max_range=cy_cfg.get("max_range", 0.03),
            min_depth=3.0,
            max_spread=cy_cfg.get("max_spread", 0.025),
            quantity=1.0,
            prefix="conservative-yield",
            strategy_name="conservative-yield",
        ),

        # 3. Microstructure Retail Herd Fader (Contrarian Fade of Retail Speculative Frenzy)
        # Replaces ruined deep-oversold. Fades retail mob rushes on lottery longshots (<= 0.35)
        # when institutional ask walls absorb the move and micro-price pulls toward bid.
        # Strictly limited to intra-day horizon with take-profit and stop-loss.
        "microstructure-herd-fader": MicrostructureRetailHerdFader(
            lookback_snapshots=mh_cfg.get("lookback_snapshots", 15),
            max_target_price=mh_cfg.get("max_target_price", 0.35),
            min_price_runup=mh_cfg.get("min_price_runup", 0.04),
            min_depth_absorption=mh_cfg.get("min_depth_absorption", 2.0),
            min_microprice_discount=0.015,
            max_spread=mh_cfg.get("max_spread", 0.035),
            take_profit=mh_cfg.get("take_profit", 0.04),
            stop_loss=mh_cfg.get("stop_loss", 0.05),
            max_holding_ticks=mh_cfg.get("max_holding_ticks", 40),
            quantity=1.0,
            prefix="herd-fade",
            strategy_name="microstructure-herd-fader",
        ),

        # 4. Parity & Dutch Book Arbitrage (Risk-Free Structural Mispricing across All Markets)
        # Scans EVERY market discovered (all categories, zero basket limits) for locked/crossed books.
        "complement-arbitrage": ComplementArbitrageStrategy(
            min_edge=ca_cfg.get("min_edge", 0.0),
            quantity=1.0,
        ),
    }

    strategy_baskets = {
        # Exclude sports from market making completely! Only quote slow, non-courtsided markets
        "spread-harvesting": {"macro_finance", "weather"},
        "conservative-yield": {"macro_finance", "weather"},
        "microstructure-herd-fader": {"macro_finance", "sports"},
        # Arbitrage scans across ALL markets without restriction
        "complement-arbitrage": "all",
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
