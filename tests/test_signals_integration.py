def test_signals_module_has_independent_state_and_uses_union_of_existing_universes():
    from core.engine import SignalEngine

    class FakeSymbols:
        normal_symbols = ["B-BTC_USDT", "B-ETH_USDT"]
        momentum_symbols = ["B-ETH_USDT", "B-SOL_USDT"]

    engine = SignalEngine(rest=object(), symbols=FakeSymbols(), candles=object())
    assert engine.signals["signals"] is not engine.signals["normal"]
    assert engine.signals["signals"] is not engine.signals["momentum"]
    assert engine.cooldowns["signals"] is not engine.cooldowns["normal"]
    assert engine.watchlists["signals"] is not engine.watchlists["momentum"]
    assert engine._last_processed["signals"] is not engine._last_processed["normal"]
    assert list(dict.fromkeys(FakeSymbols.normal_symbols + FakeSymbols.momentum_symbols)) == [
        "B-BTC_USDT", "B-ETH_USDT", "B-SOL_USDT"
    ]


def test_signals_module_dedup_namespace_is_independent_from_legacy_engines(tmp_path, monkeypatch):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from database import db as database_db
    from database.db import Base
    from database import models, step_channel_model  # noqa: F401
    from database.repositories import claim_signal_once

    async def run():
        test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'signals_dedup.db'}")
        old_engine, old_factory = database_db.engine, database_db.SessionLocal
        database_db.engine = test_engine
        database_db.SessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            base = {"pair": "B-BTC_USDT", "direction": "LONG", "score": 10.0}
            assert await claim_signal_once({**base, "engine": "normal"}, 1_800_000)
            assert await claim_signal_once({**base, "engine": "signals"}, 1_800_000)
            assert not await claim_signal_once({**base, "engine": "signals"}, 1_800_000)
        finally:
            database_db.engine, database_db.SessionLocal = old_engine, old_factory
            await test_engine.dispose()

    asyncio.run(run())


def test_step_channel_snapshot_round_trips_through_database(tmp_path):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from database import db as database_db
    from database.db import Base
    from database import models, step_channel_model  # noqa: F401
    from database.repositories import save_step_channel_state, load_step_channel_states

    async def run():
        test_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'step_state.db'}")
        old_engine, old_factory = database_db.engine, database_db.SessionLocal
        database_db.engine = test_engine
        database_db.SessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            snapshot = {"direction": "BULLISH", "event": "UP", "event_time_ms": 15000000,
                        "candle_time_ms": 18000000, "mid": 100.5, "atr": 2.1,
                        "step_atr": 1.9, "in_plus": 105.25}
            assert await save_step_channel_state("B-BTC_USDT", snapshot)
            restored = await load_step_channel_states()
            assert restored["B-BTC_USDT"] == snapshot
        finally:
            database_db.engine, database_db.SessionLocal = old_engine, old_factory
            await test_engine.dispose()

    asyncio.run(run())
