"""Continuously capture Kalshi books and run a no-order paper strategy."""

import argparse
import asyncio
import logging

from pm_alpha.kalshi_source import KalshiPublicSource
from pm_alpha.discovery import KalshiMarketDiscovery
from pm_alpha.paper import PaperConfig, PaperRunner
from pm_alpha.storage import SnapshotStore


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="+", help="Kalshi market tickers")
    parser.add_argument("--db", default="data/market_data.sqlite")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--discovery-interval", type=float, default=300.0)
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

    class NoOrderStrategy:
        def on_snapshot(self, snapshot):
            return []

    runner = PaperRunner(
        source,
        NoOrderStrategy(),
        store,
        PaperConfig(poll_interval_seconds=args.interval),
        refresh=refresh,
    )
    try:
        await runner.run()
    except KeyboardInterrupt:
        runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
