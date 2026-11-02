"""Simple repository helpers (optional persistence)."""
from typing import Optional
from utils.logger import logger


async def save_signal(signal: dict) -> bool:
    """Persist an active delivered signal for restart recovery and outcome tracking."""
    import json
    from database import db as database_db
    from database.models import SignalRecord

    factory = database_db.SessionLocal
    if factory is None:
        logger.error("Cannot persist signal: database session unavailable")
        return False
    async with factory() as session:
        try:
            rec = SignalRecord(
                pair=signal["pair"],
                engine=signal["engine"],
                direction=signal["direction"],
                score=signal.get("score", 0),
                grade=signal.get("grade", ""),
                price=float(signal.get("price", 0)),
                status=signal.get("status", "active"),
                payload=json.dumps(signal, default=str),
            )
            session.add(rec)
            await session.commit()
            return True
        except Exception as exc:
            await session.rollback()
            logger.error(f"Signal persistence failed: {exc}")
            return False


async def load_active_signals(max_age_hours: int = 4) -> list:
    """Load unexpired active signal payloads for in-memory lifecycle restoration."""
    import json
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, update
    from database import db as database_db
    from database.models import SignalRecord

    factory = database_db.SessionLocal
    if factory is None:
        return []
    cutoff = datetime.utcnow() - timedelta(hours=max(1, int(max_age_hours)))
    async with factory() as session:
        try:
            # Mark orphaned active rows older than the configured signal TTL.
            await session.execute(
                update(SignalRecord)
                .where(SignalRecord.status == "active", SignalRecord.created_at < cutoff)
                .values(status="expired")
            )
            result = await session.execute(
                select(SignalRecord)
                .where(SignalRecord.status == "active", SignalRecord.created_at >= cutoff)
                .order_by(SignalRecord.created_at.desc())
            )
            records = result.scalars().all()
            signals = []
            now_utc = datetime.now(timezone.utc)
            for record in records:
                try:
                    signal = json.loads(record.payload or "{}")
                    expires_at = datetime.fromisoformat(
                        str(signal.get("expires_at", "")).replace("Z", "+00:00")
                    )
                    if expires_at <= now_utc:
                        record.status = "expired"
                        continue
                    if signal.get("pair") and signal.get("engine") and signal.get("direction"):
                        signals.append(signal)
                    else:
                        record.status = "invalid"
                except (TypeError, ValueError):
                    record.status = "invalid"
                    logger.warning(f"Skipping malformed persisted signal row id={record.id}")
            await session.commit()
            return signals
        except Exception as exc:
            await session.rollback()
            logger.error(f"Active signal restore query failed: {exc}")
            return []


async def update_signal_record(signal: dict, status: str) -> bool:
    """Persist TP/SL/expiry lifecycle changes for matching active signal records."""
    import json
    from sqlalchemy import select
    from database import db as database_db
    from database.models import SignalRecord

    factory = database_db.SessionLocal
    if factory is None:
        return False
    async with factory() as session:
        try:
            result = await session.execute(
                select(SignalRecord).where(
                    SignalRecord.pair == signal["pair"],
                    SignalRecord.engine == signal["engine"],
                    SignalRecord.direction == signal["direction"],
                    SignalRecord.status == "active",
                )
            )
            records = result.scalars().all()
            for record in records:
                record.status = status
                record.payload = json.dumps(signal, default=str)
            await session.commit()
            return bool(records)
        except Exception as exc:
            await session.rollback()
            logger.error(f"Signal lifecycle persistence failed: {exc}")
            return False


async def claim_signal_once(signal: dict, candle_time_ms: int, ttl_hours: int = 4) -> bool:
    """Claim one alert per pair/engine/direction across process restarts."""
    import json
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, delete
    from sqlalchemy.exc import IntegrityError
    from database import db as database_db
    from database.models import SignalDedupRecord

    factory = database_db.SessionLocal
    if factory is None:
        logger.error("Persistent signal deduplication unavailable; suppressing alert")
        return False

    # One alert per pair/direction/entry candle across BOTH engines.
    # Including candle time avoids a four-hour lock suppressing legitimate
    # signals on later candles, while excluding engine prevents cross-engine
    # duplicates for the same market move.
    base_key = f"{signal['pair']}:{signal['direction']}:{int(candle_time_ms)}"
    key = f"signals:{base_key}" if signal.get("engine") == "signals" else base_key
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires = now + timedelta(hours=max(1, int(ttl_hours)))
    async with factory() as session:
        try:
            # Expired candle claims no longer protect against duplicates; prune
            # them opportunistically so the persistent dedup table stays bounded.
            await session.execute(
                delete(SignalDedupRecord).where(SignalDedupRecord.expires_at <= now)
            )
            result = await session.execute(
                select(SignalDedupRecord)
                .where(SignalDedupRecord.dedup_key == key)
                .with_for_update()
            )
            record = result.scalar_one_or_none()
            if record is not None and record.expires_at and record.expires_at > now:
                return False
            if record is None:
                record = SignalDedupRecord(
                    dedup_key=key, candle_time_ms=int(candle_time_ms),
                    payload=json.dumps(signal, default=str),
                    created_at=now, expires_at=expires,
                )
                session.add(record)
            else:
                record.candle_time_ms = int(candle_time_ms)
                record.payload = json.dumps(signal, default=str)
                record.created_at = now
                record.expires_at = expires
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False
        except Exception as exc:
            await session.rollback()
            logger.error(f"Persistent signal claim failed: {exc}")
            return False


async def save_step_channel_state(pair: str, state: dict) -> bool:
    """Upsert the latest confirmed Step Channel snapshot for one market."""
    import json
    from datetime import datetime
    from sqlalchemy import select
    from database import db as database_db
    from database.step_channel_model import StepChannelStateRecord
    if database_db.SessionLocal is None:
        return False
    async with database_db.SessionLocal() as session:
        try:
            row = await session.get(StepChannelStateRecord, pair)
            if row is None:
                row = StepChannelStateRecord(pair=pair)
                session.add(row)
            row.direction = str(state.get("direction", "UNKNOWN"))
            row.event = str(state.get("event") or "")
            row.event_time_ms = int(state.get("event_time_ms") or 0)
            row.candle_time_ms = int(state.get("candle_time_ms") or 0)
            row.payload = json.dumps(state, default=str)
            row.updated_at = datetime.utcnow()
            await session.commit()
            return True
        except Exception as exc:
            await session.rollback()
            logger.error(f"Step Channel state persistence failed for {pair}: {exc}")
            return False


async def load_step_channel_states() -> dict:
    """Restore persisted Step Channel snapshots; fresh OHLCV replay remains authoritative."""
    import json
    from sqlalchemy import select
    from database import db as database_db
    from database.step_channel_model import StepChannelStateRecord
    if database_db.SessionLocal is None:
        return {}
    async with database_db.SessionLocal() as session:
        try:
            result = await session.execute(select(StepChannelStateRecord))
            restored = {}
            for row in result.scalars().all():
                try:
                    restored[row.pair] = json.loads(row.payload or "{}")
                except (TypeError, ValueError):
                    logger.warning(f"Invalid persisted Step Channel state for {row.pair}")
            return restored
        except Exception as exc:
            logger.error(f"Step Channel state restore failed: {exc}")
            return {}
