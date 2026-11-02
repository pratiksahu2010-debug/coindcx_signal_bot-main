"""Strict sequential rules for the independent signals module."""
from __future__ import annotations
import numpy as np
from indicators.ema import ema_series
from indicators.rsi import rsi
from indicators.adx import adx, adx_series
from indicators.vwap import vwap
from .step_channel import calculate_step_channel

def evaluate_signal(pair, opens, highs, lows, closes, volumes, times,
                    max_cross_age=3, adx_min=25.0,
                    max_vwap_extension_atr=1.5, max_mid_extension_atr=3.0, step_channel=None):
    o=np.asarray(opens,dtype=float); h=np.asarray(highs,dtype=float); l=np.asarray(lows,dtype=float)
    c=np.asarray(closes,dtype=float); vol=np.asarray(volumes,dtype=float); ts=np.asarray(times,dtype=np.int64)
    n=len(c)
    if n<60 or any(len(x)!=n for x in (o,h,l,vol,ts)):
        return {"valid":False,"reason":"insufficient_history"}
    if not all(np.all(np.isfinite(x)) for x in (o,h,l,c,vol)) or np.any(vol<0):
        return {"valid":False,"reason":"invalid_ohlcv"}
    e9=ema_series(c,9); e21=ema_series(c,21)
    cross_idx=None; direction=None
    for age in range(0,min(max_cross_age,n-1)):
        i=n-1-age
        if e9[i-1]<=e21[i-1] and e9[i]>e21[i]: cross_idx=i; direction="LONG"; break
        if e9[i-1]>=e21[i-1] and e9[i]<e21[i]: cross_idx=i; direction="SHORT"; break
    if direction is None: return {"valid":False,"reason":"no_fresh_ema_crossover"}
    if direction=="LONG" and not e9[-1]>e21[-1] or direction=="SHORT" and not e9[-1]<e21[-1]:
        return {"valid":False,"reason":"ema_alignment_lost"}
    day_start=(int(ts[-1])//86_400_000)*86_400_000
    day_idx=int(np.searchsorted(ts,day_start,side="left"))
    vw=vwap(h[day_idx:],l[day_idx:],c[day_idx:],vol[day_idx:],period=None)
    prev_vw=vwap(h[day_idx:-1],l[day_idx:-1],c[day_idx:-1],vol[day_idx:-1],period=None) if len(c[day_idx:])>1 else None
    vwap_rising=bool(vw is not None and prev_vw is not None and vw>prev_vw)
    if vw is None: return {"valid":False,"reason":"vwap_unavailable"}
    if direction=="LONG" and not c[-1]>vw or direction=="SHORT" and not c[-1]<vw:
        return {"valid":False,"reason":"wrong_vwap_side"}
    rv=rsi(c,14)
    if rv is None or (direction=="LONG" and not 50<rv<=75) or (direction=="SHORT" and not 25<=rv<50):
        return {"valid":False,"reason":"rsi_confirmation"}
    adx_result=adx(h,l,c,14)
    if adx_result is None: return {"valid":False,"reason":"adx_unavailable"}
    av,pdi,mdi=adx_result
    adx_hist=adx_series(h,l,c,14)
    adx_rising=bool(adx_hist is not None and np.isfinite(adx_hist[0][-1]) and np.isfinite(adx_hist[0][-2]) and adx_hist[0][-1]>adx_hist[0][-2])
    if av<adx_min: return {"valid":False,"reason":"adx_below_minimum"}
    if direction=="LONG" and not pdi>mdi or direction=="SHORT" and not mdi>pdi:
        return {"valid":False,"reason":"wrong_di_direction"}
    channel=step_channel or calculate_step_channel(h,l,c,ts)
    if channel.direction != ("BULLISH" if direction=="LONG" else "BEARISH"):
        return {"valid":False,"reason":"step_channel_direction"}
    if channel.event_time_ms is None or channel.event not in ("UP","DOWN"):
        return {"valid":False,"reason":"step_event_unavailable"}
    atr_v=channel.atr
    if atr_v is None or atr_v<=0: return {"valid":False,"reason":"atr_unavailable"}
    vwap_ext=abs(float(c[-1])-float(vw))/atr_v
    mid_ext=abs(float(c[-1])-float(channel.mid))/atr_v
    if vwap_ext>max_vwap_extension_atr: return {"valid":False,"reason":"vwap_extension"}
    if mid_ext>max_mid_extension_atr: return {"valid":False,"reason":"channel_extension"}
    candle_range=max(float(h[-1]-l[-1]),1e-12)
    body_ratio=abs(float(c[-1]-o[-1]))/candle_range
    close_loc=float(c[-1]-l[-1])/candle_range
    if direction=="LONG" and (c[-1]<=o[-1] or close_loc<0.60 or body_ratio<0.35):
        return {"valid":False,"reason":"candle_quality"}
    if direction=="SHORT" and (c[-1]>=o[-1] or close_loc>0.40 or body_ratio<0.35):
        return {"valid":False,"reason":"candle_quality"}
    avg_vol=float(np.mean(vol[-21:-1])) if n>=21 else 0.0
    rvol=float(vol[-1]/avg_vol) if avg_vol>0 else None
    if (direction=="LONG" and channel.event!="UP") or (direction=="SHORT" and channel.event!="DOWN"):
        return {"valid":False,"reason":"step_event_conflict"}
    return {"valid":True,"pair":pair,"direction":direction,"cross_time_ms":int(ts[cross_idx]),
        "cross_age_bars":int(n-1-cross_idx),"candle_time_ms":int(ts[-1]),"close":float(c[-1]),
        "ema9":float(e9[-1]),"ema21":float(e21[-1]),"vwap":float(vw),"rsi":float(rv),
        "adx":float(av),"plus_di":float(pdi),"minus_di":float(mdi),"adx_rising":adx_rising,"vwap_rising":vwap_rising,"atr":float(atr_v),
        "vwap_extension_atr":float(vwap_ext),"channel_extension_atr":float(mid_ext),
        "body_ratio":float(body_ratio),"close_location":float(close_loc),"rvol":rvol,
        "step":{"direction":channel.direction,"event":channel.event,"event_time_ms":channel.event_time_ms,
            "mid":channel.mid,"step_atr":channel.step_atr,"in_plus":channel.inner_up,"in_minus":channel.inner_down,
            "out_plus":channel.outer_up,"out_minus":channel.outer_down},
        "indicators":{"ema9":float(e9[-1]),"ema21":float(e21[-1]),"vwap":float(vw),
            "rsi":float(rv),"adx":float(av),"plus_di":float(pdi),"minus_di":float(mdi),
            "atr":float(atr_v),"rvol":rvol,"adx_rising":adx_rising,"vwap_rising":vwap_rising},
        "state_trace":["WAITING","EMA_CROSS_DETECTED","VWAP_CONFIRMED","RSI_CONFIRMED",
                       "ADX_CONFIRMED","STEP_CHANNEL_CONFIRMED","ENTRY_VALIDATION","CONFIRMED_SIGNAL"]}
