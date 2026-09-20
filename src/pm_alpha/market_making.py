from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .models import MarketSnapshot


@dataclass(frozen=True)
class ReferencePrice:
    timestamp_ms: int
    market_id: str
    fair_probability: float
    source: str

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0 or not 0.0 <= self.fair_probability <= 1.0:
            raise ValueError("invalid reference price")


@dataclass(frozen=True)
class MarketMakerConfig:
    half_spread: float = 0.03
    max_inventory: float = 5.0
    quote_size: float = 1.0
    fee_rate: float = 0.0
    adverse_selection_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.half_spread <= 0 or self.max_inventory <= 0 or self.quote_size <= 0:
            raise ValueError("market-maker limits must be positive")
        if self.fee_rate < 0 or self.adverse_selection_bps < 0:
            raise ValueError("cost assumptions must be non-negative")


@dataclass(frozen=True)
class MarketMakerResult:
    fills: int
    bought: float
    sold: float
    inventory: float
    cash_pnl: float
    marked_pnl: float
    fees: float
    rejected_quotes: int


def simulate_reference_market_maker(
    snapshots: Iterable[MarketSnapshot],
    references: Iterable[ReferencePrice],
    config: MarketMakerConfig | None = None,
) -> MarketMakerResult:
    """Stress passive quotes against observed books and an external fair price.

    A quote is considered filled only when the observed book crosses it. This is
    intentionally conservative for paper research: it does not assume queue
    priority or fill a quote merely because the market trades nearby.
    """

    cfg = config or MarketMakerConfig()
    ref_by_time = sorted(references, key=lambda item: item.timestamp_ms)
    current_ref: Optional[ReferencePrice] = None
    inventory = 0.0
    cash = 0.0
    fees = 0.0
    fills = 0
    bought = 0.0
    sold = 0.0
    rejected = 0
    last_mark = 0.0

    for snapshot in sorted(snapshots, key=lambda item: item.timestamp_ms):
        while ref_by_time and ref_by_time[0].timestamp_ms <= snapshot.timestamp_ms:
            current_ref = ref_by_time.pop(0)
        if current_ref is None or current_ref.market_id != snapshot.market_id:
            rejected += 1
            continue
        fair = current_ref.fair_probability
        bid = max(0.0, fair - cfg.half_spread)
        ask = min(1.0, fair + cfg.half_spread)
        if snapshot.yes_ask is not None and snapshot.yes_ask <= bid and inventory < cfg.max_inventory:
            quantity = min(cfg.quote_size, cfg.max_inventory - inventory, snapshot.ask_size)
            if quantity > 0:
                cost = quantity * snapshot.yes_ask
                fee = cost * cfg.fee_rate
                cash -= cost + fee
                fees += fee
                inventory += quantity
                bought += quantity
                fills += 1
        if snapshot.yes_bid is not None and snapshot.yes_bid >= ask and inventory > -cfg.max_inventory:
            quantity = min(cfg.quote_size, inventory + cfg.max_inventory, snapshot.bid_size)
            if quantity > 0:
                proceeds = quantity * snapshot.yes_bid
                fee = proceeds * cfg.fee_rate
                cash += proceeds - fee
                fees += fee
                inventory -= quantity
                sold += quantity
                fills += 1
        last_mark = snapshot.yes_bid if snapshot.yes_bid is not None else last_mark

    marked_pnl = cash + inventory * max(0.0, last_mark - cfg.adverse_selection_bps / 10_000)
    return MarketMakerResult(
        fills=fills,
        bought=bought,
        sold=sold,
        inventory=inventory,
        cash_pnl=cash,
        marked_pnl=marked_pnl,
        fees=fees,
        rejected_quotes=rejected,
    )
