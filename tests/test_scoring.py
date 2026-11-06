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
        "rsi": 50.1, "directional_candle": True,
    }
    assert all(_normal_threshold_gates(long_indicators, "LONG", 25.0).values())

    short_indicators = {
        "ema9": 99.0, "ema21": 100.0,
        "close": 98.0, "vwap": 99.0,
        "adx": 25.0, "confirm_15m": True,
        "rsi": 49.9, "directional_candle": True,
    }
    assert all(_normal_threshold_gates(short_indicators, "SHORT", 25.0).values())


def test_normal_five_condition_gate_rejects_each_missing_confirmation():
    from core.engine import _normal_threshold_gates

    base = {
        "ema9": 101.0, "ema21": 100.0,
        "close": 102.0, "vwap": 101.0,
        "adx": 25.0, "confirm_15m": True,
        "rsi": 55.0, "directional_candle": True,
    }
    assert all(_normal_threshold_gates(base, "LONG", 25.0).values())

    for key, value in (
        ("ema9", 99.0),
        ("close", 100.0),
        ("adx", 24.99),
        ("confirm_15m", False),
        ("rsi", 50.0),
        ("directional_candle", False),
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


def test_normal_relative_volume_gate_uses_strength_without_direction():
    from core.engine import _normal_relative_volume_gate

    # Passing RVOL is sufficient even when the candle-direction proxy opposes
    # the signal direction or is absent entirely.
    for indicators in (
        {"rvol": 1.2, "directional_volume_proxy_ok": False},
        {"rvol": 1.2},
    ):
        assert _normal_relative_volume_gate(indicators, "LONG", 1.2) == {
            "relative_volume": True,
        }

    # Low RVOL remains a single failed volume threshold, not a directional veto.
    weak_volume = _normal_relative_volume_gate(
        {"rvol": 1.19, "directional_volume_proxy_ok": True}, "LONG", 1.2
    )
    assert weak_volume == {"relative_volume": False}

    missing_volume = _normal_relative_volume_gate(
        {"rvol": None, "directional_volume_proxy_ok": False}, "SHORT", 1.3
    )
    assert missing_volume == {"relative_volume": False}

def test_normal_relative_volume_below_threshold_stays_watchlist_eligible():
    from core.engine import _normal_relative_volume_gate

    for rvol in (0.0, 0.75, 0.99, 1.0, 1.19):
        gates = _normal_relative_volume_gate(
            {"rvol": rvol, "directional_volume_proxy_ok": True}, "LONG", 1.2
        )
        assert gates["relative_volume"] is False
        # Engine disposition for failed RVOL is WATCHLIST,
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

def test_watchlist_removes_stale_normal_setup_when_rvol_recovers_or_core_setup_fails():
    from core.watchlist_manager import WatchlistManager

    watchlist = WatchlistManager(max_entries=10, ttl_minutes=60)
    watchlist.add({
        "pair": "B-BTC_USDT", "engine": "normal", "direction": "LONG",
        "score": 0.0, "grade": "WATCHLIST", "watch_reason": "WAITING_FOR_RVOL_1_2",
    })
    watchlist.add({
        "pair": "B-BTC_USDT", "engine": "normal", "direction": "SHORT",
        "score": 0.0, "grade": "WATCHLIST", "watch_reason": "WAITING_FOR_RVOL_1_2",
    })
    watchlist.add({
        "pair": "B-BTC_USDT", "engine": "momentum", "direction": "LONG",
        "score": 5.5, "grade": "WATCHLIST",
    })

    removed = watchlist.remove_pair("B-BTC_USDT", "normal")

    assert removed == 2
    assert watchlist.get("B-BTC_USDT", "normal", "LONG") is None
    assert watchlist.get("B-BTC_USDT", "normal", "SHORT") is None
    # Normal cleanup must not touch the independent Momentum watchlist.
    assert watchlist.get("B-BTC_USDT", "momentum", "LONG") is not None


def test_normal_rvol_gate_rejects_nonfinite_and_nonpositive_thresholds():
    from core.engine import _normal_relative_volume_gate

    for value in (float("nan"), float("inf"), -float("inf"), None):
        gates = _normal_relative_volume_gate(
            {"rvol": value, "directional_volume_proxy_ok": True}, "LONG", 1.2
        )
        assert gates["relative_volume"] is False

    assert _normal_relative_volume_gate(
        {"rvol": 2.0, "directional_volume_proxy_ok": True}, "LONG", 0.0
    )["relative_volume"] is False



def test_normal_reversal_rsi_gate_accepts_improving_extreme_readings():
    from core.engine import _normal_threshold_gates

    long_reversal = {
        "ema9": 101.0, "ema21": 100.0, "close": 102.0, "vwap": 101.0,
        "adx": 25.0, "confirm_15m": True, "rsi": 38.0, "rsi_prev": 35.0,
        "directional_candle": True,
    }
    short_reversal = {
        "ema9": 99.0, "ema21": 100.0, "close": 98.0, "vwap": 99.0,
        "adx": 25.0, "confirm_15m": True, "rsi": 62.0, "rsi_prev": 65.0,
        "directional_candle": True,
    }
    assert all(_normal_threshold_gates(long_reversal, "LONG", 25.0, strategy="REVERSAL").values())
    assert all(_normal_threshold_gates(short_reversal, "SHORT", 25.0, strategy="REVERSAL").values())


def test_normal_reversal_rsi_gate_rejects_non_improving_extreme_readings():
    from core.engine import _normal_threshold_gates

    indicators = {
        "ema9": 101.0, "ema21": 100.0, "close": 102.0, "vwap": 101.0,
        "adx": 25.0, "confirm_15m": True, "rsi": 38.0, "rsi_prev": 39.0,
        "directional_candle": True,
    }
    assert not _normal_threshold_gates(
        indicators, "LONG", 25.0, strategy="REVERSAL"
    )["rsi_direction"]


def test_normal_reversal_receives_rsi_score_when_recovering_from_extreme():
    engine = ScoringEngine()
    result = engine.score_normal({
        "close": 102.0, "ema9": 101.0, "ema21": 100.0, "vwap": 101.0,
        "adx": 30.0, "rsi": 38.0, "rsi_prev": 35.0,
    }, "LONG")
    rsi_component = next(c for c in result.components if c.name == "RSI")
    assert rsi_component.points == 10.0 / 9.0
    assert "reversal improving" in rsi_component.explanation


def test_orb_scoring_preserves_zero_close_location_and_zero_extension():
    result = ScoringEngine().score_orb({
        "rvol": 2.0, "body_ratio": 0.8, "close_location": 0.0,
        "orb_breakout_extension_atr": 0.0, "orb_range_width_atr": 0.8,
        "ema_aligned": True, "macd_direction_ok": True, "roc_direction_ok": True,
    }, "SHORT")
    location = next(c for c in result.components if c.name == "ORB Close Location")
    extension = next(c for c in result.components if c.name == "ORB Entry Distance")
    assert location.points == 1.25
    assert extension.points == 2.0


def test_normal_adx_score_rewards_di_alignment_without_rejecting_opposition():
    engine = ScoringEngine()
    base = {
        "close": 102.0, "ema9": 101.0, "ema21": 100.0, "vwap": 101.0,
        "adx": 32.0, "rsi": 58.0, "rvol": 1.8, "atr_pct": 0.9,
        "body_ratio": 0.7, "confirm_15m": True, "structure_direction_ok": True,
    }
    aligned = engine.score_normal({**base, "di_direction_ok": True}, "LONG")
    opposed = engine.score_normal({**base, "di_direction_ok": False}, "LONG")
    aligned_adx = next(c for c in aligned.components if c.name == "ADX")
    opposed_adx = next(c for c in opposed.components if c.name == "ADX")
    assert aligned_adx.points > opposed_adx.points
    # DI opposition reduces quality; it does not become an extra hard gate.
    assert opposed_adx.points > 0


def test_momentum_macd_direction_gets_partial_credit_before_acceleration():
    engine = ScoringEngine()
    aligned = engine.score_momentum({
        "macd_direction_ok": True, "macd_accel": False,
    }, "LONG")
    component = next(c for c in aligned.components if c.name == "MACD Accel")
    assert component.points == 0.5
    assert component.valid
