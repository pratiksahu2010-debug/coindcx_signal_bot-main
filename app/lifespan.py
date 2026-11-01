"""Application lifespan — startup / shutdown with graceful recovery."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import FastAPI

from config import settings
from utils.logger import logger
from utils.memory_monitor import check_memory
from exchange.coindcx_rest import CoinDCXRest
from exchange.symbol_manager import SymbolManager
from exchange.candle_builder import CandleBuilder
from core.engine import SignalEngine
from tgbot.bot import TelegramBot
from database.db import init_db, dispose_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("=== CoinDCX Signal Bot v2.0 starting ===")
    start_time = datetime.now(timezone.utc)

    database_ready = False
    try:
        await init_db()
        database_ready = True
    except Exception as exc:
        logger.exception(f"Database initialization failed: {exc}")

    rest = CoinDCXRest()
    await rest.start()

    symbol_mgr = SymbolManager(rest)
    try:
        await symbol_mgr.load_and_validate()
    except Exception as e:
        logger.error(f"Symbol validation failed: {e}")

    candle_builder = CandleBuilder()
    engine = SignalEngine(rest, symbol_mgr, candle_builder)

    # Telegram
    tg = TelegramBot()
    await tg.start()
    engine.set_telegram(tg)

    app.state.rest = rest
    app.state.symbol_mgr = symbol_mgr
    app.state.candle_builder = candle_builder
    app.state.engine = engine
    app.state.telegram = tg
    app.state.start_time = start_time
    app.state.bot_state = {
        "websocket_status": "disabled_rest_mode" if not settings.enable_websocket else "configured_but_not_started",
        "normal_engine": "starting" if settings.enable_normal_engine else "disabled",
        "momentum_engine": "starting" if settings.enable_momentum_engine else "disabled",
        "last_market_update": None,
        "last_scan": None,
        "active_signals": 0,
        "watchlist_count": 0,
        "valid_symbols_normal": len(symbol_mgr.normal_symbols),
        "valid_symbols_momentum": len(symbol_mgr.momentum_symbols),
        "scan_count": 0,
        "ws_reconnects": 0,
        "database": ("postgres" if settings.use_postgres else "sqlite") if database_ready else "unavailable",
        "uptime_seconds": 0,
        "telegram": "polling" if tg.polling_active else ("send_only" if tg.bot else "disabled"),
        "telegram_polling": bool(tg.polling_active),
        "eligible_symbols": len(symbol_mgr.valid_pairs),
        "skipped_symbols": len(symbol_mgr.skipped_reasons),
    }

    scan_task = asyncio.create_task(engine.run_scan_loop(app))

    logger.info("Startup complete. Engines ready.")
    check_memory()

    yield

    logger.info("Shutting down...")
    scan_task.cancel()
    try:
        await scan_task
    except asyncio.CancelledError:
        pass
    await engine.shutdown()
    await tg.stop()
    await rest.stop()
    if database_ready:
        await dispose_db()
    logger.info("Shutdown complete.")
