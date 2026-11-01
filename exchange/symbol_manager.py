"""Dynamic CoinDCX USDT universe selection for independent strategy engines."""
import re
import yaml
from pathlib import Path
from typing import Dict, List, Set, Tuple
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
            pairs = list(self.valid_pairs)
            has_liquidity = any(self.liquidity.get(pair, 0) > 0 for pair in pairs)
            if has_liquidity:
                missing_volume = [pair for pair in pairs if self.liquidity.get(pair, 0.0) <= 0]
                for pair in missing_volume:
                    self.skipped_reasons[pair] = "no matching positive ticker volume"
                pairs = [pair for pair in pairs if self.liquidity.get(pair, 0.0) > 0]
                pairs.sort(key=lambda pair: (
                    -self.liquidity.get(pair, 0.0),
                    preferences.get(self._norm(pair), 10**9),
                    pair,
                ))
                logger.info(f"{label}: ranked markets with positive 24h ticker volume")
            else:
                pairs.sort(key=lambda pair: (preferences.get(self._norm(pair), 10**9), pair))
                logger.warning(f"{label}: no usable ticker volume; using configured preference order then symbol order")
            selected = pairs[:max(1, int(limit))]
            logger.info(f"{label} engine selected {len(selected)} / {len(pairs)} eligible markets")
            return selected

        self.normal_symbols = rank_universe(normal_rank, settings.normal_symbol_limit, "Normal")
        self.momentum_symbols = rank_universe(momentum_rank, settings.momentum_symbol_limit, "Momentum")

        selected = set(self.normal_symbols) | set(self.momentum_symbols)
        for pair in self.valid_pairs - selected:
            self.skipped_reasons.setdefault(pair, "outside configured engine universe limit")
        logger.info(
            f"Dynamic universe ready: normal={len(self.normal_symbols)}, "
            f"momentum={len(self.momentum_symbols)}, eligible={len(self.valid_pairs)}"
        )
        if not self.valid_pairs:
            logger.error("No eligible active USDT markets were discovered from CoinDCX")

    def display_name(self, pair: str) -> str:
        return self.pair_to_symbol.get(pair, pair.removeprefix("B-").removeprefix("I-").replace("_", "/"))
