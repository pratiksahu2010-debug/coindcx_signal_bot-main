"""Confirmed volatility/volume explosion."""
def evaluate(indicators: dict) -> bool:
    return bool(indicators.get("squeeze_release") and indicators.get("bw_expand")
                and indicators.get("rvol",0)>=1.8 and indicators.get("body_ratio",0)>=0.55
                and indicators.get("atr_expansion",0)>=1.15)
