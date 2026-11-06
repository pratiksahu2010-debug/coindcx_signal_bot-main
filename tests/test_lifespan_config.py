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
