"""Central signal processing engine — Normal + Momentum, closed-candle driven."""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import settings
from utils.logger import logger
from utils.time_utils import utc_iso, utc_now
from utils.memory_monitor import check_memory, safe_gc
from exchange.coindcx_rest import CoinDCXRest
from exchange.symbol_manager import SymbolManager
from exchange.candle_builder import CandleBuilder
from core.scoring_engine import ScoringEngine
from core.cooldown_manager import CooldownManager
from core.watchlist_manager import WatchlistManager
from core.signal_manager import SignalManager
from core.risk_calculator import calculate_risk
from strategies.normal.strategy_manager import detect_strategy
from strategies.momentum.strategy_manager import detect_momentum_state
from indicators import (
    ema, rsi, atr, adx, macd, vwap, relative_volume,
    bollinger_bands, bandwidth, keltner_channels, roc,
)
import numpy as np


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
        self.scoring = ScoringEngine()
        self.cooldowns = CooldownManager(default_seconds=1800)
        self.watchlist = WatchlistManager(max_entries=50, ttl_minutes=60)
        self.signals = SignalManager(max_active=30, ttl_hours=4)
        self._scan_lock = asyncio.Lock()
        self._running = True
        self.last_scan_time: Optional[datetime] = None
        self.telegram = None
        self.scan_errors = {"normal": 0, "momentum": 0}
        self._last_processed = {}

    def set_telegram(self, tg) -> None:
        self.telegram = tg

    async def run_scan_loop(self, app: Any) -> None:
        """Main background loop — respects free-tier constraints."""
        # Initial warm-up
        await self._warmup()

        while self._running:
            try:
                async with self._scan_lock:
                    start = utc_now()
                    if settings.enable_normal_engine:
                        await self._scan_normal()
                    if settings.enable_momentum_engine:
                        await self._scan_momentum()
                    duration_ms = int((utc_now() - start).total_seconds() * 1000)
                    self.last_scan_time = utc_now()

                    # Update app state
                    state = app.state.bot_state
                    state["last_scan"] = utc_iso(self.last_scan_time)
                    state["last_scan_duration_ms"] = duration_ms
                    state["scan_count"] = state.get("scan_count", 0) + 1
                    state["active_signals"] = self.signals.count()
                    state["watchlist_count"] = self.watchlist.count()
                    state["scan_errors_normal"] = self.scan_errors["normal"]
                    state["scan_errors_momentum"] = self.scan_errors["momentum"]
                    state["uptime_seconds"] = int(
                        (utc_now() - app.state.start_time).total_seconds()
                    )
                    state["normal_engine"] = "running"
                    state["momentum_engine"] = "running"
                    state["last_market_update"] = utc_iso()

                    mem = check_memory(force_gc=True)
                    if mem["warning"]:
                        safe_gc()

                await asyncio.sleep(settings.scan_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"Scan loop error: {e}")
                await asyncio.sleep(30)

    async def _warmup(self) -> None:
        logger.info("Warming up candle buffers...")
        pairs = list(set(self.symbols.normal_symbols[:15] + self.symbols.momentum_symbols[:15]))
        for pair in pairs:
            try:
                for tf in [settings.candle_primary_tf, settings.candle_confirm_tf]:
                    data = await self.rest.get_candles(pair, tf, limit=120)
                    if data:
                        self.candles.seed(pair, tf, data)
            except Exception as e:
                logger.warning(f"Warmup failed for {pair}: {e}")
            await asyncio.sleep(0.2)  # polite
        logger.info("Warmup complete.")

    async def _scan_normal(self) -> None:
        for pair in self.symbols.normal_symbols:
            try:
                await self._process_pair(pair, engine="normal")
            except Exception as e:
                self.scan_errors["normal"] += 1
                logger.exception(f"Normal scan failed for {pair}: {e}")

    async def _scan_momentum(self) -> None:
        for pair in self.symbols.momentum_symbols:
            try:
                await self._process_pair(pair, engine="momentum")
            except Exception as e:
                self.scan_errors["momentum"] += 1
                logger.exception(f"Momentum scan failed for {pair}: {e}")

    async def _process_pair(self, pair: str, engine: str) -> None:
        # Refresh primary TF candles
        primary = settings.candle_primary_tf
        data = await self.rest.get_candles(pair, primary, limit=100)
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
        candle_key = (pair, engine)
        candle_ts = int(arr["time"][-1])
        # Update active signal outcomes on every fresh closed candle before deciding
        # whether this engine has already evaluated the candle.
        self._update_signal_outcomes(pair, engine, float(high[-1]), float(low[-1]))
        if self._last_processed.get(candle_key) == candle_ts:
            return

        # Indicators
        ema9 = ema(close, 9)
        ema21 = ema(close, 21)
        ema50 = ema(close, 50)
        rsi_v = rsi(close, 14)
        atr_v = atr(high, low, close, 14)
        adx_res = adx(high, low, close, 14)
        adx_v = adx_res[0] if adx_res is not None else None
        plus_di = adx_res[1] if adx_res is not None else None
        minus_di = adx_res[2] if adx_res is not None else None
        macd_res = macd(close)
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
        conf_data = await self.rest.get_candles(pair, settings.candle_confirm_tf, limit=80)
        if conf_data and len(conf_data) >= 55:
            self.candles.seed(pair, settings.candle_confirm_tf, conf_data)
            c_arr = self.candles.get_arrays(pair, settings.candle_confirm_tf)
            c_close = np.asarray(c_arr.get("close", []), dtype=float)
            c_high = np.asarray(c_arr.get("high", []), dtype=float)
            c_low = np.asarray(c_arr.get("low", []), dtype=float)
            c_vol = np.asarray(c_arr.get("volume", []), dtype=float)
            c_ema9, c_ema21, c_ema50 = ema(c_close, 9), ema(c_close, 21), ema(c_close, 50)
            c_times = np.asarray(c_arr.get("time", []), dtype=np.int64)
            c_day_start = (int(c_times[-1]) // 86_400_000) * 86_400_000 if len(c_times) else 0
            c_day_idx = int(np.searchsorted(c_times, c_day_start, side="left")) if len(c_times) else 0
            c_vwap = (
                vwap(c_high[c_day_idx:], c_low[c_day_idx:], c_close[c_day_idx:], c_vol[c_day_idx:], period=None)
                if c_close.size > 0 and c_high.size == c_close.size
                and c_low.size == c_close.size and c_vol.size == c_close.size
                else None
            )
            if all(v is not None for v in (c_ema9, c_ema21, c_ema50, c_vwap)) and c_close.size >= 2:
                confirm_long = c_ema9 > c_ema21 > c_ema50 and c_close[-1] > c_vwap and c_ema9 > ema(c_close[:-1],9)
                confirm_short = c_ema9 < c_ema21 < c_ema50 and c_close[-1] < c_vwap and c_ema9 < ema(c_close[:-1],9)

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

        # Determine direction
        direction = None
        if ema9 is not None and ema21 is not None and vwap_v is not None:
            if ema9 > ema21 and close[-1] > vwap_v and signed_body > 0:
                direction = "LONG"
            elif ema9 < ema21 and close[-1] < vwap_v and signed_body < 0:
                direction = "SHORT"

        if not direction:
            return
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
            # Mandatory ADX filter
            if adx_v is None or adx_v < settings.normal_adx_min:
                return
            # Hard quality gates prevent a high score from compensating for bad context.
            if not indicators["confirm_15m"] or not indicators["di_direction_ok"]:
                return
            if not indicators["adx_rising"] and (adx_v is None or adx_v < 30):
                return
            if not indicators["not_chasing"] or not indicators["directional_candle"]:
                return
            strategy = detect_strategy(indicators, direction)
            if strategy == "UNCLASSIFIED":
                return
            result = self.scoring.score_normal(indicators, direction)
        else:
            # Momentum engine uses its own configured minimum trend-strength gate.
            if adx_v is None or adx_v < settings.momentum_adx_explosion:
                return
            # Momentum extras
            bb = bollinger_bands(close, 20, 2.0)
            kc = keltner_channels(high, low, close, 20, 1.5)
            squeeze_release = False
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
                    was_squeezed = p_upper < p_kupper and p_lower > p_klower
                    now_released = upper_bb > upper_kc or lower_bb < lower_kc
                    squeeze_release = was_squeezed and now_released
                    bw_prev = bandwidth(p_upper,p_lower,p_mid)
                    bw_expand = bw_now is not None and bw_prev is not None and bw_now > bw_prev * 1.08
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
            # Momentum must be a directional expansion, not merely a high score.
            momentum_state = detect_momentum_state(indicators)
            valid_momentum_state = momentum_state in ("EXPLOSION", "SQUEEZE_RELEASE", "CONTINUATION")
            if not valid_momentum_state or not indicators["di_direction_ok"] or not indicators["directional_candle"]:
                return
            if not indicators["not_chasing"] and not indicators["structure_break"]:
                return
            result = self.scoring.score_momentum(indicators, direction)

        if result.total < 7.0:
            return

        if self.cooldowns.is_cooling(pair, engine, direction):
            return

        swing_low = float(np.min(low[-8:-1])) if len(low) >= 8 else None
        swing_high = float(np.max(high[-8:-1])) if len(high) >= 8 else None
        risk = calculate_risk(close[-1], atr_v or 0, direction,
                              swing_low=swing_low, swing_high=swing_high) if atr_v else None
        strategy = strategy if engine == "normal" else momentum_state
        if not risk or min(risk.rr1, risk.rr2, risk.rr3) < 1.0:
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
        }
        if risk:
            signal["risk"] = {
                "entry": risk.entry,
                "entry_zone": [risk.entry_zone_low, risk.entry_zone_high],
                "stop_loss": risk.stop_loss,
                "tp1": risk.tp1,
                "tp2": risk.tp2,
                "tp3": risk.tp3,
                "risk_pct": risk.risk_pct,
                "rr": [risk.rr1, risk.rr2, risk.rr3],
                "invalidation": risk.invalidation,
            }

        if result.total >= max(7.0, settings.signal_threshold):
            if self.signals.add(signal):
                self._last_processed[candle_key] = candle_ts
                self.cooldowns.set(pair, engine, direction)
                logger.info(f"SIGNAL {result.grade} {direction} {pair} score={result.total} engine={engine} strategy={strategy}")
                # Push to Telegram
                if self.telegram:
                    try:
                        await self.telegram.send_signal(signal)
                    except Exception as e:
                        logger.error(f"Telegram send error: {e}")
        elif result.total >= 6.0:
            self._last_processed[candle_key] = candle_ts
            self.watchlist.add(signal)
            logger.info(f"WATCHLIST {direction} {pair} score={result.total}")

    def _update_signal_outcomes(self, pair: str, engine: str, high: float, low: float) -> None:
        """Conservatively monitor active alerts; if SL and TP touch same bar, SL wins."""
        for sig in self.signals.list_active(engine):
            if sig.get("pair") != pair:
                continue
            risk = sig.get("risk") or {}
            direction = sig.get("direction")
            sl, t1, t2, t3 = (risk.get(k) for k in ("stop_loss", "tp1", "tp2", "tp3"))
            if sl is None or t1 is None:
                continue
            sl_hit = low <= sl if direction == "LONG" else high >= sl
            if sl_hit:
                self.signals.mark_sl(pair, engine, direction)
                continue
            hits = [i for i, target in enumerate((t1, t2, t3), 1)
                    if target is not None and (high >= target if direction == "LONG" else low <= target)]
            for level in hits:
                self.signals.mark_tp(pair, engine, direction, level)

    async def shutdown(self) -> None:
        self._running = False
        logger.info("Signal engine stopped.")
