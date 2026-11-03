"""Health, status, ping, metrics endpoints — lightweight for keep-alive."""
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from config import settings
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
async def health(request: Request):
    """Report degraded startup state so Render does not mistake a paused bot for healthy."""
    state = getattr(request.app.state, "bot_state", None)
    if state is not None:
        database = state.get("database")
        enabled = (
            (settings.enable_normal_engine and state.get("normal_engine") == "paused_database_unavailable")
            or (settings.enable_momentum_engine and state.get("momentum_engine") == "paused_database_unavailable")
        )
        if database == "unavailable" or enabled:
            return JSONResponse(
                status_code=503,
                content={"ok": False, "status": "degraded", "database": database,
                         "normal_engine": state.get("normal_engine"),
                         "momentum_engine": state.get("momentum_engine")},
            )
    return {"ok": True, "status": "healthy"}


@router.api_route("/ping", methods=["GET", "HEAD"], response_class=Response)
async def ping() -> Response:
    """Process-liveness only: empty 204 with no scans or external/DB work."""
    return Response(status_code=204)


@router.get("/status")
async def status(request: Request) -> Dict[str, Any]:
    app_state = getattr(request.app.state, "bot_state", {})
    mem = check_memory()
    telegram = getattr(request.app.state, "telegram", None)
    telegram_polling = bool(getattr(telegram, "polling_active", False))
    telegram_status = "polling" if telegram_polling else ("send_only" if getattr(telegram, "bot", None) else "disabled")
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
        "telegram": telegram_status,
        "telegram_polling": telegram_polling,
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
        "futures_rest_requests": getattr(getattr(request.app.state, "rest", None), "request_count", 0),
        "futures_rest_failures": getattr(getattr(request.app.state, "rest", None), "request_failures", 0),
        "candle_cache_hits": getattr(getattr(request.app.state, "rest", None), "candle_cache_hits", 0),
        "pending_retests": len(getattr(getattr(request.app.state, "engine", None), "pending_retests", {})),
        "pending_retest_checks": getattr(getattr(request.app.state, "engine", None), "pending_retest_checks", 0),
        "pending_retest_failures": getattr(getattr(request.app.state, "engine", None), "pending_retest_failures", 0),
        "telegram_delivery_failures": getattr(getattr(request.app.state, "engine", None), "telegram_delivery_failures", 0),
        "memory_mb": mem["memory_mb"],
        "timestamp": utc_iso(),
    }