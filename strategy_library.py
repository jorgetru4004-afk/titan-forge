"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                    strategy_library.py — Layer 2                            ║
║  FORGE-TS-01: Gamma Flip Breakout (75% win rate, 2.5:1 R:R)                ║
║  FORGE-TS-23: New York Kill Zone Power Hour (74% win rate, 2.5:1 R:R)      ║
║  + key ICT, VOL, ORD, SES strategies from the 30-strategy library           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import time
from enum import Enum, auto
from typing import Optional, Callable

logger = logging.getLogger("titan_forge.strategy_library")


@dataclass
class StrategySignal:
    """Unified signal output from any strategy in the library."""
    strategy_id:    str
    strategy_name:  str
    direction:      Optional[str]     # "long" / "short" / None
    entry:          Optional[float]
    stop:           Optional[float]
    target:         Optional[float]
    valid:          bool
    win_rate_hist:  float             # Documented historical win rate
    rr_ratio:       float
    confidence:     float             # Current signal confidence 0–1
    reason:         str

    @property
    def expected_value(self) -> float:
        return (self.win_rate_hist * self.rr_ratio) - (1.0 - self.win_rate_hist)


# ── FORGE-TS-01: Gamma Flip Breakout ─────────────────────────────────────────
# GEX flips from positive to negative = dealers switch from stabilizing to amplifying.
# This creates momentum as dealers must hedge aggressively.
# 75% win rate, 2.5:1 R:R

def strategy_gamma_flip_breakout(
    prior_gex:        float,   # Prior GEX value (positive = pinning)
    current_gex:      float,   # Current GEX (negative = flipped)
    gex_flip_price:   float,   # Price at which the flip occurred
    current_price:    float,
    atr:              float,
    vwap:             float,
    direction:        str,     # "long" (bullish flip) / "short" (bearish flip)
) -> StrategySignal:
    """
    FORGE-TS-01: GEX-01 Gamma Flip Breakout.

    Entry conditions:
    - GEX flips from positive to negative
    - Price is at or near the flip level
    - Direction aligns with the flip
    """
    gex_flipped = prior_gex > 0 and current_gex < 0

    if not gex_flipped:
        return StrategySignal(
            "GEX-01", "Gamma Flip Breakout", None, None, None, None,
            False, 0.75, 2.5, 0.0,
            "GEX has not flipped. Prior positive, current must be negative."
        )

    # Price near the flip level
    distance_from_flip = abs(current_price - gex_flip_price) / atr
    if distance_from_flip > 1.5:
        return StrategySignal(
            "GEX-01", "Gamma Flip Breakout", None, None, None, None,
            False, 0.75, 2.5, 0.3,
            f"Price {distance_from_flip:.1f} ATR from flip level — too extended."
        )

    if direction == "long":
        entry  = max(current_price, gex_flip_price)
        stop   = entry - (atr * 1.0)
        target = entry + (atr * 2.5)
    else:
        entry  = min(current_price, gex_flip_price)
        stop   = entry + (atr * 1.0)
        target = entry - (atr * 2.5)

    conf = max(0.60, 0.90 - distance_from_flip * 0.15)

    return StrategySignal(
        "GEX-01", "Gamma Flip Breakout",
        direction, entry, stop, target, True,
        0.75, 2.5, round(conf, 2),
        f"GEX flipped: {prior_gex:.0f} → {current_gex:.0f}. "
        f"Dealers now amplifying. Entry: {entry:.2f}, Stop: {stop:.2f}, Target: {target:.2f}."
    )


# ── FORGE-TS-23: NY Kill Zone Power Hour ─────────────────────────────────────
# 9:30am–11am ET: institutional flow concentration.
# Same directional patterns repeat because institutions are consistent.
# 74% win rate, 2.5:1 R:R

NY_KILL_ZONE_START = time(9, 30)
NY_KILL_ZONE_END   = time(11, 0)

def strategy_ny_kill_zone(
    current_time_et:    time,
    current_price:      float,
    open_price:         float,      # Today's open
    vwap:               float,
    atr:                float,
    session_bias:       str,        # "bullish" / "bearish" / "neutral"
    prior_day_high:     float,
    prior_day_low:      float,
    gex_direction:      str,        # "negative" / "neutral" / "positive"
) -> StrategySignal:
    """
    FORGE-TS-23: SES-01 New York Kill Zone Power Hour.

    Best setups during 9:30-11:00am ET:
    - Breakout from early consolidation
    - Reclaim/rejection of prior day levels
    - VWAP alignment with session bias
    """
    in_kill_zone = NY_KILL_ZONE_START <= current_time_et <= NY_KILL_ZONE_END
    if not in_kill_zone:
        return StrategySignal(
            "SES-01", "NY Kill Zone Power Hour", None, None, None, None,
            False, 0.74, 2.5, 0.0,
            f"Outside kill zone ({NY_KILL_ZONE_START.strftime('%H:%M')}–"
            f"{NY_KILL_ZONE_END.strftime('%H:%M')} ET). "
            f"Current: {current_time_et.strftime('%H:%M')}."
        )

    # Determine direction from session bias + GEX
    if session_bias == "neutral" and gex_direction == "neutral":
        return StrategySignal(
            "SES-01", "NY Kill Zone Power Hour", None, None, None, None,
            False, 0.74, 2.5, 0.3,
            "Session bias and GEX both neutral — no clear kill zone setup."
        )

    direction = "long" if session_bias == "bullish" or gex_direction == "negative" else "short"

    # Entry near VWAP with momentum
    if direction == "long":
        entry  = max(current_price, vwap)
        stop   = min(open_price, prior_day_low) - (atr * 0.3)
        target = entry + (current_price - stop) * 2.5
    else:
        entry  = min(current_price, vwap)
        stop   = max(open_price, prior_day_high) + (atr * 0.3)
        target = entry - (stop - current_price) * 2.5

    conf = 0.80 if gex_direction == "negative" else 0.65

    return StrategySignal(
        "SES-01", "NY Kill Zone Power Hour",
        direction, entry, stop, target, True,
        0.74, 2.5, conf,
        f"NY Kill Zone active. {direction.upper()} with {session_bias} bias. "
        f"GEX: {gex_direction}. Entry: {entry:.2f}."
    )


# ── ICT-01: Order Block + FVG Confluence ──────────────────────────────────────
# Highest win rate ICT setup: 76%, 2.5:1 R:R

def strategy_order_block_fvg(
    current_price:      float,
    order_block_high:   float,
    order_block_low:    float,
    fvg_high:           float,
    fvg_low:            float,
    atr:                float,
    direction:          str,   # "long" / "short"
) -> StrategySignal:
    """ICT-01: Order Block + Fair Value Gap Confluence."""
    # Check if order block and FVG overlap (confluence zone)
    ob_fvg_overlap = not (order_block_high < fvg_low or order_block_low > fvg_high)

    if not ob_fvg_overlap:
        return StrategySignal(
            "ICT-01", "Order Block + FVG Confluence",
            None, None, None, None, False, 0.76, 2.5, 0.0,
            "No OB+FVG confluence. Zones don't overlap."
        )

    # Price must be entering the confluence zone
    zone_high = max(order_block_high, fvg_high)
    zone_low  = min(order_block_low,  fvg_low)

    if direction == "long":
        price_in_zone = zone_low <= current_price <= zone_high
        entry  = current_price if price_in_zone else zone_high
        stop   = zone_low - (atr * 0.3)
        target = entry + (entry - stop) * 2.5
    else:
        price_in_zone = zone_low <= current_price <= zone_high
        entry  = current_price if price_in_zone else zone_low
        stop   = zone_high + (atr * 0.3)
        target = entry - (stop - entry) * 2.5

    conf = 0.85 if price_in_zone else 0.70

    return StrategySignal(
        "ICT-01", "Order Block + FVG Confluence",
        direction, entry, stop, target, True,
        0.76, 2.5, conf,
        f"OB+FVG confluence zone [{zone_low:.2f}–{zone_high:.2f}]. "
        f"Institutional zone confirmed. Entry: {entry:.2f}."
    )


# ── Strategy Registry ─────────────────────────────────────────────────────────

class StrategyRegistry:
    """Registry of all 30 strategies with metadata for fast lookup."""

    # From Section 10 of the document
    METADATA: dict[str, dict] = {
        "GEX-01": {"name": "Gamma Flip Breakout",           "win_rate": 0.75, "rr": 2.5},
        "GEX-02": {"name": "Dealer Hedging Cascade",        "win_rate": 0.74, "rr": 3.0},
        "GEX-03": {"name": "GEX Pin and Break",             "win_rate": 0.73, "rr": 2.0},
        "GEX-04": {"name": "Vanna Flow Drift",              "win_rate": 0.70, "rr": 2.0},
        "GEX-05": {"name": "Charm Decay Fade",              "win_rate": 0.68, "rr": 1.8},
        "ICT-01": {"name": "Order Block + FVG Confluence",  "win_rate": 0.76, "rr": 2.5},
        "ICT-02": {"name": "Liquidity Sweep and Reverse",   "win_rate": 0.74, "rr": 3.0},
        "ICT-03": {"name": "Kill Zone OTE Entry",           "win_rate": 0.73, "rr": 2.5},
        "ICT-04": {"name": "Breaker Block Retest",          "win_rate": 0.72, "rr": 2.0},
        "ICT-05": {"name": "Asian Range Raid and Reverse",  "win_rate": 0.71, "rr": 2.5},
        "ICT-06": {"name": "Premium/Discount Zone Filter",  "win_rate": 0.70, "rr": 2.5},
        "ICT-07": {"name": "FVG Inversion Play",            "win_rate": 0.69, "rr": 2.0},
        "ICT-08": {"name": "Market Structure Break + OTE",  "win_rate": 0.73, "rr": 3.0},
        "VOL-01": {"name": "POC Magnetic Revert",           "win_rate": 0.74, "rr": 1.8},
        "VOL-02": {"name": "Value Area Edge Fade",          "win_rate": 0.72, "rr": 2.0},
        "VOL-03": {"name": "Low Volume Node Express",       "win_rate": 0.73, "rr": 2.5},
        "VOL-04": {"name": "High Volume Node Cluster",      "win_rate": 0.70, "rr": 2.0},
        "VOL-05": {"name": "Anchored VWAP Confluence",      "win_rate": 0.71, "rr": 2.0},
        "ORD-01": {"name": "Delta Divergence Reversal",     "win_rate": 0.75, "rr": 2.5},
        "ORD-02": {"name": "Footprint Absorption Entry",    "win_rate": 0.73, "rr": 2.5},
        "ORD-03": {"name": "Order Block Stacking Breakout", "win_rate": 0.71, "rr": 2.0},
        "ORD-04": {"name": "Bid/Ask Imbalance Cascade",     "win_rate": 0.70, "rr": 2.0},
        "SES-01": {"name": "NY Kill Zone Power Hour",       "win_rate": 0.74, "rr": 2.5},
        "SES-02": {"name": "London-NY Overlap Momentum",   "win_rate": 0.73, "rr": 2.0},
        "SES-03": {"name": "First Hour Reversal Pattern",  "win_rate": 0.70, "rr": 2.0},
        "SES-04": {"name": "Pre-Close Institutional",      "win_rate": 0.69, "rr": 1.8},
        "SES-05": {"name": "Monday Gap Fill Strategy",     "win_rate": 0.72, "rr": 2.0},
        "INS-01": {"name": "Unusual Options Flow Follow",  "win_rate": 0.75, "rr": 3.0},
        "INS-02": {"name": "Dark Pool Print Entry",        "win_rate": 0.73, "rr": 2.5},
        "INS-03": {"name": "COT Extreme Reversal",         "win_rate": 0.71, "rr": 3.0},
    }

    @classmethod
    def get(cls, strategy_id: str) -> Optional[dict]:
        return cls.METADATA.get(strategy_id)

    @classmethod
    def all_ids(cls) -> list[str]:
        return list(cls.METADATA.keys())

    @classmethod
    def average_win_rate(cls) -> float:
        wrs = [v["win_rate"] for v in cls.METADATA.values()]
        return sum(wrs) / len(wrs) if wrs else 0.0

    @classmethod
    def average_rr(cls) -> float:
        rrs = [v["rr"] for v in cls.METADATA.values()]
        return sum(rrs) / len(rrs) if rrs else 0.0
