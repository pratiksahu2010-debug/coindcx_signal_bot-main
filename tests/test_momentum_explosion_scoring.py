from core.scoring_engine import ScoringEngine


def early_direct_breakout(**overrides):
    values = {
        "direction": "LONG",
        "structure_break": True,
        "squeeze_release": False,
        "bw_expand": False,
        "atr_expansion": 1.16,
        "rvol": 1.65,
        "body_ratio": 0.72,
        "close_location": 0.84,
        "ema_aligned": False,
        "roc_direction_ok": False,
        "macd_accel": False,
        "rsi_accel": False,
        "macd_direction_ok": False,
    }
    values.update(overrides)
    return values


def test_early_direct_breakout_reaches_signal_floor_without_lagging_ema_or_macd():
    result = ScoringEngine().score_momentum_explosion(
        early_direct_breakout(), "LONG"
    )

    assert result.total >= 7.0
    assert result.total <= 10.0
    assert result.grade in ("SIGNAL", "STRONG", "HIGH-CONVICTION")


def test_short_early_breakout_scores_directional_close_correctly():
    result = ScoringEngine().score_momentum_explosion(
        early_direct_breakout(
            structure_break=True,
            close_location=0.16,
            ema_aligned=False,
            roc_direction_ok=False,
        ),
        "SHORT",
    )

    assert result.total >= 7.0
    close_component = next(c for c in result.components if c.name == "Directional Close")
    assert close_component.points == 0.75


def test_explosion_score_rewards_independent_confirmation_without_requiring_it():
    base = ScoringEngine().score_momentum_explosion(
        early_direct_breakout(), "LONG"
    )
    confirmed = ScoringEngine().score_momentum_explosion(
        early_direct_breakout(
            squeeze_release=True,
            bw_expand=True,
            ema_aligned=True,
            roc_direction_ok=True,
            macd_accel=True,
        ),
        "LONG",
    )

    assert confirmed.total > base.total
    assert confirmed.total <= 10.0
