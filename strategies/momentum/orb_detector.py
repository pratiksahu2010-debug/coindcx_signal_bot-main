"""DST-aware London and New York opening-range breakout detector."""
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

SESSIONS = {
    "london": {"timezone": "Europe/London", "open": time(8, 0)},
    "new_york": {"timezone": "America/New_York", "open": time(9, 30)},
}


def _local(ts, tz):
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).astimezone(tz)


def _finite(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _result(state, meta, **values):
    return {"state": state, **values, **meta}


def evaluate(
    candles, atr_value, rvol, session="london", min_rvol=1.30,
    max_extension_atr=0.80, active_minutes=180, adx_value=None,
    plus_di=None, minus_di=None, adx_rising=False, adx_min=18.0,
):
    """Evaluate a session's 15-minute range using completed 5-minute candles.

    ADX/DI is a quality confirmation, not an absolute veto, because early
    breakouts can precede ADX expansion. A failed breakout is explicitly marked.
    """
    try:
        session_name = str(session).lower()
        spec = SESSIONS[session_name]
        tz = ZoneInfo(spec["timezone"])
        ts = list(candles["time"])
        op, hi, lo, cl = (list(candles[k]) for k in ("open", "high", "low", "close"))
        if not ts or not all(len(values) == len(ts) for values in (op, hi, lo, cl)):
            return {"state": "INVALID_DATA", "session": session_name}
        if any(_finite(value) is None for values in (op, hi, lo, cl) for value in values):
            return {"state": "INVALID_DATA", "session": session_name}
        stamps = [int(value) for value in ts]
        if any(stamps[i] >= stamps[i + 1] for i in range(len(stamps) - 1)):
            return {"state": "INVALID_DATA", "session": session_name}

        now = _local(stamps[-1], tz)
        range_start = datetime.combine(now.date(), spec["open"], tzinfo=tz)
        range_end = range_start + timedelta(minutes=15)
        session_end = range_end + timedelta(minutes=max(15, int(active_minutes)))
        meta = {
            "session": session_name,
            "session_timezone": spec["timezone"],
            "session_open_local": range_start.isoformat(),
            "range_start_local": range_start.isoformat(),
            "range_end_local": range_end.isoformat(),
            "session_date": now.date().isoformat(),
            "session_start_utc_ms": int(range_start.astimezone(timezone.utc).timestamp() * 1000),
        }
        if now < range_end:
            return _result("BUILDING_RANGE", meta)
        if now >= session_end:
            return _result("SESSION_CLOSED", meta)

        expected = [range_start + timedelta(minutes=offset) for offset in (0, 5, 10)]
        bars = {
            _local(stamp, tz): i for i, stamp in enumerate(stamps)
            if _local(stamp, tz).date() == now.date()
        }
        if not all(stamp in bars for stamp in expected):
            return _result("INCOMPLETE_RANGE", meta)

        range_indices = [bars[stamp] for stamp in expected]
        range_high = max(float(hi[i]) for i in range_indices)
        range_low = min(float(lo[i]) for i in range_indices)
        atr = _finite(atr_value) or 0.0
        if atr <= 0 or range_high <= range_low:
            return _result("INVALID_VOLATILITY", meta)
        width_atr = (range_high - range_low) / atr
        if width_atr < 0.10 or width_atr > 2.50:
            return _result(
                "UNTRADEABLE_RANGE", meta, range_high=range_high,
                range_low=range_low, range_width_atr=round(width_atr, 4),
            )

        o, h, l, c = map(float, (op[-1], hi[-1], lo[-1], cl[-1]))
        span = max(h - l, 1e-12)
        body_ratio = abs(c - o) / span
        close_location = (c - l) / span
        upper_wick = (h - max(o, c)) / span
        lower_wick = (min(o, c) - l) / span
        volume = _finite(rvol)
        adx = _finite(adx_value)
        pdi, mdi = _finite(plus_di), _finite(minus_di)

        direction = "LONG" if c > range_high else "SHORT" if c < range_low else None
        previous_close = float(cl[-2]) if len(cl) >= 2 else c
        previous_direction = (
            "LONG" if previous_close > range_high
            else "SHORT" if previous_close < range_low else None
        )
        if direction is None:
            if previous_direction is not None:
                return _result(
                    "ORB_FAILED_BREAKOUT", meta, direction=previous_direction,
                    range_high=round(range_high, 10), range_low=round(range_low, 10),
                    range_width_atr=round(width_atr, 4), failed_breakout=True,
                )
            return _result(
                "INSIDE_RANGE", meta, range_high=round(range_high, 10),
                range_low=round(range_low, 10), range_width_atr=round(width_atr, 4),
            )

        edge = range_high if direction == "LONG" else range_low
        extension = abs(c - edge) / atr
        retest = (
            direction == "LONG" and previous_close > range_high
            and l <= range_high + 0.15 * atr and c >= range_high
        ) or (
            direction == "SHORT" and previous_close < range_low
            and h >= range_low - 0.15 * atr and c <= range_low
        )
        directional_close = close_location >= 0.62 if direction == "LONG" else close_location <= 0.38
        opposing_wick = upper_wick if direction == "LONG" else lower_wick
        min_body = 0.40 if retest else 0.45
        wick_quality = opposing_wick <= 0.35
        di_aligned = None if pdi is None or mdi is None else (
            pdi > mdi if direction == "LONG" else mdi > pdi
        )
        trend_confirmed = bool(adx is not None and adx >= float(adx_min) and di_aligned is True)

        if not directional_close or body_ratio < min_body or not wick_quality:
            state = "WEAK_BREAKOUT"
        elif volume is None or volume < float(min_rvol):
            state = "LOW_VOLUME_BREAKOUT"
        elif extension > float(max_extension_atr):
            state = "EXTENDED_BREAKOUT"
        else:
            state = "ORB_RETEST" if retest else "ORB_BREAKOUT"

        return _result(
            state, meta, direction=direction,
            range_high=round(range_high, 10), range_low=round(range_low, 10),
            breakout_level=round(edge, 10), range_width_atr=round(width_atr, 4),
            breakout_extension_atr=round(extension, 4),
            body_ratio=round(body_ratio, 4), close_location=round(close_location, 4),
            upper_wick_ratio=round(upper_wick, 4), lower_wick_ratio=round(lower_wick, 4),
            opposing_wick_ratio=round(opposing_wick, 4), wick_quality=bool(wick_quality),
            retest_confirmed=bool(retest),
            rvol=round(volume, 4) if volume is not None else None,
            adx=round(adx, 4) if adx is not None else None,
            di_aligned=di_aligned, adx_rising=bool(adx_rising),
            trend_confirmed=trend_confirmed, failed_breakout=False,
        )
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return {"state": "INVALID_DATA", "session": session}
