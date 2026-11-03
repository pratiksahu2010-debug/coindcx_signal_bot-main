"""Momentum regime dispatcher with separate pre-explosion watch state."""
from typing import Dict, Any
from . import explosion_detector, squeeze_detector, volatility_expansion, volume_expansion, momentum_indicators

def detect_momentum_state(indicators: Dict[str, Any]) -> str:
    if explosion_detector.evaluate(indicators):
        return "EXPLOSION"
    if squeeze_detector.evaluate(indicators):
        return "SQUEEZE_RELEASE"
    if squeeze_detector.pre_explosion(indicators):
        return "PRE_EXPLOSION"
    if volatility_expansion.evaluate(indicators) and momentum_indicators.evaluate(indicators):
        return "CONTINUATION"
    if volume_expansion.evaluate(indicators) and indicators.get("macd_direction_ok"):
        return "VOLUME_SPIKE"
    if indicators.get("rvol", 0) >= 1.5 and indicators.get("body_ratio", 0) < 0.3:
        return "POSSIBLE_EXHAUSTION"
    return "MOMENTUM_SETUP"
