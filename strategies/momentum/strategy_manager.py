"""Momentum regime dispatcher using substantive detectors."""
from typing import Dict, Any
from . import explosion_detector, squeeze_detector, volatility_expansion, volume_expansion, momentum_indicators

def detect_momentum_state(indicators: Dict[str, Any]) -> str:
    if explosion_detector.evaluate(indicators): return "EXPLOSION"
    if squeeze_detector.evaluate(indicators): return "SQUEEZE_RELEASE"
    if volatility_expansion.evaluate(indicators) and momentum_indicators.evaluate(indicators): return "CONTINUATION"
    if volume_expansion.evaluate(indicators) and not indicators.get("ema_aligned"): return "VOLUME_SPIKE"
    if indicators.get("rvol",0)>=1.5 and indicators.get("body_ratio",0)<0.3: return "POSSIBLE_EXHAUSTION"
    return "MOMENTUM_SETUP"
