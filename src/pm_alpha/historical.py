from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import MarketSnapshot


def load_snapshots_csv(path: str | Path) -> list[MarketSnapshot]:
    """Load normalized snapshots from CSV without silently dropping bad rows."""

    required = {"timestamp_ms", "venue", "market_id", "yes_bid", "yes_ask", "bid_size", "ask_size"}
    snapshots: list[MarketSnapshot] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        for line_number, row in enumerate(reader, start=2):
            try:
                resolved = _parse_bool(row.get("resolved", "false"))
                settlement = row.get("settlement_yes", "")
                snapshots.append(
                    MarketSnapshot(
                        timestamp_ms=int(row["timestamp_ms"]),
                        venue=row["venue"],
                        market_id=row["market_id"],
                        yes_bid=_optional_float(row["yes_bid"]),
                        yes_ask=_optional_float(row["yes_ask"]),
                        bid_size=float(row["bid_size"]),
                        ask_size=float(row["ask_size"]),
                        resolved=resolved,
                        settlement_yes=None if settlement == "" else _parse_bool(settlement),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid snapshot at CSV line {line_number}: {exc}") from exc
    return snapshots


def fetch_kalshi_candlesticks(
    ticker: str,
    *,
    start_ts: int,
    end_ts: int,
    period_interval: int = 60,
    base_url: str = "https://external-api.kalshi.com/trade-api/v2",
) -> list[MarketSnapshot]:
    """Fetch public Kalshi candles and convert them to replayable mid snapshots.

    Candles are directional research data, not execution-grade order-book data.
    A synthetic one-contract spread is used only to make the data compatible
    with signal backtests; it must not be used for latency or market-making claims.
    """

    if start_ts < 0 or end_ts <= start_ts:
        raise ValueError("invalid candle time range")
    query = urlencode(
        {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval}
    )
    request = Request(f"{base_url.rstrip('/')}/markets/{ticker}/candlesticks?{query}")
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)
    candles = payload.get("candlesticks", [])
    snapshots: list[MarketSnapshot] = []
    for candle in candles:
        close = _candle_close(candle)
        if close is None:
            continue
        timestamp_ms = _candle_timestamp_ms(candle)
        snapshots.append(
            MarketSnapshot(
                timestamp_ms=timestamp_ms,
                venue="kalshi",
                market_id=ticker,
                yes_bid=max(0.0, close - 0.005),
                yes_ask=min(1.0, close + 0.005),
                bid_size=1.0,
                ask_size=1.0,
            )
        )
    return snapshots


def _candle_close(candle: dict) -> float | None:
    value = candle.get("yes") or candle.get("price") or candle.get("close")
    if isinstance(value, dict):
        value = value.get("close") or value.get("price")
    if value is None:
        return None
    numeric = float(value)
    if numeric > 1:
        numeric /= 100.0
    return numeric


def _candle_timestamp_ms(candle: dict) -> int:
    value = candle.get("end_ts") or candle.get("ts") or candle.get("timestamp")
    if value is None:
        raise ValueError("candle has no timestamp")
    numeric = int(value)
    return numeric * 1000 if numeric < 10_000_000_000 else numeric


def _optional_float(value: str | None) -> float | None:
    return None if value in (None, "") else float(value)


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"invalid boolean value {value!r}")
