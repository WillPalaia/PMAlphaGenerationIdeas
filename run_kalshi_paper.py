"""Continuously capture Kalshi books and run a no-order paper strategy."""

import argparse
import asyncio
import logging

from pm_alpha.kalshi_source import KalshiPublicSource
from pm_alpha.paper import PaperConfig, PaperRunner
from pm_alpha.storage import SnapshotStore


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("tickers", nargs="+", help="Kalshi market tickers")
    parser.add_argument("--db", default="data/market_data.sqlite")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    source = KalshiPublicSource(args.tickers)
    store = SnapshotStore(args.db)

    class NoOrderStrategy:
        def on_snapshot(self, snapshot):
            return []

    runner = PaperRunner(
        source,
        NoOrderStrategy(),
        store,
        PaperConfig(poll_interval_seconds=args.interval),
    )
    try:
        await runner.run()
    except KeyboardInterrupt:
        runner.stop()


if __name__ == "__main__":
    asyncio.run(main())
