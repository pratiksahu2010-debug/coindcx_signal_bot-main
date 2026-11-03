from datetime import time, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
START = time(9, 15)
RANGE_END = time(9, 30)
END = time(15, 30)


def _local(ts):
    from datetime import datetime
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).astimezone(IST)


def evaluate(candles, atr_value, rvol, min_rvol=1.30, max_extension_atr=0.80):
    """Evaluate a completed-candle 15-minute IST opening range breakout."""
    try:
        ts = candles["time"]
        op, hi, lo, cl = (candles[k] for k in ("open", "high", "low", "close"))
        if len(ts) == 0 or not all(len(x) == len(ts) for x in (op, hi, lo, cl)):
            return {"state": "INVALID_DATA"}
        now = _local(ts[-1])
        if now.time() < RANGE_END:
            return {"state": "BUILDING_RANGE"}
        if now.time() >= END:
            return {"state": "SESSION_CLOSED"}
        idx = [i for i, stamp in enumerate(ts)
               if _local(stamp).date() == now.date()
               and START <= _local(stamp).time() < RANGE_END]
        if len(idx) < 3:
            return {"state": "INCOMPLETE_RANGE"}
        rh = max(float(hi[i]) for i in idx)
        rl = min(float(lo[i]) for i in idx)
        atr = float(atr_value or 0)
        if atr <= 0 or rh <= rl:
            return {"state": "INVALID_VOLATILITY"}
        width_atr = (rh - rl) / atr
        if width_atr < 0.10 or width_atr > 2.50:
            return {"state": "UNTRADEABLE_RANGE", "range_high": rh,
                    "range_low": rl, "range_width_atr": round(width_atr, 4)}
        o, h, l, c = map(float, (op[-1], hi[-1], lo[-1], cl[-1]))
        span = max(h - l, 1e-12)
        body = abs(c - o) / span
        location = (c - l) / span
        if c > rh:
            direction, edge = "LONG", rh
            directional = c > o and location >= 0.60
        elif c < rl:
            direction, edge = "SHORT", rl
            directional = c < o and location <= 0.40
        else:
            return {"state": "INSIDE_RANGE", "range_high": rh, "range_low": rl}
        extension = abs(c - edge) / atr
        previous_close = float(cl[-2]) if len(cl) >= 2 else c
        retest = (
            direction == "LONG" and previous_close > rh and float(lo[-1]) <= rh + 0.15 * atr
            or direction == "SHORT" and previous_close < rl and float(hi[-1]) >= rl - 0.15 * atr
        )
        if not directional or body < 0.35:
            state = "WEAK_BREAKOUT"
        elif rvol is None or float(rvol) < min_rvol:
            state = "LOW_VOLUME_BREAKOUT"
        elif extension > max_extension_atr:
            state = "EXTENDED_BREAKOUT"
        else:
            state = "ORB_RETEST" if retest else "ORB_BREAKOUT"
        return {
            "state": state, "direction": direction,
            "range_high": round(rh, 10), "range_low": round(rl, 10),
            "breakout_level": round(edge, 10),
            "range_width_atr": round(width_atr, 4),
            "breakout_extension_atr": round(extension, 4),
            "body_ratio": round(body, 4), "close_location": round(location, 4),
            "rvol": round(float(rvol), 4) if rvol is not None else None,
            "session_date": now.date().isoformat(),
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return {"state": "INVALID_DATA"}
