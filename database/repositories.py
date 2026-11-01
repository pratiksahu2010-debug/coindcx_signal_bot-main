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
