"""Directional trend strategy validation."""
def evaluate(indicators: dict, direction: str) -> bool:
    e9, e21, e50 = indicators.get("ema9"), indicators.get("ema21"), indicators.get("ema50")
    adx = indicators.get("adx")
    close, vwap = indicators.get("close"), indicators.get("vwap")
    if any(v is None for v in (e9,e21,e50,adx,close,vwap)): return False
    if adx < 18: return False
    if direction == "LONG":
        return e9 > e21 > e50 and close > vwap and indicators.get("macd_hist",0)>0
    if direction == "SHORT":
        return e9 < e21 < e50 and close < vwap and indicators.get("macd_hist",0)<0
    return False
