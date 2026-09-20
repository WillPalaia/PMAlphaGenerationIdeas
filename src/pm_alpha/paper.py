from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from .models import MarketSnapshot, OrderIntent
from .storage import SnapshotStore

logger = logging.getLogger(__name__)


class SnapshotSource(Protocol):
    async def snapshots(self) -> list[MarketSnapshot]:
        """Fetch the latest normalized snapshots without placing orders."""


class PaperStrategy(Protocol):
    def on_snapshot(self, snapshot: MarketSnapshot) -> list[OrderIntent]:
        ...


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
    ) -> None:
        self.source = source
        self.strategy = strategy
        self.store = store
        self.config = config or PaperConfig()
        self.on_intent = on_intent
        self._stop = asyncio.Event()
        self._market_exposure: dict[tuple[str, str], float] = {}

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Poll until stopped; errors are surfaced to the caller."""
        while not self._stop.is_set():
            snapshots = await self.source.snapshots()
            for snapshot in snapshots:
                self.store.append(snapshot)
                for intent in self.strategy.on_snapshot(snapshot):
                    if not self._is_allowed(intent):
                        logger.warning("Rejected paper intent %s by risk limits", intent.client_order_id)
                        continue
                    self._market_exposure[(intent.venue, intent.market_id)] = (
                        self._market_exposure.get((intent.venue, intent.market_id), 0.0)
                        + intent.quantity * intent.limit_price
                    )
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
