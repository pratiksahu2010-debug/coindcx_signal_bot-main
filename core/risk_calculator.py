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
    # Conservative combined round-trip fee/slippage allowance (15 bps).
    # Keep gross target levels intact for display; use net reward for validation.
    cost_per_unit = price * 0.0015
    rrs=[max(0.0, abs(t-price)-cost_per_unit)/risk for t in targets]
    return RiskLevels(round(price,8),round(zone[0],8),round(zone[1],8),round(sl,8),
        round(targets[0],8),round(targets[1],8),round(targets[2],8),round(risk/price*100,4),
        round(rrs[0],2),round(rrs[1],2),round(rrs[2],2),round(sl,8))


def classify_entry_state(live_price: float, entry: float, zone_low: float,
                          zone_high: float, direction: str) -> str:
    """Classify entry without widening the actionable zone into a chase."""
    try:
        import math
        price, anchor = float(live_price), float(entry)
        low, high = float(zone_low), float(zone_high)
    except (TypeError, ValueError, OverflowError):
        return "EXPIRED"
    if not all(math.isfinite(x) for x in (price, anchor, low, high)) or min(price, anchor, low) <= 0 or high <= low:
        return "EXPIRED"
    if low <= price <= high:
        return "IMMEDIATE"
    # Outside the entry band but still close enough to monitor for a pullback.
    # It is never an alert until price returns to the original zone.
    width = high - low
    if direction == "LONG" and high < price <= high + 1.5 * width:
        return "RETEST"
    if direction == "SHORT" and low - 1.5 * width <= price < low:
        return "RETEST"
    return "EXPIRED"


def is_entry_price_actionable(live_price: float, zone_low: float, zone_high: float) -> bool:
    """Backward-compatible strict check for immediate entries only."""
    return classify_entry_state(live_price, (zone_low + zone_high) / 2.0,
                                zone_low, zone_high, "LONG") == "IMMEDIATE"
