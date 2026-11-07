"""Fail-closed market microstructure checks for signal candidates."""
import math
from typing import Any, Dict, Optional, Tuple


def evaluate_orderbook_spread(
    snapshot: Any, max_spread_bps: float
) -> Tuple[bool, Optional[float], str]:
    """Accept only a finite, non-negative spread within the configured limit."""
    try:
        spread = float(snapshot["spread_bps"])
        maximum = float(max_spread_bps)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False, None, "orderbook_unavailable"
    if not math.isfinite(spread) or spread < 0:
        return False, None, "invalid_orderbook_spread"
    if not math.isfinite(maximum) or maximum <= 0:
        return False, spread, "invalid_spread_limit"
    if spread > maximum:
        return False, spread, "spread_too_wide"
    return True, spread, "ok"
