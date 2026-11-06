"""Early, direction-aware momentum ignition detector.

A genuine explosion may begin with a direct range/swing breakout; it does not
have to follow a Bollinger/Keltner squeeze. All inputs are from completed bars.
"""


def _number(indicators: dict, key: str, default: float = 0.0) -> float:
    try:
        value = float(indicators.get(key, default))
        return value if value == value and abs(value) != float("inf") else default
    except (TypeError, ValueError, OverflowError):
        return default


def evaluate(indicators: dict) -> bool:
    """Return True for an early, directional expansion with real participation.

    Core quality: volume + ATR expansion + a decisive directional candle.
    Ignition may come from squeeze release, a confirmed swing break, or a
    confluence of expanding bandwidth and directional momentum.
    """
    rvol = _number(indicators, "rvol")
    atr_expansion = _number(indicators, "atr_expansion")
    body = _number(indicators, "body_ratio")
    close_location = _number(indicators, "close_location", 0.5)
    extension = _number(indicators, "ema_extension_atr", 0.0)
    direction = str(indicators.get("direction", "")).upper()

    if rvol < 1.35 or atr_expansion < 1.08 or body < 0.45:
        return False

    # Reject candles that do not close decisively in the direction of travel.
    if direction == "LONG":
        directional_close = close_location >= 0.58
    elif direction == "SHORT":
        directional_close = close_location <= 0.42
    else:
        directional_close = bool(indicators.get("directional_candle"))
    if not directional_close or not indicators.get("directional_volume_proxy_ok", False):
        return False

    # Avoid late alerts. A strong move is useful only while an entry is still
    # reasonably close to its short-term mean.
    if extension > 1.50:
        return False

    atr_value = _number(indicators, "atr")
    candle_range = _number(indicators, "high") - _number(indicators, "low")
    range_atr = candle_range / atr_value if atr_value > 0 and candle_range > 0 else None
    range_expansion = (
        range_atr is None
        or range_atr >= 0.95
        or atr_expansion >= 1.20
    )
    if not range_expansion:
        return False

    squeeze_release = bool(indicators.get("squeeze_release"))
    structure_break = bool(indicators.get("structure_break"))
    bandwidth_momentum = bool(
        indicators.get("bw_expand")
        and (
            indicators.get("roc_direction_ok")
            or indicators.get("macd_accel")
        )
    )
    trend_ignition = bool(
        indicators.get("ema_aligned")
        and indicators.get("roc_direction_ok")
        and (
            indicators.get("rsi_accel")
            or indicators.get("macd_direction_ok")
            or indicators.get("macd_accel")
        )
    )
    high_participation_impulse = bool(
        rvol >= 1.80
        and range_atr is not None
        and range_atr >= 1.25
        and indicators.get("roc_direction_ok")
    )

    # Any one of these distinct ignition patterns can qualify. This removes
    # the old squeeze-only dependency without turning every volume spike into
    # an alert.
    ignition = (
        squeeze_release
        or structure_break
        or bandwidth_momentum
        or trend_ignition
        or high_participation_impulse
    )
    return bool(ignition)
