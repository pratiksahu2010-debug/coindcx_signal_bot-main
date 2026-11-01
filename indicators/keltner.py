"""Keltner Channels."""
import numpy as np
from typing import Optional, Tuple
from .ema import ema
from .atr import atr


def keltner_channels(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 20,
    atr_mult: float = 1.5,
) -> Optional[Tuple[float, float, float]]:
    """Return (upper, middle, lower) or None."""
    mid = ema(close, period)
    atr_val = atr(high, low, close, period)
    if mid is None or atr_val is None:
        return None
    upper = mid + atr_mult * atr_val
    lower = mid - atr_mult * atr_val
    return float(upper), float(mid), float(lower)