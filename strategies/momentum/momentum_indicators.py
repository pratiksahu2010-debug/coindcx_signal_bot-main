"""Momentum quality predicates; avoid requiring every lagging confirmation."""
def evaluate(indicators: dict) -> bool:
    return bool(
        indicators.get("macd_direction_ok")
        and indicators.get("roc_direction_ok")
        and indicators.get("rvol", 0) >= 1.2
        and (indicators.get("macd_accel") or indicators.get("structure_break")
             or indicators.get("atr_expansion", 0) >= 1.08)
    )
