from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import numpy as np

from strategies.momentum.orb_detector import evaluate

IST = ZoneInfo("Asia/Kolkata")


def ts(hour, minute):
    return int(datetime(2026, 11, 3, hour, minute, tzinfo=IST).astimezone(timezone.utc).timestamp() * 1000)


def base_candles(last_open, last_high, last_low, last_close):
    times = [ts(9, 15), ts(9, 20), ts(9, 25), ts(*last_open)]
    return {
        "time": np.asarray(times, dtype=np.int64),
        "open": np.asarray([100.0, 100.2, 100.1, 101.0]),
        "high": np.asarray([101.0, 101.2, 101.1, last_high]),
        "low": np.asarray([99.8, 100.0, 99.9, last_low]),
        "close": np.asarray([100.5, 100.8, 100.7, last_close]),
    }


def test_orb_waits_until_full_15_minute_range_is_closed():
    candles = base_candles((9, 25), 101.0, 100.0, 100.8)
    result = evaluate(candles, 1.0, 2.0)
    assert result["state"] == "BUILDING_RANGE"


def test_orb_detects_long_breakout_inside_extension_and_volume_limits():
    candles = base_candles((9, 30), 102.0, 100.8, 101.8)
    result = evaluate(candles, 1.5, 1.8)
    assert result["state"] == "ORB_BREAKOUT"
    assert result["direction"] == "LONG"
    assert result["range_high"] == 101.2


def test_orb_rejects_late_extended_breakout():
    candles = base_candles((9, 30), 105.0, 104.0, 104.8)
    result = evaluate(candles, 1.0, 2.0)
    assert result["state"] == "EXTENDED_BREAKOUT"


def test_orb_requires_volume_expansion():
    candles = base_candles((9, 30), 102.0, 100.8, 101.8)
    result = evaluate(candles, 1.5, 1.0)
    assert result["state"] == "LOW_VOLUME_BREAKOUT"


def test_orb_session_expires_after_india_cash_session():
    candles = base_candles((15, 30), 102.0, 100.8, 101.8)
    result = evaluate(candles, 1.5, 2.0)
    assert result["state"] == "SESSION_CLOSED"

def test_orb_detects_retest_after_initial_breakout():
    candles = base_candles((9, 30), 102.0, 100.8, 101.8)
    candles["time"] = np.append(candles["time"], ts(9, 35))
    candles["open"] = np.append(candles["open"], 101.3)
    candles["high"] = np.append(candles["high"], 102.0)
    candles["low"] = np.append(candles["low"], 101.1)
    candles["close"] = np.append(candles["close"], 101.7)
    result = evaluate(candles, 1.5, 1.8)
    assert result["state"] == "ORB_RETEST"
    assert result["direction"] == "LONG"


def test_orb_rejects_unusually_wide_opening_range():
    candles = base_candles((9, 30), 104.0, 100.8, 103.8)
    result = evaluate(candles, 0.5, 2.0)
    assert result["state"] == "UNTRADEABLE_RANGE"
