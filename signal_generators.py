"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   signal_generators.py — Layer 2                            ║
║  FORGE-17: Opening Range Breakout (72% continuation rate)                   ║
║  FORGE-18: VWAP Reclaim (institutional buyers stepping in)                  ║
║  FORGE-19: London Session Forex (8am-12pm ET only)                          ║
║  FORGE-20: Trend Day Momentum (GEX negative = trend confirmed)              ║
║  FORGE-21: Mean Reversion (high positive GEX = ranging day)                 ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import time
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.signal_generators")


class SignalVerdict(Enum):
    CONFIRMED = auto()
    REJECTED  = auto()
    PENDING   = auto()   # Conditions not yet met — wait


@dataclass
class Signal:
    setup_id:    str
    verdict:     SignalVerdict
    direction:   Optional[str]   # "long" / "short" / None
    entry_price: Optional[float]
    stop_price:  Optional[float]
    target_price:Optional[float]
    confidence:  float            # 0.0–1.0
    reason:      str

    @property
    def is_confirmed(self) -> bool:
        return self.verdict == SignalVerdict.CONFIRMED


# ── FORGE-17: Opening Range Breakout ─────────────────────────────────────────
# First 15 min establishes range. Break above/below after 9:45am with 2× volume.
# 72% continuation rate.

def check_opening_range_breakout(
    current_price:     float,
    range_high:        float,
    range_low:         float,
    current_time_et:   time,
    current_volume:    float,
    avg_volume:        float,
    atr:               float,
) -> Signal:
    """FORGE-17: Opening Range Breakout — break above/below OR range after 9:45am."""
    # Must be after 9:45am ET
    if current_time_et < time(9, 45):
        return Signal("FORGE-17", SignalVerdict.PENDING, None, None, None, None, 0.0,
                      "Too early — range still establishing (before 9:45am ET).")

    # Require 2× average volume
    volume_ok = current_volume >= avg_volume * 2.0

    if current_price > range_high:
        direction = "long"
        entry     = range_high + (atr * 0.10)   # Slight buffer above range
        stop      = range_low
        target    = range_high + (range_high - range_low) * 1.5
        conf      = 0.85 if volume_ok else 0.50
        verdict   = SignalVerdict.CONFIRMED if volume_ok else SignalVerdict.REJECTED
        reason    = (f"ORB Long: Price {current_price:.2f} above range high {range_high:.2f}. "
                     f"Volume: {'2×+ ✓' if volume_ok else 'insufficient ✗'}.")
    elif current_price < range_low:
        direction = "short"
        entry     = range_low - (atr * 0.10)
        stop      = range_high
        target    = range_low - (range_high - range_low) * 1.5
        conf      = 0.85 if volume_ok else 0.50
        verdict   = SignalVerdict.CONFIRMED if volume_ok else SignalVerdict.REJECTED
        reason    = (f"ORB Short: Price {current_price:.2f} below range low {range_low:.2f}. "
                     f"Volume: {'2×+ ✓' if volume_ok else 'insufficient ✗'}.")
    else:
        return Signal("FORGE-17", SignalVerdict.PENDING, None, None, None, None, 0.0,
                      f"Price {current_price:.2f} inside range [{range_low:.2f}–{range_high:.2f}].")

    return Signal("FORGE-17", verdict, direction, entry, stop, target, conf, reason)


# ── FORGE-18: VWAP Reclaim ────────────────────────────────────────────────────
# Dip below VWAP with reclaim and volume. Institutional buyers stepping in.

def check_vwap_reclaim(
    current_price:      float,
    prior_close:        float,    # Price before the dip below VWAP
    vwap:               float,
    dipped_below:       bool,     # True if price recently traded below VWAP
    volume_at_reclaim:  float,
    avg_volume:         float,
    atr:                float,
) -> Signal:
    """FORGE-18: VWAP Reclaim — dip below then reclaim with volume."""
    if not dipped_below:
        return Signal("FORGE-18", SignalVerdict.PENDING, None, None, None, None, 0.0,
                      "No VWAP dip detected — waiting for setup condition.")

    reclaimed = current_price > vwap
    volume_ok = volume_at_reclaim >= avg_volume * 1.3

    if reclaimed and volume_ok:
        entry  = current_price
        stop   = vwap - (atr * 0.5)    # Stop below VWAP
        target = vwap + (atr * 2.0)    # 2 ATR target above VWAP
        return Signal("FORGE-18", SignalVerdict.CONFIRMED, "long", entry, stop, target, 0.80,
                      f"VWAP Reclaim confirmed. Price {current_price:.2f} above VWAP {vwap:.2f}. "
                      f"Volume: {volume_at_reclaim/avg_volume:.1f}× avg.")
    elif reclaimed and not volume_ok:
        return Signal("FORGE-18", SignalVerdict.REJECTED, None, None, None, None, 0.40,
                      f"VWAP reclaimed but volume insufficient ({volume_at_reclaim/avg_volume:.1f}× avg). "
                      f"Need 1.3× minimum.")
    else:
        return Signal("FORGE-18", SignalVerdict.PENDING, None, None, None, None, 0.30,
                      f"VWAP not yet reclaimed. Current: {current_price:.2f}, VWAP: {vwap:.2f}.")


# ── FORGE-19: London Session Forex ───────────────────────────────────────────
# London-NY overlap 8am-12pm ET only. Major pairs. No forex outside this window.

LONDON_SESSION_START_ET = time(8, 0)
LONDON_SESSION_END_ET   = time(12, 0)
MAJOR_FOREX_PAIRS = frozenset({"EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
                                "USDCHF", "USDCAD", "NZDUSD"})

def check_london_session_forex(
    pair:              str,
    current_time_et:   time,
    is_evaluation:     bool = True,
) -> Signal:
    """FORGE-19: London session forex gate — 8am-12pm ET only during evaluation."""
    pair_upper = pair.upper()

    if pair_upper not in MAJOR_FOREX_PAIRS:
        return Signal("FORGE-19", SignalVerdict.REJECTED, None, None, None, None, 0.0,
                      f"{pair} is not a major forex pair. Only majors permitted.")

    in_window = LONDON_SESSION_START_ET <= current_time_et < LONDON_SESSION_END_ET

    if is_evaluation and not in_window:
        return Signal("FORGE-19", SignalVerdict.REJECTED, None, None, None, None, 0.0,
                      f"Outside London-NY overlap ({LONDON_SESSION_START_ET.strftime('%I%p')}"
                      f"–{LONDON_SESSION_END_ET.strftime('%I%p')} ET). "
                      f"No forex trading outside this window during evaluation.")

    return Signal("FORGE-19", SignalVerdict.CONFIRMED, None, None, None, None, 0.75,
                  f"{pair} in London-NY overlap window. Major pair confirmed.")


# ── FORGE-20: Trend Day Momentum ──────────────────────────────────────────────
# GEX negative = trend day confirmed. Size up and ride the full move.

def check_trend_day_momentum(
    gex_negative:       bool,     # GEX < 0 = dealers amplify moves
    price_direction:    str,      # "bullish" or "bearish" — current session bias
    current_price:      float,
    vwap:               float,
    atr:                float,
    is_first_pullback:  bool,     # True = first retrace after trend leg (optimal)
) -> Signal:
    """FORGE-20: Trend Day Momentum — GEX negative = ride the trend."""
    if not gex_negative:
        return Signal("FORGE-20", SignalVerdict.REJECTED, None, None, None, None, 0.30,
                      "GEX not negative — trend day not confirmed. "
                      "Use mean reversion strategy for positive GEX days.")

    direction = "long" if price_direction == "bullish" else "short"
    # Optimal entry on first pullback to VWAP
    if is_first_pullback:
        entry  = current_price
        stop   = vwap - (atr * 0.5) if direction == "long" else vwap + (atr * 0.5)
        target = current_price + (atr * 3.0) if direction == "long" else current_price - (atr * 3.0)
        conf   = 0.82
        reason = f"Trend Day Momentum: GEX negative + first pullback to VWAP. Ride the move."
    else:
        entry  = current_price
        stop   = vwap - atr if direction == "long" else vwap + atr
        target = current_price + (atr * 2.0) if direction == "long" else current_price - (atr * 2.0)
        conf   = 0.68
        reason = f"Trend Day Momentum: GEX negative. Not first pullback — reduced confidence."

    return Signal("FORGE-20", SignalVerdict.CONFIRMED, direction, entry, stop, target, conf, reason)


# ── FORGE-21: Mean Reversion ──────────────────────────────────────────────────
# High positive GEX = ranging day. Sell extremes, buy pullbacks, shorter holds.

def check_mean_reversion(
    gex_positive:       bool,     # GEX > 0 = dealers dampen moves
    current_price:      float,
    vwap:               float,
    upper_band:         float,    # e.g. VWAP + 1.5 ATR
    lower_band:         float,    # e.g. VWAP - 1.5 ATR
    atr:                float,
) -> Signal:
    """FORGE-21: Mean Reversion — high positive GEX = range trade, shorter holds."""
    if not gex_positive:
        return Signal("FORGE-21", SignalVerdict.REJECTED, None, None, None, None, 0.30,
                      "GEX not positive — no ranging day signal. Use trend momentum instead.")

    at_upper = current_price >= upper_band
    at_lower = current_price <= lower_band

    if at_upper:
        entry  = current_price
        stop   = upper_band + (atr * 0.3)   # Tight stop above upper band
        target = vwap                         # Target: mean (VWAP)
        return Signal("FORGE-21", SignalVerdict.CONFIRMED, "short", entry, stop, target, 0.74,
                      f"Mean Reversion Short: Price {current_price:.2f} at/above upper band "
                      f"{upper_band:.2f}. GEX positive = ranging day. Target VWAP {vwap:.2f}.")
    elif at_lower:
        entry  = current_price
        stop   = lower_band - (atr * 0.3)
        target = vwap
        return Signal("FORGE-21", SignalVerdict.CONFIRMED, "long", entry, stop, target, 0.74,
                      f"Mean Reversion Long: Price {current_price:.2f} at/below lower band "
                      f"{lower_band:.2f}. GEX positive = ranging day. Target VWAP {vwap:.2f}.")
    else:
        return Signal("FORGE-21", SignalVerdict.PENDING, None, None, None, None, 0.0,
                      f"Price {current_price:.2f} between bands [{lower_band:.2f}–{upper_band:.2f}]. "
                      f"Wait for extreme before fading.")
