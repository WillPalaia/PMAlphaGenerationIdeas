from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PairedExecutionResult:
    requested_quantity: float
    yes_filled: float
    no_filled: float
    yes_cost: float
    no_cost: float
    hedge_cost: float
    guaranteed_quantity: float
    worst_case_pnl: float


def simulate_paired_execution(
    *,
    requested_quantity: float,
    yes_price: float,
    no_price: float,
    yes_fill_ratio: float,
    no_fill_ratio: float,
    hedge_price: float | None = None,
    fee_rate: float = 0.0,
) -> PairedExecutionResult:
    """Stress a binary complement trade when the two legs do not fill atomically."""

    if requested_quantity <= 0:
        raise ValueError("requested_quantity must be positive")
    if not 0 <= yes_fill_ratio <= 1 or not 0 <= no_fill_ratio <= 1:
        raise ValueError("fill ratios must be between 0 and 1")
    if not 0 < yes_price <= 1 or not 0 < no_price <= 1:
        raise ValueError("prices must be in (0, 1]")
    if fee_rate < 0:
        raise ValueError("fee_rate must be non-negative")

    yes_filled = requested_quantity * yes_fill_ratio
    no_filled = requested_quantity * no_fill_ratio
    matched = min(yes_filled, no_filled)
    hedge_cost = 0.0
    if yes_filled > no_filled:
        if hedge_price is None:
            hedge_price = 1.0 - no_price
        hedge_cost = (yes_filled - no_filled) * hedge_price
    elif no_filled > yes_filled:
        if hedge_price is None:
            hedge_price = 1.0 - yes_price
        hedge_cost = (no_filled - yes_filled) * hedge_price
    costs = yes_filled * yes_price + no_filled * no_price + hedge_cost
    fees = costs * fee_rate
    payout = max(yes_filled, no_filled)
    return PairedExecutionResult(
        requested_quantity=requested_quantity,
        yes_filled=yes_filled,
        no_filled=no_filled,
        yes_cost=yes_filled * yes_price,
        no_cost=no_filled * no_price,
        hedge_cost=hedge_cost,
        guaranteed_quantity=matched,
        worst_case_pnl=payout - costs - fees,
    )
