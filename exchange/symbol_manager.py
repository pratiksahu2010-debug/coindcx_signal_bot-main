"""Symbol validation and universe management."""
import yaml
from pathlib import Path
from typing import Dict, List, Set
from utils.logger import logger
from exchange.coindcx_rest import CoinDCXRest


class SymbolManager:
    def __init__(self, rest: CoinDCXRest):
        self.rest = rest
        self.valid_pairs: Set[str] = set()
        self.normal_symbols: List[str] = []
        self.momentum_symbols: List[str] = []
        self.pair_to_symbol: Dict[str, str] = {}
        self.metadata: Dict[str, dict] = {}

    async def load_and_validate(self) -> None:
        details: list = []
        try:
            details = await self.rest.get_market_details()
        except Exception as e:
            logger.error(f"markets_details failed: {e}. Trying fallback via markets list.")
            try:
                markets = await self.rest.get_markets()
                # Build minimal synthetic details from market codes like "BTCUSDT"
                for m in markets or []:
                    if isinstance(m, str) and m.endswith("USDT"):
                        target = m[:-4]
                        pair = f"B-{target}_USDT"
                        details.append({
                            "pair": pair,
                            "coindcx_name": m,
                            "base_currency_short_name": "USDT",
                            "target_currency_short_name": target,
                            "symbol": m,
                            "ecode": "B",
                            "status": "active",
                        })
            except Exception as e2:
                logger.error(f"Fallback markets list also failed: {e2}")

        self.valid_pairs.clear()
        self.metadata.clear()
        for m in details:
            pair = m.get("pair") or m.get("coindcx_name")
            if not pair:
                continue
            base = m.get("base_currency_short_name", "")
            target = m.get("target_currency_short_name", "")
            status = (m.get("status") or "active").lower()
            if status not in ("active", ""):
                continue
            # Accept USDT pairs
            if base == "USDT" or str(pair).endswith("_USDT") or str(pair).endswith("USDT"):
                # Normalize pair format to B-XXX_USDT when possible
                if not str(pair).startswith("B-") and target and base == "USDT":
                    pair = f"B-{target}_USDT"
                self.valid_pairs.add(pair)
                self.metadata[pair] = m
                display = f"{target}/USDT" if target else str(pair).replace("B-", "").replace("_", "/")
                self.pair_to_symbol[pair] = display

        logger.info(f"Loaded {len(self.valid_pairs)} valid USDT pairs from CoinDCX")

        self.normal_symbols = self._load_yaml("config/normal_symbols.yaml")
        self.momentum_symbols = self._load_yaml("config/momentum_symbols.yaml")

        self.normal_symbols = self._filter_valid(self.normal_symbols, "Normal")
        self.momentum_symbols = self._filter_valid(self.momentum_symbols, "Momentum")

        logger.info(f"Normal engine symbols: {len(self.normal_symbols)}")
        logger.info(f"Momentum engine symbols: {len(self.momentum_symbols)}")

        if not self.normal_symbols and not self.momentum_symbols:
            logger.warning("No symbols validated. Check YAML pairs against live CoinDCX markets.")

    def _load_yaml(self, rel_path: str) -> List[str]:
        path = Path(__file__).resolve().parent.parent / rel_path
        if not path.exists():
            logger.error(f"Symbol file missing: {path}")
            return []
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return list(dict.fromkeys(data.get("symbols", [])))

    def _filter_valid(self, symbols: List[str], engine: str) -> List[str]:
        valid = []
        for s in symbols:
            if s in self.valid_pairs:
                valid.append(s)
            else:
                # try alternate form without B- prefix or with different ecode
                alt = s.replace("B-", "I-") if s.startswith("B-") else f"B-{s}"
                if alt in self.valid_pairs:
                    valid.append(alt)
                else:
                    logger.warning(f"{engine}: unsupported symbol skipped: {s}")
        return valid

    def display_name(self, pair: str) -> str:
        return self.pair_to_symbol.get(pair, pair.replace("B-", "").replace("_", "/"))
