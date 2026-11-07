from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from strategies.momentum.orb_detector import evaluate


def ts(day, tz_name, hour, minute):
    local = datetime(2026, 6, day, hour, minute, tzinfo=ZoneInfo(tz_name))
    return int(local.astimezone(timezone.utc).timestamp() * 1000)


def base_candles(session, last_hour, last_minute, last_high=102.0, last_low=100.8, last_close=101.8):
    spec = {"london": ("Europe/London", 8, 0), "new_york": ("America/New_York", 9, 30)}
    tz_name, start_hour, start_minute = spec[session]
    base = datetime(2026, 6, 2, start_hour, start_minute, tzinfo=ZoneInfo(tz_name))
    starts = [base, base.replace(minute=base.minute + 5), base.replace(minute=base.minute + 10)]
    last = datetime(2026, 6, 2, last_hour, last_minute, tzinfo=ZoneInfo(tz_name))
    times = [int(x.astimezone(timezone.utc).timestamp() * 1000) for x in starts + [last]]
    return {
        "time": np.asarray(times, dtype=np.int64),
        "open": np.asarray([100.0, 100.2, 100.1, 101.0]),
        "high": np.asarray([101.0, 101.2, 101.1, last_high]),
        "low": np.asarray([99.8, 100.0, 99.9, last_low]),
        "close": np.asarray([100.5, 100.8, 100.7, last_close]),
    }


def test_london_orb_uses_bst_local_open_and_detects_breakout():
    result = evaluate(base_candles("london", 8, 15), 1.5, 1.8, session="london")
    assert result["state"] == "ORB_BREAKOUT"
    assert result["direction"] == "LONG"
    assert result["session_timezone"] == "Europe/London"
    assert result["range_high"] == 101.2


def test_new_york_orb_uses_edt_local_open_and_detects_breakout():
    result = evaluate(base_candles("new_york", 9, 45), 1.5, 1.8, session="new_york")
    assert result["state"] == "ORB_BREAKOUT"
    assert result["direction"] == "LONG"
    assert result["session_timezone"] == "America/New_York"


def test_orb_waits_until_full_range_is_closed():
    result = evaluate(base_candles("london", 8, 10, 101.0, 100.0, 100.8),
                      1.0, 2.0, session="london")
    assert result["state"] == "BUILDING_RANGE"


def test_orb_rejects_late_extended_breakout():
    result = evaluate(base_candles("london", 8, 15, 105.0, 104.0, 104.8),
                      1.0, 2.0, session="london")
    assert result["state"] == "EXTENDED_BREAKOUT"


def test_orb_requires_volume_expansion():
    result = evaluate(base_candles("new_york", 9, 45), 1.5, 1.0, session="new_york")
    assert result["state"] == "LOW_VOLUME_BREAKOUT"


def test_orb_detects_retest_after_initial_breakout():
    candles = base_candles("london", 8, 15)
    candles["time"] = np.append(candles["time"], ts(2, "Europe/London", 8, 20))
    candles["open"] = np.append(candles["open"], 101.3)
    candles["high"] = np.append(candles["high"], 102.0)
    candles["low"] = np.append(candles["low"], 101.1)
    candles["close"] = np.append(candles["close"], 101.7)
    result = evaluate(candles, 1.5, 1.8, session="london")
    assert result["state"] == "ORB_RETEST"


def test_orb_rejects_incomplete_opening_range():
    candles = base_candles("new_york", 9, 45)
    for key in ("time", "open", "high", "low", "close"):
        candles[key] = np.delete(candles[key], 1)
    result = evaluate(candles, 1.5, 1.8, session="new_york")
    assert result["state"] == "INCOMPLETE_RANGE"


def test_orb_session_expires_after_active_window():
    result = evaluate(base_candles("new_york", 13, 0), 1.5, 2.0,
                      session="new_york", active_minutes=180)
    assert result["state"] == "SESSION_CLOSED"


def test_dst_conversion_is_automatic_for_winter_dates():
    london = datetime(2026, 1, 5, 8, 0, tzinfo=ZoneInfo("Europe/London"))
    new_york = datetime(2026, 1, 5, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert london.astimezone(timezone.utc).hour == 8
    assert new_york.astimezone(timezone.utc).hour == 14



def test_orb_exposes_adx_di_confirmation_without_vetoing_early_breakout():
    result = evaluate(
        base_candles("london", 8, 15), 1.5, 1.8, session="london",
        adx_value=24.0, plus_di=28.0, minus_di=14.0, adx_rising=True,
    )
    assert result["state"] == "ORB_BREAKOUT"
    assert result["trend_confirmed"] is True
    assert result["di_aligned"] is True
    assert result["adx_rising"] is True


def test_orb_does_not_mark_trend_confirmed_when_di_opposes_direction():
    result = evaluate(
        base_candles("london", 8, 15), 1.5, 1.8, session="london",
        adx_value=26.0, plus_di=12.0, minus_di=29.0,
    )
    assert result["state"] == "ORB_BREAKOUT"
    assert result["trend_confirmed"] is False
    assert result["di_aligned"] is False


def test_orb_detects_failed_breakout_when_close_returns_inside_range():
    candles = base_candles("london", 8, 15)
    candles["time"] = np.append(candles["time"], ts(2, "Europe/London", 8, 20))
    candles["open"] = np.append(candles["open"], 101.5)
    candles["high"] = np.append(candles["high"], 101.7)
    candles["low"] = np.append(candles["low"], 100.8)
    candles["close"] = np.append(candles["close"], 101.0)
    result = evaluate(candles, 1.5, 1.8, session="london")
    assert result["state"] == "ORB_FAILED_BREAKOUT"
    assert result["direction"] == "LONG"
    assert result["failed_breakout"] is True


def test_orb_rejects_breakout_with_large_opposing_wick():
    candles = base_candles("london", 8, 15, last_high=103.0, last_low=100.8, last_close=101.8)
    result = evaluate(candles, 1.5, 1.8, session="london")
    assert result["state"] == "WEAK_BREAKOUT"
