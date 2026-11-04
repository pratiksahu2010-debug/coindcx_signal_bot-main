"""Central signal processing engine — Normal + Momentum, closed-candle driven."""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import settings
from utils.logger import logger
from utils.time_utils import utc_iso, utc_now, parse_interval
from core.candle_timing import candle_age_seconds, latest_asof_candle_index, seconds_until_next_candle_boundary
from utils.memory_monitor import check_memory, safe_gc
from exchange.coindcx_rest import CoinDCXRest
from exchange.symbol_manager import SymbolManager
from exchange.candle_builder import CandleBuilder
from core.scoring_engine import ScoringEngine
from core.cooldown_manager import CooldownManager
from core.watchlist_manager import WatchlistManager
from core.signal_manager import SignalManager
from core.risk_calculator import calculate_risk, is_entry_price_actionable, classify_entry_state
from strategies.normal.strategy_manager import detect_strategy
from strategies.momentum.strategy_manager import detect_momentum_state
from strategies.momentum import orb_detector
from indicators import (
    ema, rsi, atr, adx, macd, vwap, relative_volume,
    bollinger_bands, bandwidth, keltner_channels, roc,
)
import numpy as np


# Covers a full UTC day (288 x 5m / 96 x 15m) plus a small history margin.
PRIMARY_CANDLE_HISTORY = 300
CONFIRM_CANDLE_HISTORY = 120


def _score_disposition(score: float, signal_threshold: float) -> str:
    """Classify momentum scores without allowing watchlist scores to become alerts."""
    if score >= max(7.0, signal_threshold):
        return "SIGNAL"
    if score >= 5.5:
        return "WATCHLIST"
    return "REJECT"


def _normal_threshold_gates(indicators: Dict[str, Any], direction: str, adx_min: float) -> Dict[str, bool]:
    """The six explicit Normal Engine approval conditions; no candle-shape gate."""
    is_long = direction == "LONG"
    ema9, ema21 = indicators.get("ema9"), indicators.get("ema21")
    price, vwap_value = indicators.get("close"), indicators.get("vwap")
    rsi_value = indicators.get("rsi")
    adx_value = indicators.get("adx")
    return {
        "ema_alignment": (
            ema9 is not None and ema21 is not None
            and ((ema9 > ema21) if is_long else (ema9 < ema21))
        ),
        "price_vwap": (
            price is not None and vwap_value is not None
            and ((price > vwap_value) if is_long else (price < vwap_value))
        ),
        "adx_min": adx_value is not None and adx_value >= adx_min,
        "context_15m": bool(indicators.get("confirm_15m")),
        "rsi_direction": (
            rsi_value is not None
            and ((rsi_value > 50.0) if is_long else (rsi_value < 50.0))
        ),
    }


def _normal_relative_volume_gate(
    indicators: Dict[str, Any], direction: str, min_rvol: float
) -> Dict[str, bool]:
    """Require elevated completed-candle volume with a directional candle proxy.

    CoinDCX OHLCV candles do not expose aggressor-side buy/sell volume. The
    candle body direction is therefore used only as a transparent proxy for
    directional participation; this is not exchange-reported taker volume.
    """
    try:
        rvol_value = float(indicators.get("rvol"))
        threshold = float(min_rvol)
    except (TypeError, ValueError, OverflowError):
        rvol_value = float("nan")
        threshold = float("nan")
    relative_volume_ok = (
        np.isfinite(rvol_value) and np.isfinite(threshold)
        and threshold > 0 and rvol_value >= threshold
    )
    # Candle-body direction is deliberately NOT a Normal Engine gate.
    # CoinDCX OHLCV has no aggressor-side volume; keep the user's simple
    # threshold model and use RVOL only to decide alert vs watchlist.
    return {
        "relative_volume": bool(relative_volume_ok),
    }


def _macd_hist_history(closes: Any, points: int = 4) -> List[float]:
    """Return recent MACD histogram observations.

    indicators.macd() intentionally returns a single (line, signal, histogram)
    tuple, not a historical series. Recalculate the last few prefixes to obtain
    comparable closed-candle histogram observations for momentum acceleration.
    """
    try:
        values = np.asarray(closes, dtype=float).reshape(-1)
    except (TypeError, ValueError, OverflowError):
        return []

    # MACD needs slow(26) + signal(9) observations.
    if values.size < 35 or points < 3:
        return []

    first_end = max(35, int(values.size) - int(points) + 1)
    history: List[float] = []
    for end in range(first_end, int(values.size) + 1):
        result = macd(values[:end])
        if result is None:
            continue
        try:
            histogram = float(result[2])
        except (TypeError, ValueError, IndexError, OverflowError):
            continue
        if np.isfinite(histogram):
            history.append(histogram)
    return history


class SignalEngine:
    def __init__(
        self,
        rest: CoinDCXRest,
        symbols: SymbolManager,
        candles: CandleBuilder,
    ):
        self.rest = rest
        self.symbols = symbols
        self.candles = candles
        # Keep stateful components isolated per strategy engine.
        self.normal_scoring = ScoringEngine()
        self.momentum_scoring = ScoringEngine()
        self.cooldowns = {
            "normal": CooldownManager(default_seconds=1800),
            "momentum": CooldownManager(default_seconds=1800),
        }
        self.watchlists = {
            "normal": WatchlistManager(max_entries=50, ttl_minutes=60),
            "momentum": WatchlistManager(max_entries=50, ttl_minutes=60),
        }
        self.signals = {
            "normal": SignalManager(max_active=30, ttl_hours=4),
            "momentum": SignalManager(max_active=30, ttl_hours=4),
        }
        self._scan_lock = asyncio.Lock()
        self._running = True
        self.last_scan_time: Optional[datetime] = None
        self.telegram = None
        self.scan_errors = {"normal": 0, "momentum": 0}
        # Independent candle cursors prevent one strategy engine suppressing the other.
        self._last_processed = {"normal": {}, "momentum": {}}
        # Durable one-shot retest candidates, restored after process restarts.
        self.pending_retests: Dict[str, dict] = {}
        self.pending_retest_checks = 0
        self.pending_retest_failures = 0
        self.telegram_delivery_failures = 0
        self._pending_retest_cursor = 0

    def set_telegram(self, tg) -> None:
        self.telegram = tg

    def on_intrabar_candle(self, pair: str, timeframe: str, candle: Dict[str, Any]) -> None:
        """Record early forming-candle momentum as watchlist context only.

        Intrabar candidates are never Telegram signals. The normal closed-candle
        REST scan must independently confirm the final Momentum setup.
        """
        if timeframe != settings.candle_primary_tf:
            return
        arr = self.candles.get_arrays(pair, timeframe)
        if not arr or len(arr.get("close", [])) < 25:
            return
        try:
            close = np.asarray(arr["close"], dtype=float)
            volume = np.asarray(arr["volume"], dtype=float)
            opens = float(candle["open"])
            live_close = float(candle["close"])
            avg_volume = float(np.mean(volume[-21:-1]))
            if avg_volume <= 0 or live_close <= 0:
                return
            rvol_live = float(volume[-1]) / avg_volume
            short_roc = (live_close / float(close[-4]) - 1.0) * 100.0
            if rvol_live < 1.2 or abs(short_roc) < 0.08:
                return
            direction = "LONG" if live_close > opens and short_roc > 0 else (
                "SHORT" if live_close < opens and short_roc < 0 else None
            )
            if direction is None:
                return
            self.watchlists["momentum"].add({
                "pair": pair, "display": self.symbols.display_name(pair),
                "direction": direction, "engine": "momentum",
                "strategy": "INTRABAR_PRESETUP", "score": 5.5,
                "grade": "WATCHLIST", "price": live_close,
                "indicators": {"live_rvol": round(rvol_live, 3),
                               "live_roc": round(short_roc, 4)},
                "timestamp": utc_iso(utc_now()),
            })
        except (KeyError, TypeError, ValueError, ZeroDivisionError, FloatingPointError):
            return

    async def restore_active_signals(self) -> int:
        """Rehydrate active signal lifecycle state from the configured database."""
        try:
            from database.repositories import load_active_signals
            records = await load_active_signals(max_age_hours=4)
        except Exception as exc:
            logger.error(f"Active signal restore failed: {exc}")
            return 0
        restored = 0
        for signal in records:
            engine = signal.get("engine")
            if engine in self.signals and self.signals[engine].restore(signal):
                restored += 1
                try:
                    created = datetime.fromisoformat(
                        str(signal.get("created_at", "")).replace("Z", "+00:00")
                    )
                    elapsed = max(0.0, (utc_now() - created).total_seconds())
                    remaining = int(self.cooldowns[engine].default_seconds - elapsed)
                    if remaining > 0:
                        self.cooldowns[engine].set(
                            signal["pair"], engine, signal["direction"], seconds=remaining
                        )
                except (TypeError, ValueError):
                    logger.warning(
                        f"Could not restore cooldown timestamp for {signal.get('pair')}"
                    )
        logger.info(f"ACTIVE_SIGNALS_RESTORED count={restored}")
        return restored


    async def restore_pending_retests(self) -> int:
        """Restore only unexpired persisted retest candidates."""
        try:
            from database.repositories import load_pending_retests
            records = await load_pending_retests(limit=500)
            now = utc_now().timestamp()
            for key, pending in records:
                if float(pending.get("expires_at", 0)) > now:
                    self.pending_retests[key] = pending
            logger.info(f"PENDING_RETESTS_RESTORED count={len(self.pending_retests)}")
            return len(self.pending_retests)
        except Exception as exc:
            logger.error(f"Pending retest restore failed: {exc}")
            return 0

    async def pending_retest_monitor_loop(self, interval_seconds: float = 2.0) -> None:
        """Rate-limited fresh-LTP monitor; bounded to five checks per interval."""
        while self._running:
            try:
                all_keys = list(self.pending_retests)
                if all_keys:
                    start = self._pending_retest_cursor % len(all_keys)
                    keys = (all_keys[start:] + all_keys[:start])[:5]
                    self._pending_retest_cursor = (start + len(keys)) % max(1, len(all_keys))
                else:
                    keys = []
                for key in keys:
                    pending = self.pending_retests.get(key)
                    if not pending:
                        continue
                    signal = pending.get("signal", {})
                    await self._check_pending_retest(signal.get("pair", ""), signal.get("engine", ""))
                    self.pending_retest_checks += 1
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.pending_retest_failures += 1
                logger.warning(f"Pending retest monitor cycle failed: {exc}")
            await asyncio.sleep(max(1.0, min(float(interval_seconds), 10.0)))

    async def _wait_for_next_primary_boundary(self) -> None:
        interval_ms = int(parse_interval(settings.candle_primary_tf).total_seconds() * 1000)
        grace_ms = max(0, int(settings.candle_close_grace_seconds)) * 1000
        now_ms = int(utc_now().timestamp() * 1000)
        delay = seconds_until_next_candle_boundary(now_ms, interval_ms, grace_ms)
        next_scan_utc = datetime.fromtimestamp(
            (now_ms / 1000.0) + delay, tz=timezone.utc
        ).isoformat()
        logger.info(
            f"SCAN_SCHEDULE mode=completed_candle_boundary "
            f"timeframe={settings.candle_primary_tf} "
            f"grace_seconds={grace_ms / 1000:.0f} wait_seconds={delay:.1f} "
            f"next_scan_utc={next_scan_utc} "
            f"max_signal_age_seconds={settings.max_signal_age_seconds}"
        )
        if delay > 0:
            await asyncio.sleep(delay)

    async def run_scan_loop(self, app: Any) -> None:
        """Run Normal and Momentum on independent candle-boundary schedules.

        A long Momentum universe scan must never hold up the next Normal scan.
        Each engine owns its own loop and cursor while sharing the bounded REST
        client and the same completed-candle/freshness rules.
        """
        # Every symbol fetches and seeds its own current candle history in _process_pair;
        # a separate serial warmup duplicated REST calls and delayed the first scan.
        await self.symbols.refresh_universes()

        # A Render restart can occur mid-candle. Do not spend the first scan
        # fetching a full universe only to reject every bar as older than the
        # 120-second signal-age limit. Start on the next usable close boundary.
        await self._wait_for_next_primary_boundary()

        tasks = []
        if settings.enable_normal_engine:
            tasks.append(asyncio.create_task(self._run_engine_loop(app, "normal")))
        if settings.enable_momentum_engine:
            tasks.append(asyncio.create_task(self._run_engine_loop(app, "momentum")))
        if tasks:
            await asyncio.gather(*tasks)

    async def _run_engine_loop(self, app: Any, engine: str) -> None:
        """Scan one strategy engine without waiting for the other engine."""
        if engine not in ("normal", "momentum"):
            raise ValueError(f"Unknown strategy engine: {engine}")

        while self._running:
            try:
                # Apply stale-feed quarantine before selecting the next scan's
                # symbols. The other engine may have observed the second stale
                # response during its previous cycle.
                self.symbols._refresh_quarantine_from_rest()
                self.symbols._select_active_symbols()

                start = utc_now()
                if engine == "normal":
                    await self._scan_normal()
                elif engine == "momentum":
                    await self._scan_momentum()

                # Universe refresh is serialized through the Normal loop; if
                # Normal is disabled, Momentum owns refresh responsibility.
                if engine == "normal" or (engine == "momentum" and not settings.enable_normal_engine):
                    await self.symbols.refresh_universes()

                finished = utc_now()
                duration_ms = int((finished - start).total_seconds() * 1000)
                interval_seconds = parse_interval(settings.candle_primary_tf).total_seconds()
                if duration_ms > interval_seconds * 1000:
                    logger.warning(
                        f"SCAN_CYCLE_OVERRUN engine={engine} duration_ms={duration_ms} "
                        f"candle_interval_seconds={interval_seconds:.0f} "
                        f"late_symbols_may_be_rejected=true"
                    )
                self.last_scan_time = finished
                state = app.state.bot_state
                state["last_scan"] = utc_iso(finished)
                state[f"last_scan_{engine}"] = utc_iso(finished)
                state[f"last_scan_duration_ms_{engine}"] = duration_ms
                state["last_scan_duration_ms"] = duration_ms
                state["scan_count"] = state.get("scan_count", 0) + 1
                state["active_signals"] = sum(manager.count() for manager in self.signals.values())
                state["watchlist_count"] = sum(manager.count() for manager in self.watchlists.values())
                state["scan_errors_normal"] = self.scan_errors["normal"]
                state["scan_errors_momentum"] = self.scan_errors["momentum"]
                state["valid_symbols_normal"] = len(self.symbols.normal_symbols)
                state["valid_symbols_momentum"] = len(self.symbols.momentum_symbols)
                state["eligible_symbols"] = len(self.symbols.valid_pairs)
                state["skipped_symbols"] = len(self.symbols.skipped_reasons)
                state["normal_engine"] = (
                    "running" if settings.enable_normal_engine and self.symbols.normal_symbols
                    else "no_symbols" if settings.enable_normal_engine else "disabled"
                )
                state["momentum_engine"] = (
                    "running" if settings.enable_momentum_engine and self.symbols.momentum_symbols
                    else "no_symbols" if settings.enable_momentum_engine else "disabled"
                )
                state["last_market_update"] = utc_iso(finished)
                state["uptime_seconds"] = int(
                    (finished - app.state.start_time).total_seconds()
                )

                if engine == "normal" or not settings.enable_normal_engine:
                    mem = check_memory(force_gc=True)
                    if mem["warning"]:
                        safe_gc()

                await self._wait_for_next_primary_boundary()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception(f"{engine.title()} scan loop error: {exc}")
                await self._wait_for_next_primary_boundary()

    async def _scan_pairs(self, pairs: List[str], engine: str) -> None:
        """Scan one engine with bounded concurrency matching the REST semaphore."""
        started = asyncio.get_running_loop().time()
        errors_before = self.scan_errors[engine]

        cycle_started_utc = utc_iso(utc_now())
        symbol_durations = []
        logger.info(
            f"SCAN_CYCLE_START engine={engine} symbols_submitted={len(pairs)} "
            f"started_at_utc={cycle_started_utc} "
            f"max_signal_age_seconds={settings.max_signal_age_seconds}"
        )

        async def process(pair: str) -> None:
            symbol_started = asyncio.get_running_loop().time()
            outcome = "ok"
            try:
                await self._process_pair(pair, engine=engine)
            except Exception as exc:
                outcome = "error"
                self.scan_errors[engine] += 1
                logger.exception(f"{engine.title()} scan failed for {pair}: {exc}")
            finally:
                elapsed_ms = int((asyncio.get_running_loop().time() - symbol_started) * 1000)
                symbol_durations.append(elapsed_ms)
                logger.info(
                    f"SCAN_SYMBOL_TIMING engine={engine} pair={pair} "
                    f"duration_ms={elapsed_ms} outcome={outcome}"
                )

        # CoinDCXRest applies a five-request semaphore. Scheduling the symbol
        # tasks concurrently lets that limit work instead of serializing markets
        # and allowing later signals to age out before evaluation.
        await asyncio.gather(*(process(pair) for pair in pairs))

        duration_ms = int((asyncio.get_running_loop().time() - started) * 1000)
        active = len(self.signals[engine].list_active())
        watching = self.watchlists[engine].count()
        max_symbol_ms = max(symbol_durations, default=0)
        avg_symbol_ms = round(sum(symbol_durations) / len(symbol_durations), 1) if symbol_durations else 0
        logger.info(
            f"SCAN_CYCLE_SUMMARY engine={engine} symbols_submitted={len(pairs)} "
            f"symbols_finished={len(symbol_durations)} "
            f"scan_errors={self.scan_errors[engine]} "
            f"errors_this_cycle={self.scan_errors[engine] - errors_before} "
            f"active_signals={active} watchlist={watching} duration_ms={duration_ms} "
            f"max_symbol_duration_ms={max_symbol_ms} "
            f"avg_symbol_duration_ms={avg_symbol_ms} "
            f"started_at_utc={cycle_started_utc} finished_at_utc={utc_iso(utc_now())}"
        )

    async def _scan_normal(self) -> None:
        await self._scan_pairs(self.symbols.normal_symbols, "normal")

    async def _scan_momentum(self) -> None:
        await self._scan_pairs(self.symbols.momentum_symbols, "momentum")

    async def _process_pair(self, pair: str, engine: str) -> None:
        pair_started = asyncio.get_running_loop().time()
        # Refresh primary TF candles
        primary = settings.candle_primary_tf
        candle_fetch_started = asyncio.get_running_loop().time()
        data = await self.rest.get_candles(pair, primary, limit=PRIMARY_CANDLE_HISTORY)
        candle_fetch_ms = (asyncio.get_running_loop().time() - candle_fetch_started) * 1000.0
        logger.info(f"SIGNAL_STAGE pair={pair} engine={engine} stage=primary_candle_fetch duration_ms={candle_fetch_ms:.1f}")
        if len(data) < 50:
            return
        self.candles.seed(pair, primary, data)
        arr = self.candles.get_arrays(pair, primary)
        if not arr or len(arr["close"]) < 50:
            return

        close = arr["close"]
        high = arr["high"]
        low = arr["low"]
        volume = arr["volume"]
        opens = arr["open"]
        # A candle timestamp is its opening time; REST client has already removed
        # unfinished bars. Process each completed bar once per engine.
        if engine not in ("normal", "momentum"):
            raise ValueError(f"Unknown strategy engine: {engine}")
        candle_ts = int(arr["time"][-1])
        primary_interval_ms = int(parse_interval(primary).total_seconds() * 1000)
        candle_close_ms = candle_ts + primary_interval_ms
        # Update active signal outcomes on every fresh closed candle before deciding
        # whether this engine has already evaluated the candle.
        await self._update_signal_outcomes(pair, engine, float(high[-1]), float(low[-1]))
        await self._check_pending_retest(pair, engine)
        if self._last_processed.setdefault(engine, {}).get(pair) == candle_ts:
            return
        initial_age = candle_age_seconds(int(utc_now().timestamp() * 1000), candle_ts, primary_interval_ms)
        if initial_age < 0 or initial_age > settings.max_signal_age_seconds:
            self._last_processed[engine][pair] = candle_ts
            logger.info(
                f"STALE_CANDLE_SKIP {pair} engine={engine} candle_open_ms={candle_ts} "
                f"candle_close_ms={candle_close_ms} age_seconds={initial_age:.1f} "
                f"max_age_seconds={settings.max_signal_age_seconds}"
            )
            return

        # Indicators
        indicator_started = asyncio.get_running_loop().time()
        ema9 = ema(close, 9)
        ema21 = ema(close, 21)
        ema50 = ema(close, 50)
        rsi_v = rsi(close, 14)
        atr_v = atr(high, low, close, 14)
        adx_res = adx(high, low, close, 14)
        adx_v = adx_res[0] if adx_res is not None else None
        plus_di = adx_res[1] if adx_res is not None else None
        minus_di = adx_res[2] if adx_res is not None else None
        # MACD is calculated only for Momentum; Normal decisions do not depend on it.
        macd_res = macd(close) if engine == "momentum" else None
        macd_hist = macd_res[2] if macd_res is not None else None
        # UTC-session anchored VWAP; avoids an arbitrary rolling 100-bar reset.
        day_start = (int(arr["time"][-1]) // 86_400_000) * 86_400_000
        day_idx = int(np.searchsorted(arr["time"], day_start, side="left"))
        vwap_v = vwap(high[day_idx:], low[day_idx:], close[day_idx:], volume[day_idx:], period=None)
        rvol = relative_volume(volume, 20)
        atr_pct = (atr_v / close[-1] * 100) if atr_v and close[-1] else None
        candle_range = max(float(high[-1] - low[-1]), 1e-12)
        signed_body = float(close[-1] - opens[-1])
        body = abs(signed_body)
        body_ratio = body / candle_range
        close_location = float((close[-1] - low[-1]) / candle_range)
        upper_wick = float((high[-1] - max(opens[-1], close[-1])) / candle_range)
        lower_wick = float((min(opens[-1], close[-1]) - low[-1]) / candle_range)
        ema9_prev = ema(close[:-1], 9)
        ema21_prev = ema(close[:-1], 21)
        adx_series = None
        adx_rising = False
        if len(close) >= 3:
            from indicators.adx import adx_series as _adx_series
            _a = _adx_series(high, low, close, 14)
            adx_series = _a[0] if _a is not None else None
            adx_rising = bool(adx_series is not None and np.isfinite(adx_series[-1]) and np.isfinite(adx_series[-2]) and adx_series[-1] > adx_series[-2])

        # 15m confirmation: require actual directional EMA alignment, price/VWAP
        # location, and EMA slope. Mere indicator availability is not confirmation.
        confirm_long = confirm_short = False
        confirm_candle_time_ms = None
        conf_data = await self.rest.get_candles(pair, settings.candle_confirm_tf, limit=CONFIRM_CANDLE_HISTORY)
        if conf_data and len(conf_data) >= 21:
            self.candles.seed(pair, settings.candle_confirm_tf, conf_data)
            c_arr = self.candles.get_arrays(pair, settings.candle_confirm_tf)
            c_close = np.asarray(c_arr.get("close", []), dtype=float)
            c_high = np.asarray(c_arr.get("high", []), dtype=float)
            c_low = np.asarray(c_arr.get("low", []), dtype=float)
            c_vol = np.asarray(c_arr.get("volume", []), dtype=float)
            c_times = np.asarray(c_arr.get("time", []), dtype=np.int64)
            confirm_interval_ms = int(parse_interval(settings.candle_confirm_tf).total_seconds() * 1000)
            # Use only context candles that had closed by the entry candle's close.
            # This prevents a REST request crossing a 15m boundary from leaking
            # a newer 15m candle into an older 5m decision.
            context_idx = latest_asof_candle_index(c_times, confirm_interval_ms, candle_close_ms)
            if context_idx is not None:
                c_close = c_close[:context_idx + 1]
                c_high = c_high[:context_idx + 1]
                c_low = c_low[:context_idx + 1]
                c_vol = c_vol[:context_idx + 1]
                c_times = c_times[:context_idx + 1]
            else:
                c_close = c_close[:0]
                c_high = c_high[:0]
                c_low = c_low[:0]
                c_vol = c_vol[:0]
                c_times = c_times[:0]
            c_ema9, c_ema21 = ema(c_close, 9), ema(c_close, 21)
            confirm_candle_time_ms = int(c_times[-1]) if c_times.size else None
            # Keep the higher-timeframe context deliberately simple: 15m EMA9/21
            # direction only. No EMA50, VWAP, or slope requirement is added.
            if c_ema9 is not None and c_ema21 is not None:
                confirm_long = c_ema9 > c_ema21
                confirm_short = c_ema9 < c_ema21

        # Market structure: last close must break the prior confirmed swing.
        structure_ok = False
        resistance = support = None
        if len(close) >= 22:
            lookback_high = high[-21:-1]
            lookback_low = low[-21:-1]
            resistance = float(np.max(lookback_high)) if lookback_high.size > 0 else None
            support = float(np.min(lookback_low)) if lookback_low.size > 0 else None
            structure_ok = bool(resistance is not None and support is not None and
                               (close[-1] > resistance or close[-1] < support))

        indicators = {
            "close": close[-1],
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "rsi": rsi_v,
            "atr": atr_v,
            "atr_pct": atr_pct,
            "adx": adx_v,
            "macd_hist": macd_hist,
            "vwap": vwap_v,
            "rvol": rvol,
            "body_ratio": body_ratio,
            "confirm_15m": False,
            "structure_ok": structure_ok,
            "high": float(high[-1]), "low": float(low[-1]),
            "resistance": resistance, "support": support,
            "plus_di": plus_di, "minus_di": minus_di,
            "adx_rising": adx_rising,
            "ema9_slope": (ema9-ema9_prev) if ema9 is not None and ema9_prev is not None else None,
            "ema21_slope": (ema21-ema21_prev) if ema21 is not None and ema21_prev is not None else None,
            "body_ratio": body_ratio, "close_location": close_location,
            "upper_wick_ratio": upper_wick, "lower_wick_ratio": lower_wick,
            "rsi_prev": rsi(close[:-1],14),
        }

        # ORB owns a fixed 5-minute Futures feed, independent of the configurable
        # Momentum primary timeframe. Its opening range is three completed 5m bars.
        orb_result = {}
        if engine == "momentum":
            try:
                orb_data = await self.rest.get_candles(pair, "5m", limit=PRIMARY_CANDLE_HISTORY)
                if len(orb_data) >= 25:
                    orb_arr = {name: np.asarray([row[name] for row in orb_data], dtype=float)
                               for name in ("open", "high", "low", "close", "volume")}
                    orb_arr["time"] = np.asarray([row["time"] for row in orb_data], dtype=np.int64)
                    orb_atr = atr(orb_arr["high"], orb_arr["low"], orb_arr["close"], 14)
                    orb_avg_volume = float(np.mean(orb_arr["volume"][-21:-1]))
                    orb_rvol = float(orb_arr["volume"][-1] / orb_avg_volume) if orb_avg_volume > 0 else None
                    orb_result = orb_detector.evaluate(orb_arr, orb_atr, orb_rvol)
            except Exception as exc:
                logger.warning(f"ORB_5M_DATA_UNAVAILABLE {pair}: {exc}")
        indicators["orb_state"] = orb_result.get("state")
        indicators["orb_range_high"] = orb_result.get("range_high")
        indicators["orb_range_low"] = orb_result.get("range_low")
        indicators["orb_breakout_level"] = orb_result.get("breakout_level")
        indicators["orb_breakout_extension_atr"] = orb_result.get("breakout_extension_atr")
        indicators["orb_range_width_atr"] = orb_result.get("range_width_atr")

        # Determine direction from EMA9/EMA21 only. VWAP is evaluated as its
        # own mandatory gate below; candle direction/body is not a gate.
        direction = None
        if engine == "normal":
            if ema9 is not None and ema21 is not None:
                if ema9 > ema21:
                    direction = "LONG"
                elif ema9 < ema21:
                    direction = "SHORT"
        else:
            # Momentum may lead EMA/MACD confirmation: short ROC seeds the
            # directional candidate. MACD remains a trade-alert gate, while
            # PRE_EXPLOSION can be observed before MACD fully turns.
            short_roc = ((float(close[-1]) / float(close[-4]) - 1.0) * 100.0
                         if len(close) >= 4 and float(close[-4]) > 0 else 0.0)
            if orb_result.get("state") in ("ORB_BREAKOUT", "ORB_RETEST"):
                direction = orb_result.get("direction")
            elif short_roc > 0:
                # ROC can seed an early watch candidate; MACD remains a hard
                # confirmation for Momentum trade alerts later in the pipeline.
                direction = "LONG"
            elif short_roc < 0:
                direction = "SHORT"

        if not direction:
            if engine == "normal":
                self.watchlists["normal"].remove_pair(pair, "normal")
            logger.info(
                f"CANDLE_EVAL pair={pair} engine={engine} direction=NONE "
                f"decision=REJECT reason=direction_alignment "
                f"candle_open_ms={candle_ts} candle_close_ms={candle_close_ms} "
                f"close={float(close[-1]):.8g} ema9={ema9} ema21={ema21} vwap={vwap_v} "
                f"body={signed_body:.8g}"
            )
            return
        # CoinDCX OHLCV does not expose aggressor-side buy/sell volume.
        # Use completed candle body direction as an explicit participation proxy.
        indicators["directional_volume_proxy_ok"] = bool(
            (direction == "LONG" and signed_body > 0) or
            (direction == "SHORT" and signed_body < 0)
        )
        indicators["macd_direction_ok"] = bool(
            macd_hist is not None and (
                (direction == "LONG" and macd_hist > 0) or
                (direction == "SHORT" and macd_hist < 0)
            )
        )
        indicators["rsi_direction_ok"] = bool(
            rsi_v is not None and (
                (direction == "LONG" and rsi_v > 50.0) or
                (direction == "SHORT" and rsi_v < 50.0)
            )
        )
        indicators["confirm_15m"] = confirm_long if direction == "LONG" else confirm_short
        indicators["structure_direction_ok"] = bool(
            (direction == "LONG" and close[-1] > (resistance or float("inf"))) or
            (direction == "SHORT" and close[-1] < (support or -float("inf"))))
        indicators["di_direction_ok"] = bool(
            plus_di is not None and minus_di is not None and
            ((direction == "LONG" and plus_di > minus_di) or
             (direction == "SHORT" and minus_di > plus_di)))
        extension = abs(float(close[-1])-float(ema9))/atr_v if atr_v and ema9 is not None else 999
        indicators["ema_extension_atr"] = extension
        indicators["not_chasing"] = extension <= 1.25
        indicators["directional_candle"] = bool(
            (direction == "LONG" and close_location >= 0.65 and signed_body > 0) or
            (direction == "SHORT" and close_location <= 0.35 and signed_body < 0))

        if engine == "normal":
            # Exactly five mandatory Normal Engine conditions. MACD and strategy labels,
            # candle shape, DI, ADX slope, EMA50 and extra 15m filters are not gates.
            strategy = detect_strategy(indicators, direction)
            if strategy == "UNCLASSIFIED":
                strategy = "EMA_VWAP_TREND"
            gates = _normal_threshold_gates(indicators, direction, settings.normal_adx_min)
            failed = [name for name, passed in gates.items() if not passed]
            if failed:
                # A previously watched setup is no longer valid once one of the
                # five core Normal conditions fails.
                self.watchlists["normal"].remove_pair(pair, "normal")
                self._log_candle_evaluation(
                    pair, engine, direction, candle_ts, candle_close_ms,
                    confirm_candle_time_ms, indicators, "REJECT", failed=failed,
                    strategy=strategy,
                )
                return
            # Relative-volume confirmation is an additional Normal-only gate.
            # It is deliberately separate from the original five mandatory gates.
            volume_gates = _normal_relative_volume_gate(
                indicators, direction, settings.normal_rvol_min
            )
            volume_failed = [name for name, passed in volume_gates.items() if not passed]
            if volume_failed:
                # Do not discard an otherwise-qualified Normal setup for weak
                # participation. Keep it visible and re-evaluate on each new
                # completed candle until RVOL reaches the configured 1.2x floor.
                self._log_candle_evaluation(
                    pair, engine, direction, candle_ts, candle_close_ms,
                    confirm_candle_time_ms, indicators, "WATCHLIST",
                    failed=volume_failed, strategy=strategy,
                )
                # Replace any prior opposite-direction watch entry; only the
                # latest qualified direction should remain visible for this pair.
                self.watchlists["normal"].remove_pair(pair, "normal")
                watch_score = self.normal_scoring.score_normal(indicators, direction)
                self.watchlists["normal"].add({
                    "pair": pair, "display": self.symbols.display_name(pair),
                    "direction": direction, "engine": "normal",
                    "strategy": strategy, "score": watch_score.total,
                    "grade": "WATCHLIST", "price": float(close[-1]),
                    "indicators": {
                        k: (round(v, 4) if isinstance(v, (float, np.floating)) else v)
                        for k, v in indicators.items() if v is not None
                    },
                    "components": [
                        {"name": item.name, "points": item.points, "max": item.max_points}
                        for item in watch_score.components
                    ],
                    "watch_reason": "WAITING_FOR_RVOL_1_2",
                    "timestamp": utc_iso(utc_now()),
                })
                self._last_processed.setdefault(engine, {})[pair] = candle_ts
                logger.info(
                    f"NORMAL_RVOL_WATCHLIST pair={pair} direction={direction} "
                    f"rvol={indicators.get('rvol')} min_rvol={settings.normal_rvol_min} "
                    f"failed={','.join(volume_failed)} "
                    f"directional_proxy_ok={indicators.get('directional_volume_proxy_ok')} "
                    "action=WAIT_FOR_NEXT_COMPLETED_CANDLE"
                )
                return
            # RVOL has recovered and directional participation agrees. Remove
            # the waiting entry before continuing through entry/risk validation.
            self.watchlists["normal"].remove_pair(pair, "normal")
            # Keep anti-chasing as a separate entry-safety rule, not an eighth
            # indicator threshold.
            if not indicators["not_chasing"]:
                self._log_candle_evaluation(
                    pair, engine, direction, candle_ts, candle_close_ms,
                    confirm_candle_time_ms, indicators, "REJECT",
                    failed=["anti_chasing"], strategy=strategy,
                )
                return
            result = self.normal_scoring.score_normal(indicators, direction)
        else:
            # Do not impose ADX before regime classification: PRE_EXPLOSION and
            # first ignition often occur before lagging ADX reaches trend thresholds.
            # ADX remains a quality feature for CONTINUATION, not an early-setup veto.
            # Momentum extras
            bb = bollinger_bands(close, 20, 2.0)
            kc = keltner_channels(high, low, close, 20, 1.5)
            squeeze_release = False
            compression_active = False
            bw_expand = False
            bw_now = bw_prev = None
            if bb is not None and kc is not None and len(close) >= 22:
                upper_bb, mid_bb, lower_bb = bb
                upper_kc, _, lower_kc = kc
                bw_now = bandwidth(upper_bb, lower_bb, mid_bb)
                prev_bb = bollinger_bands(close[:-1],20,2.0)
                prev_kc = keltner_channels(high[:-1],low[:-1],close[:-1],20,1.5)
                if prev_bb is not None and prev_kc is not None:
                    p_upper,p_mid,p_lower=prev_bb
                    p_kupper,_,p_klower=prev_kc
                    # Compression is a rolling state, not a one-candle event.
                    recent_compression = False
                    for back in range(1, min(7, len(close) - 20)):
                        hist_bb = bollinger_bands(close[:-back], 20, 2.0)
                        hist_kc = keltner_channels(high[:-back], low[:-back], close[:-back], 20, 1.5)
                        if hist_bb is None or hist_kc is None:
                            continue
                        hu, _, hl = hist_bb
                        hku, _, hkl = hist_kc
                        if hu < hku and hl > hkl:
                            recent_compression = True
                            break
                    now_released = upper_bb > upper_kc or lower_bb < lower_kc
                    compression_active = recent_compression and not now_released
                    squeeze_release = recent_compression and now_released
                    bw_prev = bandwidth(p_upper,p_lower,p_mid)
                    bw_expand = bw_now is not None and bw_prev is not None and bw_now > bw_prev * 1.03
            atr_series = [atr(high[:i+1],low[:i+1],close[:i+1],14) for i in range(max(14,len(close)-8),len(close))]
            valid_atr = [x for x in atr_series if x is not None]
            atr_expansion = (valid_atr[-1]/np.mean(valid_atr[:-1])) if len(valid_atr)>=3 and np.mean(valid_atr[:-1])>0 else 1.0
            hist_series = _macd_hist_history(close, points=4)
            macd_accel = (
                len(hist_series) >= 3
                and (
                    hist_series[-1] > hist_series[-2] > hist_series[-3]
                    if direction == "LONG"
                    else hist_series[-1] < hist_series[-2] < hist_series[-3]
                )
            )
            rsi_series = [rsi(close[:i+1],14) for i in range(max(15,len(close)-4),len(close))]
            valid_rsi = [x for x in rsi_series if x is not None]
            rsi_accel = len(valid_rsi)>=3 and ((valid_rsi[-1]>valid_rsi[-2]>valid_rsi[-3]) if direction=="LONG" else (valid_rsi[-1]<valid_rsi[-2]<valid_rsi[-3]))
            roc_v = roc(close,10)
            indicators["squeeze_release"] = squeeze_release
            indicators["compression_active"] = compression_active
            indicators["bw_expand"] = bw_expand
            indicators["atr_expansion"] = float(atr_expansion)
            indicators["ema_aligned"] = bool(ema9 is not None and ema21 is not None and ema50 is not None and
                ((direction=="LONG" and ema9>ema21>ema50) or (direction=="SHORT" and ema9<ema21<ema50)))
            indicators["macd_accel"] = bool(macd_accel)
            indicators["rsi_accel"] = bool(rsi_accel)
            indicators["structure_break"] = structure_ok
            indicators["roc"] = roc_v
            indicators["roc_direction_ok"] = roc_v is not None and ((direction=="LONG" and roc_v>0) or (direction=="SHORT" and roc_v<0))
            indicators["structure_break"] = bool(
                (direction == "LONG" and resistance is not None and close[-1] > resistance) or
                (direction == "SHORT" and support is not None and close[-1] < support))
            indicators["structure_direction_ok"] = indicators["structure_break"]
            # Evaluate the dedicated IST ORB on the same active Momentum universe.
            # ORB has its own range/volume/extension rules and shares only the
            # common risk, freshness, deduplication and delivery pipeline.
            # Momentum must be a directional expansion, not merely a high score.
            momentum_state = (
                orb_result.get("state") if orb_result.get("state") in ("ORB_BREAKOUT", "ORB_RETEST")
                and orb_result.get("direction") == direction
                else detect_momentum_state(indicators)
            )
            if momentum_state == "PRE_EXPLOSION":
                # Compression + early participation is a watch state only.
                # It must never generate a Telegram trade alert before release.
                self._log_candle_evaluation(
                    pair, engine, direction, candle_ts, candle_close_ms,
                    confirm_candle_time_ms, indicators, "WATCHLIST",
                    failed=["awaiting_volatility_release"], strategy=momentum_state,
                )
                self._last_processed.setdefault(engine, {})[pair] = candle_ts
                self.watchlists[engine].add({
                    "pair": pair, "display": self.symbols.display_name(pair),
                    "direction": direction, "engine": engine,
                    "strategy": momentum_state, "score": 0.0,
                    "grade": "WATCHLIST", "price": float(close[-1]),
                    "timestamp": utc_iso(utc_now()),
                })
                return
            valid_momentum_state = momentum_state in (
                "EXPLOSION", "SQUEEZE_RELEASE", "CONTINUATION", "ORB_BREAKOUT", "ORB_RETEST"
            )
            breakout_extension = indicators.get("orb_breakout_extension_atr")
            if momentum_state in ("ORB_BREAKOUT", "ORB_RETEST"):
                momentum_entry_actionable = (
                    breakout_extension is not None and breakout_extension <= 0.80
                    and extension <= 2.00
                )
            else:
                structure_extension = (
                    abs(float(close[-1]) - float(resistance if direction == "LONG" else support)) / atr_v
                    if structure_ok and atr_v and
                    (resistance if direction == "LONG" else support) is not None
                    else 0.0
                )
                momentum_entry_actionable = (
                    extension <= 1.75 and
                    (not indicators["structure_break"] or structure_extension <= 0.80)
                )
            momentum_gates = {
                "momentum_state": valid_momentum_state,
                "macd_direction": bool(indicators["macd_direction_ok"] or momentum_state in ("ORB_BREAKOUT", "ORB_RETEST")),
                # A 15m close, DI cross and large candle body can lag the move.
                # A structure break no longer bypasses extension protection.
                "anti_chase_and_breakout_extension": bool(momentum_entry_actionable),
            }
            failed = [name for name, passed in momentum_gates.items() if not passed]
            if failed:
                self._log_candle_evaluation(
                    pair, engine, direction, candle_ts, candle_close_ms,
                    confirm_candle_time_ms, indicators, "REJECT", failed=failed,
                    strategy=momentum_state,
                )
                return
            result = (
                self.momentum_scoring.score_orb(indicators, direction)
                if momentum_state in ("ORB_BREAKOUT", "ORB_RETEST")
                else self.momentum_scoring.score_momentum(indicators, direction)
            )
            strategy = momentum_state

        # A Normal candidate has already passed all six mandatory gates.
        # Do not label it WATCHLIST/NONE merely because optional score components
        # (RVOL, ATR, candle quality, structure) contribute fewer points.
        if engine == "normal" and result.grade in ("NONE", "WATCHLIST"):
            result.grade = "SIGNAL"

        score_parts = ",".join(
            f"{c.name}:{c.points:g}/{c.max_points:g}" for c in result.components
        )
        if engine == "momentum" and result.total < 5.5:
            self._log_candle_evaluation(
                pair, engine, direction, candle_ts, candle_close_ms,
                confirm_candle_time_ms, indicators, "REJECT",
                failed=[f"score_below_watchlist_min_5_5({result.total:.2f})"], strategy=strategy,
                score=result.total, score_parts=score_parts,
            )
            return

        if self.cooldowns[engine].is_cooling(pair, engine, direction):
            self._log_candle_evaluation(
                pair, engine, direction, candle_ts, candle_close_ms,
                confirm_candle_time_ms, indicators, "REJECT",
                failed=["cooldown"], strategy=strategy, score=result.total,
                score_parts=score_parts,
            )
            return

        swing_low = float(np.min(low[-8:-1])) if len(low) >= 8 else None
        swing_high = float(np.max(high[-8:-1])) if len(high) >= 8 else None
        if engine == "momentum" and strategy in ("ORB_BREAKOUT", "ORB_RETEST"):
            # ORB invalidation belongs beyond the opening-range opposite edge,
            # not an arbitrary last-seven-candle micro swing.
            swing_low = indicators.get("orb_range_low")
            swing_high = indicators.get("orb_range_high")
        risk = calculate_risk(close[-1], atr_v or 0, direction,
                              swing_low=swing_low, swing_high=swing_high) if atr_v else None
        strategy = strategy if engine == "normal" else momentum_state
        if not risk or min(risk.rr1, risk.rr2, risk.rr3) < 1.0:
            self._log_candle_evaluation(
                pair, engine, direction, candle_ts, candle_close_ms,
                confirm_candle_time_ms, indicators, "REJECT",
                failed=["risk_or_rr_invalid"], strategy=strategy,
                score=result.total, score_parts=score_parts,
            )
            return

        signal = {
            "pair": pair,
            "display": self.symbols.display_name(pair),
            "direction": direction,
            "engine": engine,
            "strategy": strategy,
            "score": result.total,
            "grade": result.grade,
            "price": close[-1],
            "indicators": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in indicators.items() if v is not None},
            "components": [{"name": c.name, "points": c.points, "max": c.max_points, "expl": c.explanation} for c in result.components],
            "timestamp": utc_iso(utc_now()),
            "candle_time_ms": candle_ts,
            "candle_close_time_ms": candle_close_ms,
            "signal_age_seconds": round(candle_age_seconds(int(utc_now().timestamp() * 1000), candle_ts, primary_interval_ms), 1),
            "confirm_candle_time_ms": confirm_candle_time_ms,
        }
        if risk:
            signal["risk"] = {
                "entry": risk.entry,
                "entry_zone": [risk.entry_zone_low, risk.entry_zone_high],
                "stop_loss": risk.stop_loss,
                "tp1": risk.tp1,                "tp2": risk.tp2,
                "tp3": risk.tp3,
                "risk_pct": risk.risk_pct,
                "rr": [risk.rr1, risk.rr2, risk.rr3],
                "invalidation": risk.invalidation,
            }

        # Normal signals are qualified by the six explicit gates plus separate
        # risk/entry safety checks. Scoring is informational, not a hidden gate.
        disposition = (
            "SIGNAL" if engine == "normal"
            else _score_disposition(result.total, settings.signal_threshold)
        )
        if disposition == "SIGNAL":
            detection_elapsed_ms = (asyncio.get_running_loop().time() - pair_started) * 1000.0
            detected_age = candle_age_seconds(int(utc_now().timestamp() * 1000), candle_ts, primary_interval_ms)
            logger.info(
                f"SIGNAL_DETECTED pair={pair} engine={engine} direction={direction} "
                f"strategy={strategy} candle_close_ms={candle_close_ms} "
                f"candle_age_seconds={detected_age:.1f} "
                f"pair_processing_ms={detection_elapsed_ms:.1f} "
                f"indicator_and_strategy_ms={(asyncio.get_running_loop().time() - indicator_started) * 1000.0:.1f}"
            )
            self._log_candle_evaluation(
                pair, engine, direction, candle_ts, candle_close_ms,
                confirm_candle_time_ms, indicators, "CANDIDATE",
                failed=[], strategy=strategy, score=result.total,
                score_parts=score_parts,
            )
            # Recheck immediately before claiming/sending: the scan may have
            # taken long enough for this otherwise-valid candle to go stale.
            final_age = candle_age_seconds(int(utc_now().timestamp() * 1000), candle_ts, primary_interval_ms)
            if final_age < 0 or final_age > settings.max_signal_age_seconds:
                self._last_processed.setdefault(engine, {})[pair] = candle_ts
                logger.info(
                    f"STALE_SIGNAL_SUPPRESSED {pair} engine={engine} direction={direction} "
                    f"candle_open_ms={candle_ts} age_seconds={final_age:.1f}"
                )
                return
            signal["signal_age_seconds"] = round(final_age, 1)
            # Require a fresh public ticker to remain inside the planned entry zone.
            entry_price_started = asyncio.get_running_loop().time()
            try:
                live_price = await self.rest.get_last_price(pair)
            except Exception as exc:
                logger.warning(f"ENTRY_PRICE_CHECK_FAILED {pair}: {exc}")
                self._last_processed.setdefault(engine, {})[pair] = candle_ts
                return
            logger.info(f"SIGNAL_STAGE pair={pair} engine={engine} stage=initial_entry_price_check duration_ms={(asyncio.get_running_loop().time() - entry_price_started) * 1000.0:.1f}")
            zone_low, zone_high = risk.entry_zone_low, risk.entry_zone_high
            entry_state = (
                classify_entry_state(live_price, risk.entry, zone_low, zone_high, direction)
                if live_price is not None else "EXPIRED"
            )
            if entry_state == "RETEST":
                # Preserve this qualified setup briefly. It can alert only if a
                # fresh Futures LTP returns to the original entry band without
                # touching invalidation; it is never promoted on candle close alone.
                interval_seconds = parse_interval(primary).total_seconds()
                pending_key = f"{pair}:{engine}:{direction}"
                pending_record = {
                    "signal": signal, "created_at": utc_iso(utc_now()),
                    "expires_at": utc_now().timestamp() + min(900, max(180, interval_seconds * 3)),
                    "entry": risk.entry, "zone_low": zone_low, "zone_high": zone_high,
                    "stop_loss": risk.stop_loss, "candle_ts": candle_ts,
                }
                self.pending_retests[pending_key] = pending_record
                try:
                    from database.repositories import save_pending_retest
                    if not await save_pending_retest(pending_key, pending_record):
                        self.pending_retests.pop(pending_key, None)
                        self._last_processed.setdefault(engine, {})[pair] = candle_ts
                        logger.error(
                            f"PENDING_RETEST_PERSIST_FAILED_CLOSED {pending_key}; "
                            "candidate discarded because restart-safe state was not committed"
                        )
                        return
                except Exception as exc:
                    logger.error(f"PENDING_RETEST_PERSIST_ERROR {pending_key}: {exc}")
                self._last_processed.setdefault(engine, {})[pair] = candle_ts
                logger.info(
                    f"ENTRY_RETEST_PENDING {pair} engine={engine} direction={direction} "
                    f"live_price={live_price} entry_zone={zone_low}-{zone_high} "
                    f"ttl_seconds={min(900, max(180, interval_seconds * 3)):.0f}"
                )
                return
            if entry_state != "IMMEDIATE":
                self._last_processed.setdefault(engine, {})[pair] = candle_ts
                logger.info(
                    f"ENTRY_EXPIRED_SKIP {pair} engine={engine} direction={direction} "
                    f"live_price={live_price} entry_zone={zone_low}-{zone_high}"
                )
                return
            # Recheck freshness after the extra ticker request.
            final_age = candle_age_seconds(int(utc_now().timestamp() * 1000), candle_ts, primary_interval_ms)
            if final_age < 0 or final_age > settings.max_signal_age_seconds:
                self._last_processed.setdefault(engine, {})[pair] = candle_ts
                logger.info(f"STALE_AFTER_TICKER_SKIP {pair} age_seconds={final_age:.1f}")
                return
            signal["live_price"] = live_price
            signal["entry_checked_at"] = utc_iso(utc_now())
            signal["signal_age_seconds"] = round(final_age, 1)
            signal["timestamp"] = utc_iso(utc_now())
            self._last_processed.setdefault(engine, {})[pair] = candle_ts
            if self.signals[engine].add(signal):
                # Durable claim blocks duplicate alerts after a Render restart or
                # a second worker sharing the same database.
                try:
                    from database.repositories import claim_signal_once
                    claim_started = asyncio.get_running_loop().time()
                    claimed = await claim_signal_once(signal, candle_ts, ttl_hours=4)
                    logger.info(f"SIGNAL_STAGE pair={pair} engine={engine} stage=database_claim duration_ms={(asyncio.get_running_loop().time() - claim_started) * 1000.0:.1f} claimed={claimed}")
                except Exception as exc:
                    logger.error(f"Persistent signal deduplication unavailable: {exc}")
                    claimed = False
                if not claimed:
                    self.signals[engine].invalidate(pair, engine, direction)
                    logger.warning(f"Signal suppressed: persistent duplicate/claim unavailable for {pair} {engine} {direction}")
                    return
                logger.info(
                    f"SIGNAL_CLAIMED pair={pair} engine={engine} direction={direction} "
                    f"candle_open_ms={candle_ts}"
                )
                # Recheck price and age after the durable DB claim, immediately
                # before Telegram delivery; a slow DB write must not stale the entry.
                if self.telegram:
                    delivery_price_started = asyncio.get_running_loop().time()
                    try:
                        delivery_price = await self.rest.get_last_price(pair)
                    except Exception as exc:
                        delivery_price = None
                        logger.warning(f"DELIVERY_PRICE_CHECK_FAILED {pair}: {exc}")
                    logger.info(f"SIGNAL_STAGE pair={pair} engine={engine} stage=final_delivery_price_check duration_ms={(asyncio.get_running_loop().time() - delivery_price_started) * 1000.0:.1f}")
                    delivery_age = candle_age_seconds(int(utc_now().timestamp() * 1000), candle_ts, primary_interval_ms)
                    if (
                        delivery_price is None
                        or classify_entry_state(delivery_price, risk.entry, zone_low, zone_high, direction) != "IMMEDIATE"
                        or delivery_age < 0
                        or delivery_age > settings.max_signal_age_seconds
                    ):
                        try:
                            from database.repositories import release_signal_claim
                            released = await release_signal_claim(signal, candle_ts)
                            logger.info(
                                f"SIGNAL_CLAIM_RELEASE_AFTER_VALIDATION_SKIP pair={pair} "
                                f"released={released}"
                            )
                        except Exception as exc:
                            logger.error(f"SIGNAL_CLAIM_RELEASE_FAILED {pair}: {exc}")
                        self.signals[engine].invalidate(pair, engine, direction)
                        logger.info(
                            f"DELIVERY_STALE_SKIP {pair} engine={engine} price={delivery_price} "
                            f"entry_zone={zone_low}-{zone_high} age_seconds={delivery_age:.1f}"
                        )
                        return
                    signal["live_price"] = delivery_price
                    signal["entry_checked_at"] = utc_iso(utc_now())
                    signal["signal_age_seconds"] = round(delivery_age, 1)
                    signal["timestamp"] = utc_iso(utc_now())
                # Only start cooldown after Telegram confirms at least one delivery.
                # A transient Telegram outage must not suppress this market for 30 minutes.
                delivered = False
                if self.telegram:
                    logger.info(
                        f"TELEGRAM_DELIVERY_ATTEMPT pair={pair} engine={engine} "
                        f"direction={direction} candle_open_ms={candle_ts}"
                    )
                    telegram_started = asyncio.get_running_loop().time()
                    try:
                        delivered = bool(await self.telegram.send_signal(signal))
                    except Exception as e:
                        logger.error(f"Telegram send error: {e}")
                if delivered:
                    telegram_duration_ms = (asyncio.get_running_loop().time() - telegram_started) * 1000.0 if self.telegram else 0.0
                    end_to_end_age = candle_age_seconds(int(utc_now().timestamp() * 1000), candle_ts, primary_interval_ms)
                    logger.info(
                        f"TELEGRAM_DELIVERY_CONFIRMED pair={pair} engine={engine} "
                        f"telegram_duration_ms={telegram_duration_ms:.1f} "
                        f"candle_close_to_confirmation_seconds={end_to_end_age:.1f}"
                    )
                    self.cooldowns[engine].set(pair, engine, direction)
                    logger.info(
                        f"SIGNAL_DELIVERED {result.grade} {direction} {pair} "
                        f"score={result.total} engine={engine} strategy={strategy} "
                        f"candle_open_ms={candle_ts} candle_close_ms={candle_close_ms} "
                        f"age_seconds={signal['signal_age_seconds']:.1f} "
                        f"live_price={signal.get('live_price')} "
                        f"confirm_15m_open_ms={signal.get('confirm_candle_time_ms')}"
                    )
                    persisted_signal = self.signals[engine].get(pair, engine, direction)
                    if persisted_signal:
                        try:
                            from database.repositories import save_signal, record_signal_outcome
                            if not await save_signal(persisted_signal):
                                logger.error(f"ACTIVE_SIGNAL_PERSIST_FAILED {pair} engine={engine}")
                            else:
                                logger.info(
                                    f"ACTIVE_SIGNAL_PERSISTED pair={pair} engine={engine} "
                                    f"direction={direction} candle_open_ms={candle_ts}"
                                )
                            from database.repositories import record_signal_outcome
                            await record_signal_outcome(persisted_signal, float(delivery_price), "ACTIVE")
                        except Exception as exc:
                            logger.error(f"ACTIVE_SIGNAL_PERSIST_ERROR {pair}: {exc}")
                else:
                    telegram_duration_ms = (asyncio.get_running_loop().time() - telegram_started) * 1000.0 if self.telegram else 0.0
                    logger.warning(f"TELEGRAM_DELIVERY_FAILED_TIMING pair={pair} engine={engine} telegram_duration_ms={telegram_duration_ms:.1f}")
                    self.telegram_delivery_failures += 1
                    try:
                        from database.repositories import release_signal_claim
                        await release_signal_claim(signal, candle_ts)
                    except Exception as exc:
                        logger.error(f"SIGNAL_CLAIM_RELEASE_FAILED {pair}: {exc}")
                    self.signals[engine].invalidate(pair, engine, direction)
                    logger.warning(
                        f"SIGNAL_NOT_DELIVERED {pair} engine={engine} direction={direction}; "
                        "claim released for a controlled retry"
                    )
        elif disposition == "WATCHLIST":
            self._log_candle_evaluation(
                pair, engine, direction, candle_ts, candle_close_ms,
                confirm_candle_time_ms, indicators, "WATCHLIST",
                failed=[f"signal_threshold({settings.signal_threshold:g})"],
                strategy=strategy, score=result.total, score_parts=score_parts,
            )
            self._last_processed.setdefault(engine, {})[pair] = candle_ts
            self.watchlists[engine].add(signal)
            logger.info(f"WATCHLIST {direction} {pair} score={result.total}")

    async def _check_pending_retest(self, pair: str, engine: str) -> None:
        """Promote a persisted setup only while fresh LTP is in its original zone."""
        for key, pending in list(self.pending_retests.items()):
            signal = pending.get("signal") or {}
            if signal.get("pair") != pair or signal.get("engine") != engine:
                continue
            async def remove(reason: str) -> None:
                self.pending_retests.pop(key, None)
                try:
                    from database.repositories import delete_pending_retest
                    await delete_pending_retest(key)
                except Exception as exc:
                    logger.warning(f"PENDING_RETEST_DELETE_FAILED {key}: {exc}")
                logger.info(f"PENDING_RETEST_REMOVED {key} reason={reason}")
            if utc_now().timestamp() >= float(pending.get("expires_at", 0)):
                await remove("expired")
                continue
            try:
                live_price = await self.rest.get_last_price(pair)
                self.pending_retest_checks += 1
            except Exception as exc:
                self.pending_retest_failures += 1
                logger.warning(f"RETEST_PRICE_CHECK_FAILED {pair}: {exc}")
                continue
            if live_price is None:
                continue
            try:
                price = float(live_price)
                direction = signal["direction"]
                stop = float(pending["stop_loss"])
            except (KeyError, TypeError, ValueError, OverflowError):
                await remove("invalid_payload")
                continue
            if (price <= stop if direction == "LONG" else price >= stop):
                await remove("stop_invalidated")
                continue
            if classify_entry_state(price, pending["entry"], pending["zone_low"], pending["zone_high"], direction) != "IMMEDIATE":
                continue
            try:
                delivery_price = await self.rest.get_last_price(pair)
            except Exception as exc:
                self.pending_retest_failures += 1
                logger.warning(f"RETEST_DELIVERY_PRICE_FAILED {pair}: {exc}")
                continue
            if delivery_price is None or classify_entry_state(float(delivery_price), pending["entry"], pending["zone_low"], pending["zone_high"], direction) != "IMMEDIATE":
                continue
            signal = dict(signal)
            signal.update({"live_price": float(delivery_price), "entry_type": "RETEST_CONFIRMED",
                           "entry_checked_at": utc_iso(utc_now()), "timestamp": utc_iso(utc_now())})
            try:
                from database.repositories import claim_signal_once
                claimed = await claim_signal_once(signal, int(pending["candle_ts"]), ttl_hours=4)
            except Exception as exc:
                self.pending_retest_failures += 1
                logger.error(f"RETEST_DEDUP_ERROR {key}: {exc}")
                continue
            if not claimed:
                await remove("duplicate")
                continue
            delivered = False
            if self.telegram:
                try:
                    delivered = bool(await self.telegram.send_signal(signal))
                except Exception as exc:
                    logger.error(f"RETEST_TELEGRAM_ERROR {key}: {exc}")
            if delivered:
                if self.signals[engine].add(signal):
                    self.cooldowns[engine].set(pair, engine, direction)
                    from database.repositories import save_signal, record_signal_outcome
                    active = self.signals[engine].get(pair, engine, direction) or signal
                    if not await save_signal(active):
                        logger.error(f"RETEST_ACTIVE_SIGNAL_PERSIST_FAILED {key}")
                    else:
                        logger.info(f"RETEST_ACTIVE_SIGNAL_PERSISTED {key}")
                    await record_signal_outcome(signal, float(delivery_price), "ACTIVE")
                logger.info(f"RETEST_SIGNAL_DELIVERED {key} price={delivery_price}")
            else:
                self.telegram_delivery_failures += 1
                try:
                    from database.repositories import release_signal_claim
                    await release_signal_claim(signal, int(pending["candle_ts"]))
                except Exception as exc:
                    logger.error(f"RETEST_CLAIM_RELEASE_FAILED {key}: {exc}")
                logger.warning(f"RETEST_SIGNAL_NOT_DELIVERED {key}; candidate remains retryable")
                continue
            await remove("delivered")

    def _log_candle_evaluation(
        self, pair: str, engine: str, direction: str, candle_ts: int,
        candle_close_ms: int, confirm_candle_time_ms: Optional[int],
        indicators: Dict[str, Any], decision: str, failed: List[str],
        strategy: str, score: Optional[float] = None,
        score_parts: str = "",
    ) -> None:
        """Emit a compact, timestamped audit trail for each directional candidate."""
        def value(name: str) -> str:
            item = indicators.get(name)
            if item is None:
                return "NA"
            if isinstance(item, (float, np.floating)):
                return f"{float(item):.5g}"
            return str(item)

        age = candle_age_seconds(
            int(utc_now().timestamp() * 1000), candle_ts,
            int(parse_interval(settings.candle_primary_tf).total_seconds() * 1000),
        )
        logger.info(
            f"CANDLE_EVAL pair={pair} engine={engine} direction={direction} "
            f"decision={decision} strategy={strategy} "
            f"candle_open_ms={candle_ts} candle_close_ms={candle_close_ms} "
            f"confirm_15m_open_ms={confirm_candle_time_ms} age_seconds={age:.1f} "
            f"close={value('close')} ema9={value('ema9')} ema21={value('ema21')} "
            f"ema50={value('ema50')} vwap={value('vwap')} rsi={value('rsi')} "
            f"adx={value('adx')} plus_di={value('plus_di')} minus_di={value('minus_di')} "
            f"macd_hist={value('macd_hist')} rvol={value('rvol')} atr_pct={value('atr_pct')} "
            f"ema_extension_atr={value('ema_extension_atr')} "
            f"confirm_15m={value('confirm_15m')} adx_rising={value('adx_rising')} "
            f"directional_candle={value('directional_candle')} "
            f"score={score if score is not None else 'NA'} "
            f"score_parts=[{score_parts}] failed={','.join(failed) if failed else 'none'}"
        )

    async def _update_signal_outcomes(self, pair: str, engine: str, high: float, low: float) -> None:
        """Conservatively monitor active alerts; if SL and TP touch same bar, SL wins."""
        for sig in self.signals[engine].list_active():
            if sig.get("pair") != pair:
                continue
            risk = sig.get("risk") or {}
            direction = sig.get("direction")
            sl, t1, t2, t3 = (risk.get(k) for k in ("stop_loss", "tp1", "tp2", "tp3"))
            if sl is None or t1 is None:
                continue
            sl_hit = low <= sl if direction == "LONG" else high >= sl
            try:
                from database.repositories import record_signal_outcome
                await record_signal_outcome(sig, high, "ACTIVE")
                await record_signal_outcome(sig, low, "ACTIVE")
            except Exception as exc:
                logger.warning(f"Outcome metrics update failed for {pair}: {exc}")
            if sl_hit:
                if self.signals[engine].mark_sl(pair, engine, direction):
                    try:
                        from database.repositories import update_signal_record, record_signal_outcome
                        await update_signal_record(sig, "closed_sl")
                        final_outcome = "FAILED_BREAKOUT" if str(sig.get("strategy", "")).startswith("ORB") else "STOP"
                        await record_signal_outcome(sig, float(sl), final_outcome)
                    except Exception as exc:
                        logger.error(f"Persist SL outcome failed for {pair}: {exc}")
                continue
            hits = [
                i for i, target in enumerate((t1, t2, t3), 1)
                if target is not None
                and not sig.get(f"tp{i}_hit", False)
                and (high >= target if direction == "LONG" else low <= target)
            ]
            for level in hits:
                self.signals[engine].mark_tp(pair, engine, direction, level)
            if hits:
                current = self.signals[engine].get(pair, engine, direction) or sig
                status = current.get("status", "active")
                try:
                    from database.repositories import update_signal_record, record_signal_outcome
                    await update_signal_record(current, status)
                    if 1 in hits:
                        await record_signal_outcome(current, float(t1), "TP1")
                except Exception as exc:
                    logger.error(f"Persist TP outcome failed for {pair}: {exc}")

    async def shutdown(self) -> None:
        self._running = False
        logger.info("Signal engine stopped.")