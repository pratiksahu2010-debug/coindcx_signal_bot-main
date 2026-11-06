import pytest

from strategies.momentum.explosion_detector import evaluate


def setup(**overrides):
    values = {
        "direction": "LONG",
        "rvol": 1.65,
        "atr_expansion": 1.16,
        "body_ratio": 0.72,
        "close_location": 0.84,
        "directional_volume_proxy_ok": True,
        "ema_extension_atr": 0.55,
        "atr": 10.0,
        "high": 112.0,
        "low": 100.0,
        "squeeze_release": False,
        "structure_break": True,
        "bw_expand": True,
        "roc_direction_ok": True,
        "macd_accel": False,
        "macd_direction_ok": False,
        "rsi_accel": False,
        "ema_aligned": False,
    }
    values.update(overrides)
    return values


def test_direct_swing_breakout_does_not_need_squeeze_release():
    assert evaluate(setup(squeeze_release=False, structure_break=True))


def test_bandwidth_and_directional_momentum_can_ignite_without_squeeze():
    assert evaluate(setup(
        squeeze_release=False,
        structure_break=False,
        bw_expand=True,
        roc_direction_ok=True,
    ))


def test_short_explosion_uses_lower_candle_close_location():
    assert evaluate(setup(
        direction="SHORT",
        close_location=0.16,
        structure_break=True,
    ))


def test_squeeze_release_remains_a_valid_ignition_pattern():
    assert evaluate(setup(
        squeeze_release=True,
        structure_break=False,
        bw_expand=False,
        roc_direction_ok=False,
    ))


@pytest.mark.parametrize("rvol", [0.0, 1.0, 1.34])
def test_rejects_insufficient_relative_volume(rvol):
    assert not evaluate(setup(rvol=rvol))


@pytest.mark.parametrize("body_ratio", [0.0, 0.30, 0.44])
def test_rejects_weak_candle_body(body_ratio):
    assert not evaluate(setup(body_ratio=body_ratio))


def test_rejects_candle_that_closes_back_inside_directional_range():
    assert not evaluate(setup(close_location=0.51))


def test_rejects_late_extended_entry():
    assert not evaluate(setup(ema_extension_atr=1.51))


def test_rejects_low_atr_expansion():
    assert not evaluate(setup(atr_expansion=1.07))


def test_rejects_random_volume_spike_without_ignition_evidence():
    assert not evaluate(setup(
        squeeze_release=False,
        structure_break=False,
        bw_expand=False,
        roc_direction_ok=False,
        ema_aligned=False,
        rvol=1.70,
    ))


def test_rejects_narrow_candle_even_when_other_indicators_align():
    assert not evaluate(setup(
        high=108.0,
        low=100.0,
        atr=10.0,
        atr_expansion=1.10,
        structure_break=True,
    ))
