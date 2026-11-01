from datetime import datetime, timezone, timedelta
import numpy as np
from core.signal_manager import SignalManager
from core.risk_calculator import calculate_risk
from exchange.candle_builder import CandleBuffer


def test_signal_can_reenter_after_terminal_state():
    m = SignalManager(ttl_hours=1)
    s = {"pair":"B-BTC_USDT", "engine":"normal", "direction":"LONG", "score":8}
    assert m.add(s)
    assert not m.add(s)
    assert m.mark_sl(s["pair"], s["engine"], s["direction"])
    assert m.add(s)


def test_signal_expires_and_can_reenter():
    m = SignalManager(ttl_hours=1)
    s = {"pair":"B-ETH_USDT", "engine":"normal", "direction":"SHORT", "score":8}
    assert m.add(s)
    m._signals[m._key(s['pair'],s['engine'],s['direction'])]['expires_at'] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    assert m.add(s)


def test_candle_buffer_deduplicates_and_ignores_older():
    b=CandleBuffer(10)
    c={"time":1000,"open":1,"high":2,"low":.5,"close":1.5,"volume":10}
    assert b.update(c)
    assert not b.update({**c,"close":1.6})
    assert len(b)==1 and b.last_close()==1.6
    assert not b.update({**c,"time":900})


def test_structure_risk_has_positive_rr():
    r=calculate_risk(100,2,"LONG",swing_low=98.5)
    assert r and r.stop_loss < 100 and r.tp1 > 100
    assert r.rr1 >= 1.0 and r.rr2 > r.rr1 and r.rr3 > r.rr2


def test_short_structure_risk():
    r=calculate_risk(100,2,"SHORT",swing_high=101.5)
    assert r and r.stop_loss > 100 and r.tp1 < 100


def test_rest_client_filters_open_and_duplicate_candles():
    import asyncio
    import time
    from exchange.coindcx_rest import CoinDCXRest

    async def run():
        client = CoinDCXRest()
        now = int(time.time() * 1000)
        closed_open = (now // 300000) * 300000 - 300000
        open_open = (now // 300000) * 300000
        rows = [
            {"time": closed_open // 1000, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10},
            {"time": closed_open, "open": 100, "high": 103, "low": 99, "close": 102, "volume": 11},
            {"time": open_open, "open": 102, "high": 104, "low": 101, "close": 103, "volume": 12},
        ]
        async def fake_get(url, params=None): return rows
        client._get = fake_get
        got = await client.get_candles("B-BTC_USDT", "5m", 10)
        assert len(got) == 1
        assert got[0]["time"] == closed_open
        assert got[0]["close"] == 102
    asyncio.run(run())
