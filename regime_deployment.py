"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  regime_deployment.py — FORGE-72/60 — Layer 2               ║
║  FORGE-72: Regime-Specific Strategy Deployment                               ║
║    5 regimes: low-vol trending, low-vol ranging, high-vol trending,          ║
║    high-vol ranging, expansion.                                              ║
║  FORGE-60: Expected Value Calculator                                         ║
║    Full EV including firm constraints.                                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger("titan_forge.regime_deployment")

class MarketRegimeType(Enum):
    LOW_VOL_TRENDING  = "low_vol_trending"
    LOW_VOL_RANGING   = "low_vol_ranging"
    HIGH_VOL_TRENDING = "high_vol_trending"
    HIGH_VOL_RANGING  = "high_vol_ranging"
    EXPANSION         = "expansion"

# Strategy sets per regime (from document Section 10 + FORGE-72)
REGIME_STRATEGIES: dict[MarketRegimeType, list[str]] = {
    MarketRegimeType.LOW_VOL_TRENDING: [
        "GEX-01", "GEX-02", "ICT-01", "ICT-08", "ORD-01", "SES-01",
    ],
    MarketRegimeType.LOW_VOL_RANGING: [
        "GEX-05", "VOL-01", "VOL-02", "ICT-06", "SES-03", "ICT-07",
    ],
    MarketRegimeType.HIGH_VOL_TRENDING: [
        "GEX-01", "GEX-02", "ICT-02", "ORD-01", "ORD-04", "ICT-08",
    ],
    MarketRegimeType.HIGH_VOL_RANGING: [
        "VOL-01", "VOL-02", "GEX-03", "ICT-06", "SES-03",
    ],
    MarketRegimeType.EXPANSION: [
        "GEX-01", "GEX-02", "ORD-03", "ORD-04", "ICT-02",
    ],
}

# Regime-specific size multipliers (expansion = higher, ranging = lower)
REGIME_SIZE_MULTIPLIERS: dict[MarketRegimeType, float] = {
    MarketRegimeType.LOW_VOL_TRENDING:  1.00,
    MarketRegimeType.LOW_VOL_RANGING:   0.85,
    MarketRegimeType.HIGH_VOL_TRENDING: 1.10,   # High vol trending = big moves
    MarketRegimeType.HIGH_VOL_RANGING:  0.75,   # High vol ranging = whipsaw risk
    MarketRegimeType.EXPANSION:         1.15,   # Expansion = ride big moves
}

@dataclass
class RegimeDeployment:
    regime:               MarketRegimeType
    active_strategies:    list[str]
    excluded_strategies:  list[str]
    size_multiplier:      float
    priority_setup:       str    # #1 setup for this regime
    reason:               str

def deploy_for_regime(
    regime:              MarketRegimeType,
    all_available_setups: Optional[list[str]] = None,
) -> RegimeDeployment:
    """FORGE-72: Select the right strategies for the current market regime."""
    active = REGIME_STRATEGIES.get(regime, [])
    size   = REGIME_SIZE_MULTIPLIERS.get(regime, 1.0)

    if all_available_setups:
        excluded = [s for s in all_available_setups if s not in active]
    else:
        excluded = []

    priority = active[0] if active else "GEX-01"

    reason = (f"Regime: {regime.value}. "
              f"Active setups: {active[:3]}... Size: {size:.2f}×.")

    logger.info("[FORGE-72] Regime %s: %d strategies active. Size: %.2f×.",
                regime.value, len(active), size)

    return RegimeDeployment(
        regime=regime,
        active_strategies=active,
        excluded_strategies=excluded,
        size_multiplier=size,
        priority_setup=priority,
        reason=reason,
    )

def detect_regime(
    vix_level:       float,
    vix_rising:      bool,
    gex_negative:    bool,   # True = dealers amplifying moves (trending)
    adr_pct:         float,  # Average daily range as % — proxy for volatility
) -> MarketRegimeType:
    """Simple regime detection from market inputs."""
    high_vol = vix_level > 20 or vix_rising or adr_pct > 0.012

    if high_vol:
        if gex_negative:
            return MarketRegimeType.HIGH_VOL_TRENDING
        else:
            return MarketRegimeType.HIGH_VOL_RANGING
    else:
        if gex_negative:
            return MarketRegimeType.LOW_VOL_TRENDING
        else:
            return MarketRegimeType.LOW_VOL_RANGING


# ── FORGE-60: Expected Value Calculator ──────────────────────────────────────

@dataclass
class EVResult:
    raw_ev:              float   # Simple EV without context
    context_adjusted_ev: float   # EV after firm constraints
    is_positive:         bool
    breakdown:           str

def calculate_expected_value(
    win_rate:            float,
    avg_win:             float,     # Average win in dollars
    avg_loss:            float,     # Average loss in dollars (positive)
    firm_consistency_pct: Optional[float] = None,  # e.g. DNA 40% cap
    today_profit:        float = 0.0,
    total_profit:        float = 0.0,
) -> EVResult:
    """
    FORGE-60: Full EV including firm constraints.
    A positive EV trade in isolation may be negative EV in context.
    """
    raw_ev = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)

    # Context adjustment: consistency rule risk
    context_penalty = 0.0
    if firm_consistency_pct and total_profit > 0:
        # How close is today's profit to the cap?
        cap_dollars = total_profit * firm_consistency_pct
        if today_profit >= cap_dollars * 0.80:
            # Within 20% of cap — any more profit today risks the rule
            context_penalty = raw_ev * 0.5   # Reduce EV by 50%

    context_ev = raw_ev - context_penalty

    breakdown = (
        f"Raw EV: ${raw_ev:+.2f} | "
        f"WR: {win_rate:.0%} × ${avg_win:.0f} win − {1-win_rate:.0%} × ${avg_loss:.0f} loss. "
        + (f"Context penalty: −${context_penalty:.2f}" if context_penalty else "")
    )

    return EVResult(
        raw_ev=round(raw_ev, 4),
        context_adjusted_ev=round(context_ev, 4),
        is_positive=context_ev > 0,
        breakdown=breakdown,
    )
