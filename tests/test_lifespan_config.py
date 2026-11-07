"""Startup configuration validation tests."""
import pytest

from app.lifespan import validate_runtime_config
from config import settings


def test_runtime_config_accepts_current_5m_15m_defaults():
    validate_runtime_config()


def test_runtime_config_rejects_non_higher_timeframe_context(monkeypatch):
    monkeypatch.setattr(settings, "candle_confirm_tf", settings.candle_primary_tf)
    with pytest.raises(ValueError, match="CANDLE_CONFIRM_TF"):
        validate_runtime_config()


def test_runtime_config_rejects_nonpositive_primary_interval(monkeypatch):
    monkeypatch.setattr(settings, "candle_primary_tf", "0m")
    with pytest.raises(ValueError, match="Interval must be positive"):
        validate_runtime_config()


def test_runtime_config_rejects_nonpositive_orb_rvol(monkeypatch):
    monkeypatch.setattr(settings, "orb_min_rvol", 0)
    with pytest.raises(ValueError, match="ORB_MIN_RVOL"):
        validate_runtime_config()


def test_runtime_config_rejects_invalid_log_level(monkeypatch):
    monkeypatch.setattr(settings, "log_level", "NOT_A_LEVEL")
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        validate_runtime_config()


def test_runtime_config_rejects_timeframe_not_supported_by_futures_adapter(monkeypatch):
    monkeypatch.setattr(settings, "candle_primary_tf", "30m")
    with pytest.raises(ValueError, match="unsupported by the CoinDCX Futures candle adapter"):
        validate_runtime_config()



@pytest.mark.asyncio
async def test_expected_background_task_cancellation_during_shutdown_is_not_error(monkeypatch):
    import asyncio
    from app.lifespan import _watch_background_task

    state = {"shutting_down": True}
    events = []
    monkeypatch.setattr("app.lifespan.logger.info", lambda message: events.append(("info", message)))
    monkeypatch.setattr("app.lifespan.logger.error", lambda message: events.append(("error", message)))

    task = asyncio.create_task(asyncio.sleep(60))
    _watch_background_task(task, state, "scan_task")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)

    assert state["scan_task_status"] == "stopped"
    assert state["scan_task_error"] == "task_cancelled"
    assert events == [("info", "BACKGROUND_TASK_STOPPED task=scan_task reason=application_shutdown")]
