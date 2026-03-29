"""
FORGE v22 — Signal Generation Engine
======================================
10 proven strategies from 5 research runs on real Polygon data.
Every instrument is mean-reverting. Trend-following is DEAD.

Philosophy: "ALL GAS FIRST THEN BRAKES"
- Single strategy signal is enough to enter
- Conviction threshold: 0.20 (NOT 0.35)
- The intelligence is in TRADE MANAGEMENT, not trade filtering
"""

import logging
import numpy as np
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from forge_instruments_v22 import (
    Strategy, TradeType, OrderType, Direction,
    InstrumentSetup, SETUP_CONFIG, STRATEGY_DEFAULTS,
    TIME_OF_DAY_EDGES, TOD_EDGE_BOOST, TOD_SUPPRESS,
    MONTHLY_SEASONALITY, get_sl_tp_for_direction,
)

logger = logging.getLogger("FORGE.signals")


# ─── Signal Output ───────────────────────────────────────────────────────────

@dataclass
class Signal:
    """Output from signal generation — everything needed to place a trade."""
    symbol: str
    strategy: Strategy
    direction: str              # "LONG" or "SHORT"
    trade_type: TradeType       # SCALP or RUNNER
    order_type: OrderType       # LIMIT or MARKET
    entry_price: float          # Exact entry price (for LIMIT) or current price (for MARKET)
    sl_price: float             # Stop loss price
    tp_price: float             # Take profit price
    sl_atr_mult: float          # SL in ATR multiples (for reference)
    tp_atr_mult: float          # TP in ATR multiples (for reference)
    risk_pct: float             # Risk % of balance
    raw_confidence: float       # Pre-boost confidence [0-1]
    final_confidence: float     # After time-of-day + seasonality boost
    atr_value: float            # Current ATR value
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Limit order specifics
    limit_offset_atr: float = 0.2       # How far from signal to place limit
    limit_valid_bars: int = 5           # Bars before limit expires
    # Runner specifics
    partial_pct: float = 0.50           # Partial exit at 1R (50%)
    trailing_r: float = 1.5             # Trailing stop distance in R
    # Context fingerprint (for Ghost + evidence logging)
    context: Dict = field(default_factory=dict)


# ─── Market Data Container ───────────────────────────────────────────────────

@dataclass
class MarketSnapshot:
    """All market data needed for signal generation on one instrument."""
    symbol: str
    # Price data (most recent candles, newest last)
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    # Current tick
    bid: float
    ask: float
    # Pre-computed indicators (computed in forge_market.py)
    atr: float                  # 14-period ATR
    rsi: float                  # 14-period RSI
    stoch_k: float              # Stochastic %K (14,3)
    stoch_d: float              # Stochastic %D (14,3)
    stoch_k_prev: float         # Previous %K
    stoch_d_prev: float         # Previous %D
    ema_50: float               # 50-period EMA
    ema_200: float              # 200-period EMA
    bb_upper: float             # Bollinger upper (20, 2.0)
    bb_lower: float             # Bollinger lower (20, 2.0)
    bb_middle: float            # Bollinger middle (20-SMA)
    vwap: float                 # Session VWAP
    vwap_std: float             # VWAP standard deviation
    adx: float                  # ADX (14)
    adx_prev: float             # ADX 5 bars ago
    plus_di: float              # +DI
    minus_di: float             # -DI
    # Session data
    prev_day_high: float
    prev_day_low: float
    prev_day_close: float
    session_open: float
    session_high: float
    session_low: float
    # Opening range (first 30 min)
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_complete: bool = False
    # Asian range (00:00-07:00 UTC)
    asian_high: Optional[float] = None
    asian_low: Optional[float] = None
    asian_complete: bool = False
    # Keltner channels (for vol compress)
    keltner_upper: Optional[float] = None
    keltner_lower: Optional[float] = None
    # Bar count since session open
    bars_since_open: int = 0
    # Current UTC hour
    current_hour_utc: int = 0


# ─── Helper Functions ────────────────────────────────────────────────────────

def _is_rejection_candle(opens, highs, lows, closes, direction: str) -> bool:
    """Check if the last candle is a rejection (long wick) in the given direction.
    
    For LONG rejection: lower wick > 2x body size
    For SHORT rejection: upper wick > 2x body size
    """
    if len(closes) < 1:
        return False
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    if body < 1e-10:
        body = 1e-10  # Avoid division by zero

    if direction == "LONG":
        lower_wick = min(o, c) - l
        return lower_wick > 2.0 * body
    else:  # SHORT
        upper_wick = h - max(o, c)
        return upper_wick > 2.0 * body


def _is_reversal_candle(opens, highs, lows, closes, against_direction: str) -> bool:
    """Check for engulfing or pin bar AGAINST our trade direction.
    
    against_direction is the direction that would hurt us:
    - If we are LONG, against = "SHORT" (bearish reversal hurts)
    - If we are SHORT, against = "LONG" (bullish reversal hurts)
    """
    if len(closes) < 2:
        return False

    o1, c1 = opens[-2], closes[-2]
    o2, h2, l2, c2 = opens[-1], highs[-1], lows[-1], closes[-1]
    body1 = abs(c1 - o1)
    body2 = abs(c2 - o2)

    if against_direction == "LONG":
        # Bullish engulfing or bullish pin bar
        bullish_engulf = c2 > o2 and body2 > body1 * 1.5 and c2 > max(o1, c1)
        lower_wick = min(o2, c2) - l2
        bullish_pin = lower_wick > 2.5 * body2 and c2 > o2
        return bullish_engulf or bullish_pin
    else:
        # Bearish engulfing or bearish pin bar
        bearish_engulf = c2 < o2 and body2 > body1 * 1.5 and c2 < min(o1, c1)
        upper_wick = h2 - max(o2, c2)
        bearish_pin = upper_wick > 2.5 * body2 and c2 < o2
        return bearish_engulf or bearish_pin


def _volume_spike(volumes: np.ndarray, lookback: int = 20, threshold: float = 2.0) -> bool:
    """Check if current volume is a spike (>threshold x average)."""
    if len(volumes) < lookback + 1:
        return False
    avg_vol = np.mean(volumes[-lookback - 1:-1])
    if avg_vol < 1e-10:
        return False
    return volumes[-1] > threshold * avg_vol


# ─── Strategy Implementations ────────────────────────────────────────────────

def _signal_mean_revert(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """MEAN REVERT: RSI extremes + Bollinger Band breach.
    
    LONG: RSI < 30 + price below BB lower
    SHORT: RSI > 70 + price above BB upper
    """
    price = snap.closes[-1]
    direction = None
    confidence = 0.0

    if snap.rsi < 30 and price < snap.bb_lower:
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            # Confidence scales with how extreme the RSI is
            confidence = 0.30 + (30 - snap.rsi) / 100  # 0.30-0.60
    elif snap.rsi > 70 and price > snap.bb_upper:
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            confidence = 0.30 + (snap.rsi - 70) / 100

    if direction is None:
        return None

    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    offset = 0.2 * snap.atr
    if direction == "LONG":
        entry = price - offset
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price + offset
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    return Signal(
        symbol=snap.symbol, strategy=Strategy.MEAN_REVERT,
        direction=direction, trade_type=TradeType.SCALP,
        order_type=OrderType.LIMIT, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"rsi": snap.rsi, "bb_lower": snap.bb_lower, "bb_upper": snap.bb_upper},
    )


def _signal_vwap_revert(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """VWAP REVERT: Price at 2 std from VWAP with rejection candle.
    
    LONG: Price < VWAP - 2*std + rejection candle (long lower wick)
    SHORT: Price > VWAP + 2*std + rejection candle (long upper wick)
    """
    price = snap.closes[-1]
    vwap_upper = snap.vwap + 2.0 * snap.vwap_std
    vwap_lower = snap.vwap - 2.0 * snap.vwap_std
    direction = None
    confidence = 0.0

    if price < vwap_lower and _is_rejection_candle(snap.opens, snap.highs, snap.lows, snap.closes, "LONG"):
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            dist = abs(price - snap.vwap) / snap.vwap_std if snap.vwap_std > 0 else 2.0
            confidence = min(0.25 + dist * 0.10, 0.60)
    elif price > vwap_upper and _is_rejection_candle(snap.opens, snap.highs, snap.lows, snap.closes, "SHORT"):
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            dist = abs(price - snap.vwap) / snap.vwap_std if snap.vwap_std > 0 else 2.0
            confidence = min(0.25 + dist * 0.10, 0.60)

    if direction is None:
        return None

    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    offset = 0.2 * snap.atr
    if direction == "LONG":
        entry = price - offset
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price + offset
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    return Signal(
        symbol=snap.symbol, strategy=Strategy.VWAP_REVERT,
        direction=direction, trade_type=TradeType.SCALP,
        order_type=OrderType.LIMIT, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"vwap": snap.vwap, "vwap_std": snap.vwap_std, "distance_std": dist},
    )


def _signal_stoch_reversal(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """STOCH REVERSAL: %K crosses %D from oversold/overbought zones.
    
    LONG: %K crosses above %D from below 20
    SHORT: %K crosses above %D from above 80 (bearish) — wait, that's wrong.
    SHORT: %K crosses below %D from above 80
    """
    direction = None
    confidence = 0.0

    # Bullish crossover from oversold
    if (snap.stoch_k_prev < snap.stoch_d_prev and snap.stoch_k > snap.stoch_d
            and snap.stoch_k < 25):  # Recently oversold
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            confidence = 0.30 + (20 - min(snap.stoch_k_prev, 20)) / 40  # More oversold = more confident

    # Bearish crossover from overbought
    elif (snap.stoch_k_prev > snap.stoch_d_prev and snap.stoch_k < snap.stoch_d
            and snap.stoch_k > 75):  # Recently overbought
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            confidence = 0.30 + (min(snap.stoch_k_prev, 100) - 80) / 40

    if direction is None:
        return None

    price = snap.closes[-1]
    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    offset = 0.2 * snap.atr
    if direction == "LONG":
        entry = price - offset
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price + offset
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    return Signal(
        symbol=snap.symbol, strategy=Strategy.STOCH_REVERSAL,
        direction=direction, trade_type=TradeType.SCALP,
        order_type=OrderType.LIMIT, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"stoch_k": snap.stoch_k, "stoch_d": snap.stoch_d,
                 "stoch_k_prev": snap.stoch_k_prev},
    )


def _signal_ema_bounce(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """EMA BOUNCE: Price touches EMA 50 and bounces, EMA 50 > EMA 200 for trend.
    
    LONG: Price touches EMA50 from above + bounces + EMA50 > EMA200
    SHORT: Price touches EMA50 from below + bounces + EMA50 < EMA200
    Can upgrade to RUNNER if trend is strong (ADX > 30).
    """
    price = snap.closes[-1]
    prev_price = snap.closes[-2] if len(snap.closes) >= 2 else price
    direction = None
    confidence = 0.0

    # Price within 0.3 ATR of EMA50 = "touching"
    ema_dist = abs(price - snap.ema_50) / snap.atr if snap.atr > 0 else 999

    if ema_dist < 0.3:
        if snap.ema_50 > snap.ema_200 and price > snap.ema_50:
            # Bullish bounce — price touched EMA50 and bounced up
            if prev_price <= snap.ema_50 * 1.001:  # Was near or below
                if setup.direction in (Direction.LONG, Direction.BOTH):
                    direction = "LONG"
                    confidence = 0.30 + min(ema_dist, 0.3) * 0.50  # Closer touch = higher confidence
        elif snap.ema_50 < snap.ema_200 and price < snap.ema_50:
            # Bearish bounce — price touched EMA50 and bounced down
            if prev_price >= snap.ema_50 * 0.999:
                if setup.direction in (Direction.SHORT, Direction.BOTH):
                    direction = "SHORT"
                    confidence = 0.30 + min(ema_dist, 0.3) * 0.50

    if direction is None:
        return None

    # Determine trade type: RUNNER if strong trend (ADX > 30)
    trade_type = TradeType.RUNNER if snap.adx > 30 else TradeType.SCALP

    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    offset = 0.2 * snap.atr
    if direction == "LONG":
        entry = price - offset
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price + offset
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    return Signal(
        symbol=snap.symbol, strategy=Strategy.EMA_BOUNCE,
        direction=direction, trade_type=trade_type,
        order_type=OrderType.LIMIT, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"ema_50": snap.ema_50, "ema_200": snap.ema_200,
                 "ema_dist_atr": ema_dist, "adx": snap.adx,
                 "upgraded_to_runner": trade_type == TradeType.RUNNER},
    )


def _signal_prev_day_hl(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """PREV DAY HL: Breakout above previous day high or below previous day low.
    
    LONG: Price breaks above prev day high
    SHORT: Price breaks below prev day low
    MARKET order — breakouts need speed. RUNNER trade type.
    """
    price = snap.closes[-1]
    prev_price = snap.closes[-2] if len(snap.closes) >= 2 else price
    direction = None
    confidence = 0.0

    # Break above prev day high
    if price > snap.prev_day_high and prev_price <= snap.prev_day_high:
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            # Confidence based on how clean the break is
            break_pct = (price - snap.prev_day_high) / snap.atr if snap.atr > 0 else 0
            confidence = 0.30 + min(break_pct * 0.15, 0.25)

    # Break below prev day low
    elif price < snap.prev_day_low and prev_price >= snap.prev_day_low:
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            break_pct = (snap.prev_day_low - price) / snap.atr if snap.atr > 0 else 0
            confidence = 0.30 + min(break_pct * 0.15, 0.25)

    if direction is None:
        return None

    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    if direction == "LONG":
        entry = price
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    return Signal(
        symbol=snap.symbol, strategy=Strategy.PREV_DAY_HL,
        direction=direction, trade_type=TradeType.RUNNER,
        order_type=OrderType.MARKET, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"prev_high": snap.prev_day_high, "prev_low": snap.prev_day_low,
                 "break_type": "HIGH" if direction == "LONG" else "LOW"},
    )


def _signal_orb(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """ORB (Opening Range Breakout): Break of first 30min high/low.
    
    Requires ORB to be complete (30 min elapsed since session open).
    MARKET order, RUNNER trade type.
    """
    if not snap.orb_complete or snap.orb_high is None or snap.orb_low is None:
        return None

    price = snap.closes[-1]
    prev_price = snap.closes[-2] if len(snap.closes) >= 2 else price
    direction = None
    confidence = 0.0

    # Break above ORB high
    if price > snap.orb_high and prev_price <= snap.orb_high:
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            orb_range = snap.orb_high - snap.orb_low
            # Tighter range = better breakout (more coiled energy)
            if snap.atr > 0 and orb_range > 0:
                range_ratio = orb_range / snap.atr
                confidence = 0.35 + max(0, (1.0 - range_ratio) * 0.15)
            else:
                confidence = 0.30

    # Break below ORB low
    elif price < snap.orb_low and prev_price >= snap.orb_low:
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            orb_range = snap.orb_high - snap.orb_low
            if snap.atr > 0 and orb_range > 0:
                range_ratio = orb_range / snap.atr
                confidence = 0.35 + max(0, (1.0 - range_ratio) * 0.15)
            else:
                confidence = 0.30

    if direction is None:
        return None

    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    if direction == "LONG":
        entry = price
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    return Signal(
        symbol=snap.symbol, strategy=Strategy.ORB,
        direction=direction, trade_type=TradeType.RUNNER,
        order_type=OrderType.MARKET, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"orb_high": snap.orb_high, "orb_low": snap.orb_low,
                 "orb_range": snap.orb_high - snap.orb_low},
    )


def _signal_gap_fill(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """GAP FILL: Fade session gaps > 0.5 ATR back toward previous close.
    
    Only fires near session open (first 10 bars).
    LIMIT order, SCALP trade type.
    """
    if snap.bars_since_open > 10:
        return None

    gap = snap.session_open - snap.prev_day_close
    gap_atr = abs(gap) / snap.atr if snap.atr > 0 else 0

    if gap_atr < 0.5:
        return None  # Gap too small

    price = snap.closes[-1]
    direction = None
    confidence = 0.0

    if gap > 0:
        # Gap up — fade it SHORT (expect price to fill back down)
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            confidence = 0.35 + min(gap_atr * 0.08, 0.25)
    else:
        # Gap down — fade it LONG (expect price to fill back up)
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            confidence = 0.35 + min(gap_atr * 0.08, 0.25)

    if direction is None:
        return None

    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    offset = 0.2 * snap.atr
    if direction == "LONG":
        entry = price - offset
        sl = entry - sl_mult * snap.atr
        tp = min(entry + tp_mult * snap.atr, snap.prev_day_close)  # Target = gap fill level
    else:
        entry = price + offset
        sl = entry + sl_mult * snap.atr
        tp = max(entry - tp_mult * snap.atr, snap.prev_day_close)

    return Signal(
        symbol=snap.symbol, strategy=Strategy.GAP_FILL,
        direction=direction, trade_type=TradeType.SCALP,
        order_type=OrderType.LIMIT, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"gap_size": gap, "gap_atr": gap_atr,
                 "prev_close": snap.prev_day_close, "session_open": snap.session_open},
    )


def _signal_confluence(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """CONFLUENCE: Multiple indicators agree (RSI + ADX + trend alignment).
    
    LONG: RSI < 40 + ADX > 20 + EMA50 > EMA200
    SHORT: RSI > 60 + ADX > 20 + EMA50 < EMA200
    v22 note: relaxed thresholds (RSI 40/60 instead of 30/70) for more signals.
    """
    direction = None
    confidence = 0.0
    signals_aligned = 0

    # Check SHORT conditions (most common based on research)
    if setup.direction in (Direction.SHORT, Direction.BOTH):
        checks = [
            snap.rsi > 60,
            snap.adx > 20,
            snap.ema_50 < snap.ema_200,
            snap.closes[-1] < snap.vwap,
            snap.minus_di > snap.plus_di,
        ]
        signals_aligned = sum(checks)
        if signals_aligned >= 3:
            direction = "SHORT"
            confidence = 0.20 + signals_aligned * 0.08  # 0.44 at 3, 0.52 at 4, 0.60 at 5

    # Check LONG conditions
    if direction is None and setup.direction in (Direction.LONG, Direction.BOTH):
        checks = [
            snap.rsi < 40,
            snap.adx > 20,
            snap.ema_50 > snap.ema_200,
            snap.closes[-1] > snap.vwap,
            snap.plus_di > snap.minus_di,
        ]
        signals_aligned = sum(checks)
        if signals_aligned >= 3:
            direction = "LONG"
            confidence = 0.20 + signals_aligned * 0.08

    if direction is None:
        return None

    price = snap.closes[-1]
    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    if direction == "LONG":
        entry = price
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    return Signal(
        symbol=snap.symbol, strategy=Strategy.CONFLUENCE,
        direction=direction, trade_type=TradeType.RUNNER,
        order_type=OrderType.MARKET, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"rsi": snap.rsi, "adx": snap.adx, "signals_aligned": signals_aligned,
                 "ema_50": snap.ema_50, "ema_200": snap.ema_200},
    )


def _signal_vol_compress(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """VOL COMPRESS: Bollinger squeeze inside Keltner, enter on release.
    
    Squeeze: BB inside Keltner channels
    Release: BB breaks outside Keltner + momentum bar
    """
    if snap.keltner_upper is None or snap.keltner_lower is None:
        return None

    # Check for squeeze release (BB was inside Keltner, now breaking out)
    squeeze = snap.bb_upper < snap.keltner_upper and snap.bb_lower > snap.keltner_lower
    if squeeze:
        return None  # Still squeezed, wait for release

    # Check if we just released (need previous squeeze state — approximate)
    bb_range = snap.bb_upper - snap.bb_lower
    keltner_range = snap.keltner_upper - snap.keltner_lower
    if keltner_range > 0 and bb_range / keltner_range < 1.05:
        # Just barely released — check momentum direction
        price = snap.closes[-1]
        prev_price = snap.closes[-2] if len(snap.closes) >= 2 else price
        direction = None
        confidence = 0.0

        if price > snap.bb_middle and price > prev_price:
            if setup.direction in (Direction.LONG, Direction.BOTH):
                direction = "LONG"
                confidence = 0.35
        elif price < snap.bb_middle and price < prev_price:
            if setup.direction in (Direction.SHORT, Direction.BOTH):
                direction = "SHORT"
                confidence = 0.35

        if direction is None:
            return None

        sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
        if direction == "LONG":
            entry = price
            sl = entry - sl_mult * snap.atr
            tp = entry + tp_mult * snap.atr
        else:
            entry = price
            sl = entry + sl_mult * snap.atr
            tp = entry - tp_mult * snap.atr

        return Signal(
            symbol=snap.symbol, strategy=Strategy.VOL_COMPRESS,
            direction=direction, trade_type=TradeType.RUNNER,
            order_type=OrderType.MARKET, entry_price=entry,
            sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
            tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
            raw_confidence=confidence, final_confidence=confidence,
            atr_value=snap.atr,
            context={"bb_range": bb_range, "keltner_range": keltner_range,
                     "squeeze_ratio": bb_range / keltner_range if keltner_range > 0 else 0},
        )

    return None


def _signal_asian_breakout(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """ASIAN BREAKOUT: Break of Asian session (00:00-07:00 UTC) range.
    
    Only fires after Asian session is complete.
    """
    if not snap.asian_complete or snap.asian_high is None or snap.asian_low is None:
        return None

    price = snap.closes[-1]
    prev_price = snap.closes[-2] if len(snap.closes) >= 2 else price
    direction = None
    confidence = 0.0

    asian_range = snap.asian_high - snap.asian_low
    range_ratio = asian_range / snap.atr if snap.atr > 0 else 1.0

    if price > snap.asian_high and prev_price <= snap.asian_high:
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            confidence = 0.30 + max(0, (1.0 - range_ratio) * 0.15)
    elif price < snap.asian_low and prev_price >= snap.asian_low:
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            confidence = 0.30 + max(0, (1.0 - range_ratio) * 0.15)

    if direction is None:
        return None

    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    if direction == "LONG":
        entry = price
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    return Signal(
        symbol=snap.symbol, strategy=Strategy.ASIAN_BREAKOUT,
        direction=direction, trade_type=TradeType.RUNNER,
        order_type=OrderType.MARKET, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"asian_high": snap.asian_high, "asian_low": snap.asian_low,
                 "asian_range": asian_range},
    )


# ─── Strategy Router ─────────────────────────────────────────────────────────

STRATEGY_FUNCTIONS = {
    Strategy.MEAN_REVERT:     _signal_mean_revert,
    Strategy.VWAP_REVERT:     _signal_vwap_revert,
    Strategy.STOCH_REVERSAL:  _signal_stoch_reversal,
    Strategy.EMA_BOUNCE:      _signal_ema_bounce,
    Strategy.PREV_DAY_HL:     _signal_prev_day_hl,
    Strategy.ORB:             _signal_orb,
    Strategy.GAP_FILL:        _signal_gap_fill,
    Strategy.CONFLUENCE:      _signal_confluence,
    Strategy.VOL_COMPRESS:    _signal_vol_compress,
    Strategy.ASIAN_BREAKOUT:  _signal_asian_breakout,
}


# ─── Confidence Boosting ────────────────────────────────────────────────────

def _apply_tod_boost(signal: Signal, hour_utc: int) -> Signal:
    """Apply time-of-day confidence boost/suppress."""
    edges = TIME_OF_DAY_EDGES.get(hour_utc, [])
    
    for sym, direction, p_value in edges:
        if sym == signal.symbol and direction == signal.direction:
            # Edge hour — boost confidence
            signal.final_confidence += TOD_EDGE_BOOST
            signal.context["tod_edge"] = True
            signal.context["tod_p_value"] = p_value
            logger.info(f"TOD BOOST: {signal.symbol} {signal.direction} at {hour_utc}:00 UTC "
                       f"(p={p_value}) -> confidence +{TOD_EDGE_BOOST}")
            return signal

    # Check if this symbol HAS an edge at a different hour — suppress
    has_edge = any(
        sym == signal.symbol
        for hour_edges in TIME_OF_DAY_EDGES.values()
        for sym, _, _ in hour_edges
    )
    if has_edge:
        signal.final_confidence += TOD_SUPPRESS
        signal.context["tod_suppressed"] = True

    return signal


def _apply_seasonality(signal: Signal, month: int) -> Signal:
    """Apply monthly seasonality sizing adjustment."""
    seasons = MONTHLY_SEASONALITY.get(month, [])
    for sym, direction, boost in seasons:
        if sym == signal.symbol and direction == signal.direction:
            signal.risk_pct *= (1.0 + boost)
            signal.context["seasonal_boost"] = boost
            logger.info(f"SEASONAL: {signal.symbol} {signal.direction} month={month} "
                       f"-> risk_pct *= {1.0 + boost:.2f}")
    return signal


# ─── Main Signal Generator ──────────────────────────────────────────────────

class SignalEngine:
    """
    FORGE v22 Signal Engine.
    
    Philosophy: "ALL GAS FIRST THEN BRAKES"
    - Single strategy signal is enough to enter
    - Conviction threshold: 0.20
    - Time-of-day edges boost/suppress confidence
    - Seasonality adjusts position sizing
    """

    CONVICTION_THRESHOLD = 0.20  # v21 was 0.35 — killed trade flow

    def __init__(self):
        self._signal_count = 0

    def generate_signals(
        self,
        snapshots: Dict[str, MarketSnapshot],
        current_time: Optional[datetime] = None,
    ) -> List[Signal]:
        """
        Generate signals for all instruments.
        
        Args:
            snapshots: Market data for each instrument
            current_time: Current UTC time (default: now)
            
        Returns:
            List of signals that pass conviction threshold.
            Sorted by confidence (highest first).
        """
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        hour_utc = current_time.hour
        month = current_time.month
        signals = []

        for symbol, setup in SETUP_CONFIG.items():
            snap = snapshots.get(symbol)
            if snap is None:
                continue

            # Get the strategy function for this instrument
            strategy_fn = STRATEGY_FUNCTIONS.get(setup.strategy)
            if strategy_fn is None:
                logger.warning(f"No strategy function for {setup.strategy}")
                continue

            try:
                signal = strategy_fn(snap, setup)
            except Exception as e:
                logger.error(f"Signal generation error on {symbol}/{setup.strategy}: {e}")
                continue

            if signal is None:
                continue

            # Apply time-of-day boost
            signal = _apply_tod_boost(signal, hour_utc)

            # Apply seasonality
            signal = _apply_seasonality(signal, month)

            # Clamp confidence to [0, 1]
            signal.final_confidence = max(0.0, min(1.0, signal.final_confidence))

            # Check conviction threshold — ALL GAS
            if signal.final_confidence < self.CONVICTION_THRESHOLD:
                logger.debug(f"SKIP {symbol}: confidence {signal.final_confidence:.3f} "
                           f"< threshold {self.CONVICTION_THRESHOLD}")
                continue

            signals.append(signal)
            self._signal_count += 1
            logger.info(
                f"SIGNAL #{self._signal_count}: {signal.symbol} {signal.strategy.value} "
                f"{signal.direction} | conf={signal.final_confidence:.3f} "
                f"| type={signal.trade_type.value} | order={signal.order_type.value} "
                f"| entry={signal.entry_price:.5f} SL={signal.sl_price:.5f} TP={signal.tp_price:.5f}"
            )

        # Sort by confidence (highest first)
        signals.sort(key=lambda s: s.final_confidence, reverse=True)
        return signals

    @property
    def total_signals(self) -> int:
        return self._signal_count
