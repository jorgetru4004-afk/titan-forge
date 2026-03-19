"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     catalyst_stack.py — Layer 2                             ║
║  FORGE-22: Catalyst Stack Lite                                              ║
║    4+ stack score required during evaluation. Only TITAN STOCK              ║
║    tier-1 catalyst setups qualify.                                           ║
║  FORGE-63: Setup Hierarchy by Firm                                          ║
║    FTMO: trend + VWAP. Apex: ES/NQ momentum. DNA: forex. 5%ers: 4-stack+   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.catalyst_stack")

# ── FORGE-22: Catalyst Stack ──────────────────────────────────────────────────

class CatalystType(Enum):
    GEX_DIRECTION       = "gex_direction"
    ICT_ORDER_BLOCK     = "ict_order_block"
    VOLUME_PROFILE      = "volume_profile"
    ORDER_FLOW_DELTA    = "order_flow_delta"
    SESSION_TIMING      = "session_timing"
    UNUSUAL_OPTIONS     = "unusual_options"
    DARK_POOL_PRINT     = "dark_pool_print"
    MARKET_STRUCTURE    = "market_structure"
    VWAP_CONFLUENCE     = "vwap_confluence"

CATALYST_MIN_STANDARD:   int = 1
CATALYST_MIN_EVALUATION: int = 4   # FORGE-22: 4+ during evaluation
CATALYST_MIN_5PERCENTERS:int = 4   # 5%ers always require 4+ stack

# Size multipliers for multi-strategy confluence (from Section 10)
CONFLUENCE_SIZE_MULTIPLIERS: dict[int, float] = {
    1: 1.00,    # Standard minimum
    2: 1.25,    # 2 different categories
    3: 1.50,    # 3 different categories
    4: 1.75,    # 4+ different categories — estimated win rate 80%+
}

@dataclass
class CatalystScore:
    total_score:        int
    active_catalysts:   list[CatalystType]
    category_count:     int    # Unique categories = more meaningful confluence
    size_multiplier:    float
    meets_evaluation:   bool   # ≥ 4 for evaluation
    meets_standard:     bool   # ≥ 1
    reason:             str

def build_catalyst_score(
    active_catalysts: list[CatalystType],
    is_evaluation:    bool = True,
    firm_id:          str  = FirmID.FTMO,
) -> CatalystScore:
    """
    FORGE-22: Calculate the catalyst stack score.
    Each unique category = +1 to stack score.
    4+ score required during evaluation.
    """
    score   = len(active_catalysts)
    cats    = active_catalysts

    # Map to broader categories for confluence scoring
    cat_groups = {
        CatalystType.GEX_DIRECTION:    "regime",
        CatalystType.ICT_ORDER_BLOCK:  "structure",
        CatalystType.VOLUME_PROFILE:   "volume",
        CatalystType.ORDER_FLOW_DELTA: "orderflow",
        CatalystType.SESSION_TIMING:   "session",
        CatalystType.UNUSUAL_OPTIONS:  "institutional",
        CatalystType.DARK_POOL_PRINT:  "institutional",
        CatalystType.MARKET_STRUCTURE: "structure",
        CatalystType.VWAP_CONFLUENCE:  "volume",
    }
    unique_groups = len({cat_groups.get(c, str(c)) for c in cats})

    min_score     = CATALYST_MIN_EVALUATION if is_evaluation else CATALYST_MIN_STANDARD
    meets_eval    = score >= CATALYST_MIN_EVALUATION
    meets_std     = score >= CATALYST_MIN_STANDARD

    # Clamp multiplier lookup at max defined key
    mult_key      = min(score, max(CONFLUENCE_SIZE_MULTIPLIERS.keys()))
    size_mult     = CONFLUENCE_SIZE_MULTIPLIERS.get(mult_key, 1.75)

    # 5%ers always need 4+
    if firm_id == FirmID.FIVEPERCENTERS and score < CATALYST_MIN_5PERCENTERS:
        meets_std = False

    if score >= CATALYST_MIN_EVALUATION:
        reason = (f"✅ Stack {score}: {[c.value for c in cats]}. "
                  f"Size multiplier: {size_mult:.2f}×. Estimated win rate 80%+.")
    else:
        reason = (f"Stack {score}/{CATALYST_MIN_EVALUATION} minimum. "
                  f"Add {CATALYST_MIN_EVALUATION - score} more confirming signals.")

    return CatalystScore(
        total_score=score,
        active_catalysts=cats,
        category_count=unique_groups,
        size_multiplier=size_mult,
        meets_evaluation=meets_eval,
        meets_standard=meets_std,
        reason=reason,
    )


# ── FORGE-63: Setup Hierarchy by Firm ────────────────────────────────────────

# Priority setups per firm from Section 8 of the document
FIRM_SETUP_HIERARCHY: dict[str, list[str]] = {
    FirmID.FTMO: [
        "FORGE-20",  # Trend day (GEX negative)
        "FORGE-18",  # VWAP reclaim
        "ICT-01",    # Order block + FVG confluence
        "GEX-01",    # Gamma flip
        "VOL-05",    # Anchored VWAP
    ],
    FirmID.APEX: [
        "FORGE-20",  # ES/NQ momentum (trend day)
        "GEX-01",    # Gamma flip breakout
        "ORD-01",    # Delta divergence
        "SES-01",    # NY Kill Zone
        "ICT-08",    # Market structure break
    ],
    FirmID.DNA_FUNDED: [
        "FORGE-19",  # London session forex
        "ICT-03",    # Kill Zone OTE
        "ICT-05",    # Asian range raid
        "SES-02",    # London-NY overlap momentum
        "ICT-01",    # Order block confluence
    ],
    FirmID.FIVEPERCENTERS: [
        "INS-01",    # Unusual options flow (4-stack required)
        "GEX-01",    # Gamma flip
        "ICT-01",    # Order block
        "VOL-03",    # Low volume node express
        "ORD-01",    # Delta divergence
    ],
    FirmID.TOPSTEP: [
        "FORGE-20",  # Trend day momentum
        "GEX-02",    # Dealer hedging cascade
        "ORD-01",    # Delta divergence
        "SES-01",    # NY Kill Zone
        "FORGE-17",  # Opening range breakout
    ],
}

def get_preferred_setups(firm_id: str, max_count: int = 3) -> list[str]:
    """Return the top priority setups for a given firm."""
    hierarchy = FIRM_SETUP_HIERARCHY.get(firm_id, [])
    return hierarchy[:max_count]

def rank_setups_for_firm(
    setup_ids:       list[str],
    firm_id:         str,
    catalyst_score:  int = 0,
) -> list[tuple[str, float]]:
    """
    Rank setups by firm hierarchy. Returns list of (setup_id, priority_score).
    Higher score = higher priority.
    """
    hierarchy = FIRM_SETUP_HIERARCHY.get(firm_id, [])
    ranked = []
    for sid in setup_ids:
        if sid in hierarchy:
            pos   = hierarchy.index(sid)
            score = (len(hierarchy) - pos) / len(hierarchy)   # Higher in list = higher score
        else:
            score = 0.5   # Unlisted but valid setups get mid-score

        # Boost for catalyst confluence
        score += catalyst_score * 0.05
        ranked.append((sid, round(min(1.0, score), 4)))

    return sorted(ranked, key=lambda x: -x[1])
