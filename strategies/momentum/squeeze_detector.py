"""Squeeze-release detector (requires actual transition from compression)."""
def evaluate(indicators: dict) -> bool:
    return bool(indicators.get("squeeze_release") and indicators.get("bw_expand")
                and indicators.get("rvol",0)>=1.3)
