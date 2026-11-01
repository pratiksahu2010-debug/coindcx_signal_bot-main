"""Exponential Moving Average."""
import numpy as np
from typing import Optional, Union


def ema(data: np.ndarray, period: int) -> Optional[float]:
    """Return the latest EMA value."""
    if len(data) < period:
        return None
    series = ema_series(data, period)
    return float(series[-1]) if series is not None else None


def ema_series(data: np.ndarray, period: int) -> Optional[np.ndarray]:
    if len(data) < period or period < 1:
        return None
    alpha = 2.0 / (period + 1)
    out = np.empty(len(data), dtype=np.float64)
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    # Seed with SMA for first period for better accuracy
    sma = np.mean(data[:period])
    out[period - 1] = sma
    for i in range(period, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out