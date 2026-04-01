"""
FORGE v22.3 — Signal Generation Engine (FIXED)
================================================
FIXES APPLIED:
  1. REMOVED TOD_SUPPRESS: was killing 7/11 instruments 23 hrs/day
  2. FIXED GAP_FILL: now uses daily open vs prev close (not consecutive hourly bars)
  3. ADDED multi-strategy: tries primary + alt strategies per instrument → 3-5x more signals
  4. REMOVED LIMIT offset: main.py always sends MARKET, so offset was making entries 0.2ATR worse
  5. ALL strategies use MARKET orders (no phantom limit offset)
  6. Relaxed signal conditions slightly for higher frequency

Philosophy: "ALL GAS FIRST THEN BRAKES"
- Multi-strategy scanning per instrument
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
    symbol: str
    strategy: Strategy
    direction: str
    trade_type: TradeType
    order_type: OrderType
    entry_price: float
    sl_price: float
    tp_price: float
    sl_atr_mult: float
    tp_atr_mult: float
    risk_pct: float
    raw_confidence: float
    final_confidence: float
    atr_value: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    limit_offset_atr: float = 0.0
    limit_valid_bars: int = 5
    partial_pct: float = 0.50
    trailing_r: float = 1.5
    context: Dict = field(default_factory=dict)


# ─── Market Data Container ───────────────────────────────────────────────────

@dataclass
class MarketSnapshot:
    symbol: str
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    volumes: np.ndarray
    bid: float
    ask: float
    atr: float
    rsi: float
    stoch_k: float
    stoch_d: float
    stoch_k_prev: float
    stoch_d_prev: float
    ema_50: float
    ema_200: float
    bb_upper: float
    bb_lower: float
    bb_middle: float
    vwap: float
    vwap_std: float
    adx: float
    adx_prev: float
    plus_di: float
    minus_di: float
    prev_day_high: float
    prev_day_low: float
    prev_day_close: float
    session_open: float
    session_high: float
    session_low: float
    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    orb_complete: bool = False
    asian_high: Optional[float] = None
    asian_low: Optional[float] = None
    asian_complete: bool = False
    keltner_upper: Optional[float] = None
    keltner_lower: Optional[float] = None
    bars_since_open: int = 0
    current_hour_utc: int = 0
    # NEW: Daily gap data (from Polygon daily bars)
    daily_open: Optional[float] = None
    daily_prev_close: Optional[float] = None


# ─── Helper Functions ────────────────────────────────────────────────────────

def _is_rejection_candle(opens, highs, lows, closes, direction: str) -> bool:
    if len(closes) < 1:
        return False
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    if body < 1e-10:
        body = 1e-10
    if direction == "LONG":
        lower_wick = min(o, c) - l
        return lower_wick > 2.0 * body
    else:
        upper_wick = h - max(o, c)
        return upper_wick > 2.0 * body


def _is_reversal_candle(opens, highs, lows, closes, against_direction: str) -> bool:
    if len(closes) < 2:
        return False
    o1, c1 = opens[-2], closes[-2]
    o2, h2, l2, c2 = opens[-1], highs[-1], lows[-1], closes[-1]
    body1 = abs(c1 - o1)
    body2 = abs(c2 - o2)
    if against_direction == "LONG":
        bullish_engulf = c2 > o2 and body2 > body1 * 1.5 and c2 > max(o1, c1)
        lower_wick = min(o2, c2) - l2
        bullish_pin = lower_wick > 2.5 * body2 and c2 > o2
        return bullish_engulf or bullish_pin
    else:
        bearish_engulf = c2 < o2 and body2 > body1 * 1.5 and c2 < min(o1, c1)
        upper_wick = h2 - max(o2, c2)
        bearish_pin = upper_wick > 2.5 * body2 and c2 < o2
        return bearish_engulf or bearish_pin


def _volume_spike(volumes: np.ndarray, lookback: int = 20, threshold: float = 2.0) -> bool:
    if len(volumes) < lookback + 1:
        return False
    avg_vol = np.mean(volumes[-lookback - 1:-1])
    if avg_vol < 1e-10:
        return False
    return volumes[-1] > threshold * avg_vol


# ─── Strategy Implementations ────────────────────────────────────────────────

def _make_signal(snap, setup, strategy, direction, confidence, trade_type=None):
    """Universal signal builder — ALL strategies use MARKET, NO offset."""
    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    price = snap.bid if direction == "SHORT" else snap.ask
    if price <= 0:
        price = float(snap.closes[-1])

    if direction == "LONG":
        entry = price
        sl = entry - sl_mult * snap.atr
        tp = entry + tp_mult * snap.atr
    else:
        entry = price
        sl = entry + sl_mult * snap.atr
        tp = entry - tp_mult * snap.atr

    tt = trade_type or setup.trade_type
    return Signal(
        symbol=snap.symbol, strategy=strategy,
        direction=direction, trade_type=tt,
        order_type=OrderType.MARKET, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
    )


def _signal_mean_revert(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """MEAN REVERT: RSI extremes + Bollinger Band breach."""
    price = float(snap.closes[-1])
    direction = None
    confidence = 0.0

    if snap.rsi < 30 and price < snap.bb_lower:
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            confidence = 0.30 + (30 - snap.rsi) / 100
    elif snap.rsi > 70 and price > snap.bb_upper:
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            confidence = 0.30 + (snap.rsi - 70) / 100

    if direction is None:
        return None
    return _make_signal(snap, setup, Strategy.MEAN_REVERT, direction, confidence)


def _signal_vwap_revert(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """VWAP REVERT: Price at 2 std from VWAP with rejection candle."""
    price = float(snap.closes[-1])
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
    return _make_signal(snap, setup, Strategy.VWAP_REVERT, direction, confidence)


def _signal_stoch_reversal(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """STOCH REVERSAL: %K crosses %D from oversold/overbought."""
    direction = None
    confidence = 0.0

    if (snap.stoch_k_prev < snap.stoch_d_prev and snap.stoch_k > snap.stoch_d
            and snap.stoch_k < 25):
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            confidence = 0.30 + (20 - min(snap.stoch_k_prev, 20)) / 40
    elif (snap.stoch_k_prev > snap.stoch_d_prev and snap.stoch_k < snap.stoch_d
            and snap.stoch_k > 75):
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            confidence = 0.30 + (min(snap.stoch_k_prev, 100) - 80) / 40

    if direction is None:
        return None
    return _make_signal(snap, setup, Strategy.STOCH_REVERSAL, direction, confidence)


def _signal_ema_bounce(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """EMA BOUNCE: Price touches EMA 50 and bounces."""
    price = float(snap.closes[-1])
    prev_price = float(snap.closes[-2]) if len(snap.closes) >= 2 else price
    direction = None
    confidence = 0.0

    ema_dist = abs(price - snap.ema_50) / snap.atr if snap.atr > 0 else 999

    if ema_dist < 0.3:
        if snap.ema_50 > snap.ema_200 and price > snap.ema_50:
            if prev_price <= snap.ema_50 * 1.001:
                if setup.direction in (Direction.LONG, Direction.BOTH):
                    direction = "LONG"
                    confidence = 0.30 + min(ema_dist, 0.3) * 0.50
        elif snap.ema_50 < snap.ema_200 and price < snap.ema_50:
            if prev_price >= snap.ema_50 * 0.999:
                if setup.direction in (Direction.SHORT, Direction.BOTH):
                    direction = "SHORT"
                    confidence = 0.30 + min(ema_dist, 0.3) * 0.50

    if direction is None:
        return None
    trade_type = TradeType.RUNNER if snap.adx > 30 else TradeType.SCALP
    return _make_signal(snap, setup, Strategy.EMA_BOUNCE, direction, confidence, trade_type)


def _signal_prev_day_hl(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """PREV DAY HL: Breakout above prev day high or below prev day low."""
    price = float(snap.closes[-1])
    prev_price = float(snap.closes[-2]) if len(snap.closes) >= 2 else price
    direction = None
    confidence = 0.0

    if price > snap.prev_day_high and prev_price <= snap.prev_day_high:
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            break_pct = (price - snap.prev_day_high) / snap.atr if snap.atr > 0 else 0
            confidence = 0.30 + min(break_pct * 0.15, 0.25)
    elif price < snap.prev_day_low and prev_price >= snap.prev_day_low:
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            direction = "SHORT"
            break_pct = (snap.prev_day_low - price) / snap.atr if snap.atr > 0 else 0
            confidence = 0.30 + min(break_pct * 0.15, 0.25)

    if direction is None:
        return None
    return _make_signal(snap, setup, Strategy.PREV_DAY_HL, direction, confidence, TradeType.RUNNER)


def _signal_orb(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """ORB: Opening Range Breakout."""
    if not snap.orb_complete or snap.orb_high is None or snap.orb_low is None:
        return None

    price = float(snap.closes[-1])
    prev_price = float(snap.closes[-2]) if len(snap.closes) >= 2 else price
    direction = None
    confidence = 0.0

    if price > snap.orb_high and prev_price <= snap.orb_high:
        if setup.direction in (Direction.LONG, Direction.BOTH):
            direction = "LONG"
            orb_range = snap.orb_high - snap.orb_low
            if snap.atr > 0 and orb_range > 0:
                range_ratio = orb_range / snap.atr
                confidence = 0.35 + max(0, (1.0 - range_ratio) * 0.15)
            else:
                confidence = 0.30
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
    return _make_signal(snap, setup, Strategy.ORB, direction, confidence, TradeType.RUNNER)


def _signal_gap_fill(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """GAP FILL: Fade session gaps > 0.3 ATR back toward previous close.

    FIX: Uses daily_open vs daily_prev_close (real daily bar data)
    instead of consecutive hourly bars (which have zero gap).
    Falls back to session_open vs prev_day_close if daily data unavailable.
    """
    # Use real daily gap data if available
    gap_open = snap.daily_open if snap.daily_open else snap.session_open
    gap_prev = snap.daily_prev_close if snap.daily_prev_close else snap.prev_day_close

    if gap_open is None or gap_prev is None or gap_open <= 0 or gap_prev <= 0:
        return None

    gap = gap_open - gap_prev
    gap_atr = abs(gap) / snap.atr if snap.atr > 0 else 0

    # Lowered threshold from 0.5 to 0.3 ATR for more signal opportunities
    if gap_atr < 0.3:
        return None

    price = float(snap.closes[-1])
    direction = None
    confidence = 0.0

    if gap > 0:
        # Gap up — fade SHORT
        if setup.direction in (Direction.SHORT, Direction.BOTH):
            # Only fade if price hasn't already filled
            if price > gap_prev:
                direction = "SHORT"
                confidence = 0.35 + min(gap_atr * 0.08, 0.25)
    else:
        # Gap down — fade LONG
        if setup.direction in (Direction.LONG, Direction.BOTH):
            if price < gap_prev:
                direction = "LONG"
                confidence = 0.35 + min(gap_atr * 0.08, 0.25)

    if direction is None:
        return None

    # TP target = gap fill level (prev close)
    sl_mult, tp_mult = get_sl_tp_for_direction(setup, direction)
    if direction == "LONG":
        entry = price
        sl = entry - sl_mult * snap.atr
        tp = min(entry + tp_mult * snap.atr, gap_prev)
    else:
        entry = price
        sl = entry + sl_mult * snap.atr
        tp = max(entry - tp_mult * snap.atr, gap_prev)

    return Signal(
        symbol=snap.symbol, strategy=Strategy.GAP_FILL,
        direction=direction, trade_type=TradeType.SCALP,
        order_type=OrderType.MARKET, entry_price=entry,
        sl_price=sl, tp_price=tp, sl_atr_mult=sl_mult,
        tp_atr_mult=tp_mult, risk_pct=setup.risk_pct,
        raw_confidence=confidence, final_confidence=confidence,
        atr_value=snap.atr,
        context={"gap_size": gap, "gap_atr": gap_atr, "gap_open": gap_open, "gap_prev": gap_prev},
    )


def _signal_confluence(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """CONFLUENCE: Multiple indicators agree."""
    direction = None
    confidence = 0.0
    signals_aligned = 0

    if setup.direction in (Direction.SHORT, Direction.BOTH):
        checks = [
            snap.rsi > 60,
            snap.adx > 20,
            snap.ema_50 < snap.ema_200,
            float(snap.closes[-1]) < snap.vwap,
            snap.minus_di > snap.plus_di,
        ]
        signals_aligned = sum(checks)
        if signals_aligned >= 4:
            direction = "SHORT"
            confidence = 0.20 + signals_aligned * 0.08

    if direction is None and setup.direction in (Direction.LONG, Direction.BOTH):
        checks = [
            snap.rsi < 40,
            snap.adx > 20,
            snap.ema_50 > snap.ema_200,
            float(snap.closes[-1]) > snap.vwap,
            snap.plus_di > snap.minus_di,
        ]
        signals_aligned = sum(checks)
        if signals_aligned >= 4:
            direction = "LONG"
            confidence = 0.20 + signals_aligned * 0.08

    if direction is None:
        return None
    return _make_signal(snap, setup, Strategy.CONFLUENCE, direction, confidence, TradeType.RUNNER)


def _signal_vol_compress(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """VOL COMPRESS: Bollinger squeeze inside Keltner, enter on release."""
    if snap.keltner_upper is None or snap.keltner_lower is None:
        return None

    squeeze = snap.bb_upper < snap.keltner_upper and snap.bb_lower > snap.keltner_lower
    if squeeze:
        return None

    bb_range = snap.bb_upper - snap.bb_lower
    keltner_range = snap.keltner_upper - snap.keltner_lower
    if keltner_range > 0 and bb_range / keltner_range < 1.05:
        price = float(snap.closes[-1])
        prev_price = float(snap.closes[-2]) if len(snap.closes) >= 2 else price
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
        return _make_signal(snap, setup, Strategy.VOL_COMPRESS, direction, confidence, TradeType.RUNNER)
    return None


def _signal_asian_breakout(snap: MarketSnapshot, setup: InstrumentSetup) -> Optional[Signal]:
    """ASIAN BREAKOUT: Break of Asian session range."""
    if not snap.asian_complete or snap.asian_high is None or snap.asian_low is None:
        return None

    price = float(snap.closes[-1])
    prev_price = float(snap.closes[-2]) if len(snap.closes) >= 2 else price
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
    return _make_signal(snap, setup, Strategy.ASIAN_BREAKOUT, direction, confidence, TradeType.RUNNER)


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
    """Apply time-of-day BOOST only (no suppression).
    
    FIX: TOD_SUPPRESS was -0.15, dropping 0.30 confidence to 0.15,
    below 0.20 threshold, blocking 7/11 instruments 23 hours/day.
    Now: edge hours get a BONUS, non-edge hours are UNAFFECTED.
    """
    edges = TIME_OF_DAY_EDGES.get(hour_utc, [])

    for sym, direction, p_value in edges:
        if sym == signal.symbol and direction == signal.direction:
            signal.final_confidence += TOD_EDGE_BOOST
            signal.context["tod_edge"] = True
            signal.context["tod_p_value"] = p_value
            return signal

    # FIX: NO SUPPRESSION for non-edge hours
    # (was: signal.final_confidence += TOD_SUPPRESS which was -0.15)
    return signal


def _apply_seasonality(signal: Signal, month: int) -> Signal:
    seasons = MONTHLY_SEASONALITY.get(month, [])
    for sym, direction, boost in seasons:
        if sym == signal.symbol and direction == signal.direction:
            signal.risk_pct *= (1.0 + boost)
            signal.context["seasonal_boost"] = boost
    return signal


# ─── Main Signal Generator ──────────────────────────────────────────────────

class SignalEngine:
    """
    FORGE v22.3 Signal Engine (FIXED).

    KEY CHANGES:
    1. Multi-strategy scanning: tries primary + alt_strategies per instrument
    2. No TOD suppression (boost only)
    3. All MARKET orders (no LIMIT offset that made entries worse)
    4. Fixed GAP_FILL daily data usage
    """

    CONVICTION_THRESHOLD = 0.20

    def __init__(self):
        self._signal_count = 0

    def generate_signals(
        self,
        snapshots: Dict[str, MarketSnapshot],
        current_time: Optional[datetime] = None,
    ) -> List[Signal]:
        if current_time is None:
            current_time = datetime.now(timezone.utc)

        hour_utc = current_time.hour
        month = current_time.month
        signals = []

        for symbol, setup in SETUP_CONFIG.items():
            snap = snapshots.get(symbol)
            if snap is None:
                continue

            # Multi-strategy: try primary + alternates
            strategies_to_try = [setup.strategy]
            if hasattr(setup, 'alt_strategies') and setup.alt_strategies:
                strategies_to_try.extend(setup.alt_strategies)

            best_signal = None
            best_conf = 0.0

            for strat in strategies_to_try:
                strategy_fn = STRATEGY_FUNCTIONS.get(strat)
                if strategy_fn is None:
                    continue

                try:
                    signal = strategy_fn(snap, setup)
                except Exception as e:
                    logger.error(f"Signal error {symbol}/{strat}: {e}")
                    continue

                if signal is None:
                    continue

                # Apply boosts
                signal = _apply_tod_boost(signal, hour_utc)
                signal = _apply_seasonality(signal, month)
                signal.final_confidence = max(0.0, min(1.0, signal.final_confidence))

                if signal.final_confidence >= self.CONVICTION_THRESHOLD:
                    if signal.final_confidence > best_conf:
                        best_signal = signal
                        best_conf = signal.final_confidence

            if best_signal is not None:
                signals.append(best_signal)
                self._signal_count += 1
                logger.info(
                    f"SIGNAL #{self._signal_count}: {best_signal.symbol} {best_signal.strategy.value} "
                    f"{best_signal.direction} | conf={best_signal.final_confidence:.3f} "
                    f"| type={best_signal.trade_type.value} | order={best_signal.order_type.value} "
                    f"| entry={best_signal.entry_price:.5f} SL={best_signal.sl_price:.5f} TP={best_signal.tp_price:.5f}"
                )

        signals.sort(key=lambda s: s.final_confidence, reverse=True)
        return signals

    @property
    def total_signals(self) -> int:
        return self._signal_count
