"""Simple repository helpers (optional persistence)."""
from typing import Optional
from utils.logger import logger


async def save_signal(session, signal: dict) -> None:
    try:
        from database.models import SignalRecord
        import json
        rec = SignalRecord(
            pair=signal["pair"],
            engine=signal["engine"],
            direction=signal["direction"],
            score=signal.get("score", 0),
            grade=signal.get("grade", ""),
            price=signal.get("price", 0),
            status=signal.get("status", "active"),
            payload=json.dumps(signal),
        )
        session.add(rec)
        await session.commit()
    except Exception as e:
        logger.debug(f"save_signal skipped: {e}")


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
    key = f"{signal['pair']}:{signal['direction']}:{int(candle_time_ms)}"
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
