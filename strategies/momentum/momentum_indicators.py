"""Momentum quality predicates shared by momentum strategies."""
def evaluate(indicators: dict) -> bool:
    return bool(indicators.get("ema_aligned") and indicators.get("macd_accel")
                and indicators.get("rsi_accel") and indicators.get("roc_direction_ok")
                and indicators.get("structure_break") and indicators.get("rvol",0)>=1.3)
