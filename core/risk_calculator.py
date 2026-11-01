"""Structure-aware ATR risk levels for signal-only alerts."""
from typing import Optional
from dataclasses import dataclass

@dataclass
class RiskLevels:
    entry: float; entry_zone_low: float; entry_zone_high: float
    stop_loss: float; tp1: float; tp2: float; tp3: float
    risk_pct: float; rr1: float; rr2: float; rr3: float; invalidation: float

def calculate_risk(price: float, atr: float, direction: str, atr_mult_sl: float=1.5,
                   atr_mult_tp1: float=1.5, atr_mult_tp2: float=2.5, atr_mult_tp3: float=4.0,
                   swing_low: Optional[float]=None, swing_high: Optional[float]=None) -> Optional[RiskLevels]:
    if not price or not atr or price<=0 or atr<=0 or direction not in ("LONG","SHORT"): return None
    is_long=direction=="LONG"
    # Structural stop with an ATR safety buffer; reject pathological distant structure.
    if is_long:
        structural=(swing_low-0.15*atr) if swing_low and swing_low<price else price-atr*atr_mult_sl
        sl=max(price-atr*2.5, min(price-atr*0.8, structural))
        if sl>=price: sl=price-atr*atr_mult_sl
        risk=price-sl
        targets=[price+1.5*risk,price+2.5*risk,price+4.0*risk]
        zone=(price-0.15*atr,price+0.10*atr)
    else:
        structural=(swing_high+0.15*atr) if swing_high and swing_high>price else price+atr*atr_mult_sl
        sl=min(price+atr*2.5, max(price+atr*0.8, structural))
        if sl<=price: sl=price+atr*atr_mult_sl
        risk=sl-price
        targets=[price-1.5*risk,price-2.5*risk,price-4.0*risk]
        zone=(price-0.10*atr,price+0.15*atr)
    if risk<=0: return None
    rrs=[abs(t-price)/risk for t in targets]
    return RiskLevels(round(price,8),round(zone[0],8),round(zone[1],8),round(sl,8),
        round(targets[0],8),round(targets[1],8),round(targets[2],8),round(risk/price*100,4),
        round(rrs[0],2),round(rrs[1],2),round(rrs[2],2),round(sl,8))


def is_entry_price_actionable(live_price: float, zone_low: float, zone_high: float) -> bool:
    """Return True only when a fresh ticker price remains inside the planned entry zone."""
    try:
        price, low, high = float(live_price), float(zone_low), float(zone_high)
    except (TypeError, ValueError, OverflowError):
        return False
    import math
    return math.isfinite(price) and math.isfinite(low) and math.isfinite(high) and 0 < low <= price <= high
