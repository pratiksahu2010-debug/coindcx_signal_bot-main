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
    def __init__(self, candle_builder, rest_client=None):
        self.candles = candle_builder
        self.rest = rest_client
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
        self.on_candle_callback = None
        self._register_handlers()

    def set_candle_callback(self, callback) -> None:
        self.on_candle_callback = callback

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

        @self.client.on("currentPrices@futures#update")
        async def on_prices(message):
            self._handle_prices(message)

    def _handle_prices(self, message: Any) -> None:
        """Consume the documented currentPrices@futures#update event safely."""
        if self.rest is None:
            return
        payload = message.get("data", message) if isinstance(message, dict) else message
        if isinstance(payload, dict):
            prices = payload.get("prices") or payload.get("data")
            event_ts = payload.get("ts") or payload.get("timestamp")
            if isinstance(prices, dict):
                before = len(self.rest._ws_prices)
                self.rest.set_ws_prices(prices, event_ts)
                accepted = len(self.rest._ws_prices) - before
                # Existing pairs can be updated without changing cache size.
                if not accepted:
                    accepted = sum(
                        1 for market, quote in prices.items()
                        if isinstance(quote, dict)
                        and (quote.get("ls") or quote.get("last_price") or quote.get("lastPrice"))
                    )
                logger.debug(
                    f"COINDCX_WS_PRICE_EVENT markets={len(prices)} "
                    f"ltp_updates={accepted} event_ts={'present' if event_ts else 'missing'}"
                )
                return
            if isinstance(prices, list):
                prices = prices
            else:
                return
        else:
            prices = payload if isinstance(payload, list) else []
            event_ts = None
        normalized = {}
        for row in prices:
            if not isinstance(row, dict):
                continue
            market = row.get("market") or row.get("pair") or row.get("symbol") or row.get("mkt")
            if market:
                normalized[str(market)] = row
        if normalized:
            self.rest.set_ws_prices(normalized, event_ts)
            logger.debug(
                f"COINDCX_WS_PRICE_EVENT markets={len(normalized)} "
                f"ltp_updates={sum(bool(v.get('ls') or v.get('last_price') or v.get('lastPrice')) for v in normalized.values())}"
            )

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
                    if self.on_candle_callback is not None:
                        try:
                            self.on_candle_callback(pair, interval, candle)
                        except Exception as exc:
                            logger.debug(f"WS_CANDLE_CALLBACK_SKIPPED {pair}: {exc}")
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
            # A single public futures price channel provides live LTP for the universe.
            await self.client.emit("join", {"channelName": "currentPrices@futures@rt"})
            self._channels.add("currentPrices@futures@1s")
            # CoinDCX Futures public candle channel naming per API docs.
            for pair in sorted(set(pairs)):
                for interval in ("5m", "15m"):
                    channel = f"{pair}_{interval}-futures"
                    await self.client.emit("join", {"channelName": channel})
                    self._channels.add(channel)
            logger.info(f"COINDCX_WS_SUBSCRIBED channels={len(self._channels)} price_channel=currentPrices@futures@rt")
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
