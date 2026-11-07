"""Universal 10-point scoring system with independent engine weights."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import math


_NUMERIC_BOUNDS = {
    "ema9": (0.0, None), "ema21": (0.0, None),
    "close": (0.0, None), "vwap": (0.0, None),
    "adx": (0.0, 100.0), "rsi": (0.0, 100.0), "rsi_prev": (0.0, 100.0),
    "rvol": (0.0, None), "atr_pct": (0.0, None),
    "body_ratio": (0.0, 1.0), "atr_expansion": (0.0, None),
    "close_location": (0.0, 1.0),
    "orb_breakout_extension_atr": (0.0, None),
    "orb_range_width_atr": (0.0, None), "roc": (None, None),
    "plus_di": (0.0, 100.0), "minus_di": (0.0, 100.0),
    "upper_wick_ratio": (0.0, 1.0), "lower_wick_ratio": (0.0, 1.0),
}
_BOOLEAN_KEYS = {
    "di_direction_ok", "adx_rising", "confirm_15m", "structure_direction_ok",
    "squeeze_release", "bw_expand", "ema_aligned", "macd_accel",
    "macd_direction_ok", "rsi_accel", "structure_break",
    "roc_direction_ok", "orb_trend_confirmed", "orb_di_aligned",
    "orb_adx_rising", "orb_wick_quality",
}


def _validated_indicators(indicators: Dict[str, Any]) -> Dict[str, Any]:
    """Copy indicator data and fail closed on malformed/non-finite values."""
    clean = dict(indicators) if isinstance(indicators, dict) else {}
    for key, (minimum, maximum) in _NUMERIC_BOUNDS.items():
        raw = clean.get(key)
        if raw is None:
            continue
        try:
            if isinstance(raw, bool):
                raise ValueError("boolean is not a numeric indicator")
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            clean[key] = None
            continue
        if (
            not math.isfinite(value)
            or (minimum is not None and value < minimum)
            or (maximum is not None and value > maximum)
        ):
            clean[key] = None
        else:
            clean[key] = value

    for key in _BOOLEAN_KEYS:
        value = clean.get(key)
        if value is None and key in ("di_direction_ok", "orb_di_aligned"):
            # Preserve the tri-state meaning: unavailable DI is not opposition.
            clean[key] = None
            continue
        # Accept native and NumPy booleans, but never truthy strings or numbers.
        clean[key] = value is True or type(value).__name__ == "bool_" and bool(value)
    return clean


def _validate_direction(direction: str) -> None:
    if direction not in ("LONG", "SHORT"):
        raise ValueError("direction must be LONG or SHORT")
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
        # Nine non-MACD quality dimensions retain the original 10-point scale.
        "ema_trend": 10.0 / 9.0,
        "vwap": 10.0 / 9.0,
        "adx": 10.0 / 9.0,
        "rsi": 10.0 / 9.0,
        "rvol": 10.0 / 9.0,
        "atr": 10.0 / 9.0,
        "candle": 10.0 / 9.0,
        "confirm_15m": 10.0 / 9.0,
        "structure": 10.0 / 9.0,
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
        _validate_direction(direction)
        indicators = _validated_indicators(indicators)
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
            di_aligned = indicators.get("di_direction_ok")
            if adx_v >= max(settings.normal_adx_min + 7, 25):
                pts = w["adx"]
                expl = f"ADX {adx_v:.1f} ≥ {max(settings.normal_adx_min + 7, 25):.1f} (strong)"
            elif adx_v >= settings.normal_adx_min:
                pts = w["adx"] * 0.5
                expl = f"ADX {adx_v:.1f} ≥ {settings.normal_adx_min:.1f} (moderate)"
            else:
                expl = f"ADX {adx_v:.1f} weak"
            # DI is a quality modifier, not a hard gate, so early turns are
            # not discarded solely because +DI/-DI has not crossed yet.
            if pts > 0 and di_aligned is False:
                pts *= 0.5
                expl += "; DI not aligned (reduced score)"
            elif pts > 0 and di_aligned is True:
                expl += "; DI aligned"
            if pts > 0 and indicators.get("adx_rising") is True:
                pts = min(w["adx"], pts + w["adx"] * 0.15)
                expl += "; ADX rising"
        comps.append(ScoreComponent("ADX", pts, w["adx"], adx_v, direction, expl, pts > 0))

        # RSI(14) directional threshold matches the Normal Engine gate.
        # Strong readings above 70 / below 30 remain valid momentum readings;
        # they are not automatically rejected as overbought/oversold.
        rsi_v = indicators.get("rsi")
        pts = 0.0
        expl = "N/A"
        if rsi_v is not None:
            aligned = (rsi_v > 50.0) if is_long else (rsi_v < 50.0)
            rsi_previous = indicators.get("rsi_prev")
            reversal_aligned = (
                rsi_previous is not None
                and ((rsi_v < 40.0 and rsi_v >= rsi_previous) if is_long
                     else (rsi_v > 60.0 and rsi_v <= rsi_previous))
            )
            if aligned:
                pts = w["rsi"]
                expl = f"RSI {rsi_v:.1f} {'bullish' if is_long else 'bearish'} (>50 / <50)"
            elif reversal_aligned:
                pts = w["rsi"]
                expl = (
                    f"RSI reversal improving from extreme ({rsi_previous:.1f} → {rsi_v:.1f})"
                )
            else:
                expl = f"RSI {rsi_v:.1f} against direction"
        comps.append(ScoreComponent("RSI", pts, w["rsi"], rsi_v, direction, expl, pts > 0))

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

    def score_momentum_explosion(self, indicators: Dict[str, Any], direction: str) -> ScoreResult:
        """Score an already-qualified early ignition without demanding lagging confirmation.

        The detector has already enforced minimum ATR expansion, RVOL, candle body,
        close location, extension and one valid ignition pattern. Those core
        conditions receive the 7-point baseline; additional structure/squeeze/
        bandwidth/trend evidence improves the grade rather than vetoing ignition.
        """
        _validate_direction(direction)
        indicators = _validated_indicators(indicators)
        is_long = direction == "LONG"
        components: List[ScoreComponent] = []

        structure = bool(indicators.get("structure_break"))
        squeeze = bool(indicators.get("squeeze_release"))
        bandwidth_expanding = bool(indicators.get("bw_expand"))
        ema_aligned = bool(indicators.get("ema_aligned"))
        roc_aligned = bool(indicators.get("roc_direction_ok"))
        macd_accel = bool(indicators.get("macd_accel"))
        ignition = structure or squeeze or (
            bandwidth_expanding and (roc_aligned or macd_accel)
        ) or (
            ema_aligned and roc_aligned and (
                indicators.get("rsi_accel") or indicators.get("macd_direction_ok") or macd_accel
            )
        ) or (
            float(indicators.get("rvol") or 0.0) >= 1.80
            and roc_aligned
        )

        components.append(ScoreComponent(
            "Ignition Evidence", 2.0 if ignition else 0.0, 2.0, ignition,
            direction, "Valid early ignition pattern" if ignition else "No ignition pattern", ignition
        ))

        atr_expansion = float(indicators.get("atr_expansion") or 0.0)
        components.append(ScoreComponent(
            "ATR Expansion", 1.5 if atr_expansion >= 1.08 else 0.0, 1.5,
            atr_expansion, direction, f"ATR expansion {atr_expansion:.2f}x", atr_expansion >= 1.08
        ))

        rvol = float(indicators.get("rvol") or 0.0)
        components.append(ScoreComponent(
            "Relative Volume", 1.5 if rvol >= 1.30 else 0.0, 1.5,
            rvol, direction, f"RVOL {rvol:.2f}x", rvol >= 1.30
        ))

        body = float(indicators.get("body_ratio") or 0.0)
        components.append(ScoreComponent(
            "Candle Body", 1.25 if body >= 0.45 else 0.0, 1.25,
            body, direction, f"Body ratio {body:.2f}", body >= 0.45
        ))

        raw_location = indicators.get("close_location")
        location = float(0.5 if raw_location is None else raw_location)
        aligned_location = location if is_long else 1.0 - location
        components.append(ScoreComponent(
            "Directional Close", 0.75 if aligned_location >= 0.58 else 0.0, 0.75,
            aligned_location, direction, f"Directional close {aligned_location:.2f}",
            aligned_location >= 0.58
        ))

        components.append(ScoreComponent(
            "Structure Bonus", 0.75 if structure else 0.0, 0.75,
            structure, direction, "Swing structure broken" if structure else "No swing break", structure
        ))
        components.append(ScoreComponent(
            "Squeeze Bonus", 0.75 if squeeze else 0.0, 0.75,
            squeeze, direction, "Squeeze released" if squeeze else "No squeeze release", squeeze
        ))
        components.append(ScoreComponent(
            "Bandwidth Expansion", 1.0 if bandwidth_expanding else 0.0, 1.0,
            bandwidth_expanding, direction,
            "Bollinger bandwidth expanding" if bandwidth_expanding else "Bandwidth not expanding",
            bandwidth_expanding
        ))
        trend_confirmation = ema_aligned or roc_aligned or macd_accel
        components.append(ScoreComponent(
            "Early Trend Confirmation", 0.5 if trend_confirmation else 0.0, 0.5,
            trend_confirmation, direction,
            "EMA/ROC/MACD supports direction" if trend_confirmation else "Early trend confirmation absent",
            trend_confirmation
        ))

        total = sum(component.points for component in components)
        return ScoreResult(total=round(total, 2), components=components, direction=direction)

    def score_orb(self, indicators: Dict[str, Any], direction: str) -> ScoreResult:
        """Score ORB quality on a fixed 10-point scale."""
        _validate_direction(direction)
        indicators = _validated_indicators(indicators)
        is_long = direction == "LONG"
        components: List[ScoreComponent] = []

        rvol = float(indicators.get("rvol") or 0.0)
        volume_points = 1.25 if rvol >= 1.8 else (1.0 if rvol >= 1.5 else 0.75)
        components.append(ScoreComponent("ORB Volume", volume_points, 1.25, rvol,
            direction, f"RVOL {rvol:.2f}x", rvol >= 1.30))

        body = float(indicators.get("body_ratio") or 0.0)
        body_points = 1.0 if body >= 0.65 else (0.70 if body >= 0.45 else 0.0)
        components.append(ScoreComponent("ORB Candle", body_points, 1.0, body,
            direction, f"Body ratio {body:.2f}", body >= 0.45))

        raw_location = indicators.get("close_location", 0.5)
        location = float(0.5 if raw_location is None else raw_location)
        aligned_location = location if is_long else 1.0 - location
        location_points = 1.25 if aligned_location >= 0.80 else (0.95 if aligned_location >= 0.65 else 0.0)
        components.append(ScoreComponent("ORB Close Location", location_points, 1.25,
            aligned_location, direction, f"Directional close location {aligned_location:.2f}",
            aligned_location >= 0.62))

        raw_extension = indicators.get("orb_breakout_extension_atr")
        extension = float(99.0 if raw_extension is None else raw_extension)
        extension_points = 2.0 if extension <= 0.35 else (1.50 if extension <= 0.55 else 1.0)
        components.append(ScoreComponent("ORB Entry Distance", extension_points, 2.0,
            extension, direction, f"Breakout extension {extension:.2f} ATR", extension <= 0.80))

        width = float(indicators.get("orb_range_width_atr") or 0.0)
        width_points = 0.75 if 0.25 <= width <= 1.50 else (0.35 if width > 0 else 0.0)
        components.append(ScoreComponent("ORB Range Quality", width_points, 0.75,
            width, direction, f"Opening range {width:.2f} ATR", width > 0))

        ema_ok = bool(indicators.get("ema_aligned"))
        components.append(ScoreComponent("EMA Context", 0.75 if ema_ok else 0.0, 0.75,
            ema_ok, direction, "EMA aligned" if ema_ok else "EMA not aligned", ema_ok))

        macd_ok = bool(indicators.get("macd_direction_ok"))
        components.append(ScoreComponent("MACD Context", 0.75 if macd_ok else 0.0, 0.75,
            macd_ok, direction, "MACD aligned" if macd_ok else "MACD not aligned", macd_ok))

        roc_ok = bool(indicators.get("roc_direction_ok"))
        components.append(ScoreComponent("ROC Context", 0.75 if roc_ok else 0.0, 0.75,
            roc_ok, direction, "ROC aligned" if roc_ok else "ROC not aligned", roc_ok))

        trend_ok = bool(indicators.get("orb_trend_confirmed"))
        adx_rising = bool(indicators.get("orb_adx_rising"))
        trend_points = 0.75 if trend_ok and adx_rising else (0.55 if trend_ok else 0.0)
        components.append(ScoreComponent("ADX + DI Context", trend_points, 0.75,
            {"adx": indicators.get("adx"), "di_aligned": indicators.get("orb_di_aligned"),
             "adx_rising": adx_rising}, direction,
            ("ADX strong and DI aligned" + ("; ADX rising" if adx_rising else ""))
            if trend_ok else "ADX/DI confirmation unavailable or not aligned", trend_ok))

        wick_ok = bool(indicators.get("orb_wick_quality"))
        components.append(ScoreComponent("Opposing Wick Quality", 0.75 if wick_ok else 0.0, 0.75,
            wick_ok, direction, "Opposing wick controlled" if wick_ok else "Opposing wick too large",
            wick_ok))

        total = round(sum(item.points for item in components), 2)
        return ScoreResult(total=total, components=components, direction=direction)

    def score_momentum(self, indicators: Dict[str, Any], direction: str) -> ScoreResult:
        _validate_direction(direction)
        indicators = _validated_indicators(indicators)
        comps: List[ScoreComponent] = []
        w = self.MOMENTUM_WEIGHTS
        is_long = direction == "LONG"

        # Squeeze release
        squeeze = indicators.get("squeeze_release", False)
        pts = w["squeeze_release"] if squeeze else 0.0
        comps.append(ScoreComponent("Squeeze Release", pts, w["squeeze_release"], squeeze, direction,
                                    "BB outside KC / expansion" if squeeze else "Still compressed", squeeze))

        # ATR expansion
        atr_exp = indicators.get("atr_expansion") or 0.0
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
        macd_acc = bool(indicators.get("macd_accel", False))
        macd_direction = bool(indicators.get("macd_direction_ok", False))
        pts = w["macd_accel"] if macd_acc else (w["macd_accel"] * 0.5 if macd_direction else 0.0)
        macd_expl = (
            "Histogram accelerating" if macd_acc
            else "MACD direction aligned" if macd_direction
            else "MACD not directionally aligned"
        )
        comps.append(ScoreComponent("MACD Accel", pts, w["macd_accel"],
                                    macd_acc or macd_direction, direction, macd_expl, pts > 0))

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
