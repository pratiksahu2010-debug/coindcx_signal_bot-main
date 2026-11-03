"""Rolling squeeze release and pre-explosion classification."""
def evaluate(indicators: dict) -> bool:
    return bool(
        indicators.get("squeeze_release")
        and indicators.get("bw_expand")
        and indicators.get("rvol", 0) >= 1.2
    )

def pre_explosion(indicators: dict) -> bool:
    return bool(
        indicators.get("compression_active")
        and indicators.get("rvol", 0) >= 1.2
        and indicators.get("atr_expansion", 0) >= 1.02
    )
