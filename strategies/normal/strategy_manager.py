"""Normal engine strategy dispatcher using real predicates."""
from typing import Dict, Any
from . import trend, breakout, pullback, reversal

def detect_strategy(indicators: Dict[str, Any], direction: str) -> str:
    # Priority: confirmed breakout, trend continuation, pullback, reversal.
    if breakout.evaluate(indicators, direction): return "BREAKOUT"
    if trend.evaluate(indicators, direction): return "TREND_CONTINUATION"
    if pullback.evaluate(indicators, direction): return "EMA_PULLBACK"
    if reversal.evaluate(indicators, direction): return "REVERSAL"
    return "UNCLASSIFIED"
