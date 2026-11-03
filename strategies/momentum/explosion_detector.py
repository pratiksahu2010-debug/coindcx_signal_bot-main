"""Early but confirmed volatility/volume expansion detector."""
def evaluate(indicators: dict) -> bool:
    return bool(
        indicators.get("squeeze_release")
        and indicators.get("bw_expand")
        and indicators.get("rvol", 0) >= 1.5
        and indicators.get("atr_expansion", 0) >= 1.08
        and indicators.get("macd_direction_ok")
    )
