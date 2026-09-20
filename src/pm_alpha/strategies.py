from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .models import MarketSnapshot, OrderIntent, Side


@dataclass(frozen=True)
class ComplementOpportunity:
    """A simultaneous YES/NO purchase that pays one unit on either outcome."""

    yes_price: float
    no_price: float
    quantity: float
    gross_cost: float
    fees: float
    guaranteed_payout: float
    net_profit: float

    @property
    def net_edge(self) -> float:
        return self.net_profit / self.gross_cost if self.gross_cost else 0.0


def find_complement_opportunity(
    yes_ask: Optional[float],
    no_ask: Optional[float],
    yes_size: float,
    no_size: float,
    *,
    fee_rate: float = 0.0,
    minimum_edge: float = 0.0,
) -> Optional[ComplementOpportunity]:
    """Find a depth-aware binary complement opportunity after proportional fees."""

    if yes_ask is None or no_ask is None:
        return None
    if not 0.0 < yes_ask <= 1.0 or not 0.0 < no_ask <= 1.0:
        raise ValueError("outcome asks must be in the open interval (0, 1]")
    if yes_size <= 0 or no_size <= 0:
        return None
    if fee_rate < 0:
        raise ValueError("fee_rate must be non-negative")

    quantity = min(yes_size, no_size)
    gross_cost = quantity * (yes_ask + no_ask)
    fees = gross_cost * fee_rate
    guaranteed_payout = quantity
    net_profit = guaranteed_payout - gross_cost - fees
    opportunity = ComplementOpportunity(
        yes_price=yes_ask,
        no_price=no_ask,
        quantity=quantity,
        gross_cost=gross_cost,
        fees=fees,
        guaranteed_payout=guaranteed_payout,
        net_profit=net_profit,
    )
    return opportunity if opportunity.net_edge >= minimum_edge else None


class BuyBelowThreshold:
    """Diagnostic directional baseline for historical candle experiments."""

    def __init__(self, threshold: float, quantity: float = 1.0, max_orders: int = 1):
        if not 0 < threshold <= 1 or quantity <= 0 or max_orders <= 0:
            raise ValueError("threshold, quantity, and max_orders must be positive")
        self.threshold = threshold
        self.quantity = quantity
        self.max_orders = max_orders
        self._orders = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        if (
            self._orders < self.max_orders
            and snapshot.yes_ask is not None
            and snapshot.yes_ask <= self.threshold
        ):
            self._orders += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"threshold-{self._orders}",
                signal="buy-below-threshold",
            )


class MomentumStrategy:
    """Simple lagged momentum baseline; intentionally no future data access."""

    def __init__(self, lookback: int = 3, minimum_move: float = 0.02, quantity: float = 1.0):
        if lookback <= 0 or minimum_move < 0 or quantity <= 0:
            raise ValueError("lookback and quantity must be positive")
        self.lookback = lookback
        self.minimum_move = minimum_move
        self.quantity = quantity
        self._history: dict[tuple[str, str], list[float]] = {}
        self._orders = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        if snapshot.yes_ask is None:
            return
        key = (snapshot.venue, snapshot.market_id)
        history = self._history.setdefault(key, [])
        if history and snapshot.yes_ask - history[-1] >= self.minimum_move:
            self._orders += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"momentum-{self._orders}",
                signal="positive-momentum",
            )
        history.append(snapshot.yes_ask)
        if len(history) > self.lookback:
            del history[0]


class MeanReversionStrategy:
    """Buys below a lagged rolling mean and caps entries per market."""

    def __init__(
        self,
        lookback: int = 10,
        deviation: float = 0.05,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if lookback <= 1 or deviation < 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid mean-reversion parameters")
        self.lookback = lookback
        self.deviation = deviation
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._history: dict[tuple[str, str], list[float]] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        if snapshot.yes_ask is None:
            return
        key = (snapshot.venue, snapshot.market_id)
        history = self._history.setdefault(key, [])
        if len(history) >= self.lookback:
            mean = sum(history[-self.lookback:]) / self.lookback
            if (
                snapshot.yes_ask <= mean - self.deviation
                and self._orders.get(key, 0) < self.max_orders_per_market
            ):
                self._orders[key] = self._orders.get(key, 0) + 1
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.BUY,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_ask,
                    client_order_id=f"reversion-{self._counter}",
                    signal="below-rolling-mean",
                )
        history.append(snapshot.yes_ask)
        if len(history) > self.lookback:
            del history[0]
