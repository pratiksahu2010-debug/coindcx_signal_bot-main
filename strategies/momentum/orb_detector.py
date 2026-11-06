"""DST-aware London and New York opening-range breakout detector."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

SESSIONS = {
    "london": {"timezone": "Europe/London", "open": time(8, 0)},
    "new_york": {"timezone": "America/New_York", "open": time(9, 30)},
}


def _local(ts, tz):
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).astimezone(tz)


def evaluate(candles, atr_value, rvol, session="london", min_rvol=1.30,
             max_extension_atr=0.80, active_minutes=180):
    """Evaluate one session ORB using completed 5-minute candle-open timestamps."""
    try:
        spec = SESSIONS[str(session).lower()]
        tz = ZoneInfo(spec["timezone"])
        ts = candles["time"]
        op, hi, lo, cl = (candles[k] for k in ("open", "high", "low", "close"))
        if len(ts) < 4 or not all(len(x) == len(ts) for x in (op, hi, lo, cl)):
            return {"state": "INVALID_DATA", "session": session}

        now = _local(ts[-1], tz)
        range_start = datetime.combine(now.date(), spec["open"], tzinfo=tz)
        range_end = range_start + timedelta(minutes=15)
        session_end = range_end + timedelta(minutes=max(15, int(active_minutes)))
        meta = {
            "session": str(session).lower(),
            "session_timezone": spec["timezone"],
            "session_open_local": range_start.isoformat(),
            "range_start_local": range_start.isoformat(),
            "range_end_local": range_end.isoformat(),
            "session_date": now.date().isoformat(),
            "session_start_utc_ms": int(range_start.astimezone(timezone.utc).timestamp() * 1000),
        }
        if now < range_end:
            return {"state": "BUILDING_RANGE", **meta}
        if now >= session_end:
            return {"state": "SESSION_CLOSED", **meta}

        expected = [range_start + timedelta(minutes=offset) for offset in (0, 5, 10)]
        bars = {
            _local(stamp, tz): i
            for i, stamp in enumerate(ts)
            if _local(stamp, tz).date() == now.date()
        }
        if not all(stamp in bars for stamp in expected):
            return {"state": "INCOMPLETE_RANGE", **meta}
        idx = [bars[stamp] for stamp in expected]
        rh = max(float(hi[i]) for i in idx)
        rl = min(float(lo[i]) for i in idx)
        atr = float(atr_value or 0)
        if atr <= 0 or rh <= rl:
            return {"state": "INVALID_VOLATILITY", **meta}

        width_atr = (rh - rl) / atr
        if width_atr < 0.10 or width_atr > 2.50:
            return {"state": "UNTRADEABLE_RANGE", "range_high": rh,
                    "range_low": rl, "range_width_atr": round(width_atr, 4), **meta}

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
            return {"state": "INSIDE_RANGE", "range_high": round(rh, 10),
                    "range_low": round(rl, 10), "range_width_atr": round(width_atr, 4), **meta}

        extension = abs(c - edge) / atr
        previous_close = float(cl[-2]) if len(cl) >= 2 else c
        retest = (
            direction == "LONG" and previous_close > rh and float(l) <= rh + 0.15 * atr
            or direction == "SHORT" and previous_close < rl and float(h) >= rl - 0.15 * atr
        )
        if not directional or body < 0.35:
            state = "WEAK_BREAKOUT"
        elif rvol is None or float(rvol) < float(min_rvol):
            state = "LOW_VOLUME_BREAKOUT"
        elif extension > float(max_extension_atr):
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
            **meta,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return {"state": "INVALID_DATA", "session": session}
