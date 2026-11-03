"""Optional CoinDCX Futures Socket.IO market-data stream.

REST remains authoritative for completed-candle decisions. This client only
feeds live, forming candles into CandleBuilder and safely degrades on disconnect.
"""
import asyncio
from typing import Any, Dict, Iterable, Optional

import socketio

from config import settings
from utils.logger import logger


class CoinDCXFuturesStream:
    def __init__(self, candle_builder):
        self.candles = candle_builder
        self.client = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=1,
            reconnection_delay_max=30,
            logger=False,
            engineio_logger=False,
        )
        self._channels = set()
        self._running = False
        self._connected = False
        self._last_event_ms: Optional[int] = None
        self._register_handlers()

    def _register_handlers(self) -> None:
        @self.client.event
        async def connect():
            self._connected = True
            logger.info("COINDCX_WS_CONNECTED")

        @self.client.event
        async def disconnect():
            self._connected = False
            logger.warning("COINDCX_WS_DISCONNECTED")

        @self.client.on("candlestick")
        async def on_candlestick(message):
            self._handle_candlestick(message)

    def _handle_candlestick(self, message: Any) -> None:
        if not isinstance(message, dict):
            return
        payload = message.get("data", message)
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                pair = str(row.get("pair") or row.get("channel", "").split("_", 1)[0])
                pair = pair.strip()
                if not pair or not pair.startswith(("B-", "I-", "HB-", "KC-")):
                    continue
                raw_time = row.get("open_time", row.get("t", row.get("time")))
                ts = int(float(raw_time))
                if ts < 10**12:
                    ts *= 1000
                candle = {
                    "time": ts,
                    "open": float(row.get("open", row.get("o"))),
                    "high": float(row.get("high", row.get("h"))),
                    "low": float(row.get("low", row.get("l"))),
                    "close": float(row.get("close", row.get("c"))),
                    "volume": float(row.get("volume", row.get("v", 0)) or 0),
                }
                duration = str(row.get("duration") or row.get("i") or "")
                interval = duration if duration else None
                if interval not in ("1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "3d", "1w", "1M"):
                    channel = str(row.get("channel", ""))
                    interval = next((x for x in ("5m", "15m") if x in channel), None)
                if interval:
                    self.candles.on_candle(pair, interval, candle)
                    self._last_event_ms = int(row.get("Ets") or row.get("close_time") or ts)
            except (TypeError, ValueError, OverflowError):
                continue

    async def start(self, pairs: Iterable[str]) -> bool:
        if not settings.enable_websocket:
            return False
        self._running = True
        try:
            await self.client.connect(
                settings.coindcx_ws_url,
                transports=["websocket"],
                wait_timeout=12,
            )
            # CoinDCX Futures public candle channel naming per API docs.
            for pair in sorted(set(pairs)):
                for interval in ("5m", "15m"):
                    channel = f"{pair}_{interval}-futures"
                    await self.client.emit("join", {"channelName": channel})
                    self._channels.add(channel)
            logger.info(f"COINDCX_WS_SUBSCRIBED channels={len(self._channels)}")
            return True
        except Exception as exc:
            logger.warning(f"COINDCX_WS_START_FAILED REST_fallback=true error={exc}")
            await self.stop()
            return False

    async def stop(self) -> None:
        self._running = False
        if self.client.connected:
            for channel in tuple(self._channels):
                try:
                    await self.client.emit("leave", {"channelName": channel})
                except Exception:
                    pass
            await self.client.disconnect()
        self._channels.clear()
        self._connected = False

    @property
    def status(self) -> str:
        return "connected" if self._connected else ("reconnecting" if self._running else "stopped")
