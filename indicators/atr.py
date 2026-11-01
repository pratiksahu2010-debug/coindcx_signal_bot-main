"""Average True Range (Wilder)."""
import numpy as np
from typing import Optional


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> Optional[float]:
    if len(close) < period + 1:
        return None
    tr = np.empty(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    # Wilder smoothing
    atr_val = np.mean(tr[1 : period + 1])
    for i in range(period + 1, len(tr)):
        atr_val = (atr_val * (period - 1) + tr[i]) / period
    return float(atr_val)