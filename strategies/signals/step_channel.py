"""Original Step Channel state calculation using the user's Pine v6 specification."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import numpy as np

@dataclass
class StepChannelResult:
    mid: Optional[float]
    inner_up: Optional[float]
    inner_down: Optional[float]
    outer_up: Optional[float]
    outer_down: Optional[float]
    atr: Optional[float]
    step_atr: Optional[float]
    direction: str
    event: Optional[str]
    event_time_ms: Optional[int]
    series: dict

def pine_atr_series(high, low, close, period: int = 14) -> np.ndarray:
    """Wilder RMA with SMA seed over the first period true ranges."""
    h=np.asarray(high,dtype=np.float64); l=np.asarray(low,dtype=np.float64); c=np.asarray(close,dtype=np.float64)
    n=len(c); out=np.full(n,np.nan,dtype=np.float64)
    if period < 1 or len(h)!=n or len(l)!=n or n<period: return out
    if not (np.all(np.isfinite(h)) and np.all(np.isfinite(l)) and np.all(np.isfinite(c))): return out
    tr=np.empty(n,dtype=np.float64); tr[0]=h[0]-l[0]
    for i in range(1,n): tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
    out[period-1]=float(np.mean(tr[:period]))
    for i in range(period,n): out[i]=(out[i-1]*(period-1)+tr[i])/period
    return out

def calculate_step_channel(high, low, close, times=None, atr_length=14, inner_mult=2.5, outer_mult=4.0, base_length=50, initial_state=None):
    h=np.asarray(high,dtype=np.float64); l=np.asarray(low,dtype=np.float64); c=np.asarray(close,dtype=np.float64); n=len(c)
    mids=np.full(n,np.nan); ins=np.full(n,np.nan); ind=np.full(n,np.nan); outs=np.full(n,np.nan); outd=np.full(n,np.nan)
    if len(h)!=n or len(l)!=n or n<max(atr_length,base_length):
        return StepChannelResult(None,None,None,None,None,None,None,"UNKNOWN",None,None,{})
    atrs=pine_atr_series(h,l,c,atr_length); ts=np.asarray(times,dtype=np.int64) if times is not None else np.arange(n,dtype=np.int64)
    initialized=False; mid=None; step_atr=None; direction="UNKNOWN"; event=None; event_time=None
    start_index=0
    # Resume from a durable prior-bar snapshot when the downloaded history overlaps it.
    saved = initial_state or {}
    try:
        saved_time = int(saved.get("candle_time_ms") or 0)
        saved_mid = float(saved.get("mid"))
        saved_atr = float(saved.get("step_atr", saved.get("atr")) )
        if saved_time and saved_time < int(ts[-1]) and int(ts[0]) <= saved_time:
            mid=saved_mid; step_atr=saved_atr; initialized=True
            direction=str(saved.get("direction") or "UNKNOWN")
            event=saved.get("event") or None
            event_time=int(saved.get("event_time_ms") or 0) or None
            start_index=int(np.searchsorted(ts, saved_time, side="right"))
        elif saved_time == int(ts[-1]) and saved_mid and saved_atr:
            mid=saved_mid; step_atr=saved_atr; initialized=True
            direction=str(saved.get("direction") or "UNKNOWN")
            event=saved.get("event") or None
            event_time=int(saved.get("event_time_ms") or 0) or None
            start_index=n
    except (TypeError, ValueError, OverflowError):
        initialized=False; mid=None; step_atr=None; direction="UNKNOWN"; event=None; event_time=None; start_index=0
    for i in range(start_index,n):
        if not initialized:
            if i>=base_length-1 and np.isfinite(atrs[i]):
                mid=float(np.mean(c[i-base_length+1:i+1])); step_atr=float(atrs[i]); initialized=True
        else:
            # Compare against the previous inner levels before updating state.
            if c[i] > mid+step_atr*inner_mult:
                mid=float(c[i]); step_atr=float(atrs[i]); direction="BULLISH"; event="UP"; event_time=int(ts[i])
            elif c[i] < mid-step_atr*inner_mult:
                mid=float(c[i]); step_atr=float(atrs[i]); direction="BEARISH"; event="DOWN"; event_time=int(ts[i])
        if initialized:
            mids[i]=mid; ins[i]=mid+step_atr*inner_mult; ind[i]=mid-step_atr*inner_mult
            outs[i]=mid+step_atr*outer_mult; outd[i]=mid-step_atr*outer_mult
    if not initialized or not np.isfinite(atrs[-1]):
        return StepChannelResult(None,None,None,None,None,None,None,"UNKNOWN",None,None,{})
    series={"mid":mids,"inner_up":ins,"inner_down":ind,"outer_up":outs,"outer_down":outd}
    return StepChannelResult(float(mids[-1]),float(ins[-1]),float(ind[-1]),float(outs[-1]),float(outd[-1]),float(atrs[-1]),float(step_atr),direction,event,event_time,series)
