"""CoinDCX REST client — public market data only. No order placement."""
import asyncio
from typing import Any, Dict, List, Optional
import httpx
import time
from utils.time_utils import parse_interval

from config import settings
from utils.logger import logger
from utils.retry import async_retry


class CoinDCXRest:
    def __init__(self):
        self.base = settings.coindcx_rest_base
        self.public = settings.coindcx_public_base
        self._client: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(5)  # rate-limit friendly
        self._candle_cache = {}

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=10.0),
                headers={"User-Agent": "CoinDCX-SignalBot/2.0"},
            )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @async_retry(max_attempts=3, delay=1.0)
    async def _get(self, url: str, params: Optional[Dict] = None) -> Any:
        async with self._semaphore:
            assert self._client is not None
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_markets(self) -> List[str]:
        data = await self._get(f"{self.base}/exchange/v1/markets")
        return data if isinstance(data, list) else []

    async def get_market_details(self) -> List[Dict]:
        data = await self._get(f"{self.base}/exchange/v1/markets_details")
        return data if isinstance(data, list) else []

    async def get_ticker(self) -> List[Dict]:
        data = await self._get(f"{self.base}/exchange/ticker")
        return data if isinstance(data, list) else []

    async def get_candles(
        self,
        pair: str,
        interval: str = "5m",
        limit: int = 200,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Dict]:
        """
        Fetch OHLCV candles.
        pair example: B-BTC_USDT
        interval: 1m, 5m, 15m, 30m, 1h, ...
        """
        cache_key = (pair, interval, limit)
        cached = self._candle_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 15:
            return [dict(x) for x in cached[1]]
        params: Dict[str, Any] = {
            "pair": pair,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        data = await self._get(f"{self.public}/market_data/candles/", params=params)
        if not isinstance(data, list):
            return []
        # Normalize to consistent dicts
        candles = []
        for c in data:
            if isinstance(c, dict):
                candles.append({
                    "time": c.get("time") or c.get("t") or c.get("timestamp"),
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                    "volume": float(c.get("volume", 0)),
                })
        # Normalize timestamp to epoch milliseconds and discard malformed candles.
        normalized = []
        for c in candles:
            try:
                raw = float(c["time"])
                ts = int(raw if raw > 10**12 else raw * 1000)
                vals = [c[k] for k in ("open", "high", "low", "close", "volume")]
                if not all(__import__("math").isfinite(v) for v in vals):
                    continue
                if min(c["open"], c["high"], c["low"], c["close"]) <= 0 or c["volume"] < 0:
                    continue
                if c["high"] < max(c["open"], c["close"], c["low"]) or c["low"] > min(c["open"], c["close"], c["high"]):
                    continue
                c["time"] = ts
                normalized.append(c)
            except (TypeError, ValueError, OverflowError):
                continue
        normalized.sort(key=lambda x: x["time"])
        # Remove duplicate timestamps deterministically (last API occurrence wins).
        dedup = {c["time"]: c for c in normalized}
        candles = [dedup[k] for k in sorted(dedup)]
        # Only completed candles may reach the strategy engine.
        duration_ms = int(parse_interval(interval).total_seconds() * 1000)
        now_ms = int(time.time() * 1000)
        candles = [c for c in candles if c["time"] + duration_ms <= now_ms]
        self._candle_cache[cache_key] = (time.monotonic(), [dict(x) for x in candles])
        return candles