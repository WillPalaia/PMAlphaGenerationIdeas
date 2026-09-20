import pytest

from pm_alpha.market_making import (
    MarketMakerConfig,
    ReferencePrice,
    simulate_reference_market_maker,
)
from pm_alpha.models import MarketSnapshot


def test_market_maker_accounts_for_inventory_and_fees():
    snapshots = [
        MarketSnapshot(0, "kalshi", "m1", 0.40, 0.44, 2, 2),
        MarketSnapshot(1, "kalshi", "m1", 0.60, 0.64, 2, 2),
    ]
    references = [ReferencePrice(0, "m1", 0.50, "sportsbook")]
    result = simulate_reference_market_maker(
        snapshots,
        references,
        MarketMakerConfig(half_spread=0.05, quote_size=1, fee_rate=0.01),
    )
    assert result.fills == 2
    assert result.inventory == 0
    assert result.marked_pnl == pytest.approx(0.1496)
