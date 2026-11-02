import numpy as np

from strategies.signals.step_channel import calculate_step_channel, pine_atr_series


def test_step_channel_initializes_from_sma50_and_wilder_atr():
    close = np.full(60, 100.0)
    high = close + 1.0
    low = close - 1.0
    result = calculate_step_channel(high, low, close)
    assert result.direction == "UNKNOWN"
    assert result.event is None
    assert result.mid == 100.0
    assert result.atr == 2.0
    assert result.inner_up == 105.0
    assert result.inner_down == 95.0
    assert result.outer_up == 108.0
    assert result.outer_down == 92.0


def test_step_channel_checks_previous_inner_level_then_updates_mid_and_atr():
    close = np.full(62, 100.0)
    high = close + 1.0
    low = close - 1.0
    close[60] = 106.0
    high[60] = 107.0
    low[60] = 99.0
    result = calculate_step_channel(high, low, close, np.arange(62) * 300_000)
    assert result.direction == "BULLISH"
    assert result.event == "UP"
    assert result.event_time_ms == 60 * 300_000
    assert result.mid == 106.0
    assert result.inner_up == 126.0


def test_step_channel_down_event_and_state_persists_until_opposite_break():
    close = np.full(64, 100.0)
    high = close + 1.0
    low = close - 1.0
    close[60] = 106.0
    high[60] = 107.0
    low[60] = 99.0
    close[61] = 106.0
    high[61] = 107.0
    low[61] = 105.0
    close[62] = 80.0
    high[62] = 107.0
    low[62] = 79.0
    close[63] = 80.0
    high[63] = 81.0
    low[63] = 79.0
    result = calculate_step_channel(high, low, close)
    assert result.direction == "BEARISH"
    assert result.event == "DOWN"
    assert result.mid == 80.0


def test_pine_atr_uses_first_true_range_in_sma_seed():
    high = np.array([11.0, 12.0, 13.0, 14.0])
    low = np.array([9.0, 10.0, 11.0, 12.0])
    close = np.array([10.0, 11.0, 12.0, 13.0])
    got = pine_atr_series(high, low, close, period=3)
    # TR = [2, 2, 2, 2], first Wilder value appears at index 2.
    assert np.isnan(got[1])
    assert got[2] == 2.0
    assert got[3] == 2.0
