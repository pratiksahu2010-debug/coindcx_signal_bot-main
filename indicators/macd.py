"""MACD indicator."""
import numpy as np
from typing import Optional, Tuple
from .ema import ema_series


def macd(
    closes: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Optional[Tuple[float, float, float]]:
    """Return (macd_line, signal_line, histogram) or None."""
    if len(closes) < slow + signal:
        return None
    fast_ema = ema_series(closes, fast)
    slow_ema = ema_series(closes, slow)
    if fast_ema is None or slow_ema is None:
        return None
    macd_line = fast_ema - slow_ema
    signal_line = ema_series(macd_line[slow - 1 :], signal)
    if signal_line is None:
        return None
    # Align
    ml = macd_line[-1]
    sl = signal_line[-1]
    hist = ml - sl
    return float(ml), float(sl), float(hist)