"""Health, status, ping, metrics endpoints — lightweight for keep-alive."""
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Request
from utils.time_utils import utc_iso
from utils.memory_monitor import check_memory

router = APIRouter()


@router.get("/")
async def root() -> Dict[str, Any]:
    return {
        "service": "coindcx-signal-bot",
        "version": "2.0",
        "docs": "/docs",
        "health": "/health",
        "ping": "/ping",
    }


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True, "status": "healthy"}


@router.get("/ping")
async def ping() -> Dict[str, Any]:
    """Lightweight keep-alive endpoint. No market scans, no Telegram, no DB writes."""
    return {
        "ok": True,
        "service": "coindcx-signal-bot",
        "status": "alive",
        "timestamp": utc_iso(),
    }


@router.get("/status")
async def status(request: Request) -> Dict[str, Any]:
    app_state = getattr(request.app.state, "bot_state", {})
    mem = check_memory()
    return {
        "ok": True,
        "service": "coindcx-signal-bot",
        "timestamp": utc_iso(),
        "uptime_seconds": app_state.get("uptime_seconds", 0),
        "websocket": app_state.get("websocket_status", "unknown"),
        "last_market_update": app_state.get("last_market_update"),
        "normal_engine": app_state.get("normal_engine", "unknown"),
        "momentum_engine": app_state.get("momentum_engine", "unknown"),
        "last_scan": app_state.get("last_scan"),
        "active_signals": app_state.get("active_signals", 0),
        "watchlist_count": app_state.get("watchlist_count", 0),
        "scan_errors_normal": app_state.get("scan_errors_normal", 0),
        "scan_errors_momentum": app_state.get("scan_errors_momentum", 0),
        "valid_symbols_normal": app_state.get("valid_symbols_normal", 0),
        "valid_symbols_momentum": app_state.get("valid_symbols_momentum", 0),
        "memory_mb": mem["memory_mb"],
        "database": app_state.get("database", "unknown"),
        "telegram": app_state.get("telegram", "unknown"),
        "telegram_polling": app_state.get("telegram_polling", False),
        "eligible_symbols": app_state.get("eligible_symbols", 0),
        "skipped_symbols": app_state.get("skipped_symbols", 0),
    }


@router.get("/metrics")
async def metrics(request: Request) -> Dict[str, Any]:
    app_state = getattr(request.app.state, "bot_state", {})
    mem = check_memory()
    return {
        "uptime_seconds": app_state.get("uptime_seconds", 0),
        "scan_count": app_state.get("scan_count", 0),
        "last_scan_duration_ms": app_state.get("last_scan_duration_ms", 0),
        "valid_symbols": app_state.get("valid_symbols_normal", 0) + app_state.get("valid_symbols_momentum", 0),
        "active_signals": app_state.get("active_signals", 0),
        "watchlist_entries": app_state.get("watchlist_count", 0),
        "scan_errors_normal": app_state.get("scan_errors_normal", 0),
        "scan_errors_momentum": app_state.get("scan_errors_momentum", 0),
        "websocket_reconnects": app_state.get("ws_reconnects", 0),
        "memory_mb": mem["memory_mb"],
        "timestamp": utc_iso(),
    }