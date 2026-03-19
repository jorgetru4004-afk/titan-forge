"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                    futures_context.py — FORGE-23 — Layer 2                  ║
║  FUTURES CONTEXT INTEGRATION                                                 ║
║  Receives overnight ES/NQ levels from TITAN PRIME.                          ║
║  Used as primary session bias filter.                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger("titan_forge.futures_context")


@dataclass
class FuturesContext:
    """Overnight futures levels from TITAN PRIME."""
    instrument:          str       # "ES", "NQ"
    prior_close:         float
    overnight_high:      float
    overnight_low:       float
    current:             float
    overnight_vwap:      float
    # Derived
    overnight_range:     float
    overnight_pct:       float     # % change from prior close
    direction:           str       # "bullish" / "bearish" / "neutral"
    above_overnight_vwap: bool
    key_levels:          list[float]   # Support/resistance levels to watch
    # Session bias
    session_bias_score:  float     # -1.0 (bearish) to +1.0 (bullish)
    bias_label:          str       # "STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR"
    updated_at:          datetime

    @property
    def gap_pct(self) -> float:
        return abs(self.overnight_pct)

    @property
    def is_gap_day(self) -> bool:
        return self.gap_pct >= 0.003   # 0.3%+ gap


def build_futures_context(
    instrument:     str,
    prior_close:    float,
    overnight_high: float,
    overnight_low:  float,
    current:        float,
    overnight_vwap: float,
    updated_at:     Optional[datetime] = None,
) -> FuturesContext:
    """Build a FuturesContext from raw TITAN PRIME data."""
    from datetime import timezone
    now = updated_at or datetime.now(timezone.utc)

    overnight_range = overnight_high - overnight_low
    overnight_pct   = (current - prior_close) / prior_close if prior_close > 0 else 0.0
    direction       = "bullish" if overnight_pct > 0.001 else "bearish" if overnight_pct < -0.001 else "neutral"
    above_vwap      = current > overnight_vwap

    # Key levels: overnight high/low + VWAP + prior close
    key_levels = sorted(set([
        prior_close, overnight_high, overnight_low,
        overnight_vwap, current,
    ]))

    # Session bias score
    score = 0.0
    if direction == "bullish":
        score += 0.4
    elif direction == "bearish":
        score -= 0.4

    if above_vwap:
        score += 0.3 if direction == "bullish" else 0.1
    else:
        score -= 0.3 if direction == "bearish" else 0.1

    # Volume implied by range vs typical
    range_pct = overnight_range / prior_close if prior_close > 0 else 0.0
    if range_pct > 0.01:   # Wide range = more conviction
        score = score * 1.2

    score = max(-1.0, min(1.0, score))

    if score >= 0.6:
        bias_label = "STRONG_BULL"
    elif score >= 0.2:
        bias_label = "BULL"
    elif score <= -0.6:
        bias_label = "STRONG_BEAR"
    elif score <= -0.2:
        bias_label = "BEAR"
    else:
        bias_label = "NEUTRAL"

    logger.info(
        "[FORGE-23] %s Futures Context: %s | Overnight: %+.2f%% | Bias: %s (%.2f)",
        instrument, direction, overnight_pct * 100, bias_label, score,
    )

    return FuturesContext(
        instrument=instrument,
        prior_close=prior_close,
        overnight_high=overnight_high,
        overnight_low=overnight_low,
        current=current,
        overnight_vwap=overnight_vwap,
        overnight_range=overnight_range,
        overnight_pct=overnight_pct,
        direction=direction,
        above_overnight_vwap=above_vwap,
        key_levels=key_levels,
        session_bias_score=round(score, 4),
        bias_label=bias_label,
        updated_at=now,
    )
