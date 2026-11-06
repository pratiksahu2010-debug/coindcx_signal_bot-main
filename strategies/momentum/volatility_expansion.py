"""True ATR expansion against prior ATR baseline and configured trend-strength floor."""
import math

from config import settings


def evaluate(indicators: dict) -> bool:
    """Accept a continuation only when ATR expands and configured ADX is met."""
    try:
        ratio = float(indicators.get("atr_expansion"))
        adx_value = float(indicators.get("adx"))
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(ratio) and math.isfinite(adx_value)
        and ratio >= 1.15
        and adx_value >= settings.momentum_adx_explosion
    )
