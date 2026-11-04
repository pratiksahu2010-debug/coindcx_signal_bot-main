"""Scoring engine unit tests."""
from core.scoring_engine import ScoringEngine


def test_normal_score_high():
    engine = ScoringEngine()
    indicators = {
        "close": 67000,
        "ema9": 67100,
        "ema21": 66800,
        "vwap": 66900,
        "adx": 32,
        "rsi": 58,
        "macd_hist": 12.5,
        "rvol": 1.8,
        "atr_pct": 0.9,
        "body_ratio": 0.7,
        "confirm_15m": True,
        "structure_ok": True,
    }
    result = engine.score_normal(indicators, "LONG")
    assert result.total >= 7.0
    assert result.grade in ("SIGNAL", "STRONG", "HIGH-CONVICTION")
    assert result.direction == "LONG"


def test_normal_score_low_adx():
    engine = ScoringEngine()
    indicators = {
        "close": 67000,
        "ema9": 67100,
        "ema21": 66800,
        "vwap": 66900,
        "adx": 15,
        "rsi": 58,
        "macd_hist": 12.5,
        "rvol": 1.1,
        "atr_pct": 0.2,
        "body_ratio": 0.3,
        "confirm_15m": False,
        "structure_ok": False,
    }
    result = engine.score_normal(indicators, "LONG")
    assert result.total < 6.0


def test_normal_five_condition_gate_long_and_short():
    from core.engine import _normal_threshold_gates

    long_indicators = {
        "ema9": 101.0, "ema21": 100.0,
        "close": 102.0, "vwap": 101.0,
        "adx": 25.0, "confirm_15m": True,
        "rsi": 50.1,
        # Candle shape is intentionally absent; it is not a gate.
    }
    assert all(_normal_threshold_gates(long_indicators, "LONG", 25.0).values())

    short_indicators = {
        "ema9": 99.0, "ema21": 100.0,
        "close": 98.0, "vwap": 99.0,
        "adx": 25.0, "confirm_15m": True,
        "rsi": 49.9,
    }
    assert all(_normal_threshold_gates(short_indicators, "SHORT", 25.0).values())


def test_normal_five_condition_gate_rejects_each_missing_confirmation():
    from core.engine import _normal_threshold_gates

    base = {
        "ema9": 101.0, "ema21": 100.0,
        "close": 102.0, "vwap": 101.0,
        "adx": 25.0, "confirm_15m": True,
        "rsi": 55.0,
    }
    assert all(_normal_threshold_gates(base, "LONG", 25.0).values())

    for key, value in (
        ("ema9", 99.0),
        ("close", 100.0),
        ("adx", 24.99),
        ("confirm_15m", False),
        ("rsi", 50.0),
    ):
        changed = dict(base)
        changed[key] = value
        gates = _normal_threshold_gates(changed, "LONG", 25.0)
        assert not all(gates.values()), key
        assert [name for name, passed in gates.items() if not passed]


def test_normal_rsi_scoring_does_not_penalize_strong_directional_momentum():
    engine = ScoringEngine()
    long_result = engine.score_normal({
        "close": 102, "ema9": 101, "ema21": 100, "vwap": 101,
        "adx": 30, "rsi": 76, "macd_hist": 1,
    }, "LONG")
    short_result = engine.score_normal({
        "close": 98, "ema9": 99, "ema21": 100, "vwap": 99,
        "adx": 30, "rsi": 24, "macd_hist": -1,
    }, "SHORT")
    assert next(c for c in long_result.components if c.name == "RSI").points == 10.0 / 9.0
    assert next(c for c in short_result.components if c.name == "RSI").points == 10.0 / 9.0

def test_orb_scoring_rewards_clean_breakout_and_penalizes_extension():
    from core.scoring_engine import ScoringEngine

    engine = ScoringEngine()
    clean = engine.score_orb({
        "rvol": 1.8, "body_ratio": 0.70, "close_location": 0.85,
        "orb_breakout_extension_atr": 0.25, "orb_range_width_atr": 0.8,
        "ema_aligned": True, "macd_direction_ok": True,
        "roc_direction_ok": True,
    }, "LONG")
    stretched = engine.score_orb({
        "rvol": 1.8, "body_ratio": 0.70, "close_location": 0.85,
        "orb_breakout_extension_atr": 0.75, "orb_range_width_atr": 0.8,
        "ema_aligned": True, "macd_direction_ok": True,
        "roc_direction_ok": True,
    }, "LONG")
    assert clean.total >= 7.0
    assert clean.total > stretched.total


def test_normal_relative_volume_gate_requires_strength_and_direction():
    from core.engine import _normal_relative_volume_gate

    long_ok = {
        "rvol": 1.3, "directional_volume_proxy_ok": True,
    }
    assert all(_normal_relative_volume_gate(long_ok, "LONG", 1.3).values())

    short_ok = {
        "rvol": 1.6, "directional_volume_proxy_ok": True,
    }
    assert all(_normal_relative_volume_gate(short_ok, "SHORT", 1.3).values())

    weak_volume = _normal_relative_volume_gate(
        {"rvol": 1.29, "directional_volume_proxy_ok": True}, "LONG", 1.3
    )
    assert weak_volume == {"relative_volume": False, "directional_volume": True}

    wrong_direction = _normal_relative_volume_gate(
        {"rvol": 1.8, "directional_volume_proxy_ok": False}, "LONG", 1.3
    )
    assert wrong_direction == {"relative_volume": True, "directional_volume": False}

    missing_volume = _normal_relative_volume_gate(
        {"rvol": None, "directional_volume_proxy_ok": True}, "SHORT", 1.3
    )
    assert missing_volume["relative_volume"] is False


def test_normal_relative_volume_below_threshold_stays_watchlist_eligible():
    from core.engine import _normal_relative_volume_gate

    for rvol in (0.0, 0.75, 0.99, 1.0, 1.19):
        gates = _normal_relative_volume_gate(
            {"rvol": rvol, "directional_volume_proxy_ok": True}, "LONG", 1.2
        )
        assert gates["relative_volume"] is False
        # Engine disposition for every failed RVOL/directional proxy is WATCHLIST,
        # never a hard rejection; next completed candle is evaluated afresh.


def test_normal_relative_volume_threshold_is_configurable():
    from config import settings

    old = settings.normal_rvol_min
    try:
        settings.normal_rvol_min = 1.5
        from core.engine import _normal_relative_volume_gate
        assert not all(_normal_relative_volume_gate(
            {"rvol": 1.4, "directional_volume_proxy_ok": True}, "LONG",
            settings.normal_rvol_min
        ).values())
    finally:
        settings.normal_rvol_min = old
