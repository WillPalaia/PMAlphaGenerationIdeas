from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class MarketSnapshot:
    """Top-of-book state for one binary contract at one exchange timestamp."""

    timestamp_ms: int
    venue: str
    market_id: str
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    bid_size: float = 0.0
    ask_size: float = 0.0
    resolved: bool = False
    settlement_yes: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        if not self.venue or not self.market_id:
            raise ValueError("venue and market_id are required")
        for name, price in (("yes_bid", self.yes_bid), ("yes_ask", self.yes_ask)):
            if price is not None and not 0.0 <= price <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.yes_bid is not None and self.yes_ask is not None and self.yes_bid > self.yes_ask:
            raise ValueError("yes_bid cannot exceed yes_ask")
        if self.bid_size < 0 or self.ask_size < 0:
            raise ValueError("book sizes must be non-negative")
        if self.resolved and self.settlement_yes is None:
            raise ValueError("resolved snapshots require settlement_yes")
        if self.resolved and (self.yes_bid is not None or self.yes_ask is not None):
            raise ValueError("resolved snapshots cannot contain live quotes")


@dataclass(frozen=True)
class OrderIntent:
    """A strategy request; it contains no venue-specific execution behavior."""

    timestamp_ms: int
    venue: str
    market_id: str
    side: Side
    quantity: float
    limit_price: float
    client_order_id: str
    signal: str = ""
    max_latency_ms: int = 0

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0 or self.max_latency_ms < 0:
            raise ValueError("timestamps and max_latency_ms must be non-negative")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if not 0.0 <= self.limit_price <= 1.0:
            raise ValueError("limit_price must be between 0 and 1")
        if not self.client_order_id:
            raise ValueError("client_order_id is required")


@dataclass(frozen=True)
class Fill:
    order_id: str
    timestamp_ms: int
    quantity: float
    price: float
    fee: float


@dataclass(frozen=True)
class BacktestResult:
    starting_cash: float
    ending_cash: float
    realized_pnl: float
    fees: float
    filled_quantity: float
    rejected_orders: int
    fills: tuple[Fill, ...]
    ending_equity: float = 0.0
    unrealized_pnl: float = 0.0
    unresolved_markets: tuple[tuple[str, str], ...] = ()
