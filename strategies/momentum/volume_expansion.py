"""Volume expansion relative to rolling baseline."""
def evaluate(indicators: dict) -> bool:
    return indicators.get("rvol") is not None and indicators["rvol"]>=1.5 and indicators.get("body_ratio",0)>=0.45
