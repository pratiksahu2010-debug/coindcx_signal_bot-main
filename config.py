"""Application configuration using Pydantic Settings with fallback."""
from functools import lru_cache
from typing import List
import os

try:
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False


if HAS_PYDANTIC:
    class Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )

        telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
        telegram_admin_ids: str = Field(default="", alias="TELEGRAM_ADMIN_IDS")
        telegram_allowed_users: str = Field(default="*", alias="TELEGRAM_ALLOWED_USERS")
        database_url: str = Field(
            default="sqlite+aiosqlite:///./local_dev.db",
            alias="DATABASE_URL",
        )
        coindcx_api_key: str = Field(default="", alias="COINDCX_API_KEY")
        coindcx_api_secret: str = Field(default="", alias="COINDCX_API_SECRET")
        coindcx_rest_base: str = "https://api.coindcx.com"
        coindcx_public_base: str = "https://public.coindcx.com"
        coindcx_ws_url: str = "https://stream.coindcx.com"
        app_env: str = Field(default="development", alias="APP_ENV")
        log_level: str = Field(default="INFO", alias="LOG_LEVEL")
        signal_threshold: float = Field(default=7.0, alias="SIGNAL_THRESHOLD")
        candle_primary_tf: str = Field(default="5m", alias="CANDLE_PRIMARY_TF")
        candle_confirm_tf: str = Field(default="15m", alias="CANDLE_CONFIRM_TF")
        max_candles_buffer: int = Field(default=300, alias="MAX_CANDLES_BUFFER")
        enable_websocket: bool = Field(default=False, alias="ENABLE_WEBSOCKET")
        enable_normal_engine: bool = Field(default=True, alias="ENABLE_NORMAL_ENGINE")
        enable_momentum_engine: bool = Field(default=True, alias="ENABLE_MOMENTUM_ENGINE")
        memory_warning_mb: int = Field(default=400, alias="MEMORY_WARNING_MB")
        normal_adx_min: float = Field(default=25.0, alias="NORMAL_ADX_MIN")
        normal_rvol_min: float = Field(default=1.2, alias="NORMAL_RVOL_MIN")
        momentum_adx_explosion: float = Field(default=18.0, alias="MOMENTUM_ADX_MIN")
        normal_symbol_limit: int = Field(default=40, alias="NORMAL_SYMBOL_LIMIT")
        momentum_symbol_limit: int = Field(default=80, alias="MOMENTUM_SYMBOL_LIMIT")
        normal_min_24h_turnover_usdt: float = Field(default=1000000.0, alias="NORMAL_MIN_24H_TURNOVER_USDT")
        momentum_min_24h_turnover_usdt: float = Field(default=500000.0, alias="MOMENTUM_MIN_24H_TURNOVER_USDT")
        candle_close_grace_seconds: int = Field(default=3, alias="CANDLE_CLOSE_GRACE_SECONDS")
        max_signal_age_seconds: int = Field(default=120, alias="MAX_SIGNAL_AGE_SECONDS")
        futures_round_trip_cost_bps: float = Field(default=15.0, alias="FUTURES_ROUND_TRIP_COST_BPS")
        orb_enabled: bool = Field(default=True, alias="ORB_ENABLED")
        orb_london_enabled: bool = Field(default=True, alias="ORB_LONDON_ENABLED")
        orb_new_york_enabled: bool = Field(default=True, alias="ORB_NEW_YORK_ENABLED")
        orb_min_rvol: float = Field(default=1.30, alias="ORB_MIN_RVOL")
        orb_adx_min: float = Field(default=18.0, alias="ORB_ADX_MIN")
        orb_max_extension_atr: float = Field(default=0.80, alias="ORB_MAX_EXTENSION_ATR")
        orb_active_minutes: int = Field(default=180, alias="ORB_ACTIVE_MINUTES")

        @property
        def signal_only(self) -> bool:
            return True

        @property
        def orders_enabled(self) -> bool:
            return False

        @property
        def admin_ids(self) -> List[int]:
            """Return unique positive numeric Telegram IDs; ignore malformed entries safely."""
            ids = []
            for item in self.telegram_admin_ids.split(","):
                try:
                    value = int(item.strip())
                except (TypeError, ValueError, OverflowError):
                    continue
                if value > 0 and value not in ids:
                    ids.append(value)
            return ids

        @property
        def is_production(self) -> bool:
            return (
                self.app_env.lower() == "production"
                or os.getenv("RENDER", "").lower() == "true"
                or bool(os.getenv("RENDER_SERVICE_ID"))
            )

        @property
        def use_postgres(self) -> bool:
            return self.database_url.lower().startswith(
                ("postgres://", "postgresql://", "postgresql+asyncpg://")
            )
else:
    class Settings:  # type: ignore
        def __init__(self):
            self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            self.telegram_admin_ids = os.getenv("TELEGRAM_ADMIN_IDS", "")
            self.telegram_allowed_users = os.getenv("TELEGRAM_ALLOWED_USERS", "*")
            self.database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./local_dev.db")
            self.coindcx_api_key = os.getenv("COINDCX_API_KEY", "")
            self.coindcx_api_secret = os.getenv("COINDCX_API_SECRET", "")
            self.coindcx_rest_base = "https://api.coindcx.com"
            self.coindcx_public_base = "https://public.coindcx.com"
            self.coindcx_ws_url = "https://stream.coindcx.com"
            self.app_env = os.getenv("APP_ENV", "development")
            self.log_level = os.getenv("LOG_LEVEL", "INFO")
            self.signal_threshold = float(os.getenv("SIGNAL_THRESHOLD", "7.0"))
            self.candle_primary_tf = os.getenv("CANDLE_PRIMARY_TF", "5m")
            self.candle_confirm_tf = os.getenv("CANDLE_CONFIRM_TF", "15m")
            self.max_candles_buffer = int(os.getenv("MAX_CANDLES_BUFFER", "300"))
            self.enable_websocket = os.getenv("ENABLE_WEBSOCKET", "false").lower() == "true"
            self.enable_normal_engine = os.getenv("ENABLE_NORMAL_ENGINE", "true").lower() == "true"
            self.enable_momentum_engine = os.getenv("ENABLE_MOMENTUM_ENGINE", "true").lower() == "true"
            self.memory_warning_mb = int(os.getenv("MEMORY_WARNING_MB", "400"))
            self.normal_adx_min = float(os.getenv("NORMAL_ADX_MIN", "25.0"))
            self.normal_rvol_min = float(os.getenv("NORMAL_RVOL_MIN", "1.2"))
            self.momentum_adx_explosion = float(os.getenv("MOMENTUM_ADX_MIN", "18.0"))
            self.normal_symbol_limit = int(os.getenv("NORMAL_SYMBOL_LIMIT", "40"))
            self.momentum_symbol_limit = int(os.getenv("MOMENTUM_SYMBOL_LIMIT", "80"))
            self.normal_min_24h_turnover_usdt = float(os.getenv("NORMAL_MIN_24H_TURNOVER_USDT", "1000000"))
            self.momentum_min_24h_turnover_usdt = float(os.getenv("MOMENTUM_MIN_24H_TURNOVER_USDT", "500000"))
            self.candle_close_grace_seconds = int(os.getenv("CANDLE_CLOSE_GRACE_SECONDS", "3"))
            self.max_signal_age_seconds = int(os.getenv("MAX_SIGNAL_AGE_SECONDS", "120"))
            self.futures_round_trip_cost_bps = float(os.getenv("FUTURES_ROUND_TRIP_COST_BPS", "15.0"))
            self.orb_enabled = os.getenv("ORB_ENABLED", "true").lower() == "true"
            self.orb_london_enabled = os.getenv("ORB_LONDON_ENABLED", "true").lower() == "true"
            self.orb_new_york_enabled = os.getenv("ORB_NEW_YORK_ENABLED", "true").lower() == "true"
            self.orb_min_rvol = float(os.getenv("ORB_MIN_RVOL", "1.30"))
            self.orb_adx_min = float(os.getenv("ORB_ADX_MIN", "18.0"))
            self.orb_max_extension_atr = float(os.getenv("ORB_MAX_EXTENSION_ATR", "0.80"))
            self.orb_active_minutes = int(os.getenv("ORB_ACTIVE_MINUTES", "180"))

        @property
        def signal_only(self) -> bool:
            return True

        @property
        def orders_enabled(self) -> bool:
            return False

        @property
        def admin_ids(self) -> List[int]:
            """Return unique positive numeric Telegram IDs; ignore malformed entries safely."""
            ids = []
            for item in self.telegram_admin_ids.split(","):
                try:
                    value = int(item.strip())
                except (TypeError, ValueError, OverflowError):
                    continue
                if value > 0 and value not in ids:
                    ids.append(value)
            return ids

        @property
        def is_production(self) -> bool:
            return (
                self.app_env.lower() == "production"
                or os.getenv("RENDER", "").lower() == "true"
                or bool(os.getenv("RENDER_SERVICE_ID"))
            )

        @property
        def use_postgres(self) -> bool:
            return self.database_url.lower().startswith(
                ("postgres://", "postgresql://", "postgresql+asyncpg://")
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()