"""Early, direction-aware momentum ignition detector."""


def _number(indicators: dict, key: str, default: float = 0.0) -> float:
    try:
        value = float(indicators.get(key, default))
        return value if value == value and abs(value) != float("inf") else default
    except (TypeError, ValueError, OverflowError):
        return default


def _optional_number(indicators: dict, key: str):
    if indicators.get(key) is None:
        return None
    try:
        value = float(indicators[key])
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError, OverflowError):
        return None


def evaluate(indicators: dict) -> bool:
    """Qualify directional volatility ignition with real participation."""
    rvol = _number(indicators, "rvol")
    atr_expansion = _number(indicators, "atr_expansion")
    body = _number(indicators, "body_ratio")
    close_location = _number(indicators, "close_location", 0.5)
    extension = _number(indicators, "ema_extension_atr", 0.0)
    direction = str(indicators.get("direction", "")).upper()

    if direction not in ("LONG", "SHORT") or atr_expansion < 1.08 or body < 0.45:
        return False
    if direction == "LONG":
        directional_close, wick_key = close_location >= 0.62, "upper_wick_ratio"
    else:
        directional_close, wick_key = close_location <= 0.38, "lower_wick_ratio"
    if not directional_close or not indicators.get("directional_volume_proxy_ok", False):
        return False

    opposing_wick = _optional_number(indicators, wick_key)
    if opposing_wick is not None and opposing_wick > 0.35:
        return False

    # Tighter than the former 1.50 ATR cap to avoid late impulse entries.
    if extension > 1.35:
        return False

    atr_value = _number(indicators, "atr")
    candle_range = _number(indicators, "high") - _number(indicators, "low")
    range_atr = candle_range / atr_value if atr_value > 0 and candle_range > 0 else None
    if not (range_atr is None or range_atr >= 0.95 or atr_expansion >= 1.20):
        return False

    strong_early_breakout = bool(
        rvol >= 1.30 and indicators.get("structure_break")
        and range_atr is not None and range_atr >= 1.35 and atr_expansion >= 1.15
    )
    if rvol < 1.50 and not strong_early_breakout:
        return False

    squeeze_release = bool(indicators.get("squeeze_release"))
    structure_break = bool(indicators.get("structure_break"))
    bandwidth_momentum = bool(
        indicators.get("bw_expand")
        and (indicators.get("roc_direction_ok") or indicators.get("macd_accel"))
    )
    trend_ignition = bool(
        indicators.get("ema_aligned") and indicators.get("roc_direction_ok")
        and (
            indicators.get("rsi_accel") or indicators.get("macd_direction_ok")
            or indicators.get("macd_accel")
        )
    )
    high_participation_impulse = bool(
        rvol >= 1.80 and range_atr is not None and range_atr >= 1.25
        and indicators.get("roc_direction_ok")
    )
    return bool(
        squeeze_release or structure_break or bandwidth_momentum
        or trend_ignition or high_participation_impulse
    )
