from __future__ import annotations

import math
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

    def __init__(
        self,
        threshold: float,
        quantity: float = 1.0,
        max_orders: int = 1,
        take_profit: float = 0.15,
        stop_loss: float = 0.15,
    ):
        if not 0 < threshold <= 1 or quantity <= 0 or max_orders <= 0:
            raise ValueError("threshold, quantity, and max_orders must be positive")
        self.threshold = threshold
        self.quantity = quantity
        self.max_orders = max_orders
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self._orders = 0
        self._positions: dict[tuple[str, str], float] = {}

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            return

        if key in self._positions and snapshot.yes_bid is not None:
            entry = self._positions[key]
            if snapshot.yes_bid >= entry + self.take_profit:
                del self._positions[key]
                self._orders += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"threshold-tp-{self._orders}",
                    signal="threshold-take-profit",
                    strategy="buy-below-threshold",
                )
                return
            elif snapshot.yes_bid <= entry - self.stop_loss:
                del self._positions[key]
                self._orders += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"threshold-sl-{self._orders}",
                    signal="threshold-stop-loss",
                    strategy="buy-below-threshold",
                )
                return

        if (
            self._orders < self.max_orders
            and snapshot.yes_ask is not None
            and snapshot.yes_ask <= self.threshold
            and key not in self._positions
        ):
            self._orders += 1
            self._positions[key] = snapshot.yes_ask
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"threshold-{self._orders}",
                signal="buy-below-threshold",
                strategy="buy-below-threshold",
            )


class MomentumStrategy:
    """Simple lagged momentum baseline; intentionally no future data access."""

    def __init__(
        self,
        lookback: int = 3,
        minimum_move: float = 0.02,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
        take_profit: float = 0.08,
        stop_loss: float = 0.06,
    ):
        if lookback <= 0 or minimum_move < 0 or quantity <= 0:
            raise ValueError("lookback and quantity must be positive")
        self.lookback = lookback
        self.minimum_move = minimum_move
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self.take_profit = take_profit
        self.stop_loss = stop_loss
        self._history: dict[tuple[str, str], list[float]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        if key in self._positions and snapshot.yes_bid is not None:
            entry = self._positions[key]
            if snapshot.yes_bid >= entry + self.take_profit:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"momentum-tp-{self._counter}",
                    signal="momentum-take-profit",
                    strategy="positive-momentum",
                )
                return
            elif snapshot.yes_bid <= entry - self.stop_loss:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"momentum-sl-{self._counter}",
                    signal="momentum-stop-loss",
                    strategy="positive-momentum",
                )
                return

        if snapshot.yes_ask is None:
            return
        history = self._history.setdefault(key, [])
        if (
            history
            and snapshot.yes_ask - history[-1] >= self.minimum_move
            and self._orders.get(key, 0) < self.max_orders_per_market
        ):
            self._orders[key] = self._orders.get(key, 0) + 1
            self._positions[key] = snapshot.yes_ask
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"momentum-{self._counter}",
                signal="positive-momentum",
                strategy="positive-momentum",
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
        stop_loss: float = 0.10,
    ):
        if lookback <= 1 or deviation < 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid mean-reversion parameters")
        self.lookback = lookback
        self.deviation = deviation
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self.stop_loss = stop_loss
        self._history: dict[tuple[str, str], list[float]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        price = snapshot.yes_ask if snapshot.yes_ask is not None else snapshot.yes_bid
        if price is None:
            return

        history = self._history.setdefault(key, [])
        mean = sum(history[-self.lookback:]) / len(history[-self.lookback:]) if history else price

        # Exit logic: sell when reverted to rolling mean or stopped out
        if key in self._positions and snapshot.yes_bid is not None and len(history) >= self.lookback:
            entry = self._positions[key]
            if snapshot.yes_bid >= mean:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"reversion-exit-{self._counter}",
                    signal="mean-reverted-exit",
                    strategy="mean-reversion",
                )
                return
            elif snapshot.yes_bid <= entry - self.stop_loss:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"reversion-sl-{self._counter}",
                    signal="mean-reversion-stop-loss",
                    strategy="mean-reversion",
                )
                return

        # Buy logic
        if len(history) >= self.lookback and snapshot.yes_ask is not None:
            if (
                snapshot.yes_ask <= mean - self.deviation
                and self._orders.get(key, 0) < self.max_orders_per_market
                and key not in self._positions
            ):
                self._orders[key] = self._orders.get(key, 0) + 1
                self._positions[key] = snapshot.yes_ask
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
                    strategy="mean-reversion",
                )
        history.append(price)
        if len(history) > self.lookback:
            del history[0]


class StableHighProbabilityStrategy:
    """Proxy for long-dated, low-drama contracts near a target probability.

    Snapshot data does not include contract expiry, so the strategy deliberately
    does not claim to identify a one-month horizon. Callers must filter markets
    by expiry metadata before replaying this strategy.
    """

    def __init__(
        self,
        lower_price: float = 0.68,
        upper_price: float = 0.72,
        lookback: int = 5,
        max_range: float = 0.03,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
        take_profit_price: float = 0.85,
        stop_loss_price: float = 0.55,
    ):
        if not 0 < lower_price <= upper_price <= 1:
            raise ValueError("invalid probability band")
        if lookback <= 0 or max_range < 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid stable-probability parameters")
        self.lower_price = lower_price
        self.upper_price = upper_price
        self.lookback = lookback
        self.max_range = max_range
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self.take_profit_price = take_profit_price
        self.stop_loss_price = stop_loss_price
        self._history: dict[tuple[str, str], list[float]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        if key in self._positions and snapshot.yes_bid is not None:
            if snapshot.yes_bid >= self.take_profit_price:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"stable-tp-{self._counter}",
                    signal="stable-high-take-profit",
                    strategy="stable-high-probability",
                )
                return
            elif snapshot.yes_bid <= self.stop_loss_price:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"stable-sl-{self._counter}",
                    signal="stable-high-stop-loss",
                    strategy="stable-high-probability",
                )
                return

        if snapshot.yes_ask is None:
            return

        history = self._history.setdefault(key, [])
        history.append(snapshot.yes_ask)
        if len(history) > self.lookback:
            del history[0]
        if (
            len(history) == self.lookback
            and self.lower_price <= snapshot.yes_ask <= self.upper_price
            and max(history) - min(history) <= self.max_range
            and self._orders.get(key, 0) < self.max_orders_per_market
            and key not in self._positions
        ):
            self._orders[key] = self._orders.get(key, 0) + 1
            self._positions[key] = snapshot.yes_ask
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"stable-probability-{self._counter}",
                signal="stable-high-probability",
                strategy="stable-high-probability",
            )


class FavoriteYieldStrategy:
    """Exploits favorite-longshot bias by buying high-probability contracts.

    Prediction markets systematically underprice heavy favorites because retail
    traders prefer lottery-style payoffs. This strategy targets contracts in a high
    confidence zone (default 0.85 - 0.96) that exhibit price stability, holding
    to capture the 4-15% discount to parity.
    """

    def __init__(
        self,
        min_probability: float = 0.85,
        max_probability: float = 0.96,
        lookback: int = 5,
        max_range: float = 0.04,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if not (0.0 < min_probability <= max_probability < 1.0):
            raise ValueError("invalid probability range")
        if lookback <= 0 or max_range < 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid favorite-yield parameters")
        self.min_probability = min_probability
        self.max_probability = max_probability
        self.lookback = lookback
        self.max_range = max_range
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._history: dict[tuple[str, str], list[float]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        # Exit logic: harvest yield early once contract is >= 0.96 or stop out on collapse
        if key in self._positions and snapshot.yes_bid is not None:
            if snapshot.yes_bid >= 0.96:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"fav-harvest-{self._counter}",
                    signal="favorite-yield-harvested",
                    strategy="favorite-yield",
                )
                return
            elif snapshot.yes_bid <= 0.72:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"fav-sl-{self._counter}",
                    signal="favorite-yield-stop-loss",
                    strategy="favorite-yield",
                )
                return

        if snapshot.yes_ask is None:
            return

        history = self._history.setdefault(key, [])
        history.append(snapshot.yes_ask)
        if len(history) > self.lookback:
            del history[0]
        if (
            len(history) == self.lookback
            and self.min_probability <= snapshot.yes_ask <= self.max_probability
            and max(history) - min(history) <= self.max_range
            and self._orders.get(key, 0) < self.max_orders_per_market
            and key not in self._positions
        ):
            self._orders[key] = self._orders.get(key, 0) + 1
            self._positions[key] = snapshot.yes_ask
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"fav-yield-{self._counter}",
                signal="favorite-yield",
                strategy="favorite-yield",
            )


class OrderBookImbalanceStrategy:
    """Microstructure strategy exploiting top-of-book depth imbalance.

    When bid depth substantially exceeds ask depth (OBI >= threshold),
    buying pressure is queued up and price is more likely to tick upward.
    Requires minimum depth and tight spreads to avoid adverse selection.
    """

    def __init__(
        self,
        imbalance_threshold: float = 0.50,
        max_spread: float = 0.05,
        min_depth: float = 1.0,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if not (-1.0 < imbalance_threshold < 1.0):
            raise ValueError("imbalance_threshold must be between -1 and 1")
        if max_spread <= 0 or min_depth <= 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid orderbook-imbalance parameters")
        self.imbalance_threshold = imbalance_threshold
        self.max_spread = max_spread
        self.min_depth = min_depth
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        total_depth = (snapshot.bid_size + snapshot.ask_size) if (snapshot.bid_size and snapshot.ask_size) else 0.0
        imbalance = ((snapshot.bid_size - snapshot.ask_size) / total_depth) if total_depth > 0 else 0.0

        # Exit logic: scalp profit or cut when queue flips to heavy selling
        if key in self._positions and snapshot.yes_bid is not None:
            entry = self._positions[key]
            if snapshot.yes_bid >= entry + 0.05:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"ob-scalp-{self._counter}",
                    signal="imbalance-scalp-profit",
                    strategy="orderbook-imbalance",
                )
                return
            elif imbalance <= -0.30 or snapshot.yes_bid <= entry - 0.05:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"ob-exit-{self._counter}",
                    signal="imbalance-flip-exit",
                    strategy="orderbook-imbalance",
                )
                return

        if snapshot.yes_ask is None or snapshot.yes_bid is None:
            return
        if total_depth < self.min_depth:
            return
        spread = snapshot.yes_ask - snapshot.yes_bid
        if spread > self.max_spread:
            return

        if (
            imbalance >= self.imbalance_threshold
            and self._orders.get(key, 0) < self.max_orders_per_market
            and snapshot.ask_size > 0
            and key not in self._positions
        ):
            self._orders[key] = self._orders.get(key, 0) + 1
            self._positions[key] = snapshot.yes_ask
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"ob-imbalance-{self._counter}",
                signal="orderbook-imbalance",
                strategy="orderbook-imbalance",
            )


class BollingerReversionStrategy:
    """Adaptive volatility mean-reversion with dynamic entry and exit bands.

    Standardizes deviations by rolling standard deviation (Z-score).
    Enters when oversold (z <= entry_z) and exits when price reverts
    to or above exit_z, capturing dynamic statistical mean reversion.
    """

    def __init__(
        self,
        lookback: int = 15,
        entry_z: float = -2.0,
        exit_z: float = 0.0,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if lookback <= 2 or quantity <= 0 or max_orders_per_market <= 0 or entry_z >= exit_z:
            raise ValueError("invalid bollinger parameters")
        self.lookback = lookback
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._history: dict[tuple[str, str], list[float]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        price = snapshot.yes_ask if snapshot.yes_ask is not None else snapshot.yes_bid
        if price is None:
            return
        key = (snapshot.venue, snapshot.market_id)
        history = self._history.setdefault(key, [])
        history.append(price)
        if len(history) > self.lookback:
            del history[0]

        if len(history) >= self.lookback:
            mean = sum(history) / len(history)
            variance = sum((x - mean) ** 2 for x in history) / (len(history) - 1)
            std = math.sqrt(variance) if variance > 1e-8 else 0.0

            if std > 0:
                z = (price - mean) / std
                pos = self._positions.get(key, 0.0)

                # Buy on oversold
                if (
                    z <= self.entry_z
                    and snapshot.yes_ask is not None
                    and self._orders.get(key, 0) < self.max_orders_per_market
                ):
                    self._orders[key] = self._orders.get(key, 0) + 1
                    self._positions[key] = pos + self.quantity
                    self._counter += 1
                    yield OrderIntent(
                        timestamp_ms=snapshot.timestamp_ms,
                        venue=snapshot.venue,
                        market_id=snapshot.market_id,
                        side=Side.BUY,
                        quantity=self.quantity,
                        limit_price=snapshot.yes_ask,
                        client_order_id=f"boll-buy-{self._counter}",
                        signal="bollinger-oversold",
                        strategy="bollinger-reversion",
                    )
                # Sell to exit on mean reversion
                elif z >= self.exit_z and pos > 0 and snapshot.yes_bid is not None:
                    sell_qty = pos
                    self._positions[key] = 0.0
                    self._counter += 1
                    yield OrderIntent(
                        timestamp_ms=snapshot.timestamp_ms,
                        venue=snapshot.venue,
                        market_id=snapshot.market_id,
                        side=Side.SELL,
                        quantity=sell_qty,
                        limit_price=snapshot.yes_bid,
                        client_order_id=f"boll-sell-{self._counter}",
                        signal="bollinger-exit",
                        strategy="bollinger-reversion",
                    )


class RangeBreakoutStrategy:
    """Channel breakout strategy capturing post-announcement drift.

    Tracks rolling high/low channel over lookback periods. When price breaks out
    above the rolling channel high by a configurable margin with ask liquidity,
    enters to capture momentum towards outcome resolution.
    """

    def __init__(
        self,
        lookback: int = 20,
        breakout_margin: float = 0.02,
        min_price: float = 0.10,
        max_price: float = 0.90,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if lookback <= 1 or breakout_margin < 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid range breakout parameters")
        if not (0.0 < min_price < max_price < 1.0):
            raise ValueError("invalid price boundaries")
        self.lookback = lookback
        self.breakout_margin = breakout_margin
        self.min_price = min_price
        self.max_price = max_price
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._history: dict[tuple[str, str], list[float]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        history = self._history.setdefault(key, [])

        # Exit logic: take profit on strong drift or exit when price falls below channel midline
        if key in self._positions and snapshot.yes_bid is not None and len(history) >= self.lookback:
            entry = self._positions[key]
            channel_mid = (max(history) + min(history)) / 2.0
            if snapshot.yes_bid >= entry + 0.12:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"breakout-tp-{self._counter}",
                    signal="breakout-take-profit",
                    strategy="range-breakout",
                )
                return
            elif snapshot.yes_bid <= channel_mid:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"breakout-mid-{self._counter}",
                    signal="breakout-channel-exit",
                    strategy="range-breakout",
                )
                return

        if snapshot.yes_ask is None:
            return

        if len(history) >= self.lookback:
            channel_high = max(history)
            if (
                snapshot.yes_ask >= channel_high + self.breakout_margin
                and self.min_price <= snapshot.yes_ask <= self.max_price
                and self._orders.get(key, 0) < self.max_orders_per_market
                and key not in self._positions
            ):
                self._orders[key] = self._orders.get(key, 0) + 1
                self._positions[key] = snapshot.yes_ask
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.BUY,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_ask,
                    client_order_id=f"breakout-{self._counter}",
                    signal="range-breakout",
                    strategy="range-breakout",
                )
        history.append(snapshot.yes_ask)
        if len(history) > self.lookback:
            del history[0]


class EmaCrossoverStrategy:
    """Dual Exponential Moving Average trend confirmation strategy.

    Smooths market noise by maintaining fast and slow EMAs. Emits buy intents
    when the fast EMA crosses above the slow EMA by at least min_cross_diff.
    """

    def __init__(
        self,
        fast_span: int = 5,
        slow_span: int = 20,
        min_cross_diff: float = 0.01,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if (
            fast_span <= 0
            or slow_span <= fast_span
            or min_cross_diff < 0
            or quantity <= 0
            or max_orders_per_market <= 0
        ):
            raise ValueError("invalid EMA crossover parameters")
        self.fast_alpha = 2.0 / (fast_span + 1.0)
        self.slow_alpha = 2.0 / (slow_span + 1.0)
        self.min_cross_diff = min_cross_diff
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._fast_ema: dict[tuple[str, str], float] = {}
        self._slow_ema: dict[tuple[str, str], float] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._was_below: dict[tuple[str, str], bool] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        price = snapshot.yes_ask if snapshot.yes_ask is not None else snapshot.yes_bid
        if price is None:
            return
        if key not in self._fast_ema:
            self._fast_ema[key] = price
            self._slow_ema[key] = price
            self._was_below[key] = True
            return

        fast = self.fast_alpha * price + (1.0 - self.fast_alpha) * self._fast_ema[key]
        slow = self.slow_alpha * price + (1.0 - self.slow_alpha) * self._slow_ema[key]
        self._fast_ema[key] = fast
        self._slow_ema[key] = slow

        current_diff = fast - slow

        # Exit logic: sell if fast EMA falls back below slow EMA (trend reversed) or take profit
        if key in self._positions and snapshot.yes_bid is not None:
            entry = self._positions[key]
            if current_diff < 0:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"ema-exit-{self._counter}",
                    signal="ema-death-cross-exit",
                    strategy="ema-crossover",
                )
                return
            elif snapshot.yes_bid >= entry + 0.15:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"ema-tp-{self._counter}",
                    signal="ema-trend-take-profit",
                    strategy="ema-crossover",
                )
                return

        if current_diff < 0:
            self._was_below[key] = True

        # Golden cross: was below or at slow EMA and now separated by at least min_cross_diff
        if (
            self._was_below.get(key, True)
            and current_diff >= self.min_cross_diff
            and snapshot.yes_ask is not None
            and self._orders.get(key, 0) < self.max_orders_per_market
            and key not in self._positions
        ):
            self._was_below[key] = False
            self._orders[key] = self._orders.get(key, 0) + 1
            self._positions[key] = snapshot.yes_ask
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"ema-cross-{self._counter}",
                signal="ema-crossover",
                strategy="ema-crossover",
            )


class SpreadHarvestingMarketMaker:
    """Autonomous market maker harvesting wide prediction market spreads.

    Estimates top-of-book micro-price and skews quotes based on inventory
    (Avellaneda-Stoikov framework). Emits inside-the-spread limit orders.
    """

    def __init__(
        self,
        min_spread: float = 0.04,
        inventory_skew: float = 0.01,
        max_inventory: float = 10.0,
        quantity: float = 1.0,
        aggressiveness: float = 0.50,
    ):
        if min_spread <= 0 or inventory_skew < 0 or max_inventory <= 0 or quantity <= 0:
            raise ValueError("invalid spread harvesting parameters")
        self.min_spread = min_spread
        self.inventory_skew = inventory_skew
        self.max_inventory = max_inventory
        self.quantity = quantity
        self.aggressiveness = aggressiveness
        self._inventory: dict[tuple[str, str], float] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        if snapshot.yes_bid is None or snapshot.yes_ask is None:
            return
        spread = snapshot.yes_ask - snapshot.yes_bid
        if spread < self.min_spread:
            return
        key = (snapshot.venue, snapshot.market_id)
        current_inv = self._inventory.get(key, 0.0)

        total_depth = snapshot.bid_size + snapshot.ask_size
        if total_depth > 0:
            micro_price = (
                snapshot.yes_bid * snapshot.ask_size + snapshot.yes_ask * snapshot.bid_size
            ) / total_depth
        else:
            micro_price = (snapshot.yes_bid + snapshot.yes_ask) / 2.0

        reservation = micro_price - current_inv * self.inventory_skew

        # Buy when inventory is below max and ask is within reservation bounds
        if current_inv < self.max_inventory and snapshot.yes_ask <= reservation + spread * self.aggressiveness:
            self._inventory[key] = current_inv + self.quantity
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"mm-buy-{self._counter}",
                signal="mm-harvest-buy",
                strategy="spread-harvesting",
            )
        # Sell when inventory exists and bid is within reservation bounds
        elif current_inv > 0 and snapshot.yes_bid >= reservation - spread * self.aggressiveness:
            sell_qty = min(self.quantity, current_inv)
            self._inventory[key] = current_inv - sell_qty
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.SELL,
                quantity=sell_qty,
                limit_price=snapshot.yes_bid,
                client_order_id=f"mm-sell-{self._counter}",
                signal="mm-harvest-sell",
                strategy="spread-harvesting",
            )


class ComplementArbitrageStrategy:
    """Parity and crossed/locked-book arbitrage strategy for binary prediction markets.

    Detects Dutch book mispricings or locked-book anomalies (bid == ask).
    Emits risk-free execution intents.
    """

    def __init__(
        self,
        min_edge: float = 0.0,
        fee_rate: float = 0.0,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if min_edge < 0 or fee_rate < 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid complement arbitrage parameters")
        self.min_edge = min_edge
        self.fee_rate = fee_rate
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        if snapshot.yes_bid is None or snapshot.yes_ask is None:
            return
        key = (snapshot.venue, snapshot.market_id)
        if self._orders.get(key, 0) >= self.max_orders_per_market:
            return

        # Locked book (zero-spread: bid == ask)
        if snapshot.yes_bid >= snapshot.yes_ask:
            self._orders[key] = self._orders.get(key, 0) + 1
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"arb-cross-{self._counter}",
                signal="locked-book-arbitrage",
                strategy="complement-arbitrage",
            )


class VwapPullbackStrategy:
    """Volume-weighted average price pullback strategy.

    Calculates rolling VWAP. When the broader market is trending upward but
    a temporary liquidity drop pushes the price below VWAP by a specified
    deviation, enters on the pullback at a discount.
    """

    def __init__(
        self,
        lookback: int = 20,
        pullback_threshold: float = 0.02,
        trend_lookback: int = 10,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if (
            lookback <= 1
            or pullback_threshold < 0
            or trend_lookback <= 0
            or quantity <= 0
            or max_orders_per_market <= 0
        ):
            raise ValueError("invalid VWAP pullback parameters")
        self.lookback = lookback
        self.pullback_threshold = pullback_threshold
        self.trend_lookback = trend_lookback
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._history: dict[tuple[str, str], list[tuple[float, float]]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        history = self._history.setdefault(key, [])

        # Exit logic: sell when rebound hits profit target or if price breaks below VWAP stop
        if key in self._positions and snapshot.yes_bid is not None and len(history) >= self.lookback:
            entry = self._positions[key]
            total_vol = sum(s for _, s in history)
            vwap = sum(p * s for p, s in history) / total_vol if total_vol > 0 else entry
            if snapshot.yes_bid >= entry + 0.08:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"vwap-tp-{self._counter}",
                    signal="vwap-bounce-profit",
                    strategy="vwap-pullback",
                )
                return
            elif snapshot.yes_bid < vwap - 0.06:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"vwap-stop-{self._counter}",
                    signal="vwap-breakdown-stop",
                    strategy="vwap-pullback",
                )
                return

        price = snapshot.yes_ask
        size = snapshot.ask_size
        if price is None or size <= 0:
            return
        history.append((price, size))
        if len(history) > self.lookback:
            del history[0]

        if len(history) >= self.lookback:
            total_vol = sum(s for _, s in history)
            if total_vol > 0:
                vwap = sum(p * s for p, s in history) / total_vol
                idx = max(0, len(history) - self.trend_lookback)
                older_price = history[0][0]
                is_uptrend = vwap > older_price

                if (
                    is_uptrend
                    and price <= vwap - self.pullback_threshold
                    and self._orders.get(key, 0) < self.max_orders_per_market
                    and key not in self._positions
                ):
                    self._orders[key] = self._orders.get(key, 0) + 1
                    self._positions[key] = price
                    self._counter += 1
                    yield OrderIntent(
                        timestamp_ms=snapshot.timestamp_ms,
                        venue=snapshot.venue,
                        market_id=snapshot.market_id,
                        side=Side.BUY,
                        quantity=self.quantity,
                        limit_price=price,
                        client_order_id=f"vwap-pullback-{self._counter}",
                        signal="vwap-pullback",
                        strategy="vwap-pullback",
                    )


class JumpFollowingStrategy:
    """Velocity / jump following strategy for sudden information shocks.

    Detects sharp, discontinuous price moves (>= jump_threshold in <= lookback ticks).
    Captures post-news momentum drift before the broader market catches up.
    """

    def __init__(
        self,
        jump_threshold: float = 0.06,
        lookback: int = 3,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if jump_threshold <= 0 or lookback <= 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid jump following parameters")
        self.jump_threshold = jump_threshold
        self.lookback = lookback
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._history: dict[tuple[str, str], list[float]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        # Exit logic: take profit on post-jump drift or cut on stall
        if key in self._positions and snapshot.yes_bid is not None:
            entry = self._positions[key]
            if snapshot.yes_bid >= entry + 0.10:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"jump-tp-{self._counter}",
                    signal="jump-follow-take-profit",
                    strategy="jump-following",
                )
                return
            elif snapshot.yes_bid <= entry - 0.05:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"jump-sl-{self._counter}",
                    signal="jump-follow-stop-loss",
                    strategy="jump-following",
                )
                return

        if snapshot.yes_ask is None:
            return

        history = self._history.setdefault(key, [])
        if history:
            price_change = snapshot.yes_ask - history[0]
            if (
                price_change >= self.jump_threshold
                and self._orders.get(key, 0) < self.max_orders_per_market
                and key not in self._positions
            ):
                self._orders[key] = self._orders.get(key, 0) + 1
                self._positions[key] = snapshot.yes_ask
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.BUY,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_ask,
                    client_order_id=f"jump-{self._counter}",
                    signal="jump-momentum",
                    strategy="jump-following",
                )
        history.append(snapshot.yes_ask)
        if len(history) > self.lookback:
            del history[0]


class TimeDecayYieldStrategy:
    """Theta-decay harvesting for consolidating high-probability contracts.

    Captures the final convergence premium as contract uncertainty collapses
    into outcome resolution.
    """

    def __init__(
        self,
        target_min_price: float = 0.80,
        target_max_price: float = 0.95,
        lookback: int = 8,
        max_volatility: float = 0.02,
        quantity: float = 1.0,
        max_orders_per_market: int = 1,
    ):
        if not (0.0 < target_min_price <= target_max_price < 1.0):
            raise ValueError("invalid price boundaries")
        if lookback <= 0 or max_volatility < 0 or quantity <= 0 or max_orders_per_market <= 0:
            raise ValueError("invalid time-decay parameters")
        self.target_min_price = target_min_price
        self.target_max_price = target_max_price
        self.lookback = lookback
        self.max_volatility = max_volatility
        self.quantity = quantity
        self.max_orders_per_market = max_orders_per_market
        self._history: dict[tuple[str, str], list[float]] = {}
        self._positions: dict[tuple[str, str], float] = {}
        self._orders: dict[tuple[str, str], int] = {}
        self._counter = 0

    def on_snapshot(self, snapshot: MarketSnapshot) -> Iterable[OrderIntent]:
        key = (snapshot.venue, snapshot.market_id)
        if snapshot.resolved:
            self._positions.pop(key, None)
            self._orders[key] = 0
            return

        # Exit logic: harvest yield once contract approaches 0.96+ or stop out on adverse move
        if key in self._positions and snapshot.yes_bid is not None:
            entry = self._positions[key]
            if snapshot.yes_bid >= 0.96 or snapshot.yes_bid >= entry + 0.08:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"decay-tp-{self._counter}",
                    signal="theta-decay-harvested",
                    strategy="time-decay-yield",
                )
                return
            elif snapshot.yes_bid <= 0.70:
                del self._positions[key]
                self._orders[key] = 0
                self._counter += 1
                yield OrderIntent(
                    timestamp_ms=snapshot.timestamp_ms,
                    venue=snapshot.venue,
                    market_id=snapshot.market_id,
                    side=Side.SELL,
                    quantity=self.quantity,
                    limit_price=snapshot.yes_bid,
                    client_order_id=f"decay-sl-{self._counter}",
                    signal="theta-decay-stop-loss",
                    strategy="time-decay-yield",
                )
                return

        if snapshot.yes_ask is None:
            return

        history = self._history.setdefault(key, [])
        history.append(snapshot.yes_ask)
        if len(history) > self.lookback:
            del history[0]

        if (
            len(history) == self.lookback
            and self.target_min_price <= snapshot.yes_ask <= self.target_max_price
            and max(history) - min(history) <= self.max_volatility
            and self._orders.get(key, 0) < self.max_orders_per_market
            and key not in self._positions
        ):
            self._orders[key] = self._orders.get(key, 0) + 1
            self._positions[key] = snapshot.yes_ask
            self._counter += 1
            yield OrderIntent(
                timestamp_ms=snapshot.timestamp_ms,
                venue=snapshot.venue,
                market_id=snapshot.market_id,
                side=Side.BUY,
                quantity=self.quantity,
                limit_price=snapshot.yes_ask,
                client_order_id=f"decay-{self._counter}",
                signal="time-decay-yield",
                strategy="time-decay-yield",
            )
