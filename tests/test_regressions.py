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
        closed_open = (now // 300000) * 300000 - 600000
        open_open = (now // 300000) * 300000
        rows = [
            {"time": closed_open + 1000, "open": 100, "high": 104, "low": 99, "close": 103, "volume": 12},
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



def test_macd_hist_history_returns_scalar_histogram_series():
    from core.engine import _macd_hist_history
    values = np.linspace(100.0, 125.0, 90)
    history = _macd_hist_history(values, points=4)
    assert len(history) == 4
    assert all(np.isfinite(value) for value in history)


def test_normal_and_momentum_candle_cursors_are_independent():
    from core.engine import SignalEngine
    engine = SignalEngine(rest=object(), symbols=object(), candles=object())
    engine._last_processed["normal"]["B-BTC_USDT"] = 123
    assert engine._last_processed["momentum"].get("B-BTC_USDT") is None


def test_dynamic_symbol_universe_uses_ticker_liquidity_and_active_status():
    import asyncio
    from exchange.symbol_manager import SymbolManager

    class FakeRest:
        async def get_market_details(self):
            return [
                {"pair": "B-BTC_USDT", "base_currency_short_name": "USDT", "target_currency_short_name": "BTC", "status": "active"},
                {"pair": "B-ETH_USDT", "base_currency_short_name": "USDT", "target_currency_short_name": "ETH", "status": "active"},
                {"pair": "B-LOW_USDT", "base_currency_short_name": "USDT", "target_currency_short_name": "LOW", "status": "active"},
                {"pair": "B-OFF_USDT", "base_currency_short_name": "USDT", "target_currency_short_name": "OFF", "status": "inactive"},
            ]

        async def get_ticker(self):
            return [
                {"market": "B-BTC_USDT", "volume": "10", "last_price": "100"},
                {"market": "B-ETH_USDT", "volume": "20", "last_price": "10"},
                {"market": "B-LOW_USDT", "volume": "1", "last_price": "1"},
            ]

    async def run():
        manager = SymbolManager(FakeRest())
        await manager.load_and_validate()
        assert manager.normal_symbols[:3] == ["B-BTC_USDT", "B-ETH_USDT", "B-LOW_USDT"]
        assert manager.momentum_symbols[:3] == ["B-BTC_USDT", "B-ETH_USDT", "B-LOW_USDT"]
        assert "B-OFF_USDT" not in manager.valid_pairs

    asyncio.run(run())


def test_persistent_signal_claim_blocks_restart_duplicate(tmp_path, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from database import db as database_db
    from database.db import Base
    from database import models  # noqa: F401
    from database.repositories import claim_signal_once

    async def run():
        test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'dedup.db'}")
        old_engine, old_factory = database_db.engine, database_db.SessionLocal
        database_db.engine = test_engine
        database_db.SessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            signal = {"pair": "B-BTC_USDT", "engine": "normal", "direction": "LONG", "score": 8.0}
            assert await claim_signal_once(signal, 1_800_000, ttl_hours=4)
            assert not await claim_signal_once(signal, 1_800_000, ttl_hours=4)
        finally:
            database_db.engine, database_db.SessionLocal = old_engine, old_factory
            await test_engine.dispose()

    asyncio.run(run())
