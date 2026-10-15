from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def classify_market(ticker: str, title: str = "") -> str:
    """Classify a prediction market into a canonical sector category."""
    t = ticker.upper()
    prefix = t.split("-")[0]

    # 1. Macro, Commodities, Rates, Financials, Crypto
    if any(prefix.startswith(x) for x in [
        "KXGOLD", "KXSILVER", "KXPLATINUM", "KXPALLADIUM", "KXWTI", "KXGAS",
        "KXAAAGASDCO", "KXNKY", "KXBTC", "KXETH", "KXINFLATION", "KXFED",
        "KXSOFR", "KXGDP", "KXJOB", "KXUNEMPLOYMENT", "KXSP500", "KXNASDAQ",
        "KXTREASURY", "KXCPI", "KXRECESSION", "KXOPEC", "KXFIN", "KXCOMMODITY",
    ]):
        return "macro_finance"

    # 2. Weather & Climate (All Cities, Rain, Temp, Snow, Wind, Hurricanes)
    if any(prefix.startswith(x) for x in [
        "KXTEMP", "KXRAIN", "KXSNOW", "KXHURRICANE", "KXWEATHER",
        "KXLOWT", "KXHIGHT", "KXAVGT", "KXHIGH", "KXLOW", "KXWIND", "MICHTEMP",
        "RAIN", "SNOW", "TEMP", "HURCAT",
    ]):
        return "weather"

    # 3. Sports (Traditional & Racing)
    if any(prefix.startswith(x) for x in [
        "KXNFL", "KXMLB", "KXNBA", "KXWNBA", "KXNHL", "KXATP", "KXWTA",
        "KXITF", "KXSOCCER", "KXEPL", "KXLIGA", "KXMLS", "KXUFC", "KXBOXING",
        "KXNCAA", "KXTTELITE", "KXPICKLEBALL", "KXGOLF", "KXPGA", "KXF1",
        "KXNASCAR", "KXCHAMPIONSLEAGUE", "KXSERIEA", "KXBUNDESLIGA",
    ]):
        return "sports"

    # 4. Esports
    if any(prefix.startswith(x) for x in [
        "KXCS2", "KXDOTA", "KXVALORANT", "KXLOL", "KXOVERWATCH", "KXROCKETLEAGUE",
        "KXWORLDS", "KXLEAGUE", "KXESPORTS",
        "KXESOCCER", "KXEBASKETBALL",
    ]):
        return "esports"

    # 5. Politics, Elections & Government
    if any(prefix.startswith(x) for x in [
        "KXPRES", "KXSENATE", "KXHOUSE", "KXPOLL", "KXELECTION", "KXCONGRESS",
        "KXSUPREME", "KXGOV", "KXDEBATE",
    ]):
        return "politics"

    # Secondary title-based heuristics
    lower_title = title.lower()
    if any(kw in lower_title for kw in [
        "league of legends", "lol worlds", "counter-strike", "cs2", "dota 2", "valorant", "esports",
        "lck", "lpl", "lec", "lcs", "worlds 202",
    ]):
        return "esports"
    if any(kw in lower_title for kw in [
        "inflation", "cpi", "fed", "interest rate", "unemployment", "gdp",
        "gas price", "crude oil", "gold", "treasury", "recession",
    ]):
        return "macro_finance"
    if any(kw in lower_title for kw in [
        "temperature", "rainfall", "snowfall", "hurricane", "high temp", "low temp",
        "temp in", "rain in", "snow in", "weather", "snowstorm", "rain daily",
    ]):
        return "weather"
    if any(kw in lower_title for kw in ["wins match", "touchdown", "points", "goals", "runs"]):
        return "sports"

    return "other"


@dataclass(frozen=True)
class DiscoveredMarket:
    ticker: str
    title: str
    close_time: str
    liquidity_dollars: float
    volume: float
    open_interest: float
    category: str = "other"
    hours_to_close: float = 9999.0
    yes_bid: float | None = None
    yes_ask: float | None = None
    bid_size: float = 0.0
    ask_size: float = 0.0
    event_ticker: str = ""
    mutually_exclusive: bool = False


class KalshiMarketDiscovery:
    """Discover ordinary binary markets with balanced category quotas and close-time prioritization."""

    def __init__(
        self,
        base_url: str = "https://external-api.kalshi.com/trade-api/v2",
        max_pages: int = 8,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_pages = max_pages

    async def discover(self, limit: int = 100) -> list[DiscoveredMarket]:
        return await asyncio.to_thread(self._discover, limit)

    def _discover(self, limit: int) -> list[DiscoveredMarket]:
        cursor = ""
        candidates: list[DiscoveredMarket] = []
        now_utc = datetime.datetime.now(datetime.timezone.utc)

        for _ in range(self.max_pages):
            url = f"{self.base_url}/markets?status=open&limit=1000&mve_filter=exclude"
            if cursor:
                url += f"&cursor={cursor}"
            request = Request(url, headers={"User-Agent": "pm-alpha-paper/0.1"})
            payload = {}
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

            markets_list = payload.get("markets", [])
            for market in markets_list:
                ticker = market.get("ticker", "")
                if not ticker or ticker.startswith("KXMV") or market.get("market_type") != "binary":
                    continue

                volume = float(market.get("volume_24h_fp") or market.get("volume") or 0)
                liquidity = float(market.get("liquidity_dollars") or 0)
                open_interest = float(market.get("open_interest_fp") or market.get("open_interest") or 0)
                bid_size = float(market.get("yes_bid_size_fp") or 0)
                ask_size = float(market.get("yes_ask_size_fp") or 0)
                yes_bid = float(market.get("yes_bid_dollars")) if market.get("yes_bid_dollars") and bid_size > 0 else None
                yes_ask = float(market.get("yes_ask_dollars")) if market.get("yes_ask_dollars") and ask_size > 0 else None

                # Discard completely dead markets (no volume, no book quotes, no liquidity)
                # But keep if small list
                if volume == 0 and liquidity == 0 and open_interest == 0 and bid_size == 0 and ask_size == 0:
                    if len(markets_list) > 10:
                        continue

                t_upper = ticker.upper()
                # Filter out virtual 24/7 esports that crowd out real esports with zero liquidity
                if any(t_upper.startswith(p) for p in ("KXESOCCER", "KXEBASKETBALL")):
                    if volume == 0 or (liquidity == 0 and bid_size == 0 and ask_size == 0):
                        continue

                title = market.get("title", "")
                close_time = market.get("close_time", "")
                hours_to_close = 9999.0
                if close_time:
                    try:
                        close_dt = datetime.datetime.fromisoformat(close_time.replace("Z", "+00:00"))
                        hours_to_close = max(0.0, (close_dt - now_utc).total_seconds() / 3600.0)
                    except Exception:
                        pass

                event_ticker = market.get("event_ticker") or ""
                mutually_exclusive = bool(market.get("mutually_exclusive") or market.get("event", {}).get("mutually_exclusive", False))

                category = classify_market(ticker, title)
                candidates.append(
                    DiscoveredMarket(
                        ticker=ticker,
                        title=title,
                        close_time=close_time,
                        liquidity_dollars=liquidity,
                        volume=volume,
                        open_interest=open_interest,
                        category=category,
                        hours_to_close=hours_to_close,
                        yes_bid=yes_bid,
                        yes_ask=yes_ask,
                        bid_size=bid_size,
                        ask_size=ask_size,
                        event_ticker=event_ticker,
                        mutually_exclusive=mutually_exclusive,
                    )
                )

            cursor = payload.get("cursor", "")
            if not cursor or len(candidates) >= limit * 6:
                break

        if not candidates:
            return []

        if len(candidates) <= limit:
            return candidates

        # Group by category and sort within category:
        # Priority 1: Closing soon (0.05h < hours_to_close <= 48h) with volume or book quotes
        # Priority 2: High activity (volume, liquidity, book depth)
        by_cat: dict[str, list[DiscoveredMarket]] = defaultdict(list)
        for cand in candidates:
            by_cat[cand.category].append(cand)

        HUMAN_ESPORTS_PREFIXES = (
            "KXLOL", "KXLOLGAME", "KXCS2", "KXCS2GAME", "KXCS2MAP",
            "KXDOTA", "KXVALORANT", "KXOVERWATCH", "KXROCKETLEAGUE",
            "KXWORLDS", "KXLEAGUE", "KXESPORTS",
        )
        for cat, items in by_cat.items():
            if cat == "esports":
                items.sort(
                    key=lambda x: (
                        # Prioritize real human esports with volume > 0 and open interest
                        3 if any(x.ticker.upper().startswith(p) for p in HUMAN_ESPORTS_PREFIXES) and x.volume > 0 and x.open_interest > 0
                        else 2 if any(x.ticker.upper().startswith(p) for p in HUMAN_ESPORTS_PREFIXES) and x.volume > 0
                        else 1 if any(x.ticker.upper().startswith(p) for p in HUMAN_ESPORTS_PREFIXES) and (x.bid_size > 0 or x.ask_size > 0 or x.liquidity_dollars > 0)
                        else 0 if any(x.ticker.upper().startswith(p) for p in HUMAN_ESPORTS_PREFIXES)
                        else -1,  # Deprioritize virtual esports
                        1 if (0.05 < x.hours_to_close <= 48.0 and (x.volume > 0 or x.bid_size > 0 or x.ask_size > 0)) else 0,
                        x.volume + x.open_interest + x.liquidity_dollars + x.bid_size + x.ask_size,
                        -x.hours_to_close if x.hours_to_close <= 48.0 else 0,
                    ),
                    reverse=True,
                )
            else:
                items.sort(
                    key=lambda x: (
                        1 if (0.05 < x.hours_to_close <= 48.0 and (x.volume > 0 or x.bid_size > 0 or x.ask_size > 0)) else 0,
                        x.volume + x.liquidity_dollars + x.bid_size + x.ask_size,
                        -x.hours_to_close if x.hours_to_close <= 48.0 else 0,
                    ),
                    reverse=True,
                )

        # Allocate category quotas dynamically based on requested limit
        # Increased esports quota to 20% (up to 40 markets on 200 market limit)
        quota_weights = {
            "weather": 0.40,
            "macro_finance": 0.25,
            "esports": 0.20,
            "sports": 0.09,
            "politics": 0.03,
            "other": 0.03,
        }

        selected: list[DiscoveredMarket] = []
        selected_tickers: set[str] = set()

        for cat, weight in quota_weights.items():
            cat_quota = max(1, int(round(limit * weight)))
            for item in by_cat[cat][:cat_quota]:
                if item.ticker not in selected_tickers and len(selected) < limit:
                    selected.append(item)
                    selected_tickers.add(item.ticker)

        # Fill remaining slots with rollover priority (weather first, then macro, then esports, etc.)
        if len(selected) < limit:
            remaining: list[DiscoveredMarket] = []
            for cat_name in ("weather", "macro_finance", "esports", "sports", "politics", "other"):
                for item in by_cat.get(cat_name, []):
                    if item.ticker not in selected_tickers:
                        remaining.append(item)
            for item in remaining:
                selected.append(item)
                selected_tickers.add(item.ticker)
                if len(selected) >= limit:
                    break

        return selected[:limit]

    async def check_settlements(self, tickers: list[str]) -> list[tuple[str, bool]]:
        """Check settlement status for a list of tickers.

        Returns a list of (ticker, settlement_yes: bool) for any markets that
        have determined/finalized with a definitive 'yes' or 'no' result.
        """
        if not tickers:
            return []
        return await asyncio.to_thread(self._check_settlements, tickers)

    def _check_settlements(self, tickers: list[str]) -> list[tuple[str, bool]]:
        resolved: list[tuple[str, bool]] = []
        batch_size = 50
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]
            url = f"{self.base_url}/markets?tickers={','.join(batch)}"
            request = Request(url, headers={"User-Agent": "pm-alpha-paper/0.1"})
            payload = {}
            for attempt in range(4):
                try:
                    with urlopen(request, timeout=15) as response:
                        payload = json.load(response)
                    break
                except HTTPError as exc:
                    if exc.code not in {429, 500, 502, 503, 504} or attempt == 3:
                        break
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    delay = float(retry_after) if retry_after else 2.0 ** attempt
                    time.sleep(min(delay, 30.0))
                except OSError:
                    break
            for market in payload.get("markets", []):
                ticker = market.get("ticker", "")
                status = str(market.get("status", "")).lower()
                result = str(market.get("result", "")).lower()
                if status in {"determined", "finalized", "settled"} or result in {"yes", "no"}:
                    resolved.append((ticker, result == "yes"))
        return resolved
