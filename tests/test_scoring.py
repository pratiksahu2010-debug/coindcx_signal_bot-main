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
