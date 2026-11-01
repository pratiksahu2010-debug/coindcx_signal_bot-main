"""Bollinger Bands."""
import numpy as np
from typing import Optional, Tuple


def bollinger_bands(
    closes: np.ndarray,
    period: int = 20,
    std_dev: float = 2.0,
) -> Optional[Tuple[float, float, float]]:
    """Return (upper, middle, lower) or None."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = float(np.mean(window))
    std = float(np.std(window, ddof=0))
    upper = mid + std_dev * std
    lower = mid - std_dev * std
    return upper, mid, lower


def bandwidth(upper: float, lower: float, mid: float) -> float:
    if mid == 0:
        return 0.0
    return (upper - lower) / mid