"""Relative volume versus preceding completed bars (excludes current bar from baseline)."""
import numpy as np
from typing import Optional

def relative_volume(volumes: np.ndarray, period: int=20) -> Optional[float]:
    values=np.asarray(volumes,dtype=np.float64)
    if period < 1 or len(values)<period+1 or not np.all(np.isfinite(values)) or np.any(values<0):
        return None
    baseline=values[-period-1:-1]
    avg=float(np.mean(baseline))
    if avg<=0: return None
    return float(values[-1]/avg)
