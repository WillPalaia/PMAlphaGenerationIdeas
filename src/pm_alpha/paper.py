from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

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
    """Fan out each snapshot to independent strategies with strategy-specific market basket filtering."""

    def __init__(
        self,
        strategies: dict[str, PaperStrategy] | list[PaperStrategy],
        baskets: dict[str, set[str] | list[str] | Callable[[MarketSnapshot], bool]] | None = None,
        market_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.strategies = (
            list(strategies.items()) if isinstance(strategies, dict)
            else [(getattr(strategy, "name", str(strategy)), strategy) for strategy in strategies]
        )
        self.baskets = baskets or {}
        self.market_metadata = market_metadata or {}

    def set_market_metadata(self, metadata: dict[str, Any]) -> None:
        self.market_metadata = metadata

    def set_strategy_basket(
        self,
        strategy_name: str,
        allowed: set[str] | list[str] | Callable[[MarketSnapshot], bool],
    ) -> None:
        self.baskets[strategy_name] = allowed

    def is_market_allowed(self, strategy_name: str, snapshot: MarketSnapshot) -> bool:
        allowed = self.baskets.get(strategy_name)
        if allowed is None or allowed == "all":
            return True
        if isinstance(allowed, (set, list)) and ("all" in allowed or "*" in allowed):
            return True
        if callable(allowed):
            return bool(allowed(snapshot))

        ticker = snapshot.market_id
        meta = self.market_metadata.get(ticker)
        if meta and hasattr(meta, "category"):
            category = meta.category
            hours_to_close = getattr(meta, "hours_to_close", 9999.0)
        else:
            from .discovery import classify_market
            category = classify_market(ticker)
            hours_to_close = 9999.0

        allowed_set = set(allowed)
        if category in allowed_set:
            return True
        if "closing_soon" in allowed_set and hours_to_close <= 48.0 and category not in ("esports", "other"):
            return True
        if "liquid" in allowed_set and meta and (getattr(meta, "volume", 0) > 0 or getattr(meta, "liquidity_dollars", 0) > 0):
            return True

        return False

    def on_snapshot(self, snapshot: MarketSnapshot) -> list[OrderIntent]:
        from dataclasses import replace
        intents: list[OrderIntent] = []
        for name, strategy in self.strategies:
            if not self.is_market_allowed(name, snapshot):
                continue
            for intent in strategy.on_snapshot(snapshot):
                intent = replace(intent, strategy=name)
                intents.append(intent)
        return intents


@dataclass
class StrategyState:
    cash: float
    total_fees: float = 0.0
    positions: dict[tuple[str, str], PaperPosition] = None

    def __post_init__(self) -> None:
        if self.positions is None:
            self.positions = {}


class PaperPortfolio:
    """Persistent, immediate-fill simulator; it never calls an exchange."""

    def __init__(
        self,
        store: SnapshotStore,
        starting_cash: float = 100.0,
        fee_rate: float = 0.01,
        max_inventory_per_market: float = 100.0,
        reset_cash: bool = False,
    ) -> None:
        if starting_cash < 0 or fee_rate < 0 or max_inventory_per_market <= 0:
            raise ValueError("invalid portfolio configuration")
        self.store = store
        self.starting_cash = starting_cash
        self.fee_rate = fee_rate
        self.max_inventory_per_market = max_inventory_per_market
        self.positions: dict[tuple[str, str], PaperPosition] = {}
        self.strategy_states: dict[str, StrategyState] = {}
        self._latest_snapshots: dict[tuple[str, str], MarketSnapshot] = {}
        self._last_mark_ms: int = 0
        with store.connection() as connection:
            row = connection.execute(
                "SELECT cash, fees FROM paper_equity ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.cash = float(starting_cash) if reset_cash or not row or float(row[0]) <= 0 else float(row[0])
            self.total_fees = float(row[1]) if row and not reset_cash else 0.0
            if not reset_cash:
                for position in connection.execute(
                    "SELECT venue, market_id, quantity, average_cost, realized_pnl, settled "
                    "FROM paper_positions"
                ):
                    self.positions[(position[0], position[1])] = PaperPosition(
                        position[2], position[3], position[4], bool(position[5])
                    )

            # Load per-strategy state if available
            has_strat_eq = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_strategy_equity'"
            ).fetchone()
            if has_strat_eq and not reset_cash:
                for strat_row in connection.execute("""
                    SELECT strategy, cash, fees FROM paper_strategy_equity
                    WHERE id IN (SELECT MAX(id) FROM paper_strategy_equity GROUP BY strategy)
                """):
                    strat = strat_row[0]
                    strat_cash = float(strat_row[1]) if float(strat_row[1]) > 0.0 else float(starting_cash)
                    self.strategy_states[strat] = StrategyState(
                        cash=strat_cash,
                        total_fees=float(strat_row[2]),
                        positions={},
                    )

            has_strat_pos = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_strategy_positions'"
            ).fetchone()
            if has_strat_pos and not reset_cash:
                for spos in connection.execute(
                    "SELECT strategy, venue, market_id, quantity, average_cost, realized_pnl, settled "
                    "FROM paper_strategy_positions"
                ):
                    strat = spos[0]
                    s_state = self.strategy_states.setdefault(strat, StrategyState(cash=float(starting_cash)))
                    s_state.positions[(spos[1], spos[2])] = PaperPosition(
                        spos[3], spos[4], spos[5], bool(spos[6])
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
        """Record an intent and fill against displayed top-of-book liquidity with isolated strategy sub-portfolios."""
        strat = intent.strategy or intent.signal or "default"
        strat_state = self.strategy_states.setdefault(strat, StrategyState(cash=float(self.starting_cash)))
        key = (intent.venue, intent.market_id)

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
                s_pos = strat_state.positions.get(key, PaperPosition())
                if snapshot.yes_bid is None or intent.limit_price > snapshot.yes_bid:
                    reject = "limit-not-marketable"
                elif s_pos.quantity <= 0:
                    reject = "inventory"
                else:
                    fill_qty = min(intent.quantity, snapshot.bid_size, s_pos.quantity)
                    fill_price = snapshot.yes_bid
            if reject is None and fill_qty <= 0:
                reject = "no-liquidity"
            if reject is None and intent.side is Side.BUY:
                s_pos = strat_state.positions.get(key, PaperPosition())
                if s_pos.quantity + fill_qty > self.max_inventory_per_market:
                    reject = "inventory-limit"
                elif strat_state.cash < fill_qty * float(fill_price) * (1 + self.fee_rate):
                    reject = "insufficient-cash"
            status = "filled" if reject is None else "rejected"
            connection.execute(
                """INSERT INTO paper_orders
                (client_order_id, timestamp_ms, venue, market_id, side, quantity,
                 limit_price, signal, strategy, status, filled_quantity, reject_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (intent.client_order_id, intent.timestamp_ms, intent.venue, intent.market_id,
                 intent.side.value, intent.quantity, intent.limit_price, intent.signal,
                 strat, status, fill_qty, reject),
            )
            if reject is not None:
                return False
            price = float(fill_price)
            fee = fill_qty * price * self.fee_rate

            if intent.side is Side.BUY:
                strat_state.cash -= fill_qty * price + fee
                s_pos = strat_state.positions.setdefault(key, PaperPosition())
                s_total = s_pos.quantity + fill_qty
                s_pos.average_cost = ((s_pos.average_cost * s_pos.quantity + fill_qty * price) / s_total)
                s_pos.quantity = s_total

                position = self.positions.setdefault(key, PaperPosition())
                total = position.quantity + fill_qty
                position.average_cost = ((position.average_cost * position.quantity + fill_qty * price) / total)
                position.quantity = total
                self.cash -= fill_qty * price + fee
            else:
                strat_state.cash += fill_qty * price - fee
                s_pos = strat_state.positions[key]
                s_pos.realized_pnl += fill_qty * (price - s_pos.average_cost) - fee
                s_pos.quantity -= fill_qty
                if s_pos.quantity <= 1e-9:
                    s_pos.quantity = 0.0
                    s_pos.average_cost = 0.0

                position = self.positions.get(key, PaperPosition())
                position.realized_pnl += fill_qty * (price - position.average_cost) - fee
                position.quantity = max(0.0, position.quantity - fill_qty)
                if position.quantity <= 1e-9:
                    position.quantity = 0.0
                    position.average_cost = 0.0
                self.cash += fill_qty * price - fee

            self.total_fees += fee
            strat_state.total_fees += fee

            connection.execute(
                """INSERT INTO paper_fills
                (order_id, timestamp_ms, venue, market_id, side, quantity, price, fee)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (intent.client_order_id, snapshot.timestamp_ms, intent.venue, intent.market_id,
                 intent.side.value, fill_qty, price, fee),
            )
            self._persist_position(connection, intent.venue, intent.market_id)
            self._persist_strategy_position(connection, strat, intent.venue, intent.market_id)

            strat_pos_val = sum(
                p.quantity * self._mark_price(self._latest_snapshots.get(k, snapshot))
                for k, p in strat_state.positions.items() if p.quantity > 0
            )
            connection.execute(
                """INSERT INTO paper_strategy_equity
                (timestamp_ms, strategy, cash, positions_value, equity, fees)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (snapshot.timestamp_ms, strat, strat_state.cash, strat_pos_val,
                 strat_state.cash + strat_pos_val, strat_state.total_fees),
            )
            self._persist_equity(connection, snapshot.timestamp_ms, self._portfolio_value(snapshot))
            return True

    def _settle(self, snapshot: MarketSnapshot) -> None:
        key = (snapshot.venue, snapshot.market_id)
        outcome = 1.0 if snapshot.settlement_yes else 0.0

        for strat_name, strat_state in self.strategy_states.items():
            s_pos = strat_state.positions.get(key)
            if s_pos and s_pos.quantity > 0:
                s_payout = s_pos.quantity * outcome
                s_pos.realized_pnl += s_payout - s_pos.quantity * s_pos.average_cost
                strat_state.cash += s_payout
                s_pos.quantity = 0.0
                s_pos.average_cost = 0.0
                s_pos.settled = True

        position = self.positions.get(key)
        if position and position.quantity > 0:
            payout = position.quantity * outcome
            position.realized_pnl += payout - position.quantity * position.average_cost
            self.cash += payout
            position.quantity = 0.0
            position.average_cost = 0.0
            position.settled = True

        with self.store.connection() as connection:
            if position:
                self._persist_position(connection, *key)
            for strat_name in self.strategy_states:
                self._persist_strategy_position(connection, strat_name, *key)
            self._persist_equity(connection, snapshot.timestamp_ms, self._portfolio_value(snapshot))

    def _persist_strategy_position(self, connection, strategy: str, venue: str, market_id: str) -> None:
        strat_state = self.strategy_states.get(strategy)
        if not strat_state:
            return
        pos = strat_state.positions.get((venue, market_id))
        if not pos:
            return
        connection.execute(
            """INSERT INTO paper_strategy_positions
            (strategy, venue, market_id, quantity, average_cost, realized_pnl, settled)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy, venue, market_id) DO UPDATE SET quantity=excluded.quantity,
            average_cost=excluded.average_cost, realized_pnl=excluded.realized_pnl,
            settled=excluded.settled""",
            (strategy, venue, market_id, pos.quantity, pos.average_cost,
             pos.realized_pnl, int(pos.settled)),
        )

    def _portfolio_value(self, snapshot: MarketSnapshot) -> float:
        value = self.cash
        self._latest_snapshots[(snapshot.venue, snapshot.market_id)] = snapshot
        for key, position in self.positions.items():
            if position.quantity and key in self._latest_snapshots:
                value += position.quantity * self._mark_price(self._latest_snapshots[key])
        return value

    def _mark(self, timestamp_ms: int, positions_value: float, force: bool = False) -> PortfolioMark:
        mark = PortfolioMark(timestamp_ms, self.cash, positions_value,
                             self.cash + positions_value, self.total_fees)
        if force or (timestamp_ms - self._last_mark_ms >= 60_000):
            self._last_mark_ms = timestamp_ms
            with self.store.connection() as connection:
                self._persist_equity(connection, timestamp_ms, positions_value)
                for strat, s_state in self.strategy_states.items():
                    s_pos_val = sum(
                        p.quantity * self._mark_price(self._latest_snapshots[k])
                        for k, p in s_state.positions.items()
                        if p.quantity > 0 and k in self._latest_snapshots
                    )
                    connection.execute(
                        """INSERT INTO paper_strategy_equity
                        (timestamp_ms, strategy, cash, positions_value, equity, fees)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (timestamp_ms, strat, s_state.cash, s_pos_val,
                         s_state.cash + s_pos_val, s_state.total_fees),
                    )
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
                if snapshot.resolved:
                    self._market_exposure.pop((snapshot.venue, snapshot.market_id), None)
                for intent in self.strategy.on_snapshot(snapshot):
                    if not self._is_allowed(intent):
                        logger.warning("Rejected paper intent %s by risk limits", intent.client_order_id)
                        continue
                    key = (intent.venue, intent.market_id)
                    notional = intent.quantity * intent.limit_price
                    filled = False
                    if self.portfolio is not None:
                        filled = self.portfolio.submit(intent, snapshot)
                    if filled:
                        if intent.side is Side.BUY:
                            self._market_exposure[key] = self._market_exposure.get(key, 0.0) + notional
                        else:
                            self._market_exposure[key] = max(0.0, self._market_exposure.get(key, 0.0) - notional)
                    if self.on_intent is not None and filled:
                        await self.on_intent(intent)
            try:
                await asyncio.wait_for(self._stop.wait(), self.config.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def _is_allowed(self, intent: OrderIntent) -> bool:
        if intent.side is Side.SELL:
            return True
        notional = intent.quantity * intent.limit_price
        if notional > self.config.max_order_notional:
            return False
        exposure = self._market_exposure.get((intent.venue, intent.market_id), 0.0)
        return exposure + notional <= self.config.max_market_exposure
