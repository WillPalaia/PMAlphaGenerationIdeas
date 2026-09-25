from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from .models import MarketSnapshot, OrderIntent, Side
from .storage import SnapshotStore

logger = logging.getLogger(__name__)


class SnapshotSource(Protocol):
    async def snapshots(self) -> list[MarketSnapshot]:
        """Fetch the latest normalized snapshots without placing orders."""


class DynamicSnapshotSource(Protocol):
    async def refresh(self) -> None:
        ...

    async def snapshots(self) -> list[MarketSnapshot]:
        ...


class PaperStrategy(Protocol):
    def on_snapshot(self, snapshot: MarketSnapshot) -> list[OrderIntent]:
        ...


@dataclass
class PaperPosition:
    quantity: float = 0.0
    average_cost: float = 0.0
    realized_pnl: float = 0.0
    settled: bool = False


@dataclass(frozen=True)
class PortfolioMark:
    timestamp_ms: int
    cash: float
    positions_value: float
    equity: float
    fees: float


class MultiStrategy:
    """Fan out each snapshot to independent strategies (still paper-only)."""

    def __init__(self, strategies: dict[str, PaperStrategy] | list[PaperStrategy]):
        self.strategies = (
            list(strategies.items()) if isinstance(strategies, dict)
            else [(str(strategy), strategy) for strategy in strategies]
        )

    def on_snapshot(self, snapshot: MarketSnapshot) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        for name, strategy in self.strategies:
            for intent in strategy.on_snapshot(snapshot):
                intents.append(intent)
        return intents


class PaperPortfolio:
    """Persistent, immediate-fill simulator; it never calls an exchange."""

    def __init__(
        self,
        store: SnapshotStore,
        starting_cash: float = 100.0,
        fee_rate: float = 0.01,
        max_inventory_per_market: float = 100.0,
    ) -> None:
        if starting_cash < 0 or fee_rate < 0 or max_inventory_per_market <= 0:
            raise ValueError("invalid portfolio configuration")
        self.store = store
        self.fee_rate = fee_rate
        self.max_inventory_per_market = max_inventory_per_market
        self.positions: dict[tuple[str, str], PaperPosition] = {}
        self._latest_snapshots: dict[tuple[str, str], MarketSnapshot] = {}
        with store.connection() as connection:
            row = connection.execute(
                "SELECT cash, fees FROM paper_equity ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.cash = float(row[0]) if row else float(starting_cash)
            self.total_fees = float(row[1]) if row else 0.0
            for position in connection.execute(
                "SELECT venue, market_id, quantity, average_cost, realized_pnl, settled "
                "FROM paper_positions"
            ):
                self.positions[(position[0], position[1])] = PaperPosition(
                    position[2], position[3], position[4], bool(position[5])
                )

    def process_snapshot(self, snapshot: MarketSnapshot) -> PortfolioMark:
        self._latest_snapshots[(snapshot.venue, snapshot.market_id)] = snapshot
        if snapshot.resolved:
            self._settle(snapshot)
        value = sum(
            position.quantity * self._mark_price(self._latest_snapshots[key])
            for key, position in self.positions.items()
            if position.quantity and key in self._latest_snapshots
        )
        return self._mark(snapshot.timestamp_ms, value)

    def submit(self, intent: OrderIntent, snapshot: MarketSnapshot) -> bool:
        """Record an intent and fill against displayed top-of-book liquidity."""
        with self.store.connection() as connection:
            existing = connection.execute(
                "SELECT 1 FROM paper_orders WHERE client_order_id = ?",
                (intent.client_order_id,),
            ).fetchone()
            if existing:
                return False
            reject: str | None = None
            fill_qty = 0.0
            fill_price: float | None = None
            if snapshot.resolved:
                reject = "market-resolved"
            elif (intent.venue, intent.market_id) != (snapshot.venue, snapshot.market_id):
                reject = "snapshot-mismatch"
            elif intent.side is Side.BUY:
                if snapshot.yes_ask is None or intent.limit_price < snapshot.yes_ask:
                    reject = "limit-not-marketable"
                else:
                    fill_qty = min(intent.quantity, snapshot.ask_size)
                    fill_price = snapshot.yes_ask
            else:
                position = self.positions.get((intent.venue, intent.market_id), PaperPosition())
                if snapshot.yes_bid is None or intent.limit_price > snapshot.yes_bid:
                    reject = "limit-not-marketable"
                elif position.quantity <= 0:
                    reject = "inventory"
                else:
                    fill_qty = min(intent.quantity, snapshot.bid_size, position.quantity)
                    fill_price = snapshot.yes_bid
            if reject is None and fill_qty <= 0:
                reject = "no-liquidity"
            if reject is None and intent.side is Side.BUY:
                position = self.positions.get((intent.venue, intent.market_id), PaperPosition())
                if position.quantity + fill_qty > self.max_inventory_per_market:
                    reject = "inventory-limit"
                elif self.cash < fill_qty * float(fill_price) * (1 + self.fee_rate):
                    reject = "insufficient-cash"
            status = "filled" if reject is None else "rejected"
            connection.execute(
                """INSERT INTO paper_orders
                (client_order_id, timestamp_ms, venue, market_id, side, quantity,
                 limit_price, signal, status, filled_quantity, reject_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (intent.client_order_id, intent.timestamp_ms, intent.venue, intent.market_id,
                 intent.side.value, intent.quantity, intent.limit_price, intent.signal,
                 status, fill_qty, reject),
            )
            if reject is not None:
                return False
            price = float(fill_price)
            fee = fill_qty * price * self.fee_rate
            if intent.side is Side.BUY:
                self.cash -= fill_qty * price + fee
                position = self.positions.setdefault((intent.venue, intent.market_id), PaperPosition())
                total = position.quantity + fill_qty
                position.average_cost = (
                    (position.average_cost * position.quantity + fill_qty * price) / total
                )
                position.quantity = total
            else:
                self.cash += fill_qty * price - fee
                position = self.positions[(intent.venue, intent.market_id)]
                position.realized_pnl += fill_qty * (price - position.average_cost) - fee
                position.quantity -= fill_qty
                if position.quantity == 0:
                    position.average_cost = 0.0
            self.total_fees += fee
            connection.execute(
                """INSERT INTO paper_fills
                (order_id, timestamp_ms, venue, market_id, side, quantity, price, fee)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (intent.client_order_id, snapshot.timestamp_ms, intent.venue, intent.market_id,
                 intent.side.value, fill_qty, price, fee),
            )
            self._persist_position(connection, intent.venue, intent.market_id)
            self._persist_equity(connection, snapshot.timestamp_ms, self._portfolio_value(snapshot))
            return True

    def _settle(self, snapshot: MarketSnapshot) -> None:
        key = (snapshot.venue, snapshot.market_id)
        position = self.positions.get(key)
        if not position or position.quantity <= 0:
            return
        payout = position.quantity * (1.0 if snapshot.settlement_yes else 0.0)
        position.realized_pnl += payout - position.quantity * position.average_cost
        self.cash += payout
        position.quantity = 0.0
        position.average_cost = 0.0
        position.settled = True
        with self.store.connection() as connection:
            self._persist_position(connection, *key)
            self._persist_equity(connection, snapshot.timestamp_ms, 0.0)

    def _portfolio_value(self, snapshot: MarketSnapshot) -> float:
        value = self.cash
        self._latest_snapshots[(snapshot.venue, snapshot.market_id)] = snapshot
        for key, position in self.positions.items():
            if position.quantity and key in self._latest_snapshots:
                value += position.quantity * self._mark_price(self._latest_snapshots[key])
        return value

    def _mark(self, timestamp_ms: int, positions_value: float) -> PortfolioMark:
        mark = PortfolioMark(timestamp_ms, self.cash, positions_value,
                             self.cash + positions_value, self.total_fees)
        with self.store.connection() as connection:
            self._persist_equity(connection, timestamp_ms, positions_value)
        return mark

    def _persist_position(self, connection, venue: str, market_id: str) -> None:
        position = self.positions[(venue, market_id)]
        connection.execute(
            """INSERT INTO paper_positions
            (venue, market_id, quantity, average_cost, realized_pnl, settled)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(venue, market_id) DO UPDATE SET quantity=excluded.quantity,
            average_cost=excluded.average_cost, realized_pnl=excluded.realized_pnl,
            settled=excluded.settled""",
            (venue, market_id, position.quantity, position.average_cost,
             position.realized_pnl, int(position.settled)),
        )

    def _persist_equity(self, connection, timestamp_ms: int, positions_value: float) -> None:
        connection.execute(
            "INSERT INTO paper_equity (timestamp_ms, cash, positions_value, equity, fees) VALUES (?, ?, ?, ?, ?)",
            (timestamp_ms, self.cash, positions_value, self.cash + positions_value, self.total_fees),
        )

    @staticmethod
    def _mark_price(snapshot: MarketSnapshot) -> float:
        if snapshot.yes_bid is not None and snapshot.yes_ask is not None:
            return (snapshot.yes_bid + snapshot.yes_ask) / 2
        return snapshot.yes_bid if snapshot.yes_bid is not None else (snapshot.yes_ask or 0.0)


@dataclass(frozen=True)
class PaperConfig:
    poll_interval_seconds: float = 1.0
    max_order_notional: float = 5.0
    max_market_exposure: float = 25.0
    max_daily_loss: float = 10.0

    def __post_init__(self) -> None:
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if min(self.max_order_notional, self.max_market_exposure, self.max_daily_loss) <= 0:
            raise ValueError("paper risk limits must be positive")


class PaperRunner:
    """Long-running, no-order shadow runner for a normalized snapshot source."""

    def __init__(
        self,
        source: SnapshotSource,
        strategy: PaperStrategy,
        store: SnapshotStore,
        config: PaperConfig | None = None,
        on_intent: Callable[[OrderIntent], Awaitable[None]] | None = None,
        refresh: Callable[[], Awaitable[None]] | None = None,
        portfolio: PaperPortfolio | None = None,
    ) -> None:
        self.source = source
        self.strategy = strategy
        self.store = store
        self.config = config or PaperConfig()
        self.on_intent = on_intent
        self.refresh = refresh
        self.portfolio = portfolio
        self._stop = asyncio.Event()
        self._market_exposure: dict[tuple[str, str], float] = {}

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Poll until stopped; errors are surfaced to the caller."""
        while not self._stop.is_set():
            if self.refresh is not None:
                await self.refresh()
            snapshots = await self.source.snapshots()
            self.store.append_many(snapshots)
            for snapshot in snapshots:
                if self.portfolio is not None:
                    self.portfolio.process_snapshot(snapshot)
                for intent in self.strategy.on_snapshot(snapshot):
                    if not self._is_allowed(intent):
                        logger.warning("Rejected paper intent %s by risk limits", intent.client_order_id)
                        continue
                    self._market_exposure[(intent.venue, intent.market_id)] = (
                        self._market_exposure.get((intent.venue, intent.market_id), 0.0)
                        + intent.quantity * intent.limit_price
                    )
                    if self.portfolio is not None:
                        self.portfolio.submit(intent, snapshot)
                    if self.on_intent is not None:
                        await self.on_intent(intent)
            try:
                await asyncio.wait_for(self._stop.wait(), self.config.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def _is_allowed(self, intent: OrderIntent) -> bool:
        notional = intent.quantity * intent.limit_price
        exposure = self._market_exposure.get((intent.venue, intent.market_id), 0.0)
        return (
            notional <= self.config.max_order_notional
            and exposure + notional <= self.config.max_market_exposure
        )
