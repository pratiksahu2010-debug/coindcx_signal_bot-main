"""Universal 10-point scoring system with independent engine weights."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from utils.logger import logger
from config import settings

@dataclass
class ScoreComponent:
    name: str
    points: float
    max_points: float
    value: Any
    direction: str  # bullish / bearish / neutral
    explanation: str
    valid: bool = True


@dataclass
class ScoreResult:
    total: float
    max_total: float = 10.0
    components: List[ScoreComponent] = field(default_factory=list)
    grade: str = "NONE"
    direction: str = "NEUTRAL"

    def __post_init__(self):
        if self.total >= 9.0:
            self.grade = "HIGH-CONVICTION"
        elif self.total >= 8.0:
            self.grade = "STRONG"
        elif self.total >= 7.0:
            self.grade = "SIGNAL"
        elif self.total >= 6.0:
            self.grade = "WATCHLIST"
        else:
            self.grade = "NONE"


class ScoringEngine:
    """Normal engine default weights (sum = 10)."""

    NORMAL_WEIGHTS = {
        "ema_trend": 1.0,
        "vwap": 1.0,
        "adx": 1.0,
        "rsi": 1.0,
        "macd": 1.0,
        "rvol": 1.0,
        "atr": 1.0,
        "candle": 1.0,
        "confirm_15m": 1.0,
        "structure": 1.0,
    }

    MOMENTUM_WEIGHTS = {
        "squeeze_release": 1.5,
        "atr_expansion": 1.2,
        "rvol_surge": 1.2,
        "ema_alignment": 1.0,
        "macd_accel": 1.0,
        "rsi_accel": 0.8,
        "candle_body": 1.0,
        "structure_break": 1.0,
        "bandwidth_expand": 0.8,
        "roc": 0.5,
    }

    def score_normal(self, indicators: Dict[str, Any], direction: str) -> ScoreResult:
        comps: List[ScoreComponent] = []
        w = self.NORMAL_WEIGHTS
        is_long = direction == "LONG"

        # EMA trend
        ema9, ema21 = indicators.get("ema9"), indicators.get("ema21")
        pts = 0.0
        expl = "Insufficient data"
        if ema9 is not None and ema21 is not None:
            bull = ema9 > ema21
            if (is_long and bull) or (not is_long and not bull):
                pts = w["ema_trend"]
                expl = f"EMA9 {'>' if bull else '<'} EMA21"
            else:
                expl = f"EMA9 {'>' if bull else '<'} EMA21 (against)"
        comps.append(ScoreComponent("EMA Trend", pts, w["ema_trend"], (ema9, ema21), direction, expl, pts > 0))

        # VWAP
        price, vwap_v = indicators.get("close"), indicators.get("vwap")
        pts = 0.0
        expl = "N/A"
        if price and vwap_v:
            above = price > vwap_v
            if (is_long and above) or (not is_long and not above):
                pts = w["vwap"]
                expl = f"Price {'above' if above else 'below'} VWAP"
            else:
                expl = f"Price {'above' if above else 'below'} VWAP (against)"
        comps.append(ScoreComponent("VWAP", pts, w["vwap"], vwap_v, direction, expl, pts > 0))

        # ADX
        adx_v = indicators.get("adx")
        pts = 0.0
        expl = "N/A"
        if adx_v is not None:
            if adx_v >= max(settings.normal_adx_min + 7, 25):
                pts = w["adx"]
                expl = f"ADX {adx_v:.1f} ≥ {max(settings.normal_adx_min + 7, 25):.1f} (strong)"
            elif adx_v >= settings.normal_adx_min:
                pts = w["adx"] * 0.5
                expl = f"ADX {adx_v:.1f} ≥ {settings.normal_adx_min:.1f} (moderate)"
            else:
                expl = f"ADX {adx_v:.1f} weak"
        comps.append(ScoreComponent("ADX", pts, w["adx"], adx_v, direction, expl, pts > 0))

        # RSI(14) directional threshold matches the Normal Engine gate.
        # Strong readings above 70 / below 30 remain valid momentum readings;
        # they are not automatically rejected as overbought/oversold.
        rsi_v = indicators.get("rsi")
        pts = 0.0
        expl = "N/A"
        if rsi_v is not None:
            aligned = (rsi_v > 50.0) if is_long else (rsi_v < 50.0)
            if aligned:
                pts = w["rsi"]
                expl = f"RSI {rsi_v:.1f} {'bullish' if is_long else 'bearish'} (>50 / <50)"
            else:
                expl = f"RSI {rsi_v:.1f} against direction"
        comps.append(ScoreComponent("RSI", pts, w["rsi"], rsi_v, direction, expl, pts > 0))

        # MACD
        macd_h = indicators.get("macd_hist")
        pts = 0.0
        expl = "N/A"
        if macd_h is not None:
            if (is_long and macd_h > 0) or (not is_long and macd_h < 0):
                pts = w["macd"]
                expl = f"MACD hist {macd_h:.4f} aligned"
            else:
                expl = f"MACD hist {macd_h:.4f} against"
        comps.append(ScoreComponent("MACD", pts, w["macd"], macd_h, direction, expl, pts > 0))

        # RVOL
        rvol = indicators.get("rvol")
        pts = 0.0
        expl = "N/A"
        if rvol is not None:
            if rvol >= 1.5:
                pts = w["rvol"]
                expl = f"RVOL {rvol:.2f}x strong"
            elif rvol >= 1.2:
                pts = w["rvol"] * 0.7
                expl = f"RVOL {rvol:.2f}x ok"
            else:
                expl = f"RVOL {rvol:.2f}x low"
        comps.append(ScoreComponent("RVOL", pts, w["rvol"], rvol, direction, expl, pts > 0))

        # ATR quality
        atr_pct = indicators.get("atr_pct")
        pts = 0.0
        expl = "N/A"
        if atr_pct is not None:
            if atr_pct >= 0.5:
                pts = w["atr"]
                expl = f"ATR {atr_pct:.2f}% good movement"
            elif atr_pct >= 0.3:
                pts = w["atr"] * 0.6
                expl = f"ATR {atr_pct:.2f}% moderate"
            else:
                expl = f"ATR {atr_pct:.2f}% low volatility"
        comps.append(ScoreComponent("ATR", pts, w["atr"], atr_pct, direction, expl, pts > 0))

        # Candle strength
        body_ratio = indicators.get("body_ratio")
        pts = 0.0
        expl = "N/A"
        if body_ratio is not None:
            if body_ratio >= 0.6:
                pts = w["candle"]
                expl = f"Strong body {body_ratio:.2f}"
            elif body_ratio >= 0.4:
                pts = w["candle"] * 0.5
                expl = f"Moderate body {body_ratio:.2f}"
            else:
                expl = f"Weak body {body_ratio:.2f}"
        comps.append(ScoreComponent("Candle", pts, w["candle"], body_ratio, direction, expl, pts > 0))

        # 15m confirmation
        conf = indicators.get("confirm_15m", False)
        pts = w["confirm_15m"] if conf else 0.0
        expl = "15m trend confirmed" if conf else "15m not confirmed"
        comps.append(ScoreComponent("15m Confirm", pts, w["confirm_15m"], conf, direction, expl, conf))

        # Structure
        struct = indicators.get("structure_direction_ok", False)
        pts = w["structure"] if struct else 0.0
        expl = "Market structure aligned" if struct else "Structure weak"
        comps.append(ScoreComponent("Structure", pts, w["structure"], struct, direction, expl, struct))

        total = sum(c.points for c in comps)
        return ScoreResult(total=round(total, 2), components=comps, direction=direction)

    def score_momentum(self, indicators: Dict[str, Any], direction: str) -> ScoreResult:
        comps: List[ScoreComponent] = []
        w = self.MOMENTUM_WEIGHTS
        is_long = direction == "LONG"

        # Squeeze release
        squeeze = indicators.get("squeeze_release", False)
        pts = w["squeeze_release"] if squeeze else 0.0
        comps.append(ScoreComponent("Squeeze Release", pts, w["squeeze_release"], squeeze, direction,
                                    "BB outside KC / expansion" if squeeze else "Still compressed", squeeze))

        # ATR expansion
        atr_exp = indicators.get("atr_expansion", 0.0)
        pts = min(w["atr_expansion"], atr_exp * w["atr_expansion"]) if atr_exp > 1.1 else 0.0
        comps.append(ScoreComponent("ATR Expansion", pts, w["atr_expansion"], atr_exp, direction,
                                    f"ATR ratio {atr_exp:.2f}", pts > 0))

        # RVOL surge
        rvol = indicators.get("rvol") or 0.0
        pts = w["rvol_surge"] if rvol >= 2.0 else (w["rvol_surge"] * 0.6 if rvol >= 1.5 else 0.0)
        comps.append(ScoreComponent("RVOL Surge", pts, w["rvol_surge"], rvol, direction,
                                    f"RVOL {rvol:.2f}x", pts > 0))

        # EMA alignment
        ema_ok = indicators.get("ema_aligned", False)
        pts = w["ema_alignment"] if ema_ok else 0.0
        comps.append(ScoreComponent("EMA Align", pts, w["ema_alignment"], ema_ok, direction,
                                    "EMA 9/21/50 aligned" if ema_ok else "EMA not aligned", ema_ok))

        # MACD accel
        macd_acc = indicators.get("macd_accel", False)
        pts = w["macd_accel"] if macd_acc else 0.0
        comps.append(ScoreComponent("MACD Accel", pts, w["macd_accel"], macd_acc, direction,
                                    "Histogram accelerating" if macd_acc else "No accel", macd_acc))

        # RSI accel
        rsi_acc = indicators.get("rsi_accel", False)
        pts = w["rsi_accel"] if rsi_acc else 0.0
        comps.append(ScoreComponent("RSI Accel", pts, w["rsi_accel"], rsi_acc, direction,
                                    "RSI accelerating" if rsi_acc else "No RSI accel", rsi_acc))

        # Candle body
        body = indicators.get("body_ratio") or 0.0
        pts = w["candle_body"] if body >= 0.65 else (w["candle_body"] * 0.5 if body >= 0.45 else 0.0)
        comps.append(ScoreComponent("Candle Body", pts, w["candle_body"], body, direction,
                                    f"Body/ATR {body:.2f}", pts > 0))

        # Structure break
        brk = indicators.get("structure_break", False)
        pts = w["structure_break"] if brk else 0.0
        comps.append(ScoreComponent("Structure Break", pts, w["structure_break"], brk, direction,
                                    "Swing break confirmed" if brk else "No break", brk))

        # Bandwidth expand
        bw_exp = indicators.get("bw_expand", False)
        pts = w["bandwidth_expand"] if bw_exp else 0.0
        comps.append(ScoreComponent("BW Expand", pts, w["bandwidth_expand"], bw_exp, direction,
                                    "Bandwidth expanding" if bw_exp else "BW flat", bw_exp))

        # ROC
        roc_v = indicators.get("roc") or 0.0
        pts = w["roc"] if (is_long and roc_v > 1) or (not is_long and roc_v < -1) else 0.0
        comps.append(ScoreComponent("ROC", pts, w["roc"], roc_v, direction, f"ROC {roc_v:.2f}%", pts > 0))

        total = sum(c.points for c in comps)
        # Normalize to max 10
        max_possible = sum(w.values())
        scaled = (total / max_possible) * 10.0 if max_possible else 0.0
        return ScoreResult(total=round(scaled, 2), components=comps, direction=direction)
