from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class DiscoveredMarket:
    ticker: str
    title: str
    close_time: str
    liquidity_dollars: float
    volume: float
    open_interest: float


class KalshiMarketDiscovery:
    """Discover ordinary binary markets, excluding multivariate collections."""

    def __init__(
        self,
        base_url: str = "https://external-api.kalshi.com/trade-api/v2",
        max_pages: int = 5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_pages = max_pages

    async def discover(self, limit: int = 100) -> list[DiscoveredMarket]:
        return await asyncio.to_thread(self._discover, limit)

    def _discover(self, limit: int) -> list[DiscoveredMarket]:
        cursor = ""
        results: list[DiscoveredMarket] = []
        for _ in range(self.max_pages):
            url = f"{self.base_url}/markets?status=open&limit=1000&mve_filter=exclude"
            if cursor:
                url += f"&cursor={cursor}"
            request = Request(url, headers={"User-Agent": "pm-alpha-paper/0.1"})
            for attempt in range(4):
                try:
                    with urlopen(request, timeout=20) as response:
                        payload = json.load(response)
                    break
                except HTTPError as exc:
                    if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                        raise
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    delay = float(retry_after) if retry_after else 2.0 ** attempt
                    time.sleep(min(delay, 30.0))
            for market in payload.get("markets", []):
                ticker = market.get("ticker", "")
                if not ticker or ticker.startswith("KXMV") or market.get("market_type") != "binary":
                    continue
                results.append(
                    DiscoveredMarket(
                        ticker=ticker,
                        title=market.get("title", ""),
                        close_time=market.get("close_time", ""),
                        liquidity_dollars=float(market.get("liquidity_dollars") or 0),
                        volume=float(market.get("volume") or 0),
                        open_interest=float(market.get("open_interest") or 0),
                    )
                )
                if len(results) >= limit:
                    return results
            cursor = payload.get("cursor", "")
            if not cursor:
                break
        return results
