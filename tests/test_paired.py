import pytest

from pm_alpha.paired import simulate_paired_execution


def test_paired_execution_exposes_unhedged_leg_cost():
    result = simulate_paired_execution(
        requested_quantity=10,
        yes_price=0.40,
        no_price=0.50,
        yes_fill_ratio=1,
        no_fill_ratio=0.5,
        hedge_price=0.80,
    )
    assert result.guaranteed_quantity == 5
    assert result.hedge_cost == pytest.approx(4.0)
    assert result.worst_case_pnl < 0
