"""Rate of Change."""
import numpy as np
from typing import Optional


def roc(closes: np.ndarray, period: int = 10) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    prev = closes[-period - 1]
    if prev == 0:
        return 0.0
    return float((closes[-1] - prev) / prev * 100)