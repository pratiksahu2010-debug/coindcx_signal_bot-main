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
