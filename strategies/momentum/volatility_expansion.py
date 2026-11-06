"""True ATR expansion against prior ATR baseline and configured trend-strength floor."""
from config import settings


def evaluate(indicators: dict) -> bool:
    """Accept a continuation only when ATR expands and configured ADX is met."""
    ratio = indicators.get("atr_expansion")
    adx_value = indicators.get("adx")
    try:
        ratio = float(ratio)
        adx_value = float(adx_value)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        ratio == ratio and adx_value == adx_value
        and ratio >= 1.15
        and adx_value >= settings.momentum_adx_explosion
    )
