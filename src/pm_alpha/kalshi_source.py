from __future__ import annotations

import asyncio
import json
import time
from urllib.request import Request, urlopen

from .models import MarketSnapshot


class KalshiPublicSource:
    """Minimal unauthenticated Kalshi order-book source for paper capture."""

    def __init__(
        self,
        market_tickers: list[str],
        base_url: str = "https://external-api.kalshi.com/trade-api/v2",
    ) -> None:
        if not market_tickers:
            raise ValueError("at least one market ticker is required")
        self.market_tickers = tuple(market_tickers)
        self.base_url = base_url.rstrip("/")

    def set_market_tickers(self, market_tickers: list[str]) -> None:
        if not market_tickers:
            raise ValueError("at least one market ticker is required")
        self.market_tickers = tuple(dict.fromkeys(market_tickers))

    async def snapshots(self) -> list[MarketSnapshot]:
        return await asyncio.gather(
            *(asyncio.to_thread(self._fetch, ticker) for ticker in self.market_tickers)
        )

    def _fetch(self, ticker: str) -> MarketSnapshot:
        request = Request(f"{self.base_url}/markets/{ticker}/orderbook")
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
        orderbook = payload.get("orderbook", {})
        yes_bids = orderbook.get("yes", [])
        no_bids = orderbook.get("no", [])
        yes_bid, bid_size = self._best(yes_bids, reverse=True)
        no_bid, no_bid_size = self._best(no_bids, reverse=True)
        yes_ask = None if no_bid is None else 1.0 - no_bid
        ask_size = no_bid_size
        return MarketSnapshot(
            timestamp_ms=time.time_ns() // 1_000_000,
            venue="kalshi",
            market_id=ticker,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            bid_size=bid_size,
            ask_size=ask_size,
        )

    @staticmethod
    def _best(levels: list, reverse: bool) -> tuple[float | None, float]:
        parsed = [(float(level[0]) / 100.0, float(level[1])) for level in levels if len(level) >= 2]
        if not parsed:
            return None, 0.0
        price, size = sorted(parsed, key=lambda level: level[0], reverse=reverse)[0]
        return price, size
