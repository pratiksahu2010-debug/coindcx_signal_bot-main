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
        """Return Futures instrument names only."""
        return [str(row.get("pair") or row.get("symbol") or "")
                for row in await self.get_market_details() if isinstance(row, dict)]

    async def get_market_details(self) -> List[Dict]:
        """Discover active USDT Futures instruments; never query Spot markets."""
        data = await self._get(
            f"{self.base}/exchange/v1/derivatives/futures/data/active_instruments",
            params={"margin_currency_short_name[]": "USDT"},
        )
        rows = data if isinstance(data, list) else (
            data.get("data", []) if isinstance(data, dict) else []
        )
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            pair = str(row.get("pair") or row.get("symbol") or row.get("instrument") or "").upper()
            if not pair:
                continue
            if not pair.startswith(("B-", "I-", "HB-", "KC-")):
                pair = "B-" + pair.replace("/", "_")
            if not pair.endswith("_USDT"):
                continue
            quote = str(row.get("margin_currency_short_name") or row.get("quote_currency_short_name") or "USDT").upper()
            if quote != "USDT":
                continue
            target = str(row.get("target_currency_short_name") or "").upper()
            if not target:
                target = pair.split("-", 1)[-1].split("_", 1)[0]
            normalized.append({
                **row, "pair": pair, "coindcx_name": pair,
                "base_currency_short_name": "USDT",
                "target_currency_short_name": target,
                "status": row.get("status", "active"),
            })
        return normalized

    async def get_ticker(self) -> List[Dict]:
        """Futures current-price snapshot. No Spot ticker fallback is permitted."""
        now = time.monotonic()
        if self._ticker_snapshot is not None and now - self._ticker_snapshot_at <= 1.0:
            return self._ticker_snapshot
        async with self._ticker_lock:
            now = time.monotonic()
            if self._ticker_snapshot is not None and now - self._ticker_snapshot_at <= 1.0:
                return self._ticker_snapshot
            data = await self._get(f"{self.public}/market_data/v3/current_prices/futures/rt")
            payload = data.get("prices", data.get("data", data)) if isinstance(data, dict) else data
            rows = []
            if isinstance(payload, dict):
                for market, quote in payload.items():
                    if not isinstance(quote, dict):
                        continue
                    rows.append({
                        "market": market,
                        "last_price": quote.get("ls") or quote.get("last_price") or quote.get("lastPrice"),
                        "mark_price": quote.get("mp"),
                        "timestamp": quote.get("ts") or quote.get("timestamp") or data.get("ts", 0),
                        "quote_volume": quote.get("qv") or quote.get("quote_volume") or 0,
                        "base_volume": quote.get("bv") or quote.get("volume") or 0,
                    })
            elif isinstance(payload, list):
                rows = [row for row in payload if isinstance(row, dict)]
            self._ticker_snapshot = rows
            self._ticker_snapshot_at = time.monotonic()
            return rows

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
        """Accept Futures LTP updates; nested market payloads use ls, never mark price."""
        now_ms = int(time.time() * 1000)
        ts = int(timestamp_ms or now_ms)
        if ts < 10**12:
            ts *= 1000
        if abs(now_ms - ts) > 10000:
            return
        for market, raw in prices.items():
            try:
                if isinstance(raw, dict):
                    value = raw.get("ls") or raw.get("last_price") or raw.get("lastPrice")
                    event_ts = raw.get("ts") or raw.get("timestamp") or ts
                    event_ts = int(float(event_ts))
                    if event_ts < 10**12:
                        event_ts *= 1000
                else:
                    value, event_ts = raw, ts
                price = float(value)
                if price > 0 and __import__("math").isfinite(price) and 0 <= now_ms - event_ts <= 5000:
                    self._ws_prices[self._market_key(market)] = (price, event_ts)
            except (TypeError, ValueError, OverflowError):
                continue

    async def get_last_price(self, pair: str) -> Optional[float]:
        """Return fresh Futures LTP only. Missing/stale Futures data fails closed."""
        wanted = self._market_key(pair)
        cached = self._ws_prices.get(wanted)
        now_ms = int(time.time() * 1000)
        if cached and 0 <= now_ms - cached[1] <= 5000:
            return cached[0]
        for row in await self.get_ticker():
            if not isinstance(row, dict):
                continue
            aliases = (row.get("market"), row.get("pair"), row.get("symbol"))
            if not any(self._market_key(alias) == wanted for alias in aliases if alias):
                continue
            try:
                ts = int(float(row.get("timestamp") or 0))
                if ts < 10**12:
                    ts *= 1000
                age = (now_ms - ts) / 1000.0
                if ts <= 0 or age < -5 or age > 10:
                    continue
                price = float(row.get("last_price") or 0)
                if price > 0 and __import__("math").isfinite(price):
                    return price
            except (TypeError, ValueError, OverflowError):
                continue
        return None

    async def get_candles(
        self, pair: str, interval: str = "5m", limit: int = 200,
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> List[Dict]:
        """Fetch completed Futures candles; synthesize 15m from three Futures 5m bars."""
        if interval == "15m":
            end_ms = int(end_time or time.time() * 1000)
            start_ms = int(start_time or (end_ms - max(limit + 5, 100) * 15 * 60 * 1000))
            five = await self.get_candles(
                pair, "5m", limit=min(max(limit * 3 + 12, 100), 1000),
                start_time=start_ms, end_time=end_ms,
            )
            buckets: Dict[int, List[Dict]] = {}
            width = 15 * 60 * 1000
            for candle in five:
                bucket = int(candle["time"]) // width * width
                buckets.setdefault(bucket, []).append(candle)
            result = []
            for bucket, bars in sorted(buckets.items()):
                bars.sort(key=lambda x: x["time"])
                if len(bars) != 3 or [x["time"] for x in bars] != [bucket, bucket + 300000, bucket + 600000]:
                    continue
                result.append({
                    "time": bucket, "open": bars[0]["open"],
                    "high": max(x["high"] for x in bars),
                    "low": min(x["low"] for x in bars),
                    "close": bars[-1]["close"],
                    "volume": sum(x["volume"] for x in bars),
                })
            return result[-limit:]

        resolution_map = {"1m": "1", "5m": "5", "1h": "60", "1d": "1D"}
        resolution = resolution_map.get(interval)
        if resolution is None:
            raise ValueError(f"Unsupported CoinDCX Futures candle interval: {interval}")
        cache_key = (pair, interval, limit, start_time, end_time)
        cached = self._candle_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 2:
            return [dict(x) for x in cached[1]]
        now_ms = int(time.time() * 1000)
        end_ms = int(end_time or now_ms)
        duration_ms = int(parse_interval(interval).total_seconds() * 1000)
        start_ms = int(start_time or (end_ms - min(limit, 1000) * duration_ms))
        params = {
            "pair": pair, "from": int(start_ms / 1000),
            "to": int(end_ms / 1000), "resolution": resolution, "pcode": "f",
        }
        data = await self._get(f"{self.public}/market_data/candlesticks", params=params)
        if isinstance(data, dict):
            data = data.get("data", data.get("candles", []))
        if not isinstance(data, list):
            return []
        normalized = []
        for row in data:
            if not isinstance(row, dict):
                continue
            try:
                raw_ts = row.get("time", row.get("t", row.get("timestamp")))
                ts = int(float(raw_ts))
                if ts < 10**12:
                    ts *= 1000
                candle = {
                    "time": ts, "open": float(row.get("open", row.get("o", 0))),
                    "high": float(row.get("high", row.get("h", 0))),
                    "low": float(row.get("low", row.get("l", 0))),
                    "close": float(row.get("close", row.get("c", 0))),
                    "volume": float(row.get("volume", row.get("v", 0))),
                }
                vals = [candle[k] for k in ("open", "high", "low", "close", "volume")]
                if not all(__import__("math").isfinite(v) for v in vals):
                    continue
                if min(vals[:4]) <= 0 or candle["volume"] < 0:
                    continue
                if candle["high"] < max(candle["open"], candle["close"], candle["low"]) or candle["low"] > min(candle["open"], candle["close"], candle["high"]):
                    continue
                if ts % duration_ms == 0 and ts + duration_ms + max(0, int(settings.candle_close_grace_seconds))*1000 <= now_ms:
                    normalized.append(candle)
            except (TypeError, ValueError, OverflowError):
                continue
        dedup = {c["time"]: c for c in normalized}
        candles = [dedup[k] for k in sorted(dedup)][-limit:]
        if candles:
            latest_close = candles[-1]["time"] + duration_ms
            age = (now_ms - latest_close) / 1000
            stale_limit = max(float(settings.max_signal_age_seconds), duration_ms * 2 / 1000)
            if age > stale_limit:
                counts = self._stale_market_timeframes.setdefault(pair, {})
                counts[interval] = counts.get(interval, 0) + 1
                logger.warning(f"FUTURES_CANDLES_STALE {pair} {interval} age={age:.1f}s; rejected")
                candles = []
            else:
                self._stale_market_timeframes.get(pair, {}).pop(interval, None)
        self._candle_cache[cache_key] = (time.monotonic(), [dict(x) for x in candles])
        return candles

