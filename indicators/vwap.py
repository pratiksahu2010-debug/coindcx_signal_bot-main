"""Volume-weighted average price over supplied candles (optionally rolling)."""
import numpy as np
from typing import Optional

def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: Optional[int]=100) -> Optional[float]:
    high=np.asarray(high,dtype=np.float64); low=np.asarray(low,dtype=np.float64)
    close=np.asarray(close,dtype=np.float64); volume=np.asarray(volume,dtype=np.float64)
    n=len(close)
    if n==0 or not (len(high)==len(low)==len(volume)==n): return None
    if not all(np.all(np.isfinite(x)) for x in (high,low,close,volume)) or np.any(volume<0): return None
    if period is not None:
        if period<1: return None
        high=high[-period:]; low=low[-period:]; close=close[-period:]; volume=volume[-period:]
    total=float(np.sum(volume))
    if total<=0: return None
    typical=(high+low+close)/3.0
    return float(np.dot(typical,volume)/total)
