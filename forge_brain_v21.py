"""
FORGE v21 — THE PROBABILITY ENGINE (BOOSTED)
==============================================
11 Bayesian dimensions with BOOSTED likelihood ratios.
Multi-framework confluence lenses: ICT, SMC, VWAP, supply/demand,
volume profile, ATR channels, correlation, divergence, candlestick
patterns, momentum, trend following, breakout, pullback.

These are not 50 separate signal generators. They are LENSES the
Bayesian engine uses to evaluate confluence. More frameworks
confirming one trade = higher conviction = bigger size.

When 6+ dimensions confirm: posterior 85-95%. That's when FORGE
hits with full size. Previous engine capped at ~70%. Fixed.

NEXUS Capital — Jorge Trujillo | Claude — AI Architect | March 2026
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import time as dtime

logger = logging.getLogger("FORGE.brain")


# ─────────────────────────────────────────────────────────────────
# REGIME MULTIPLIERS — V21: extended for new setups + CL
# ─────────────────────────────────────────────────────────────────

REGIME_MULT: Dict[str, Dict[str, float]] = {
    # TREND-FOLLOWING: boosted on TREND, suppressed on CHOP
    "ORD-02":         {"TREND": 1.20, "CHOP": 0.70, "NORMAL": 1.0, "REVERSAL": 0.85},
    "OD-01":          {"TREND": 1.25, "CHOP": 0.60, "NORMAL": 1.0, "REVERSAL": 0.75},
    "GAP-02":         {"TREND": 1.15, "CHOP": 0.70, "NORMAL": 1.0, "REVERSAL": 0.80},
    "IB-01":          {"TREND": 1.20, "CHOP": 0.70, "NORMAL": 1.0, "REVERSAL": 0.85},
    "VOL-03":         {"TREND": 1.25, "CHOP": 0.65, "NORMAL": 1.0, "REVERSAL": 0.80},
    "VWAP-03":        {"TREND": 1.15, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.90},
    "ES-ORD-02":      {"TREND": 1.15, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.85},
    # MEAN REVERSION / RANGE: boosted on CHOP, suppressed on TREND
    "VOL-05":         {"TREND": 0.60, "CHOP": 1.25, "NORMAL": 1.0, "REVERSAL": 1.15},
    "VOL-06":         {"TREND": 0.70, "CHOP": 1.20, "NORMAL": 1.0, "REVERSAL": 1.20},
    "IB-02":          {"TREND": 0.00, "CHOP": 1.20, "NORMAL": 0.85, "REVERSAL": 0.90},
    "LVL-02":         {"TREND": 0.80, "CHOP": 1.15, "NORMAL": 1.0, "REVERSAL": 1.0},
    "ASIA-REVERT-01": {"TREND": 0.70, "CHOP": 1.20, "NORMAL": 1.0, "REVERSAL": 1.10},
    "EXT-REVERT-01":  {"TREND": 0.70, "CHOP": 1.15, "NORMAL": 1.0, "REVERSAL": 1.10},
    # NEUTRAL: work across regimes
    "ICT-01":         {"TREND": 1.10, "CHOP": 0.85, "NORMAL": 1.0, "REVERSAL": 0.90},
    "ICT-02":         {"TREND": 1.05, "CHOP": 0.85, "NORMAL": 1.0, "REVERSAL": 0.90},
    "ICT-03":         {"TREND": 0.85, "CHOP": 1.05, "NORMAL": 1.0, "REVERSAL": 1.20},
    "VWAP-01":        {"TREND": 1.10, "CHOP": 0.90, "NORMAL": 1.0, "REVERSAL": 0.85},
    "VWAP-02":        {"TREND": 1.10, "CHOP": 0.90, "NORMAL": 1.0, "REVERSAL": 0.85},
    "LVL-01":         {"TREND": 1.00, "CHOP": 1.00, "NORMAL": 1.0, "REVERSAL": 1.00},
    "SES-01":         {"TREND": 1.00, "CHOP": 1.00, "NORMAL": 1.0, "REVERSAL": 1.00},
    "GOLD-CORR-01":   {"TREND": 1.00, "CHOP": 1.00, "NORMAL": 1.0, "REVERSAL": 1.00},
    # V21 NEW: Extended session setups
    "ASIA-GOLD-01":   {"TREND": 1.20, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.85},
    "LONDON-GOLD-01": {"TREND": 1.20, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.90},
    "LONDON-FX-01":   {"TREND": 1.15, "CHOP": 0.85, "NORMAL": 1.0, "REVERSAL": 0.85},
    "LONDON-NQ-01":   {"TREND": 1.15, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.85},
    "PRE-RANGE-01":   {"TREND": 1.15, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.85},
    "NEWS-MOM-01":    {"TREND": 1.20, "CHOP": 0.70, "NORMAL": 1.0, "REVERSAL": 0.80},
    # V21 NEW: CL (crude oil) setups
    "CL-TREND-01":    {"TREND": 1.25, "CHOP": 0.70, "NORMAL": 1.0, "REVERSAL": 0.80},
    "CL-MOM-01":      {"TREND": 1.20, "CHOP": 0.75, "NORMAL": 1.0, "REVERSAL": 0.85},
    "CL-GAP-01":      {"TREND": 0.80, "CHOP": 1.15, "NORMAL": 1.0, "REVERSAL": 1.15},
    # V21 NEW: Cross-market speed exploit
    "ES-LEAD-01":     {"TREND": 1.25, "CHOP": 0.60, "NORMAL": 0.90, "REVERSAL": 0.70},
    # DISABLED
    "GAP-01":         {"TREND": 0.80, "CHOP": 0.85, "NORMAL": 1.0, "REVERSAL": 1.10},
    "MID-01":         {"TREND": 0.80, "CHOP": 1.15, "NORMAL": 1.0, "REVERSAL": 1.10},
    "MID-02":         {"TREND": 1.10, "CHOP": 0.75, "NORMAL": 0.95, "REVERSAL": 0.85},
    "PWR-01":         {"TREND": 1.10, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.85},
    "PWR-02":         {"TREND": 1.10, "CHOP": 0.75, "NORMAL": 1.0, "REVERSAL": 0.85},
    "PWR-03":         {"TREND": 0.80, "CHOP": 0.90, "NORMAL": 0.90, "REVERSAL": 1.10},
}


def get_regime_mult(setup_id: str, regime: str) -> float:
    mults = REGIME_MULT.get(setup_id, {})
    return mults.get(regime, 1.0)


# ─────────────────────────────────────────────────────────────────
# BAYESIAN CONVICTION — DATACLASSES
# ─────────────────────────────────────────────────────────────────

@dataclass
class ConfluenceDimension:
    name: str
    confirms: bool
    likelihood_ratio: float
    weight: float
    detail: str

@dataclass
class BayesianConviction:
    prior: float
    posterior: float
    dimensions: List[ConfluenceDimension]
    confirming: int
    contradicting: int
    total: int
    conviction_level: str

    @property
    def is_tradeable(self) -> bool:
        return self.conviction_level != "REJECT"


# ─────────────────────────────────────────────────────────────────
# MULTI-FRAMEWORK CONFLUENCE LENSES
# ─────────────────────────────────────────────────────────────────
# ICT, SMC, VWAP, supply/demand, support/resistance, candlestick,
# momentum, trend following, breakout, pullback, volume profile,
# ATR channels, correlation, divergence.
#
# These are evaluation LENSES, not signal generators. Each one
# asks: "does this framework agree with the proposed trade?"
# More agreement = higher conviction = bigger size.

def _evaluate_ict_order_blocks(mid: float, tracker, direction: str) -> float:
    """ICT order block lens: did price recently reject from a displacement zone?"""
    if len(tracker.price_history) < 20:
        return 1.0
    prices = tracker.price_history[-20:]
    # Look for bullish order block: sharp drop followed by strong reversal up
    # Look for bearish order block: sharp rally followed by strong reversal down
    for i in range(3, len(prices) - 1):
        segment = prices[i-3:i+1]
        move = segment[-1] - segment[0]
        reversal = prices[-1] - segment[-1]
        if direction == "long" and move < 0 and reversal > abs(move) * 0.5:
            return 1.8  # bullish OB confirmed
        if direction == "short" and move > 0 and reversal < -abs(move) * 0.5:
            return 1.8  # bearish OB confirmed
    return 1.0


def _evaluate_fair_value_gap(tracker, direction: str) -> float:
    """FVG lens: is there an unfilled gap the trade aligns with?"""
    closes = getattr(tracker, 'close_prices', [])
    if len(closes) < 4:
        return 1.0
    c1, c2, c3, c4 = closes[-4], closes[-3], closes[-2], closes[-1]
    bullish_fvg = c1 < c2 and c3 > c2 and c4 > c3
    bearish_fvg = c1 > c2 and c3 < c2 and c4 < c3
    if direction == "long" and bullish_fvg:
        return 1.6
    if direction == "short" and bearish_fvg:
        return 1.6
    if direction == "long" and bearish_fvg:
        return 0.7
    if direction == "short" and bullish_fvg:
        return 0.7
    return 1.0


def _evaluate_market_structure(tracker, direction: str) -> float:
    """SMC market structure shift lens: higher highs/lows or lower highs/lows?"""
    prices = tracker.price_history
    if len(prices) < 30:
        return 1.0
    # Simple swing detection
    segment = prices[-30:]
    highs = [segment[i] for i in range(1, len(segment)-1)
             if segment[i] > segment[i-1] and segment[i] > segment[i+1]]
    lows = [segment[i] for i in range(1, len(segment)-1)
            if segment[i] < segment[i-1] and segment[i] < segment[i+1]]
    if len(highs) >= 2 and len(lows) >= 2:
        hh = highs[-1] > highs[-2]  # higher high
        hl = lows[-1] > lows[-2]    # higher low
        lh = highs[-1] < highs[-2]  # lower high
        ll = lows[-1] < lows[-2]    # lower low
        if direction == "long" and hh and hl:
            return 1.7  # bullish structure
        if direction == "short" and lh and ll:
            return 1.7  # bearish structure
        if direction == "long" and lh and ll:
            return 0.6  # fighting bearish structure
        if direction == "short" and hh and hl:
            return 0.6  # fighting bullish structure
    return 1.0


def _evaluate_supply_demand(mid: float, tracker, direction: str, atr: float) -> float:
    """Supply/demand zone lens: is price at a reaction zone?"""
    prices = tracker.price_history
    if len(prices) < 40 or atr <= 0:
        return 1.0
    # Find zones where price bounced sharply (proxy for S/D)
    zone_threshold = atr * 0.02
    for i in range(5, len(prices) - 5):
        # Demand zone: price dropped to level, reversed sharply
        if direction == "long":
            low_area = min(prices[i-2:i+3])
            bounce = max(prices[i:i+5]) - low_area
            if abs(mid - low_area) < zone_threshold and bounce > atr * 0.3:
                return 1.5
        # Supply zone: price rallied to level, reversed down
        if direction == "short":
            high_area = max(prices[i-2:i+3])
            drop = high_area - min(prices[i:i+5])
            if abs(mid - high_area) < zone_threshold and drop > atr * 0.3:
                return 1.5
    return 1.0


def _evaluate_candlestick_patterns(tracker, direction: str) -> float:
    """Candlestick pattern lens: engulfing, pin bars, doji at key levels."""
    closes = getattr(tracker, 'close_prices', [])
    opens = getattr(tracker, 'open_prices', [])
    highs_list = getattr(tracker, 'high_prices', [])
    lows_list = getattr(tracker, 'low_prices', [])
    if len(closes) < 3:
        return 1.0
    # Simplified: check last 2 candles for engulfing
    if len(closes) >= 2 and len(opens) >= 2:
        prev_body = closes[-2] - opens[-2]
        curr_body = closes[-1] - opens[-1]
        if direction == "long" and prev_body < 0 and curr_body > 0 and abs(curr_body) > abs(prev_body):
            return 1.5  # bullish engulfing
        if direction == "short" and prev_body > 0 and curr_body < 0 and abs(curr_body) > abs(prev_body):
            return 1.5  # bearish engulfing
    # Pin bar detection
    if len(highs_list) >= 1 and len(lows_list) >= 1 and len(closes) >= 1 and len(opens) >= 1:
        body = abs(closes[-1] - opens[-1])
        upper_wick = highs_list[-1] - max(closes[-1], opens[-1])
        lower_wick = min(closes[-1], opens[-1]) - lows_list[-1]
        total_range = highs_list[-1] - lows_list[-1]
        if total_range > 0:
            if direction == "long" and lower_wick > body * 2 and lower_wick > upper_wick * 2:
                return 1.4  # bullish pin bar
            if direction == "short" and upper_wick > body * 2 and upper_wick > lower_wick * 2:
                return 1.4  # bearish pin bar
    return 1.0


def _evaluate_momentum(tracker, direction: str) -> float:
    """Momentum lens: rate of change and acceleration."""
    prices = tracker.price_history
    if len(prices) < 10:
        return 1.0
    roc_5 = (prices[-1] - prices[-5]) / prices[-5] if prices[-5] > 0 else 0
    roc_10 = (prices[-1] - prices[-10]) / prices[-10] if len(prices) >= 10 and prices[-10] > 0 else 0
    # Momentum confirms direction
    if direction == "long":
        if roc_5 > 0.002 and roc_10 > 0.003:
            return 1.6  # strong bullish momentum
        elif roc_5 > 0.001:
            return 1.2
        elif roc_5 < -0.002:
            return 0.7  # fighting momentum
    else:
        if roc_5 < -0.002 and roc_10 < -0.003:
            return 1.6  # strong bearish momentum
        elif roc_5 < -0.001:
            return 1.2
        elif roc_5 > 0.002:
            return 0.7
    return 1.0


def _evaluate_volume_profile(tracker, direction: str) -> float:
    """Volume profile lens: is volume expanding in trade direction?"""
    spreads = getattr(tracker, 'volume_history', [])
    if len(spreads) < 10:
        return 1.0
    recent = spreads[-5:]
    earlier = spreads[-10:-5]
    avg_recent = sum(recent) / len(recent) if recent else 1
    avg_earlier = sum(earlier) / len(earlier) if earlier else 1
    if avg_earlier <= 0:
        return 1.0
    ratio = avg_recent / avg_earlier
    if ratio > 1.5:
        return 1.4  # volume expanding — confirms move
    elif ratio < 0.5:
        return 0.8  # volume drying up
    return 1.0


def _evaluate_atr_channel(mid: float, tracker, direction: str, atr: float) -> float:
    """ATR channel lens: is price at channel extreme (mean revert) or breaking out?"""
    vwap = getattr(tracker, 'vwap', 0) or getattr(tracker, 'rth_vwap', 0)
    if vwap <= 0 or atr <= 0:
        return 1.0
    dist = (mid - vwap) / atr
    # For trend-following trades, being extended is good
    if direction == "long" and dist > 0.3 and dist < 1.0:
        return 1.3  # trending but not overextended
    if direction == "short" and dist < -0.3 and dist > -1.0:
        return 1.3
    # Overextended
    if direction == "long" and dist > 1.0:
        return 0.7  # overextended long
    if direction == "short" and dist < -1.0:
        return 0.7
    return 1.0


# ─────────────────────────────────────────────────────────────────
# BAYESIAN CONVICTION ENGINE — V21 BOOSTED
# ─────────────────────────────────────────────────────────────────

# BOOSTED LIKELIHOOD RATIOS — the core fix from the addendum
# V20: LRs were 1.1-1.3, producing max ~70% posterior
# V21: LRs are 1.5-2.5, producing 85-95% when everything aligns
BOOSTED_LR = {
    "regime_confirms":       2.5,   # was ~1.2
    "mtf_confirms":          2.0,   # was ~1.3
    "cross_market_confirms": 2.0,   # was ~1.1
    "vwap_position":         1.5,   # was ~1.2
    "momentum_confirms":     1.8,   # was ~1.2
    "ib_direction":          1.5,   # was ~1.1
    "vix_regime":            1.3,   # was ~1.1
    "session_state":         1.2,
    "candle_pattern":        1.5,
    "volume_confirms":       1.3,
}

# Momentum setups (benefit from energy)
MOMENTUM_SETUPS = frozenset({
    "ORD-02", "VOL-03", "ICT-03", "OD-01", "GAP-02",
    "IB-01", "VWAP-03", "ES-ORD-02", "CL-TREND-01", "CL-MOM-01",
    "LONDON-GOLD-01", "LONDON-NQ-01", "PRE-RANGE-01", "NEWS-MOM-01",
    "ES-LEAD-01",
})


def compute_bayesian_conviction(
    prior_win_rate: float,
    ctx,  # MarketContext
    tracker,  # InstrumentTracker
    direction: str,
    setup_id: str,
    live_win_rate: Optional[float] = None,
    calibrated_wr: Optional[float] = None,  # V21: GENESIS conviction calibration
) -> BayesianConviction:
    """
    V21 Bayesian conviction with BOOSTED likelihood ratios.
    When 6+ dimensions confirm, posterior reaches 85-95%.
    Calibrated WR from GENESIS overrides raw posterior for sizing.
    """
    # Prior selection: calibrated > live > base
    if calibrated_wr is not None:
        prior = calibrated_wr
    elif live_win_rate is not None:
        prior = live_win_rate
    else:
        prior = prior_win_rate
    prior = max(0.30, min(0.90, prior))

    dimensions: List[ConfluenceDimension] = []
    mid = tracker.price_history[-1] if hasattr(tracker, 'price_history') and tracker.price_history else 0
    vwap = (getattr(tracker, 'rth_vwap', 0) or
            getattr(tracker, 'vwap', 0) or
            getattr(tracker, 'open_price', 0) or mid)
    atr = getattr(ctx, 'atr', 100) or 100

    # ── DIM 1: Regime Alignment (BOOSTED) ─────────────────────────────────
    regime = getattr(ctx, 'regime', 'NORMAL')
    regime_bias = getattr(ctx, 'regime_bias', 'neutral')
    regime_confirms = (
        (regime == "TREND" and direction == regime_bias) or
        (regime == "REVERSAL" and direction == regime_bias)
    )
    regime_contradicts = (
        regime_bias != "neutral" and direction != regime_bias and regime == "TREND"
    )
    if regime_confirms:
        lr = BOOSTED_LR["regime_confirms"]  # 2.5
    elif regime_contradicts:
        lr = 1.0 / BOOSTED_LR["regime_confirms"]  # 0.4
    else:
        lr = 1.0
    dimensions.append(ConfluenceDimension(
        "Regime", regime_confirms, lr, 1.0,
        f"{regime}|{regime_bias} vs {direction} → LR={lr:.2f}"))

    # ── DIM 2: Multi-Timeframe Alignment (BOOSTED) ───────────────────────
    mtf_m15 = getattr(ctx, 'mtf_trend_m15', 'neutral')
    mtf_h1 = getattr(ctx, 'mtf_trend_h1', 'neutral')
    mtf_m5 = getattr(ctx, 'mtf_m5_confirms', False)
    mtf_aligned = mtf_m15 == direction and mtf_m5
    mtf_full = mtf_aligned and mtf_h1 == direction

    if mtf_full:
        lr = BOOSTED_LR["mtf_confirms"] * 1.15  # 2.3 — all timeframes agree
    elif mtf_aligned:
        lr = BOOSTED_LR["mtf_confirms"]  # 2.0
    elif mtf_m15 != "neutral" and mtf_m15 != direction:
        lr = 1.0 / BOOSTED_LR["mtf_confirms"]  # 0.5 — fighting M15
    else:
        lr = 1.0
    dimensions.append(ConfluenceDimension(
        "MTF", mtf_aligned, lr, 1.0,
        f"M15={mtf_m15} H1={mtf_h1} M5={mtf_m5} → LR={lr:.2f}"))

    # ── DIM 3: Cross-Market Confirmation (BOOSTED) ───────────────────────
    futures_bias = getattr(ctx, 'futures_bias', 'neutral')
    futures_aligns = (
        (direction == "long" and futures_bias in ("bullish", "strong_bullish")) or
        (direction == "short" and futures_bias in ("bearish", "strong_bearish"))
    )
    futures_contradicts = (
        (direction == "long" and futures_bias in ("bearish", "strong_bearish")) or
        (direction == "short" and futures_bias in ("bullish", "strong_bullish"))
    )
    if futures_aligns:
        lr = BOOSTED_LR["cross_market_confirms"]  # 2.0
    elif futures_contradicts:
        lr = 1.0 / BOOSTED_LR["cross_market_confirms"]  # 0.5
    else:
        lr = 1.0
    dimensions.append(ConfluenceDimension(
        "Cross-Market", futures_aligns, lr, 1.0,
        f"Futures={futures_bias} vs {direction} → LR={lr:.2f}"))

    # ── DIM 4: VWAP Position (BOOSTED) ────────────────────────────────────
    if mid > 0 and vwap > 0:
        above_vwap = mid > vwap
        aligns = (direction == "long" and above_vwap) or (direction == "short" and not above_vwap)
        lr = BOOSTED_LR["vwap_position"] if aligns else (1.0 / BOOSTED_LR["vwap_position"])
    else:
        lr = 1.0
        aligns = True
    dimensions.append(ConfluenceDimension(
        "VWAP", aligns, lr, 1.0,
        f"Price {'above' if mid > vwap else 'below'} VWAP → LR={lr:.2f}"))

    # ── DIM 5: Momentum (BOOSTED) ─────────────────────────────────────────
    mom_lr = _evaluate_momentum(tracker, direction)
    # Scale the lens result into boosted range
    if mom_lr > 1.3:
        lr = BOOSTED_LR["momentum_confirms"]  # 1.8
    elif mom_lr > 1.1:
        lr = 1.4
    elif mom_lr < 0.8:
        lr = 1.0 / BOOSTED_LR["momentum_confirms"]  # 0.56
    else:
        lr = 1.0
    dimensions.append(ConfluenceDimension(
        "Momentum", lr > 1.0, lr, 1.0,
        f"Momentum={mom_lr:.2f} → LR={lr:.2f}"))

    # ── DIM 6: IB Direction (BOOSTED) ─────────────────────────────────────
    ib_locked = getattr(ctx, 'ib_locked', False) or getattr(tracker, 'ib_locked', False)
    ib_dir = getattr(ctx, 'ib_direction', None)
    if ib_locked and ib_dir and ib_dir != "none":
        ib_aligns = ib_dir == direction
        lr = BOOSTED_LR["ib_direction"] if ib_aligns else (1.0 / BOOSTED_LR["ib_direction"])
    else:
        lr = 1.0
        ib_aligns = True
    dimensions.append(ConfluenceDimension(
        "IB Direction", ib_aligns, lr, 1.0 if ib_locked else 0.0,
        f"IB={ib_dir} vs {direction} → LR={lr:.2f}"))

    # ── DIM 7: VIX Regime ─────────────────────────────────────────────────
    vix = getattr(ctx, 'vix', 20)
    vix_regime = getattr(ctx, 'vix_regime', 'NORMAL')
    if vix_regime == "LOW":
        lr = BOOSTED_LR["vix_regime"]  # 1.3
    elif vix_regime == "NORMAL":
        lr = 1.1
    elif vix_regime == "ELEVATED":
        lr = 0.80
    else:  # EXTREME
        lr = 0.60
    dimensions.append(ConfluenceDimension(
        "VIX", lr > 1.0, lr, 0.8,
        f"VIX={vix:.1f} ({vix_regime}) → LR={lr:.2f}"))

    # ── DIM 8: ATR Budget ─────────────────────────────────────────────────
    atr_consumed = getattr(ctx, 'atr_consumed_pct', 0)
    if atr_consumed < 0.40:
        lr = 1.3
    elif atr_consumed < 0.60:
        lr = 1.1
    elif atr_consumed < 0.80:
        lr = 0.80
    else:
        lr = 0.50
    dimensions.append(ConfluenceDimension(
        "ATR Budget", lr > 1.0, lr, 0.85,
        f"ATR {atr_consumed:.0%} consumed → LR={lr:.2f}"))

    # ── DIM 9: Session State ──────────────────────────────────────────────
    session_state = getattr(ctx, 'session_state', None)
    state_val = session_state.value if hasattr(session_state, 'value') else str(session_state)
    if state_val in ("IB_FORMATION", "MID_MORNING"):
        lr = BOOSTED_LR["session_state"]  # 1.2
    elif state_val in ("OPENING_DRIVE", "POWER_HOUR"):
        lr = 1.1
    elif state_val == "LUNCH_CHOP":
        lr = 0.75
    elif state_val == "CLOSE_POSITION":
        lr = 0.50
    else:
        lr = 0.95
    dimensions.append(ConfluenceDimension(
        "Session", lr > 1.0, lr, 0.75,
        f"State={state_val} → LR={lr:.2f}"))

    # ── DIM 10: Multi-Framework Confluence (V21 NEW) ──────────────────────
    # Aggregate all framework lenses into one super-dimension
    framework_scores = []
    framework_scores.append(_evaluate_ict_order_blocks(mid, tracker, direction))
    framework_scores.append(_evaluate_fair_value_gap(tracker, direction))
    framework_scores.append(_evaluate_market_structure(tracker, direction))
    framework_scores.append(_evaluate_supply_demand(mid, tracker, direction, atr))
    framework_scores.append(_evaluate_candlestick_patterns(tracker, direction))
    framework_scores.append(_evaluate_volume_profile(tracker, direction))
    framework_scores.append(_evaluate_atr_channel(mid, tracker, direction, atr))

    # Geometric mean of framework scores
    product = 1.0
    for s in framework_scores:
        product *= s
    geo_mean = product ** (1.0 / len(framework_scores))

    # Count how many frameworks confirm (LR > 1.2)
    confirming_frameworks = sum(1 for s in framework_scores if s > 1.2)

    # Scale: 0 frameworks = 1.0, 3+ = 1.8, 5+ = 2.2
    if confirming_frameworks >= 5:
        lr = 2.2
    elif confirming_frameworks >= 3:
        lr = 1.8
    elif confirming_frameworks >= 2:
        lr = 1.4
    elif confirming_frameworks >= 1:
        lr = 1.2
    elif geo_mean < 0.8:
        lr = 0.6  # multiple frameworks say no
    else:
        lr = 1.0

    dimensions.append(ConfluenceDimension(
        "Frameworks", lr > 1.0, lr, 1.0,
        f"{confirming_frameworks}/7 frameworks confirm (geo={geo_mean:.2f}) → LR={lr:.2f}"))

    # ── DIM 11: Move Energy ───────────────────────────────────────────────
    energy = compute_move_energy(tracker)
    if setup_id in MOMENTUM_SETUPS:
        lr = 1.3 if energy > 0.6 else (0.70 if energy < 0.3 else 1.0)
    else:
        lr = 1.3 if energy < 0.4 else (0.70 if energy > 0.7 else 1.0)
    dimensions.append(ConfluenceDimension(
        "Energy", lr > 1.0, lr, 0.6,
        f"Energy={energy:.2f} → LR={lr:.2f}"))

    # ═══ COMPUTE POSTERIOR ════════════════════════════════════════════════
    prior_odds = prior / (1.0 - prior) if prior < 1.0 else 100.0
    combined_lr = 1.0
    for dim in dimensions:
        if dim.weight > 0:
            # V21: NO weight dampening on boosted dimensions
            # V20 did: adjusted_lr = 1.0 + (dim.lr - 1.0) * dim.weight
            # V21: full LR multiplication for weight=1.0 dims
            if dim.weight >= 1.0:
                combined_lr *= dim.likelihood_ratio
            else:
                adjusted_lr = 1.0 + (dim.likelihood_ratio - 1.0) * dim.weight
                combined_lr *= adjusted_lr

    posterior_odds = prior_odds * combined_lr
    posterior = posterior_odds / (1.0 + posterior_odds)
    posterior = max(0.05, min(0.98, posterior))

    # Apply regime multiplier
    regime_m = get_regime_mult(setup_id, regime)
    if regime_m <= 0.0:
        posterior = 0.0
    elif regime_m != 1.0:
        adjusted_odds = posterior_odds * regime_m
        posterior = adjusted_odds / (1.0 + adjusted_odds)
        posterior = max(0.05, min(0.98, posterior))

    # Time-of-day multiplier
    try:
        from forge_sessions import now_et_time
        _tod = now_et_time()
    except ImportError:
        _tod = dtime(12, 0)

    if dtime(9, 30) <= _tod < dtime(10, 0):
        tod_mult = 0.95
    elif dtime(10, 0) <= _tod < dtime(11, 30):
        tod_mult = 1.05
    elif dtime(11, 30) <= _tod < dtime(13, 0):
        tod_mult = 0.90
    elif dtime(14, 0) <= _tod < dtime(15, 30):
        tod_mult = 1.05
    else:
        tod_mult = 1.00
    posterior = max(0.05, min(0.98, posterior * tod_mult))

    confirming = sum(1 for d in dimensions if d.confirms and d.weight > 0)
    contradicting = sum(1 for d in dimensions if not d.confirms and d.weight > 0)
    total = sum(1 for d in dimensions if d.weight > 0)

    # V21 CONVICTION LEVELS — recalibrated for boosted posteriors
    if posterior >= 0.88 and confirming >= 8:
        level = "ELITE"
    elif posterior >= 0.78 and confirming >= 6:
        level = "HIGH"
    elif posterior >= 0.65 and confirming >= 4:
        level = "STANDARD"
    elif posterior >= 0.52:
        level = "REDUCED"
    elif posterior >= 0.40 and confirming >= 3:
        level = "SCALP"
    else:
        level = "REJECT"

    logger.info("[BAYES] %s %s: Prior=%.1f%% → Post=%.1f%% | %d/%d dims | "
                "R=%s×%.2f | Frameworks=%d/7 | %s",
                setup_id, direction, prior * 100, posterior * 100,
                confirming, total, regime, regime_m, confirming_frameworks, level)

    return BayesianConviction(
        prior=prior, posterior=posterior, dimensions=dimensions,
        confirming=confirming, contradicting=contradicting, total=total,
        conviction_level=level,
    )


# ─────────────────────────────────────────────────────────────────
# ENTROPY & ENERGY (unchanged from v20)
# ─────────────────────────────────────────────────────────────────

def compute_price_entropy(tracker) -> float:
    prices = getattr(tracker, 'price_history', [])
    if len(prices) < 20:
        return 0.50
    returns = [(prices[i] - prices[i-1]) / prices[i-1]
               for i in range(1, len(prices)) if prices[i-1] > 0]
    if not returns:
        return 0.50
    threshold = 0.0005
    big_threshold = threshold * 3
    bins = {"big_down": 0, "small_down": 0, "flat": 0, "small_up": 0, "big_up": 0}
    for r in returns:
        if r < -big_threshold:     bins["big_down"] += 1
        elif r < -threshold:       bins["small_down"] += 1
        elif r > big_threshold:    bins["big_up"] += 1
        elif r > threshold:        bins["small_up"] += 1
        else:                      bins["flat"] += 1
    total = len(returns)
    probs = [count / total for count in bins.values() if count > 0]
    if not probs:
        return 0.50
    entropy = -sum(p * math.log2(p) for p in probs)
    max_entropy = math.log2(5)
    return entropy / max_entropy


def compute_move_energy(tracker) -> float:
    prices = getattr(tracker, 'price_history', [])
    spreads = getattr(tracker, 'volume_history', [])
    if len(prices) < 10 or len(spreads) < 10:
        return 0.50
    n = min(20, len(prices))
    recent_prices = prices[-n:]
    recent_spreads = spreads[-n:]
    total_move = abs(recent_prices[-1] - recent_prices[0])
    avg_price = sum(recent_prices) / len(recent_prices)
    move_pct = total_move / avg_price if avg_price > 0 else 0
    avg_spread = sum(recent_spreads) / len(recent_spreads) if recent_spreads else 1.0
    baseline_spread = sum(spreads) / len(spreads) if spreads else avg_spread
    spread_ratio = baseline_spread / avg_spread if avg_spread > 0 else 1.0
    energy = min(1.0, move_pct * 500) * min(1.5, spread_ratio)
    return max(0.0, min(1.0, energy))


# ─────────────────────────────────────────────────────────────────
# EXPECTED VALUE CALCULATOR (unchanged from v20)
# ─────────────────────────────────────────────────────────────────

@dataclass
class ExpectedValueResult:
    ev_dollars: float
    win_probability: float
    expected_reward: float
    expected_risk: float
    reward_risk_ratio: float
    kelly_fraction: float
    constrained_kelly: float
    opportunity_cost: float
    net_ev: float
    action: str


def compute_expected_value(
    win_prob: float, reward_dollars: float, risk_dollars: float,
    account_balance: float, max_position_pct: float,
    minutes_remaining: float, avg_setups_per_hour: float = 1.5,
) -> ExpectedValueResult:
    ev = (win_prob * reward_dollars) - ((1.0 - win_prob) * risk_dollars)
    rr = reward_dollars / risk_dollars if risk_dollars > 0 else 0.0
    b, p, q = rr, win_prob, 1.0 - win_prob
    kelly_raw = max(0.0, ((b * p) - q) / b) if b > 0 else 0.0
    constrained = min(kelly_raw * 0.25, max_position_pct)

    hours_left = minutes_remaining / 60.0
    expected_future_setups = hours_left * avg_setups_per_hour
    avg_future_ev = ev * 0.80
    prob_better = 1.0 - (1.0 - 0.30) ** max(1, int(expected_future_setups))
    opportunity_cost = prob_better * avg_future_ev * 1.10
    net_ev = ev - opportunity_cost

    if net_ev <= 0 or ev <= 0:
        action = "SKIP"
    elif net_ev < ev * 0.30 and minutes_remaining > 120:
        action = "WAIT"
    else:
        action = "TRADE"

    return ExpectedValueResult(
        ev_dollars=round(ev, 2), win_probability=round(win_prob, 4),
        expected_reward=round(reward_dollars, 2), expected_risk=round(risk_dollars, 2),
        reward_risk_ratio=round(rr, 2), kelly_fraction=round(kelly_raw, 4),
        constrained_kelly=round(constrained, 4), opportunity_cost=round(opportunity_cost, 2),
        net_ev=round(net_ev, 2), action=action,
    )


# ─────────────────────────────────────────────────────────────────
# MONTE CARLO STRESS TEST (unchanged)
# ─────────────────────────────────────────────────────────────────

@dataclass
class StressTestResult:
    scenarios_run: int
    worst_case_pnl: float
    median_outcome: float
    best_case_pnl: float
    prob_daily_limit_breach: float
    prob_max_loss_breach: float
    risk_approved: bool
    reason: str


def monte_carlo_stress_test(
    current_pnl: float, proposed_risk: float, win_prob: float,
    current_positions: int, open_risk: float, daily_limit: float,
    max_loss: float, current_equity: float, vix: float,
    n_scenarios: int = 500,
) -> StressTestResult:
    import random
    outcomes = []
    tail_mult = 1.0 + max(0, (vix - 20)) * 0.05
    for _ in range(n_scenarios):
        scenario_pnl = current_pnl
        if random.random() < win_prob:
            scenario_pnl += proposed_risk * 2.0
        else:
            tail = 1.0 + random.random() * 0.3 * tail_mult
            scenario_pnl -= proposed_risk * tail
        for _ in range(current_positions):
            pos_risk = open_risk / max(1, current_positions)
            if random.random() < 0.55:
                scenario_pnl += pos_risk * 1.5
            else:
                tail = 1.0 + random.random() * 0.2 * tail_mult
                scenario_pnl -= pos_risk * tail
        outcomes.append(scenario_pnl)
    outcomes.sort()
    worst_5 = outcomes[int(n_scenarios * 0.05)]
    median = outcomes[int(n_scenarios * 0.50)]
    best_95 = outcomes[int(n_scenarios * 0.95)]
    daily_breach_count = sum(1 for o in outcomes if o < -daily_limit)
    max_breach_count = sum(1 for o in outcomes
                           if (current_equity + o) < (current_equity - max_loss))
    prob_daily = daily_breach_count / n_scenarios
    prob_max = max_breach_count / n_scenarios
    approved = prob_daily < 0.05 and prob_max < 0.02
    reason = "Stress PASSED" if approved else \
             f"FAILED: P(daily)={prob_daily:.1%}, P(max)={prob_max:.1%}"
    return StressTestResult(
        scenarios_run=n_scenarios, worst_case_pnl=round(worst_5, 2),
        median_outcome=round(median, 2), best_case_pnl=round(best_95, 2),
        prob_daily_limit_breach=round(prob_daily, 4),
        prob_max_loss_breach=round(prob_max, 4),
        risk_approved=approved, reason=reason,
    )


# ─────────────────────────────────────────────────────────────────
# PARAMETER EVOLUTION + NON-REACTION + REGIME TRANSITION
# ─────────────────────────────────────────────────────────────────

class ParameterEvolver:
    def __init__(self):
        self._setup_stats: Dict[str, dict] = {}

    def update_from_evidence(self, evidence_records: list) -> None:
        stats: Dict[str, dict] = {}
        for r in evidence_records:
            sid = r.get("setup_id", "")
            outcome = r.get("outcome", "")
            if outcome not in ("WIN", "LOSS") or not sid:
                continue
            if sid not in stats:
                stats[sid] = {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": 0}
            stats[sid]["trades"] += 1
            if outcome == "WIN":
                stats[sid]["wins"] += 1
            else:
                stats[sid]["losses"] += 1
            stats[sid]["total_pnl"] += r.get("pnl", 0)
        for sid, s in stats.items():
            if s["trades"] > 0:
                self._setup_stats[sid] = {
                    "win_rate": s["wins"] / s["trades"],
                    "avg_pnl": s["total_pnl"] / s["trades"],
                    "trades": s["trades"],
                }

    def get_live_win_rate(self, setup_id: str) -> Optional[float]:
        stats = self._setup_stats.get(setup_id)
        if stats and stats["trades"] >= 15:
            return stats["win_rate"]
        return None

    def get_degradation_alert(self) -> Optional[str]:
        alerts = []
        for sid, stats in self._setup_stats.items():
            if stats["trades"] < 20:
                continue
            wr = stats["win_rate"]
            if wr < 0.50:
                alerts.append(f"{sid}: WR={wr:.0%} (critical)")
            elif wr < 0.55:
                alerts.append(f"{sid}: WR={wr:.0%} (degrading)")
        return ("DEGRADATION: " + " | ".join(alerts)) if alerts else None


def detect_non_reaction(ctx, tracker) -> Optional[str]:
    prices = getattr(tracker, 'price_history', [])
    if len(prices) < 10:
        return None
    recent_move = abs(prices[-1] - prices[-10])
    atr = getattr(ctx, 'atr', 100) or 100
    expected_move = atr * 0.05
    vix = getattr(ctx, 'vix', 20)
    futures_pct = getattr(ctx, 'futures_pct', 0)
    futures_bias = getattr(ctx, 'futures_bias', 'neutral')
    if vix >= 25 and recent_move < expected_move * 0.3:
        return "NON-REACTION: VIX elevated but price stable"
    if abs(futures_pct) > 0.005 and recent_move < expected_move * 0.2:
        if futures_bias in ("strong_bearish", "bearish"):
            return "NON-REACTION: Bearish futures but price holding"
        elif futures_bias in ("strong_bullish", "bullish"):
            return "NON-REACTION: Bullish futures but price stalling"
    return None


def predict_regime_transition(ctx, tracker) -> tuple:
    indicators = []
    atr_consumed = getattr(ctx, 'atr_consumed_pct', 0)
    if atr_consumed > 0.85:
        indicators.append(0.25)
    elif atr_consumed > 0.75:
        indicators.append(0.15)
    prices = getattr(tracker, 'price_history', [])
    if len(prices) >= 50:
        entropy = compute_price_entropy(tracker)
        if entropy > 0.70:
            indicators.append(0.20)
    if not indicators:
        return 0.05, "Regime stable"
    prob = min(0.85, sum(indicators))
    return prob, f"Transition prob: {prob:.0%}"


_evolver = ParameterEvolver()

def get_evolver() -> ParameterEvolver:
    return _evolver
