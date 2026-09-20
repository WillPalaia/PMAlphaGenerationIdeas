import pytest

from pm_alpha.strategies import find_complement_opportunity


def test_complement_arb_is_depth_capped_and_fee_aware():
    opportunity = find_complement_opportunity(
        0.40, 0.50, 10, 3, fee_rate=0.01, minimum_edge=0.01
    )

    assert opportunity is not None
    assert opportunity.quantity == 3
    assert opportunity.gross_cost == pytest.approx(2.70)
    assert opportunity.fees == pytest.approx(0.027)
    assert opportunity.net_profit == pytest.approx(0.273)


def test_complement_arb_is_rejected_after_costs():
    opportunity = find_complement_opportunity(
        0.50, 0.50, 10, 10, fee_rate=0.01, minimum_edge=0.001
    )

    assert opportunity is None
