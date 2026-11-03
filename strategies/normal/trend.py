"""Directional trend strategy validation.

Normal Engine no longer calculates or uses MACD. This predicate classifies a
trend continuation from price/EMA/VWAP structure and ADX only.
"""


def evaluate(indicators: dict, direction: str) -> bool:
    e9 = indicators.get("ema9")
    e21 = indicators.get("ema21")
    e50 = indicators.get("ema50")
    adx = indicators.get("adx")
    close = indicators.get("close")
    vwap = indicators.get("vwap")

    if any(value is None for value in (e9, e21, e50, adx, close, vwap)):
        return False
    if adx < 18:
        return False

    if direction == "LONG":
        return e9 > e21 > e50 and close > vwap
    if direction == "SHORT":
        return e9 < e21 < e50 and close < vwap
    return False
