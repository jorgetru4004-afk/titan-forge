"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  evaluation_timing.py — FORGE-26/46 — Layer 2               ║
║  FORGE-26: Evaluation Timing Intelligence (MFI gate)                        ║
║  FORGE-46: Market Favorability Index — MFI above 55 for 5 of last 7 days   ║
║    Components: VIX level (25%), GEX direction (20%), regime state (25%),    ║
║    market breadth (15%), trend strength (15%).                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

logger = logging.getLogger("titan_forge.evaluation_timing")

# MFI thresholds
MFI_GATE_THRESHOLD: float  = 55.0   # Must exceed for evaluation to proceed
MFI_GATE_DAYS_REQUIRED: int = 5     # 5 of last 7 days must be above threshold
MFI_AUGUST_PREMIUM: float  = 30.0   # August requires MFI 30% higher (P-06)
MFI_HISTORY_WINDOW: int    = 7      # Rolling 7-day window

# Best months for evaluation (P-06 seasonal timing)
FAVORABLE_MONTHS   = frozenset({1, 2, 3, 4, 5, 11, 12})
UNFAVORABLE_MONTHS = frozenset({8})   # Avoid August entirely

# Component weights
MFI_WEIGHTS = {
    "vix_level":       0.25,
    "gex_direction":   0.20,
    "regime_state":    0.25,
    "market_breadth":  0.15,
    "trend_strength":  0.15,
}

@dataclass
class MFISnapshot:
    """Single day's Market Favorability Index reading."""
    date:           date
    score:          float       # 0–100
    vix_score:      float
    gex_score:      float
    regime_score:   float
    breadth_score:  float
    trend_score:    float
    above_threshold: bool

@dataclass
class MFIGateResult:
    """MFI gate result — can evaluation start?"""
    gate_passed:        bool
    current_mfi:        float
    days_above_in_window: int   # of last 7
    required_days:      int
    seasonal_ok:        bool
    month:              int
    adjusted_threshold: float   # Higher in August
    reason:             str
    recommendation:     str


def calculate_mfi(
    vix_level:          float,   # Spot VIX
    gex_negative:       bool,    # True = negative GEX (trend day bias)
    regime_trending:    bool,    # True = trending regime (better for strategies)
    advance_decline:    float,   # Breadth: advancing / total (0–1)
    trend_strength:     float,   # ADX proxy 0–1
) -> float:
    """
    Calculate the Market Favorability Index score (0–100).

    Components (from FX-07):
        VIX level (25%): Lower VIX = higher score
        GEX direction (20%): Negative GEX (trend) = higher score
        Regime state (25%): Trending = higher score
        Market breadth (15%): Higher A/D = higher score
        Trend strength (15%): Stronger trend = higher score
    """
    # VIX score: 15 → 90pts, 20 → 70pts, 30 → 40pts, 45+ → 10pts
    if vix_level <= 15:
        vix_score = 90.0
    elif vix_level <= 20:
        vix_score = 90.0 - ((vix_level - 15) / 5) * 20
    elif vix_level <= 30:
        vix_score = 70.0 - ((vix_level - 20) / 10) * 30
    elif vix_level <= 45:
        vix_score = 40.0 - ((vix_level - 30) / 15) * 30
    else:
        vix_score = 10.0

    gex_score    = 75.0 if gex_negative else 55.0
    regime_score = 80.0 if regime_trending else 50.0
    breadth_score = advance_decline * 100.0
    trend_score  = trend_strength * 100.0

    mfi = (
        vix_score    * MFI_WEIGHTS["vix_level"]     +
        gex_score    * MFI_WEIGHTS["gex_direction"]  +
        regime_score * MFI_WEIGHTS["regime_state"]   +
        breadth_score * MFI_WEIGHTS["market_breadth"] +
        trend_score  * MFI_WEIGHTS["trend_strength"]
    )
    return round(max(0.0, min(100.0, mfi)), 2)


class MarketFavorabilityIndex:
    """FORGE-26/46: Market Favorability Index gate."""

    def __init__(self):
        self._history: list[MFISnapshot] = []

    def record_daily(
        self,
        snapshot_date:  date,
        vix_level:      float,
        gex_negative:   bool,
        regime_trending: bool,
        advance_decline: float,
        trend_strength: float,
    ) -> MFISnapshot:
        """Record today's MFI reading."""
        score = calculate_mfi(vix_level, gex_negative, regime_trending,
                              advance_decline, trend_strength)
        vix_s = score   # Simplified for individual component extraction
        snap = MFISnapshot(
            date=snapshot_date,
            score=score,
            vix_score=vix_level,      # Store raw for debugging
            gex_score=75.0 if gex_negative else 55.0,
            regime_score=80.0 if regime_trending else 50.0,
            breadth_score=advance_decline * 100,
            trend_score=trend_strength * 100,
            above_threshold=score > MFI_GATE_THRESHOLD,
        )
        self._history.append(snap)
        # Keep only last 30 days
        if len(self._history) > 30:
            self._history.pop(0)
        logger.info("[FORGE-46] MFI: %.1f (%s) for %s",
                    score, "✓" if snap.above_threshold else "✗", snapshot_date)
        return snap

    def check_gate(self, as_of_month: int = 0) -> MFIGateResult:
        """
        FORGE-26/46: Check if MFI gate is cleared for starting an evaluation.
        5 of last 7 days must be above 55. August requires higher threshold.
        """
        month = as_of_month or (self._history[-1].date.month if self._history else date.today().month)

        # Seasonal adjustment
        seasonal_ok = month not in UNFAVORABLE_MONTHS
        adjusted_threshold = MFI_GATE_THRESHOLD
        if month in UNFAVORABLE_MONTHS:
            adjusted_threshold = MFI_GATE_THRESHOLD * (1 + MFI_AUGUST_PREMIUM / 100)

        # Check last 7 days
        recent = self._history[-MFI_HISTORY_WINDOW:]
        days_above = sum(1 for s in recent if s.score > adjusted_threshold)
        gate_passed = (
            days_above >= MFI_GATE_DAYS_REQUIRED and
            seasonal_ok
        )

        current_mfi = recent[-1].score if recent else 0.0

        if gate_passed:
            reason = (
                f"✅ MFI gate CLEARED. {days_above}/{len(recent)} days above {adjusted_threshold:.0f}. "
                f"Current: {current_mfi:.1f}. Seasonal: {'✓' if seasonal_ok else '✗'}."
            )
            rec = "Proceed with paid evaluation. Conditions favorable."
        elif not seasonal_ok:
            reason = f"August — unfavorable month. MFI threshold raised to {adjusted_threshold:.0f}."
            rec = "Delay evaluation until September or increase threshold clearance."
        else:
            reason = (
                f"MFI gate NOT cleared. Only {days_above}/{MFI_GATE_DAYS_REQUIRED} "
                f"days above {adjusted_threshold:.0f}. Current: {current_mfi:.1f}."
            )
            rec = f"Wait until {MFI_GATE_DAYS_REQUIRED} of last 7 days exceed {adjusted_threshold:.0f}."

        return MFIGateResult(
            gate_passed=gate_passed,
            current_mfi=current_mfi,
            days_above_in_window=days_above,
            required_days=MFI_GATE_DAYS_REQUIRED,
            seasonal_ok=seasonal_ok,
            month=month,
            adjusted_threshold=adjusted_threshold,
            reason=reason,
            recommendation=rec,
        )

    @property
    def history(self) -> list[MFISnapshot]:
        return list(self._history)
