"""Dynamic CoinDCX USDT universe selection for independent strategy engines."""
import re
import yaml
from pathlib import Path
from typing import Dict, List, Set, Tuple
from datetime import datetime, timezone
from utils.logger import logger
from exchange.coindcx_rest import CoinDCXRest
from config import settings


class SymbolManager:
    def __init__(self, rest: CoinDCXRest):
        self.rest = rest
        self.valid_pairs: Set[str] = set()
        self.normal_symbols: List[str] = []
        self.momentum_symbols: List[str] = []
        self.pair_to_symbol: Dict[str, str] = {}
        self.metadata: Dict[str, dict] = {}
        self.liquidity: Dict[str, float] = {}
        self.skipped_reasons: Dict[str, str] = {}
        self._ranked_normal: List[str] = []
        self._ranked_momentum: List[str] = []
        self._quarantined: Dict[str, dict] = {}
        self._quarantine_retry_seconds = 15 * 60
        self._last_liquidity_refresh = 0.0
        self._liquidity_refresh_seconds = 15 * 60

    @staticmethod
    def _norm(value: object) -> str:
        value = str(value or "").upper().strip()
        value = re.sub(r"^(B-|I-)", "", value)
        return re.sub(r"[^A-Z0-9]", "", value)

    # USDT-quoted stablecoin pairs have no meaningful directional edge for
    # this momentum/trend scanner. Keep this list exact (not substring-based)
    # to avoid accidentally excluding ordinary assets with similar names.
    STABLECOIN_BASES = {
        "USDC", "BUSD", "FDUSD", "TUSD", "DAI", "USDE", "USDS",
        "PYUSD", "USDP", "USD1", "UST", "USTC", "USDD", "FRAX",
        "GUSD", "LUSD", "MIM", "CRVUSD", "EURC", "EURS",
    }

    @classmethod
    def _stablecoin_base(cls, pair: str, target: str = "") -> str:
        if target:
            return cls._norm(target)
        normalized = str(pair or "").upper().strip()
        normalized = re.sub(r"^(B-|I-)", "", normalized)
        if normalized.endswith("_USDT"):
            return cls._norm(normalized[:-5])
        if normalized.endswith("USDT"):
            return cls._norm(normalized[:-4])
        return ""

    @classmethod
    def _is_stablecoin_pair(cls, pair: str, target: str = "") -> bool:
        return cls._stablecoin_base(pair, target) in cls.STABLECOIN_BASES

    @staticmethod
    def _as_positive(value: object) -> float:
        try:
            number = float(value)
            return number if number > 0 and number < float("inf") else 0.0
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _load_yaml(self, rel_path: str) -> List[str]:
        path = Path(__file__).resolve().parent.parent / rel_path
        if not path.exists():
            logger.warning(f"Optional symbol preference file missing: {path}")
            return []
        try:
            with open(path, encoding="utf-8") as stream:
                data = yaml.safe_load(stream) or {}
            return list(dict.fromkeys(str(x).upper() for x in data.get("symbols", []) if x))
        except Exception as exc:
            logger.warning(f"Could not read optional symbol preferences {path}: {exc}")
            return []

    def _ticker_liquidity(self, ticker_rows: list) -> Dict[str, float]:
        values: Dict[str, float] = {}
        for row in ticker_rows or []:
            if not isinstance(row, dict):
                continue
            aliases = [
                row.get("market"), row.get("pair"), row.get("symbol"),
                row.get("coindcx_name"), row.get("market_name"),
            ]
            quote_volume = 0.0
            for key in ("quote_volume", "quoteVolume", "volume_quote",
                        "turnover", "turnover_24h", "quote_volume_24h",
                        "volume_24h_usdt", "volume_24h"):
                quote_volume = self._as_positive(row.get(key))
                if quote_volume:
                    break
            if not quote_volume:
                base_volume = self._as_positive(row.get("base_volume") or row.get("volume"))
                last_price = self._as_positive(row.get("last_price") or row.get("lastPrice") or row.get("price"))
                quote_volume = base_volume * last_price if base_volume and last_price else base_volume
            if quote_volume:
                for alias in aliases:
                    key = self._norm(alias)
                    if key:
                        values[key] = max(values.get(key, 0.0), quote_volume)
        return values

    async def load_and_validate(self) -> None:
        details = []
        try:
            details = await self.rest.get_market_details()
        except Exception as exc:
            logger.error(f"markets_details failed: {exc}")

        if not details:
            try:
                markets = await self.rest.get_markets()
                for market in markets or []:
                    if not isinstance(market, str) or not market.upper().endswith("USDT"):
                        continue
                    target = market[:-4]
                    details.append({
                        "pair": f"B-{target}_USDT",
                        "coindcx_name": market,
                        "base_currency_short_name": "USDT",
                        "target_currency_short_name": target,
                        "status": "active",
                    })
            except Exception as exc:
                logger.error(f"CoinDCX market-list fallback failed: {exc}")

        ticker_rows = []
        try:
            ticker_rows = await self.rest.get_ticker()
        except Exception as exc:
            logger.warning(f"Ticker liquidity ranking unavailable; using active-market fallback: {exc}")
        ticker_liquidity = self._ticker_liquidity(ticker_rows)

        self.valid_pairs.clear()
        self.metadata.clear()
        self.pair_to_symbol.clear()
        self.liquidity.clear()
        self.skipped_reasons.clear()

        for market in details:
            if not isinstance(market, dict):
                continue
            status = str(market.get("status") or "active").lower()
            if status not in ("active", "trading", "enabled", "online", "1", ""):
                continue
            base = str(market.get("base_currency_short_name") or "").upper()
            target = str(market.get("target_currency_short_name") or "").upper()
            raw_pair = str(market.get("pair") or "")
            coindcx_name = str(market.get("coindcx_name") or "")
            pair = raw_pair or coindcx_name
            if base != "USDT" and not pair.upper().endswith(("_USDT", "USDT")):
                continue
            if not pair.upper().startswith(("B-", "I-")):
                if target:
                    pair = f"B-{target}_USDT"
                else:
                    name = coindcx_name.upper()
                    if name.endswith("USDT"):
                        pair = f"B-{name[:-4]}_USDT"
                    else:
                        continue
            pair = pair.upper()
            if not pair.endswith("_USDT"):
                continue
            if self._is_stablecoin_pair(pair, target):
                self.skipped_reasons[pair] = "stablecoin-to-USDT pair excluded from directional scanning"
                continue
            self.valid_pairs.add(pair)
            self.metadata[pair] = market
            display = f"{target}/USDT" if target else pair.removeprefix("B-").removeprefix("I-").replace("_", "/")
            self.pair_to_symbol[pair] = display

            aliases = [pair, raw_pair, coindcx_name, market.get("symbol")]
            volume = max((ticker_liquidity.get(self._norm(x), 0.0) for x in aliases), default=0.0)
            self.liquidity[pair] = volume

        logger.info(
            f"Loaded {len(self.valid_pairs)} eligible active USDT markets from CoinDCX "
            f"(stablecoin pairs excluded: {sum('stablecoin-to-USDT pair' in reason for reason in self.skipped_reasons.values())})"
        )

        normal_preferences = self._load_yaml("config/normal_symbols.yaml")
        momentum_preferences = self._load_yaml("config/momentum_symbols.yaml")
        normal_rank = {self._norm(s): i for i, s in enumerate(normal_preferences)}
        momentum_rank = {self._norm(s): i for i, s in enumerate(momentum_preferences)}

        def rank_universe(preferences: Dict[str, int], limit: int, label: str) -> List[str]:
            # Momentum/ORB share an exact static configured whitelist. Do not
            # silently replace unavailable configured instruments with unrelated markets.
            if label == "Momentum":
                configured = list(preferences.keys())
                available_by_key = {self._norm(pair): pair for pair in self.valid_pairs}
                selected = []
                missing = []
                for key in configured:
                    pair = available_by_key.get(key)
                    if pair:
                        selected.append(pair)
                    else:
                        missing.append(key)
                if missing:
                    logger.warning(
                        f"MOMENTUM_WHITELIST_UNAVAILABLE count={len(missing)} "
                        f"symbols={','.join(missing)}"
                    )
                return selected[:limit]

            pairs = list(self.valid_pairs)
            has_liquidity = any(self.liquidity.get(pair, 0) > 0 for pair in pairs)
            if has_liquidity:
                pairs = [pair for pair in pairs if self.liquidity.get(pair, 0.0) > 0]
                pairs.sort(key=lambda pair: (
                    -self.liquidity.get(pair, 0.0),
                    preferences.get(self._norm(pair), 10**9), pair,
                ))
                logger.info(f"{label}: ranked Futures markets with positive 24h volume")
            else:
                pairs.sort(key=lambda pair: (preferences.get(self._norm(pair), 10**9), pair))
                logger.warning(f"{label}: no Futures volume; using configured order then symbol order")
            return pairs[:limit]

        self._ranked_normal = rank_universe(normal_rank, settings.normal_symbol_limit, "Normal")
        self._ranked_momentum = rank_universe(momentum_rank, settings.momentum_symbol_limit, "Momentum")
        self._last_liquidity_refresh = __import__("time").time()
        self._select_active_symbols()
        logger.info(
            f"Dynamic universe ready: normal={len(self.normal_symbols)}, "
            f"momentum={len(self.momentum_symbols)}, eligible={len(self.valid_pairs)}"
        )
        if not self.valid_pairs:
            logger.error("No eligible active USDT markets were discovered from CoinDCX")


    async def _refresh_liquidity_ranking(self) -> None:
        """Re-rank the discovered market universe by fresh 24h ticker volume."""
        ticker_rows = await self.rest.get_ticker()
        latest = self._ticker_liquidity(ticker_rows)
        if not latest:
            logger.warning("Liquidity refresh returned no usable ticker volumes; retaining prior ranking")
            self._last_liquidity_refresh = (
                __import__("time").time() - self._liquidity_refresh_seconds + 300
            )
            return

        updated_liquidity = {}
        for pair in self.valid_pairs:
            metadata = self.metadata.get(pair, {})
            aliases = (
                pair, metadata.get("pair"), metadata.get("coindcx_name"),
                metadata.get("symbol"),
            )
            updated_liquidity[pair] = max(
                (latest.get(self._norm(alias), 0.0) for alias in aliases), default=0.0
            )
        if not any(volume > 0 for volume in updated_liquidity.values()):
            logger.warning(
                "Liquidity refresh did not match any discovered markets; retaining prior ranking"
            )
            self._last_liquidity_refresh = (
                __import__("time").time() - self._liquidity_refresh_seconds + 300
            )
            return

        self.liquidity.update(updated_liquidity)
        for pair, volume in updated_liquidity.items():
            if volume <= 0:
                self.skipped_reasons[pair] = "no matching positive ticker volume"
            else:
                self.skipped_reasons.pop(pair, None)

        normal_preferences = {
            self._norm(symbol): index
            for index, symbol in enumerate(self._load_yaml("config/normal_symbols.yaml"))
        }
        momentum_preferences = {
            self._norm(symbol): index
            for index, symbol in enumerate(self._load_yaml("config/momentum_symbols.yaml"))
        }

        def rank(preferences: Dict[str, int], label: str) -> List[str]:
            if label == "Momentum":
                available = {self._norm(pair): pair for pair in self.valid_pairs}
                selected = [available[key] for key in preferences if key in available]
                missing = [key for key in preferences if key not in available]
                if missing:
                    logger.warning(f"MOMENTUM_WHITELIST_UNAVAILABLE count={len(missing)} symbols={','.join(missing)}")
                return selected[:settings.momentum_symbol_limit]
            pairs = [pair for pair in self.valid_pairs if self.liquidity.get(pair, 0.0) > 0]
            pairs.sort(key=lambda pair: (-self.liquidity.get(pair, 0.0), preferences.get(self._norm(pair), 10**9), pair))
            logger.info(f"{label}: refreshed Futures liquidity ranking ({len(pairs)} markets with volume)")
            return pairs

        self._ranked_normal = rank(normal_preferences, "Normal")
        self._ranked_momentum = rank(momentum_preferences, "Momentum")
        self._last_liquidity_refresh = __import__("time").time()
        self._select_active_symbols()

    def _refresh_quarantine_from_rest(self) -> None:
        """Quarantine markets whose required candle feeds repeatedly return stale history."""
        getter = getattr(self.rest, "get_stale_market_timeframes", None)
        stale = getter() if callable(getter) else {}
        required = {settings.candle_primary_tf, settings.candle_confirm_tf}
        now = __import__("time").time()
        for pair, timeframe_counts in (stale or {}).items():
            relevant = {tf: int(count) for tf, count in timeframe_counts.items() if tf in required}
            repeated = any(count >= 2 for count in relevant.values())
            both_required = required.issubset(relevant.keys())
            if not (repeated or both_required):
                continue
            reason = "repeated stale candle data: " + ", ".join(
                f"{tf} (observed {count}x)" for tf, count in sorted(relevant.items())
            )
            existing = self._quarantined.get(pair)
            if existing is None:
                retry_at = now + self._quarantine_retry_seconds
                self._quarantined[pair] = {
                    "since": now, "retry_at": retry_at, "reason": reason,
                }
                logger.warning(
                    f"MARKET_QUARANTINED {pair} reason={reason} "
                    f"retry_in_seconds={self._quarantine_retry_seconds}"
                )
            else:
                existing["reason"] = reason

    def _select_active_symbols(self) -> None:
        """Apply each engine's own liquidity ranking after excluding quarantined pairs."""
        excluded = set(self._quarantined)
        self.normal_symbols = [p for p in self._ranked_normal if p not in excluded][:max(1, int(settings.normal_symbol_limit))]
        self.momentum_symbols = [p for p in self._ranked_momentum if p not in excluded][:max(1, int(settings.momentum_symbol_limit))]
        selected = set(self.normal_symbols) | set(self.momentum_symbols)
        # A recovered market may re-enter the active universe; clear its old
        # quarantine/out-of-universe reason before rebuilding current skip state.
        for pair in selected:
            self.skipped_reasons.pop(pair, None)
        for pair, item in self._quarantined.items():
            retry_at = datetime.fromtimestamp(item["retry_at"], tz=timezone.utc).isoformat()
            self.skipped_reasons[pair] = f"temporarily quarantined until {retry_at}: {item['reason']}"
            logger.info(f"MARKET_EXCLUDED {pair} normal={'yes' if pair in self._ranked_normal else 'no'} "
                        f"momentum={'yes' if pair in self._ranked_momentum else 'no'} retry_at={retry_at}")
        for pair in self.valid_pairs - selected - excluded:
            self.skipped_reasons.setdefault(pair, "outside configured engine universe limit")
        logger.info(
            f"ACTIVE_UNIVERSE normal={len(self.normal_symbols)}/{settings.normal_symbol_limit} "
            f"momentum={len(self.momentum_symbols)}/{settings.momentum_symbol_limit} "
            f"quarantined={len(self._quarantined)}"
        )

    async def refresh_universes(self) -> None:
        """Apply stale-feed quarantine and retry quarantined pairs every 15 minutes."""
        import asyncio
        import time

        now = time.time()
        if now - self._last_liquidity_refresh >= self._liquidity_refresh_seconds:
            try:
                await self._refresh_liquidity_ranking()
            except Exception as exc:
                logger.warning(f"24h liquidity refresh failed; retaining previous universe: {exc}")
                # Retry after five minutes rather than on every 5m scan boundary.
                self._last_liquidity_refresh = now - self._liquidity_refresh_seconds + 300

        self._refresh_quarantine_from_rest()
        due = [pair for pair, item in self._quarantined.items() if item["retry_at"] <= now]
        if due:
            primary_tf = settings.candle_primary_tf
            confirm_tf = settings.candle_confirm_tf

            async def probe(pair: str):
                # Move the deadline first so concurrent refreshes cannot probe twice.
                self._quarantined[pair]["retry_at"] = time.time() + self._quarantine_retry_seconds
                try:
                    primary, confirm = await asyncio.gather(
                        self.rest.get_candles(pair, primary_tf, limit=100),
                        self.rest.get_candles(pair, confirm_tf, limit=100),
                    )
                    stale_getter = getattr(self.rest, "get_stale_market_timeframes", None)
                    stale_for_pair = (stale_getter() or {}).get(pair, {}) if callable(stale_getter) else {}
                    if len(primary) >= 50 and len(confirm) >= 55 and not stale_for_pair:
                        self._quarantined.pop(pair, None)
                        logger.info(
                            f"MARKET_RESTORED {pair} fresh_{primary_tf}={len(primary)} "
                            f"fresh_{confirm_tf}={len(confirm)}"
                        )
                    else:
                        item = self._quarantined[pair]
                        item["reason"] = (
                            f"retry still unhealthy: {primary_tf}={len(primary)} candles, "
                            f"{confirm_tf}={len(confirm)} candles, stale={stale_for_pair}"
                        )
                        retry_at = datetime.fromtimestamp(item["retry_at"], tz=timezone.utc).isoformat()
                        logger.warning(
                            f"MARKET_QUARANTINE_RETRY_FAILED {pair} reason={item['reason']} "
                            f"next_retry_at={retry_at}"
                        )
                except Exception as exc:
                    item = self._quarantined.get(pair)
                    if item:
                        item["reason"] = f"retry request failed: {type(exc).__name__}: {exc}"
                        retry_at = datetime.fromtimestamp(item["retry_at"], tz=timezone.utc).isoformat()
                        logger.warning(f"MARKET_QUARANTINE_RETRY_ERROR {pair} error={exc} next_retry_at={retry_at}")

            await asyncio.gather(*(probe(pair) for pair in due))

        self._refresh_quarantine_from_rest()
        self._select_active_symbols()

    def display_name(self, pair: str) -> str:
        return self.pair_to_symbol.get(pair, pair.removeprefix("B-").removeprefix("I-").replace("_", "/"))
