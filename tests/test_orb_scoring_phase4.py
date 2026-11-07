from core.scoring_engine import ScoringEngine


def orb_quality(**overrides):
    values = {
        "rvol": 1.9,
        "body_ratio": 0.72,
        "close_location": 0.86,
        "orb_breakout_extension_atr": 0.25,
        "orb_range_width_atr": 0.85,
        "ema_aligned": True,
        "macd_direction_ok": True,
        "roc_direction_ok": True,
        "orb_trend_confirmed": True,
        "orb_di_aligned": True,
        "orb_adx_rising": True,
        "orb_wick_quality": True,
        "adx": 25.0,
    }
    values.update(overrides)
    return values


def test_orb_score_uses_ten_point_scale_and_rewards_trend_confirmation():
    result = ScoringEngine().score_orb(orb_quality(), "LONG")
    assert result.total == 10.0
    assert result.total <= 10.0


def test_orb_score_rewards_adx_di_and_adx_rising():
    confirmed = ScoringEngine().score_orb(orb_quality(), "LONG")
    unconfirmed = ScoringEngine().score_orb(
        orb_quality(orb_trend_confirmed=False, orb_adx_rising=False), "LONG"
    )
    assert confirmed.total > unconfirmed.total


def test_orb_short_score_uses_directional_close_location():
    result = ScoringEngine().score_orb(orb_quality(close_location=0.14), "SHORT")
    close_component = next(c for c in result.components if c.name == "ORB Close Location")
    assert close_component.points == 1.0
