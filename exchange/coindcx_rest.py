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
        self._ticker_snapshot = None
        self._ticker_snapshot_at = 0.0
        self._ticker_lock = asyncio.Lock()
        self._ws_prices: Dict[str, tuple] = {}
        # pair -> timeframe -> consecutive stale API observations
        self._stale_market_timeframes: Dict[str, Dict[str, int]] = {}

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
        # Both strategy engines and delivery validation share one short-lived
        # market-wide snapshot instead of downloading the full ticker list per pair.
        now = time.monotonic()
        if self._ticker_snapshot is not None and now - self._ticker_snapshot_at <= 1.5:
            return self._ticker_snapshot
        async with self._ticker_lock:
            now = time.monotonic()
            if self._ticker_snapshot is not None and now - self._ticker_snapshot_at <= 1.5:
                return self._ticker_snapshot
            data = await self._get(f"{self.base}/exchange/ticker")
            self._ticker_snapshot = data if isinstance(data, list) else []
            self._ticker_snapshot_at = time.monotonic()
            return self._ticker_snapshot

    @staticmethod
    def _market_key(value: Any) -> str:
        import re
        text = str(value or "").upper().strip()
        text = re.sub(r"^(B-|I-)", "", text)
        return re.sub(r"[^A-Z0-9]", "", text)

    def get_stale_market_timeframes(self) -> Dict[str, Dict[str, int]]:
        """Return a copy of stale candle-feed observations for universe quarantine."""
        return {pair: dict(timeframes) for pair, timeframes in self._stale_market_timeframes.items()}

    def set_ws_prices(self, prices: Dict[str, Any], timestamp_ms: Optional[int] = None) -> None:
        """Accept a public futures price snapshot from the optional socket stream."""
        now_ms = int(time.time() * 1000)
        ts = int(timestamp_ms or now_ms)
        if ts < 10**12:
            ts *= 1000
        if abs(now_ms - ts) > 10000:
            return
        for market, raw_price in prices.items():
            try:
                value = float(raw_price)
                if value > 0 and __import__("math").isfinite(value):
                    self._ws_prices[self._market_key(market)] = (value, ts)
            except (TypeError, ValueError, OverflowError):
                continue

    async def get_last_price(self, pair: str) -> Optional[float]:
        """Prefer a fresh futures socket price; fall back to validated REST ticker."""
        wanted = self._market_key(pair)
        cached = self._ws_prices.get(wanted)
        now_ms = int(time.time() * 1000)
        if cached and 0 <= now_ms - cached[1] <= 5000:
            return cached[0]
        rows = await self.get_ticker()
        now_ms = int(time.time() * 1000)
        for row in rows:
            if not isinstance(row, dict):
                continue
            aliases = (
                row.get("market"), row.get("pair"), row.get("symbol"),
                row.get("coindcx_name"), row.get("market_name"),
            )
            if not any(self._market_key(alias) == wanted for alias in aliases if alias):
                continue

            # CoinDCX ticker responses include an epoch-millisecond timestamp.
            # A valid HTTP response can still contain an old market snapshot;
            # fail closed rather than validating an entry against stale prices.
            raw_timestamp = row.get("timestamp")
            try:
                if raw_timestamp is None:
                    continue
                ticker_ms = int(float(raw_timestamp))
                if ticker_ms < 10**12:
                    ticker_ms *= 1000
                age_seconds = (now_ms - ticker_ms) / 1000.0
                if age_seconds < -5.0 or age_seconds > 30.0:
                    logger.warning(
                        f"TICKER_STALE {pair} ticker_age_seconds={age_seconds:.1f}; "
                        "live entry validation rejected"
                    )
                    continue
            except (TypeError, ValueError, OverflowError):
                continue

            for key in ("last_price", "lastPrice", "price", "close"):
                try:
                    value = float(row.get(key))
                    if value > 0 and __import__("math").isfinite(value):
                        return value
                except (TypeError, ValueError, OverflowError):
                    continue
        return None

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
        cache_key = (pair, interval, limit, start_time, end_time)
        cached = self._candle_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 2:
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
            if not isinstance(c, dict):
                continue
            try:
                candles.append({
                    "time": c.get("time") or c.get("t") or c.get("timestamp"),
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                    "volume": float(c.get("volume", 0)),
                })
            except (TypeError, ValueError, OverflowError):
                continue
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
        grace_ms = max(0, int(settings.candle_close_grace_seconds)) * 1000
        # CoinDCX timestamps represent candle OPEN time. Reject misaligned bars
        # and wait briefly after close so the exchange can publish final OHLCV.
        candles = [
            c for c in candles
            if c["time"] % duration_ms == 0
            and c["time"] + duration_ms + grace_ms <= now_ms
        ]
        # Active listings can still return old history for dormant markets.
        # Never pass an abnormally old latest candle to warmup or strategies.
        if candles:
            latest = candles[-1]
            latest_close_ms = int(latest["time"]) + duration_ms
            latest_age_seconds = (now_ms - latest_close_ms) / 1000.0
            stale_limit_seconds = max(
                float(settings.max_signal_age_seconds),
                (duration_ms * 2) / 1000.0,
            )
            if latest_age_seconds > stale_limit_seconds:
                counts = self._stale_market_timeframes.setdefault(pair, {})
                counts[interval] = counts.get(interval, 0) + 1
                logger.warning(
                    f"MARKET_DATA_STALE {pair} timeframe={interval} "
                    f"latest_open_ms={latest['time']} latest_close_ms={latest_close_ms} "
                    f"age_seconds={latest_age_seconds:.1f} "
                    f"stale_limit_seconds={stale_limit_seconds:.1f} "
                    f"consecutive_observations={counts[interval]}; candles rejected"
                )
                candles = []
            elif candles:
                # A fresh valid response resets this timeframe's stale streak.
                timeframes = self._stale_market_timeframes.get(pair)
                if timeframes:
                    timeframes.pop(interval, None)
                    if not timeframes:
                        self._stale_market_timeframes.pop(pair, None)

        self._candle_cache[cache_key] = (time.monotonic(), [dict(x) for x in candles])
        return candles