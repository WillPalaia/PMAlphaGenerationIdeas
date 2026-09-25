from __future__ import annotations

import asyncio
import json
import time
from urllib.error import HTTPError
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
        request = Request(
            f"{self.base_url}/markets/{ticker}/orderbook",
            headers={"User-Agent": "pm-alpha-paper/0.1"},
        )
        payload = self._fetch_json_with_backoff(request)
        orderbook = payload.get("orderbook", {})
        if not orderbook and payload.get("orderbook_fp"):
            orderbook = payload["orderbook_fp"]
            yes_bids = orderbook.get("yes_dollars", [])
            no_bids = orderbook.get("no_dollars", [])
        else:
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
    def _fetch_json_with_backoff(request: Request) -> dict:
        for attempt in range(4):
            try:
                with urlopen(request, timeout=10) as response:
                    return json.load(response)
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                    raise
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after else 2.0 ** attempt
                time.sleep(min(delay, 30.0))
        raise RuntimeError("unreachable retry state")

    @staticmethod
    def _best(levels: list, reverse: bool) -> tuple[float | None, float]:
        parsed = []
        for level in levels:
            if isinstance(level, dict):
                price = level.get("price_dollars") or level.get("price")
                quantity = level.get("quantity") or level.get("size")
            elif len(level) >= 2:
                price, quantity = level[0], level[1]
            else:
                continue
            if price is None or quantity is None:
                continue
            numeric_price = float(price)
            if numeric_price > 1:
                numeric_price /= 100.0
            parsed.append((numeric_price, float(quantity)))
        if not parsed:
            return None, 0.0
        price, size = sorted(parsed, key=lambda level: level[0], reverse=reverse)[0]
        return price, size
