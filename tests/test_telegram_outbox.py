"""Durable Telegram outbox repository tests."""
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from database import db as database_db
from database.db import Base
from database import models  # noqa: F401
from database.repositories import (
    enqueue_signal_deliveries,
    claim_due_signal_deliveries,
    complete_signal_delivery,
    retry_signal_delivery,
    expire_stale_signal_deliveries,
)


def test_outbox_is_per_recipient_claimable_and_idempotent(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox.db'}")
        old_engine, old_factory = database_db.engine, database_db.SessionLocal
        database_db.engine = engine
        database_db.SessionLocal = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        monkeypatch.setattr("config.settings.max_signal_age_seconds", 120)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            signal = {
                "pair": "B-BTC_USDT", "engine": "normal", "direction": "LONG",
                "candle_time_ms": 1_800_000_000_000, "signal_age_seconds": 10,
                "price": 65000,
            }
            assert await enqueue_signal_deliveries(signal, [111, 222, 111]) == 2
            # Re-enqueueing the same candle/recipient is idempotent.
            assert await enqueue_signal_deliveries(signal, [111, 222]) == 0
            batch = await claim_due_signal_deliveries(limit=10)
            assert len(batch) == 2
            assert {item["chat_id"] for item in batch} == {111, 222}
            assert all(item["attempts"] == 1 for item in batch)
            assert await complete_signal_delivery(batch[0]["delivery_key"])
            assert await retry_signal_delivery(
                batch[1]["delivery_key"], "temporary network error", batch[1]["attempts"]
            )
            # Retry is scheduled in the future, so it is not immediately claimable.
            assert await claim_due_signal_deliveries(limit=10) == []
        finally:
            database_db.engine, database_db.SessionLocal = old_engine, old_factory
            await engine.dispose()

    asyncio.run(run())


def test_outbox_expires_alerts_after_entry_validity_window(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox_expiry.db'}")
        old_engine, old_factory = database_db.engine, database_db.SessionLocal
        database_db.engine = engine
        database_db.SessionLocal = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        monkeypatch.setattr("config.settings.max_signal_age_seconds", 120)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            signal = {
                "pair": "B-ETH_USDT", "engine": "momentum", "direction": "SHORT",
                "candle_time_ms": 1_800_000_000_000, "signal_age_seconds": 119,
                "price": 3000,
            }
            assert await enqueue_signal_deliveries(signal, [333]) == 1
            # Expiry timestamp is one second ahead at enqueue; move it into the past.
            from sqlalchemy import select
            from database.models import SignalDeliveryRecord
            async with database_db.SessionLocal() as session:
                row = (await session.execute(select(SignalDeliveryRecord))).scalar_one()
                import json
                payload = json.loads(row.payload)
                payload["expires_at"] = (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat()
                row.payload = json.dumps(payload)
                await session.commit()
            assert await expire_stale_signal_deliveries() == 1
            assert await claim_due_signal_deliveries(limit=10) == []
        finally:
            database_db.engine, database_db.SessionLocal = old_engine, old_factory
            await engine.dispose()

    asyncio.run(run())


def test_retest_outbox_gets_fresh_delivery_window_even_when_origin_candle_is_old(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox_retest.db'}")
        old_engine, old_factory = database_db.engine, database_db.SessionLocal
        database_db.engine = engine
        database_db.SessionLocal = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        monkeypatch.setattr("config.settings.max_signal_age_seconds", 120)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            signal = {
                "pair": "B-ETH_USDT", "engine": "normal", "direction": "LONG",
                "candle_time_ms": 1_800_000_000_000, "signal_age_seconds": 300,
                "entry_type": "RETEST_CONFIRMED", "price": 3000,
            }
            assert await enqueue_signal_deliveries(signal, [444]) == 1
            from sqlalchemy import select
            from database.models import SignalDeliveryRecord
            async with database_db.SessionLocal() as session:
                row = (await session.execute(select(SignalDeliveryRecord))).scalar_one()
                import json
                expiry = datetime.fromisoformat(json.loads(row.payload)["expires_at"])
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                assert 115 <= (expiry - now).total_seconds() <= 120
        finally:
            database_db.engine, database_db.SessionLocal = old_engine, old_factory
            await engine.dispose()

    asyncio.run(run())


def test_outbox_rejects_signal_that_has_exhausted_entry_validity(tmp_path, monkeypatch):
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox_stale.db'}")
        old_engine, old_factory = database_db.engine, database_db.SessionLocal
        database_db.engine = engine
        database_db.SessionLocal = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        monkeypatch.setattr("config.settings.max_signal_age_seconds", 120)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            signal = {
                "pair": "B-ETH_USDT", "engine": "normal", "direction": "LONG",
                "candle_time_ms": 1_800_000_000_000, "signal_age_seconds": 120,
                "price": 3000,
            }
            assert await enqueue_signal_deliveries(signal, [555]) == 0
        finally:
            database_db.engine, database_db.SessionLocal = old_engine, old_factory
            await engine.dispose()

    asyncio.run(run())



def test_outbox_queue_stats_reports_durable_status_counts_and_oldest_age(tmp_path):
    from datetime import datetime, timedelta, timezone
    from database.models import SignalDeliveryRecord
    from database.repositories import signal_delivery_queue_stats

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'outbox_stats.db'}")
        old_engine, old_factory = database_db.engine, database_db.SessionLocal
        database_db.engine = engine
        database_db.SessionLocal = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            async with database_db.SessionLocal() as session:
                session.add_all([
                    SignalDeliveryRecord(delivery_key="pending-1", payload="{}", status="pending", attempts=0, next_attempt_at=now, updated_at=now-timedelta(seconds=45)),
                    SignalDeliveryRecord(delivery_key="retry-1", payload="{}", status="retry", attempts=1, next_attempt_at=now, updated_at=now-timedelta(seconds=20)),
                    SignalDeliveryRecord(delivery_key="sent-1", payload="{}", status="delivered", attempts=1, next_attempt_at=now, updated_at=now),
                    SignalDeliveryRecord(delivery_key="failed-1", payload="{}", status="failed", attempts=8, next_attempt_at=now, updated_at=now),
                ])
                await session.commit()
            stats = await signal_delivery_queue_stats()
            assert stats["available"] is True
            assert stats["total"] == 4
            assert stats["counts"]["pending"] == 1
            assert stats["counts"]["retry"] == 1
            assert stats["counts"]["delivered"] == 1
            assert stats["counts"]["failed"] == 1
            assert 44 <= stats["oldest_pending_age_seconds"] <= 47
        finally:
            database_db.engine, database_db.SessionLocal = old_engine, old_factory
            await engine.dispose()

    asyncio.run(run())
