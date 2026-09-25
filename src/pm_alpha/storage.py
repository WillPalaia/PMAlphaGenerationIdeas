from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .models import MarketSnapshot


SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms INTEGER NOT NULL,
    venue TEXT NOT NULL,
    market_id TEXT NOT NULL,
    yes_bid REAL,
    yes_ask REAL,
    bid_size REAL NOT NULL,
    ask_size REAL NOT NULL,
    resolved INTEGER NOT NULL,
    settlement_yes INTEGER,
    UNIQUE(timestamp_ms, venue, market_id)
);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_lookup
    ON market_snapshots (venue, market_id, timestamp_ms);
"""


class SnapshotStore:
    """Append-only SQLite persistence for normalized market snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def append(self, snapshot: MarketSnapshot) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO market_snapshots
                (timestamp_ms, venue, market_id, yes_bid, yes_ask,
                 bid_size, ask_size, resolved, settlement_yes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.timestamp_ms,
                    snapshot.venue,
                    snapshot.market_id,
                    snapshot.yes_bid,
                    snapshot.yes_ask,
                    snapshot.bid_size,
                    snapshot.ask_size,
                    int(snapshot.resolved),
                    None if snapshot.settlement_yes is None else int(snapshot.settlement_yes),
                ),
            )
            return cursor.rowcount == 1

    def append_many(self, snapshots: Iterable[MarketSnapshot]) -> int:
        inserted = 0
        with self._connect() as connection:
            for snapshot in snapshots:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO market_snapshots
                    (timestamp_ms, venue, market_id, yes_bid, yes_ask,
                     bid_size, ask_size, resolved, settlement_yes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.timestamp_ms,
                        snapshot.venue,
                        snapshot.market_id,
                        snapshot.yes_bid,
                        snapshot.yes_ask,
                        snapshot.bid_size,
                        snapshot.ask_size,
                        int(snapshot.resolved),
                        None if snapshot.settlement_yes is None else int(snapshot.settlement_yes),
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def load(
        self,
        venue: str,
        market_id: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> list[MarketSnapshot]:
        clauses = ["venue = ?", "market_id = ?"]
        values: list[object] = [venue, market_id]
        if start_ms is not None:
            clauses.append("timestamp_ms >= ?")
            values.append(start_ms)
        if end_ms is not None:
            clauses.append("timestamp_ms <= ?")
            values.append(end_ms)
        query = (
            "SELECT timestamp_ms, venue, market_id, yes_bid, yes_ask, "
            "bid_size, ask_size, resolved, settlement_yes "
            "FROM market_snapshots WHERE "
            + " AND ".join(clauses)
            + " ORDER BY timestamp_ms"
        )
        with self._connect() as connection:
            rows = connection.execute(query, values).fetchall()
        return [
            MarketSnapshot(
                timestamp_ms=row[0],
                venue=row[1],
                market_id=row[2],
                yes_bid=row[3],
                yes_ask=row[4],
                bid_size=row[5],
                ask_size=row[6],
                resolved=bool(row[7]),
                settlement_yes=None if row[8] is None else bool(row[8]),
            )
            for row in rows
        ]

    def markets(self) -> list[tuple[str, str]]:
        """Return distinct venue/market pairs available for batch research."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT venue, market_id FROM market_snapshots "
                "ORDER BY venue, market_id"
            ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.row_factory = sqlite3.Row
        return connection
