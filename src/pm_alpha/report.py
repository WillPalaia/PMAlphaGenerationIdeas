from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MarketReport:
    venue: str
    market_id: str
    snapshots: int
    first_timestamp_ms: int
    last_timestamp_ms: int
    quote_coverage: float
    average_spread: float | None
    average_bid_size: float
    average_ask_size: float


def inspect_database(path: str | Path) -> tuple[MarketReport, ...]:
    """Return descriptive, non-trading diagnostics for captured snapshots."""
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT venue, market_id, COUNT(*), MIN(timestamp_ms), MAX(timestamp_ms),
                   AVG(CASE WHEN yes_bid IS NOT NULL AND yes_ask IS NOT NULL THEN 1.0 ELSE 0.0 END),
                   AVG(CASE WHEN yes_bid IS NOT NULL AND yes_ask IS NOT NULL
                       THEN yes_ask - yes_bid END),
                   AVG(bid_size), AVG(ask_size)
            FROM market_snapshots
            GROUP BY venue, market_id
            ORDER BY venue, market_id
            """
        ).fetchall()
    return tuple(
        MarketReport(
            venue=row[0],
            market_id=row[1],
            snapshots=int(row[2]),
            first_timestamp_ms=int(row[3]),
            last_timestamp_ms=int(row[4]),
            quote_coverage=float(row[5] or 0.0),
            average_spread=None if row[6] is None else float(row[6]),
            average_bid_size=float(row[7] or 0.0),
            average_ask_size=float(row[8] or 0.0),
        )
        for row in rows
    )
