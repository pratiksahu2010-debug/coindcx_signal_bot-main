"""Entry-state and cost-aware risk tests."""
from core.risk_calculator import calculate_risk, classify_entry_state


def test_entry_state_immediate_retest_and_expired():
    assert classify_entry_state(100.0, 100.0, 99.0, 100.5, "LONG") == "IMMEDIATE"
    assert classify_entry_state(101.0, 100.0, 99.0, 100.5, "LONG") == "RETEST"
    assert classify_entry_state(103.0, 100.0, 99.0, 100.5, "LONG") == "EXPIRED"
    assert classify_entry_state(98.0, 100.0, 99.5, 101.0, "SHORT") == "RETEST"


def test_risk_levels_include_positive_net_reward_after_costs():
    risk = calculate_risk(100.0, 2.0, "LONG", swing_low=98.0)
    assert risk is not None
    assert risk.rr1 > 1.0
    assert risk.rr2 > risk.rr1
    assert risk.rr3 > risk.rr2


def test_configured_reward_multipliers_are_applied_as_r_multiples():
    risk = calculate_risk(
        100.0, 2.0, "LONG", swing_low=98.0,
        atr_mult_tp1=1.0, atr_mult_tp2=2.0, atr_mult_tp3=3.0,
        cost_bps=0,
    )
    assert risk is not None
    assert risk.rr1 == 1.0
    assert risk.rr2 == 2.0
    assert risk.rr3 == 3.0


def test_invalid_reward_multipliers_fail_closed():
    assert calculate_risk(100.0, 2.0, "LONG", atr_mult_tp1=2.0,
                          atr_mult_tp2=1.0, atr_mult_tp3=3.0) is None
    assert calculate_risk(100.0, 2.0, "LONG", atr_mult_tp1=float("nan")) is None
