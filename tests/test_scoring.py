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