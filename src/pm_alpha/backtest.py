from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .models import BacktestResult, Fill, MarketSnapshot, OrderIntent, Side


class Strategy(Protocol):
    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        ...


@dataclass(frozen=True)
class BacktestConfig:
    starting_cash: float = 1_000.0
    taker_fee_rate: float = 0.0
    fill_latency_ms: int = 0
    allow_partial_fills: bool = True
    allow_same_snapshot_fill: bool = True

    def __post_init__(self) -> None:
        if self.starting_cash <= 0:
            raise ValueError("starting_cash must be positive")
        if self.taker_fee_rate < 0:
            raise ValueError("taker_fee_rate must be non-negative")
        if self.fill_latency_ms < 0:
            raise ValueError("fill_latency_ms must be non-negative")


class EventDrivenBacktester:
    """Conservative top-of-book replay for signal and execution experiments."""

    def __init__(self, config: BacktestConfig | None = None) -> None:
        self.config = config or BacktestConfig()

    def run(
        self,
        snapshots: Iterable[MarketSnapshot],
        strategy: Strategy,
    ) -> BacktestResult:
        ordered = sorted(snapshots, key=lambda snapshot: snapshot.timestamp_ms)
        cash = self.config.starting_cash
        fees = 0.0
        filled_quantity = 0.0
        rejected_orders = 0
        fills: list[Fill] = []
        positions: dict[tuple[str, str], float] = {}
        settled: set[tuple[str, str]] = set()

        by_key: dict[tuple[str, str], list[MarketSnapshot]] = {}
        remaining_liquidity: dict[tuple[str, str, int], tuple[float, float]] = {}
        for snapshot in ordered:
            by_key.setdefault((snapshot.venue, snapshot.market_id), []).append(snapshot)
            remaining_liquidity[(snapshot.venue, snapshot.market_id, snapshot.timestamp_ms)] = (
                snapshot.bid_size,
                snapshot.ask_size,
            )

        for snapshot in ordered:
            key = (snapshot.venue, snapshot.market_id)
            if snapshot.resolved and key not in settled:
                position = positions.pop(key, 0.0)
                if snapshot.settlement_yes:
                    cash += position
                settled.add(key)
                continue
            for order in strategy.on_snapshot(snapshot):
                if order.venue != snapshot.venue or order.market_id != snapshot.market_id:
                    rejected_orders += 1
                    continue
                if order.timestamp_ms < snapshot.timestamp_ms:
                    rejected_orders += 1
                    continue
                fill_snapshot = self._snapshot_at_or_after(
                    by_key[(snapshot.venue, snapshot.market_id)],
                    order.timestamp_ms + self.config.fill_latency_ms
                    + (0 if self.config.allow_same_snapshot_fill else 1),
                )
                if fill_snapshot is None or fill_snapshot.resolved:
                    rejected_orders += 1
                    continue
                if (
                    order.max_latency_ms
                    and fill_snapshot.timestamp_ms > order.timestamp_ms + order.max_latency_ms
                ):
                    rejected_orders += 1
                    continue
                fill_price, available = self._available_liquidity(
                    fill_snapshot,
                    order,
                    remaining_liquidity[
                        (fill_snapshot.venue, fill_snapshot.market_id, fill_snapshot.timestamp_ms)
                    ],
                )
                if fill_price is None or available <= 0:
                    rejected_orders += 1
                    continue
                quantity = min(order.quantity, available) if self.config.allow_partial_fills else (
                    order.quantity if available >= order.quantity else 0.0
                )
                if quantity <= 0:
                    rejected_orders += 1
                    continue
                notional = quantity * fill_price
                fee = notional * self.config.taker_fee_rate
                if order.side is Side.BUY and cash < notional + fee:
                    rejected_orders += 1
                    continue
                if order.side is Side.SELL and positions.get(key, 0.0) < quantity:
                    rejected_orders += 1
                    continue
                cash += notional - fee if order.side is Side.SELL else -(notional + fee)
                positions[key] = positions.get(key, 0.0) + (
                    quantity if order.side is Side.BUY else -quantity
                )
                liquidity_key = (
                    fill_snapshot.venue,
                    fill_snapshot.market_id,
                    fill_snapshot.timestamp_ms,
                )
                bid_remaining, ask_remaining = remaining_liquidity[liquidity_key]
                if order.side is Side.BUY:
                    remaining_liquidity[liquidity_key] = (bid_remaining, ask_remaining - quantity)
                else:
                    remaining_liquidity[liquidity_key] = (bid_remaining - quantity, ask_remaining)
                fees += fee
                filled_quantity += quantity
                fills.append(Fill(order.client_order_id, fill_snapshot.timestamp_ms, quantity, fill_price, fee))

        latest: dict[tuple[str, str], MarketSnapshot] = {}
        for snapshot in ordered:
            latest[(snapshot.venue, snapshot.market_id)] = snapshot
        unrealized = 0.0
        unresolved: list[tuple[str, str]] = []
        for key, position in positions.items():
            mark = latest[key].yes_bid
            if mark is None:
                unresolved.append(key)
                continue
            unrealized += position * mark

        return BacktestResult(
            starting_cash=self.config.starting_cash,
            ending_cash=cash,
            realized_pnl=cash - self.config.starting_cash,
            fees=fees,
            filled_quantity=filled_quantity,
            rejected_orders=rejected_orders,
            fills=tuple(fills),
            ending_equity=cash + unrealized,
            unrealized_pnl=unrealized,
            unresolved_markets=tuple(sorted(unresolved)),
        )

    @staticmethod
    def _snapshot_at_or_after(
        snapshots: list[MarketSnapshot], timestamp_ms: int
    ) -> MarketSnapshot | None:
        for snapshot in snapshots:
            if snapshot.timestamp_ms >= timestamp_ms:
                return snapshot
        return None

    @staticmethod
    def _available_liquidity(
        snapshot: MarketSnapshot,
        order: OrderIntent,
        remaining: tuple[float, float],
    ) -> tuple[float | None, float]:
        if order.side is Side.BUY:
            if snapshot.yes_ask is None or order.limit_price < snapshot.yes_ask:
                return None, 0.0
            return snapshot.yes_ask, remaining[1]
        if snapshot.yes_bid is None or order.limit_price > snapshot.yes_bid:
            return None, 0.0
        return snapshot.yes_bid, remaining[0]
