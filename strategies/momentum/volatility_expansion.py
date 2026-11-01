"""True ATR expansion against prior ATR baseline."""
def evaluate(indicators: dict) -> bool:
    ratio=indicators.get("atr_expansion")
    return ratio is not None and ratio>=1.15 and indicators.get("adx",0)>=18
