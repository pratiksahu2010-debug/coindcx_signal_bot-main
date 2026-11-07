"""Basic health endpoint tests."""
import pytest
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.mark.asyncio
async def test_health():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_ping_is_empty_keepalive_only():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/ping")
        assert resp.status_code == 204
        assert resp.content == b""
        assert resp.text == ""



@pytest.mark.asyncio
async def test_ping_head_is_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.head("/ping")
        assert resp.status_code == 204
        assert resp.content == b""


@pytest.mark.asyncio
async def test_ping_remains_alive_when_database_is_degraded():
    old_state = getattr(app.state, "bot_state", None)
    app.state.bot_state = {
        "database": "unavailable",
        "normal_engine": "paused_database_unavailable",
        "momentum_engine": "paused_database_unavailable",
    }
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/ping")
            assert resp.status_code == 204
            assert resp.content == b""
    finally:
        if old_state is None:
            delattr(app.state, "bot_state")
        else:
            app.state.bot_state = old_state


@pytest.mark.asyncio
async def test_health_reports_database_degraded_state():
    old_state = getattr(app.state, "bot_state", None)
    app.state.bot_state = {
        "database": "unavailable",
        "normal_engine": "paused_database_unavailable",
        "momentum_engine": "paused_database_unavailable",
    }
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 503
            assert resp.json()["status"] == "degraded"
    finally:
        if old_state is None:
            delattr(app.state, "bot_state")
        else:
            app.state.bot_state = old_state


@pytest.mark.asyncio
async def test_health_reports_empty_enabled_universe_as_degraded():
    old_state = getattr(app.state, "bot_state", None)
    app.state.bot_state = {
        "database": "postgres",
        "normal_engine": "starting",
        "momentum_engine": "starting",
        "valid_symbols_normal": 0,
        "valid_symbols_momentum": 80,
    }
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 503
            assert "normal_universe_empty" in resp.json()["reasons"]
    finally:
        if old_state is None:
            delattr(app.state, "bot_state")
        else:
            app.state.bot_state = old_state


@pytest.mark.asyncio
async def test_production_health_degrades_when_telegram_delivery_is_unavailable(monkeypatch):
    from types import SimpleNamespace
    from config import settings

    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    old_state = getattr(app.state, "bot_state", None)
    old_telegram = getattr(app.state, "telegram", None)
    app.state.bot_state = {
        "database": "postgres",
        "normal_engine": "running",
        "momentum_engine": "running",
        "valid_symbols_normal": 40,
        "valid_symbols_momentum": 80,
    }
    app.state.telegram = SimpleNamespace(bot=None, admin_ids=[])
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 503
            assert "telegram_delivery_unavailable" in resp.json()["reasons"]
    finally:
        if old_state is None:
            delattr(app.state, "bot_state")
        else:
            app.state.bot_state = old_state
        if old_telegram is None:
            if hasattr(app.state, "telegram"):
                delattr(app.state, "telegram")
        else:
            app.state.telegram = old_telegram


@pytest.mark.asyncio
async def test_production_health_accepts_configured_telegram_delivery(monkeypatch):
    from types import SimpleNamespace
    from config import settings

    monkeypatch.setattr(type(settings), "is_production", property(lambda self: True))
    old_state = getattr(app.state, "bot_state", None)
    old_telegram = getattr(app.state, "telegram", None)
    app.state.bot_state = {
        "database": "postgres",
        "normal_engine": "running",
        "momentum_engine": "running",
        "valid_symbols_normal": 40,
        "valid_symbols_momentum": 80,
    }
    app.state.telegram = SimpleNamespace(bot=object(), admin_ids=[123456])
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["telegram_delivery_ready"] is True
    finally:
        if old_state is None:
            delattr(app.state, "bot_state")
        else:
            app.state.bot_state = old_state
        if old_telegram is None:
            if hasattr(app.state, "telegram"):
                delattr(app.state, "telegram")
        else:
            app.state.telegram = old_telegram
