"""Regression tests for the MACD-free Normal Engine strategy stack."""


def test_normal_trend_strategy_does_not_require_macd_for_long_or_short():
    from strategies.normal.strategy_manager import detect_strategy

    long_indicators = {
        "ema9": 103.0,
        "ema21": 102.0,
        "ema50": 101.0,
        "adx": 25.0,
        "close": 105.0,
        "vwap": 104.0,
        # Normal Engine intentionally does not calculate MACD.
        "macd_hist": None,
    }
    short_indicators = {
        "ema9": 97.0,
        "ema21": 98.0,
        "ema50": 99.0,
        "adx": 25.0,
        "close": 95.0,
        "vwap": 96.0,
        "macd_hist": None,
    }

    assert detect_strategy(long_indicators, "LONG") == "TREND_CONTINUATION"
    assert detect_strategy(short_indicators, "SHORT") == "TREND_CONTINUATION"


def test_normal_trend_strategy_ignores_opposing_macd_value():
    from strategies.normal.trend import evaluate

    indicators = {
        "ema9": 103.0,
        "ema21": 102.0,
        "ema50": 101.0,
        "adx": 25.0,
        "close": 105.0,
        "vwap": 104.0,
        "macd_hist": -999.0,
    }

    assert evaluate(indicators, "LONG") is True
