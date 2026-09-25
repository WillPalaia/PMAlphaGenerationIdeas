"""Continuously capture Kalshi books and run a no-order paper strategy."""

import argparse
import asyncio
import logging

from pm_alpha.kalshi_source import KalshiPublicSource
from pm_alpha.discovery import KalshiMarketDiscovery
from pm_alpha.paper import MultiStrategy, PaperConfig, PaperPortfolio, PaperRunner
from pm_alpha.storage import SnapshotStore
from pm_alpha.strategies import (
    BuyBelowThreshold,
    MeanReversionStrategy,
    MomentumStrategy,
    StableHighProbabilityStrategy,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="+", help="Kalshi market tickers")
    parser.add_argument("--db", default="data/market_data.sqlite")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--discovery-interval", type=float, default=300.0)
    parser.add_argument("--starting-cash", type=float, default=100.0)
    parser.add_argument("--fee-rate", type=float, default=0.01)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    source = KalshiPublicSource(args.tickers)
    discovery = KalshiMarketDiscovery()
    next_discovery = 0.0

    async def refresh() -> None:
        nonlocal next_discovery
        now = asyncio.get_running_loop().time()
        if not args.discover or now < next_discovery:
            return
        discovered = await discovery.discover(limit=20)
        source.set_market_tickers([market.ticker for market in discovered])
        next_discovery = now + args.discovery_interval

    store = SnapshotStore(args.db)
    portfolio = PaperPortfolio(store, starting_cash=args.starting_cash, fee_rate=args.fee_rate)
    strategy = MultiStrategy({
        "threshold": BuyBelowThreshold(0.40),
        "momentum": MomentumStrategy(),
        "mean-reversion": MeanReversionStrategy(),
        "stable-high-probability": StableHighProbabilityStrategy(),
    })
    logging.getLogger(__name__).info(
        "PAPER MODE ONLY: intents are simulated against snapshots; no live orders are sent"
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
