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
        self._ws_last_update_ms: Optional[int] = None
        # pair -> timeframe -> consecutive stale API observations
        self._stale_market_timeframes: Dict[str, Dict[str, int]] = {}
        self.request_count = 0
        self.request_failures = 0
        self.candle_cache_hits = 0
        self.last_successful_request_at = None

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
            self.request_count += 1
            try:
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                self.last_successful_request_at = time.time()
                return payload
            except Exception:
                self.request_failures += 1
                raise

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
            # CoinDCX's documented active_instruments response is a JSON list
            # of instrument-name strings (for example, "B-BTC_USDT"), not
            # instrument metadata objects. Accept both the documented shape
            # and object/wrapped variants for forward compatibility.
            if isinstance(row, str):
                raw_pair = row.strip().upper()
                metadata = {}
            elif isinstance(row, dict):
                raw_pair = str(
                    row.get("pair") or row.get("symbol") or row.get("instrument") or ""
                ).strip().upper()
                metadata = row
            else:
                continue
            if not raw_pair:
                continue
            pair = raw_pair
            if not pair.startswith(("B-", "I-", "HB-", "KC-")):
                pair = "B-" + pair.replace("/", "_")
            if not pair.endswith("_USDT"):
                continue
            quote = str(
                metadata.get("margin_currency_short_name")
                or metadata.get("quote_currency_short_name")
                or "USDT"
            ).upper()
            if quote != "USDT":
                continue
            target = str(metadata.get("target_currency_short_name") or "").upper()
            if not target:
                target = pair.split("-", 1)[-1].split("_", 1)[0]
            normalized.append({
                **metadata,
                "pair": pair,
                "coindcx_name": pair,
                "base_currency_short_name": "USDT",
                "target_currency_short_name": target,
                "status": metadata.get("status", "active"),
            })
        # The endpoint should not return duplicates, but deduplicate defensively
        # while preserving exchange ordering.
        unique = {}
        for item in normalized:
            unique.setdefault(item["pair"], item)
        return list(unique.values())

    @staticmethod
    def _timestamp_ms(value: Any) -> Optional[int]:
        """Normalize Unix seconds or milliseconds without accepting invalid epochs."""
        try:
            stamp = float(value)
            if not __import__("math").isfinite(stamp) or stamp <= 0:
                return None
            # Current Unix seconds are ~1e9; milliseconds are ~1e12.
            stamp_ms = int(stamp * 1000) if stamp < 10**11 else int(stamp)
            # Reject implausible epochs instead of silently making stale data fresh.
            if stamp_ms < 946684800000 or stamp_ms > 4102444800000:
                return None
            return stamp_ms
        except (TypeError, ValueError, OverflowError):
            return None

    # Bound future exchange/host clock skew; never accept arbitrary future quotes.
    MAX_FUTURES_CLOCK_SKEW_SECONDS = 15.0
    MAX_REST_TICKER_AGE_SECONDS = 10.0
    MAX_WS_TICKER_AGE_SECONDS = 5.0

    @classmethod
    def _ticker_age_seconds(cls, timestamp: Any, now_ms: int) -> Optional[float]:
        stamp_ms = cls._timestamp_ms(timestamp)
        return (now_ms - stamp_ms) / 1000.0 if stamp_ms is not None else None

    @classmethod
    def _is_fresh_ticker_age(cls, age: Optional[float], max_age: float) -> bool:
        return age is not None and -cls.MAX_FUTURES_CLOCK_SKEW_SECONDS <= age <= max_age

    @staticmethod
    def _normalize_ticker_row(market: Any, quote: Any, fallback_ts: Any = None) -> Optional[Dict]:
        """Normalize Futures ticker rows and retain the timestamp's provenance."""
        if not isinstance(quote, dict):
            return None
        market_name = market or quote.get("market") or quote.get("pair") or quote.get("symbol") or quote.get("mkt")
        if not market_name:
            return None
        row_ts = quote.get("ts") or quote.get("timestamp")
        timestamp = row_ts if row_ts is not None else fallback_ts
        return {
            "market": str(market_name),
            "pair": quote.get("pair") or market_name,
            "symbol": quote.get("symbol") or quote.get("mkt") or market_name,
            "last_price": quote.get("ls") or quote.get("last_price") or quote.get("lastPrice"),
            "mark_price": quote.get("mp") or quote.get("mark_price"),
            "timestamp": timestamp,
            "timestamp_source": "row" if row_ts is not None else ("root" if fallback_ts is not None else "missing"),
            "quote_volume": quote.get("quote_volume") or quote.get("quoteVolume") or 0,
            "base_volume": quote.get("v") or quote.get("bv") or quote.get("base_volume") or quote.get("volume") or 0,
        }

    async def get_ticker(self) -> List[Dict]:
        """Return normalized Futures current-price rows. Spot data is never used."""
        now = time.monotonic()
        if self._ticker_snapshot is not None and now - self._ticker_snapshot_at <= 1.0:
            return self._ticker_snapshot
        async with self._ticker_lock:
            now = time.monotonic()
            if self._ticker_snapshot is not None and now - self._ticker_snapshot_at <= 1.0:
                return self._ticker_snapshot
            data = await self._get(f"{self.public}/market_data/v3/current_prices/futures/rt")
            fallback_ts = None
            payload = data
            # Accept direct and nested API envelopes such as
            # {ts, prices}, {data: {ts, prices}}, or {data: {ts, market: quote}}.
            for _ in range(3):
                if not isinstance(payload, dict):
                    break
                candidate_ts = payload.get("ts") or payload.get("timestamp")
                if candidate_ts is not None:
                    fallback_ts = candidate_ts
                if isinstance(payload.get("prices"), (dict, list)):
                    payload = payload["prices"]
                    break
                if isinstance(payload.get("data"), (dict, list)):
                    payload = payload["data"]
                    continue
                break
            rows: List[Dict] = []
            if isinstance(payload, dict):
                for market, quote in payload.items():
                    normalized = self._normalize_ticker_row(market, quote, fallback_ts)
                    if normalized:
                        rows.append(normalized)
            elif isinstance(payload, list):
                for quote in payload:
                    normalized = self._normalize_ticker_row(None, quote, fallback_ts)
                    if normalized:
                        rows.append(normalized)

            now_ms = int(time.time() * 1000)
            valid_ltp = 0
            fresh_ages = []
            stale_rows = 0
            for row in rows:
                try:
                    price = float(row.get("last_price") or 0)
                    age = self._ticker_age_seconds(row.get("timestamp"), now_ms)
                    if price > 0 and __import__("math").isfinite(price):
                        valid_ltp += 1
                        if self._is_fresh_ticker_age(age, self.MAX_REST_TICKER_AGE_SECONDS):
                            fresh_ages.append(age)
                        else:
                            stale_rows += 1
                except (TypeError, ValueError, OverflowError):
                    stale_rows += 1
            logger.info(
                f"FUTURES_TICKER_SNAPSHOT rows={len(rows)} valid_ltp={valid_ltp} "
                f"fresh_ltp={len(fresh_ages)} stale_ltp={stale_rows} "
                f"oldest_fresh_age_s={max(fresh_ages):.2f}"
                if fresh_ages else
                f"FUTURES_TICKER_SNAPSHOT rows={len(rows)} valid_ltp={valid_ltp} fresh_ltp=0 stale_ltp={stale_rows}"
            )
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

    def set_ws_prices(self, prices: Dict[str, Any], timestamp_ms: Optional[int] = None) -> int:
        """Accept Futures LTP updates; nested market payloads use ls, never mark price."""
        now_ms = int(time.time() * 1000)
        accepted = 0
        root_ts = self._timestamp_ms(timestamp_ms) if timestamp_ms is not None else now_ms
        if root_ts is None:
            return 0
        root_age = (now_ms - root_ts) / 1000.0
        if not self._is_fresh_ticker_age(root_age, self.MAX_WS_TICKER_AGE_SECONDS):
            logger.warning(f"COINDCX_WS_ROOT_TIMESTAMP_REJECTED age_seconds={root_age:.3f} max_future_skew_s={self.MAX_FUTURES_CLOCK_SKEW_SECONDS:g}")
            return 0
        accepted = 0
        for market, raw in prices.items():
            try:
                if isinstance(raw, dict):
                    value = raw.get("ls") or raw.get("last_price") or raw.get("lastPrice")
                    raw_event_ts = raw.get("ts") or raw.get("timestamp") or timestamp_ms
                else:
                    value, raw_event_ts = raw, timestamp_ms
                event_ts = self._timestamp_ms(raw_event_ts)
                price = float(value)
                if (
                    event_ts is not None and price > 0
                    and __import__("math").isfinite(price)
                    and self._is_fresh_ticker_age((now_ms - event_ts) / 1000.0, self.MAX_WS_TICKER_AGE_SECONDS)
                ):
                    self._ws_prices[self._market_key(market)] = (price, event_ts)
                    accepted += 1
            except (TypeError, ValueError, OverflowError):
                continue
        if accepted:
            self._ws_last_update_ms = now_ms
        return accepted

    async def get_last_price(self, pair: str) -> Optional[float]:
        """Return fresh Futures LTP only. Missing/stale Futures data fails closed."""
        wanted = self._market_key(pair)
        cached = self._ws_prices.get(wanted)
        now_ms = int(time.time() * 1000)
        if cached and self._is_fresh_ticker_age((now_ms - cached[1]) / 1000.0, self.MAX_WS_TICKER_AGE_SECONDS):
            return cached[0]
        logger.debug(f"FUTURES_LTP_REST_FALLBACK pair={pair} reason=ws_cache_missing_or_stale")
        rows = await self.get_ticker()
        matched = 0
        stale = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            aliases = (row.get("market"), row.get("pair"), row.get("symbol"))
            if not any(self._market_key(alias) == wanted for alias in aliases if alias):
                continue
            matched += 1
            try:
                age = self._ticker_age_seconds(row.get("timestamp"), now_ms)
                if not self._is_fresh_ticker_age(age, self.MAX_REST_TICKER_AGE_SECONDS):
                    stale += 1
                    logger.warning(
                        f"FUTURES_LTP_ROW_REJECTED pair={pair} matched_market={row.get('market')} "
                        f"timestamp_source={row.get('timestamp_source', 'unknown')} "
                        f"timestamp={row.get('timestamp')} age_seconds={age}"
                    )
                    continue
                price = float(row.get("last_price") or 0)
                if price > 0 and __import__("math").isfinite(price):
                    return price
            except (TypeError, ValueError, OverflowError):
                stale += 1
                continue
        logger.warning(
            f"FUTURES_LTP_UNAVAILABLE pair={pair} ticker_rows={len(rows)} "
            f"matched={matched} stale={stale}"
        )
        return None

    async def get_candles(
        self, pair: str, interval: str = "5m", limit: int = 200,
        start_time: Optional[int] = None, end_time: Optional[int] = None,
    ) -> List[Dict]:
        """Fetch completed Futures candles; synthesize 15m from three Futures 5m bars."""
        # Validate the request before constructing cache keys or making exchange
        # calls. In particular, limit=0 makes Python's [-0:] return every candle.
        try:
            limit = int(limit)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Candle limit must be an integer between 1 and 1000") from exc
        if limit < 1 or limit > 1000:
            raise ValueError("Candle limit must be between 1 and 1000")
        if not isinstance(pair, str) or not pair.strip():
            raise ValueError("Futures pair must be a non-empty string")
        if start_time is not None:
            try:
                start_time = int(start_time)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("start_time must be a positive Unix timestamp in milliseconds") from exc
            if start_time <= 0:
                raise ValueError("start_time must be a positive Unix timestamp in milliseconds")
        if end_time is not None:
            try:
                end_time = int(end_time)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("end_time must be a positive Unix timestamp in milliseconds") from exc
            if end_time <= 0:
                raise ValueError("end_time must be a positive Unix timestamp in milliseconds")
        if start_time is not None and end_time is not None and start_time >= end_time:
            raise ValueError("start_time must be earlier than end_time")
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
        if cached and time.monotonic() - cached[0] < 5:
            self.candle_cache_hits += 1
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
        payload = data
        for _ in range(3):
            if not isinstance(payload, dict):
                break
            if isinstance(payload.get("candles"), list):
                payload = payload["candles"]
                break
            if isinstance(payload.get("data"), (dict, list)):
                payload = payload["data"]
                continue
            break
        data = payload
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
        # Bound memory when many symbols/time windows are scanned.
        if len(self._candle_cache) > 1000:
            cutoff = time.monotonic() - 15
            self._candle_cache = {k: v for k, v in self._candle_cache.items() if v[0] >= cutoff}
            if len(self._candle_cache) > 1000:
                for old_key in list(self._candle_cache)[:len(self._candle_cache) - 1000]:
                    self._candle_cache.pop(old_key, None)
        return candles

