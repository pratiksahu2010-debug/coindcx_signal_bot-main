"""Persistence regression tests for pending retests and signal outcomes."""
import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import db
from database.db import Base
from database import repositories


def test_pending_retest_round_trip_and_expiry(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False))
        pending = {
            "signal": {"pair": "B-BTC_USDT", "engine": "normal", "direction": "LONG"},
            "expires_at": datetime.now(timezone.utc).timestamp() + 300,
            "entry": 100.0, "zone_low": 99.0, "zone_high": 101.0,
            "stop_loss": 98.0, "candle_ts": 1_800_000_000_000,
        }
        assert await repositories.save_pending_retest("btc:normal:long", pending)
        restored = await repositories.load_pending_retests()
        assert len(restored) == 1
        assert restored[0][0] == "btc:normal:long"
        assert restored[0][1]["signal"]["pair"] == "B-BTC_USDT"
        assert await repositories.delete_pending_retest("btc:normal:long")
        assert await repositories.load_pending_retests() == []
        await engine.dispose()
    asyncio.run(run())


def test_signal_outcome_report_groups_by_engine_and_strategy(monkeypatch):
    async def run():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        monkeypatch.setattr(db, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False))
        signal = {"pair": "B-BTC_USDT", "engine": "momentum", "strategy": "EXPLOSION",
                  "direction": "LONG", "price": 100.0, "live_price": 100.0,
                  "candle_time_ms": 1_800_000_000_000}
        assert await repositories.record_signal_outcome(signal, 102.0, "TP1")
        assert await repositories.record_signal_outcome(signal, 99.0, "TP1")
        report = await repositories.signal_performance_report(days=30)
        row = report["groups"]["momentum:EXPLOSION"]
        assert row["count"] == 1
        assert row["avg_mfe_pct"] == 2.0
        assert row["avg_mae_pct"] == -1.0
        await engine.dispose()
    asyncio.run(run())
