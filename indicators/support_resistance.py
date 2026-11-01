"""Simple swing high / low support & resistance."""
import numpy as np
from typing import List, Tuple


def find_swing_levels(
    high: np.ndarray,
    low: np.ndarray,
    lookback: int = 5,
    max_levels: int = 3,
) -> Tuple[List[float], List[float]]:
    """Return (resistance_levels, support_levels) sorted nearest first."""
    n = len(high)
    if n < lookback * 2 + 1:
        return [], []

    resistances = []
    supports = []
    for i in range(lookback, n - lookback):
        if high[i] == max(high[i - lookback : i + lookback + 1]):
            resistances.append(float(high[i]))
        if low[i] == min(low[i - lookback : i + lookback + 1]):
            supports.append(float(low[i]))

    # Unique and nearest
    resistances = sorted(set(resistances), reverse=True)[:max_levels]
    supports = sorted(set(supports))[:max_levels]
    return resistances, supports