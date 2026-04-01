"""
TITAN FORGE — ELITE REAL-TIME DIRECTION ENGINE v1.0
=====================================================
Replaces GENESIS with a fast-switching, multi-indicator regime detector.

GENESIS PROBLEM: Used EMA50/200 crossover → takes WEEKS to switch regime.
                 NEUTRAL config was ALL counter-trend → system fought trends.

THIS ENGINE: Fuses 8 indicators on M15 candles → switches in 3-6 bars (45-90 min).
             Outputs regime + direction + confidence + aggression per instrument.
             Tells FORGE whether to trade WITH the trend or AGAINST it.

REGIMES:
  STRONG_TREND_UP    — Clear uptrend, trade long only, aggressive
  STRONG_TREND_DOWN  — Clear downtrend, trade short only, aggressive
  WEAK_TREND_UP      — Mild uptrend, favor longs, normal size
  WEAK_TREND_DOWN    — Mild downtrend, favor shorts, normal size
  RANGE              — Choppy/mean-reverting, trade both sides, scalp mode
  HIGH_VOL           — Explosive volatility, reduce size, widen stops
  LOW_VOL            — Compressed, expect breakout, tight entries
  TRANSITION         — Regime changing, reduce aggression, wait for clarity

USAGE IN FORGE:
  from forge_direction_engine import DirectionEngine, Regime

  engine = DirectionEngine()
  result = engine.update("EURUSD", candles_m15, candles_h1=None)
  
  result.regime        # Regime.STRONG_TREND_UP
  result.direction     # 1 (LONG), -1 (SHORT), 0 (NEUTRAL)
  result.confidence    # 0-100
  result.aggression    # 1.0-2.5x multiplier
  result.strategy_filter  # ["WITH_TREND", "MOMENTUM"] or ["MEAN_REVERT", "FADE"]
"""

import numpy as np
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from collections import deque
import time
import logging

logger = logging.getLogger("FORGE.direction")


# ═══════════════════════════════════════════════════════════════
# ENUMS & DATA CLASSES
# ═══════════════════════════════════════════════════════════════

class Regime(Enum):
    STRONG_TREND_UP = "STRONG_TREND_UP"
    STRONG_TREND_DOWN = "STRONG_TREND_DOWN"
    WEAK_TREND_UP = "WEAK_TREND_UP"
    WEAK_TREND_DOWN = "WEAK_TREND_DOWN"
    RANGE = "RANGE"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"
    TRANSITION = "TRANSITION"


@dataclass
class DirectionResult:
    """Output of the direction engine for one instrument."""
    symbol: str
    regime: Regime
    direction: int          # 1=LONG bias, -1=SHORT bias, 0=NEUTRAL
    confidence: float       # 0-100
    aggression: float       # 1.0-2.5x position size multiplier
    strategy_filter: List[str]  # Which strategy types to allow
    
    # Indicator details for logging/debugging
    adx: float = 0.0
    di_plus: float = 0.0
    di_minus: float = 0.0
    trend_score: float = 0.0      # -100 to +100 (negative=bearish, positive=bullish)
    momentum_score: float = 0.0   # -100 to +100
    structure_score: float = 0.0  # -100 to +100 (price structure)
    vwap_score: float = 0.0       # -100 to +100
    vol_regime: str = "NORMAL"    # HIGH, LOW, NORMAL
    
    def __str__(self):
        dir_str = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}[self.direction]
        return (f"{self.symbol} | {self.regime.value} | {dir_str} | "
                f"conf={self.confidence:.0f} | agg={self.aggression:.1f}x | "
                f"strats={','.join(self.strategy_filter)}")


# Strategy filter constants
WITH_TREND = "WITH_TREND"       # EMA_BOUNCE, PREV_DAY_HL, MOMENTUM, BREAKOUT
MEAN_REVERT = "MEAN_REVERT"     # MEAN_REVERT, VWAP_REVERT, STOCH_REVERSAL
SCALP = "SCALP"                 # Precision scalping (your fixed-target strategy)
FADE = "FADE"                   # Counter-trend fades (only in strong range)
ALL = "ALL"                     # Any strategy allowed


# ═══════════════════════════════════════════════════════════════
# INDICATORS (optimized for speed — no pandas dependency)
# ═══════════════════════════════════════════════════════════════

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    out = np.full(len(data), np.nan)
    if len(data) < period:
        return np.full(len(data), np.mean(data) if len(data) > 0 else 0)
    mult = 2.0 / (period + 1)
    e = np.mean(data[:period])
    out[period - 1] = e
    for i in range(period, len(data)):
        e = (data[i] - e) * mult + e
        out[i] = e
    # Backfill
    first_valid = out[period - 1]
    out[:period - 1] = first_valid
    return out


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """Relative Strength Index."""
    out = np.full(len(close), 50.0)
    if len(close) < period + 1:
        return out
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    if len(close) < 2:
        return np.full(len(close), abs(high[0] - low[0]) if len(close) > 0 else 1.0)
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1]))
    )
    out = np.full(len(close), np.mean(tr[:period]) if len(tr) >= period else np.mean(tr))
    if len(tr) < period:
        return out
    a = np.mean(tr[:period])
    out[period] = a
    for i in range(period, len(tr)):
        a = (a * (period - 1) + tr[i]) / period
        out[i + 1] = a
    return out


def _adx_di(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ADX with DI+ and DI-. Returns (adx, di_plus, di_minus)."""
    n = len(close)
    adx = np.full(n, 20.0)
    di_p = np.full(n, 25.0)
    di_m = np.full(n, 25.0)
    
    if n < period * 2:
        return adx, di_p, di_m
    
    # True Range
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1]))
    )
    
    # Directional Movement
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    # Smoothed values
    atr_smooth = np.mean(tr[:period])
    plus_smooth = np.mean(plus_dm[:period])
    minus_smooth = np.mean(minus_dm[:period])
    
    dx_values = []
    
    for i in range(period, len(tr)):
        atr_smooth = atr_smooth - (atr_smooth / period) + tr[i]
        plus_smooth = plus_smooth - (plus_smooth / period) + plus_dm[i]
        minus_smooth = minus_smooth - (minus_smooth / period) + minus_dm[i]
        
        if atr_smooth > 0:
            di_p[i + 1] = 100.0 * plus_smooth / atr_smooth
            di_m[i + 1] = 100.0 * minus_smooth / atr_smooth
        
        di_sum = di_p[i + 1] + di_m[i + 1]
        if di_sum > 0:
            dx = 100.0 * abs(di_p[i + 1] - di_m[i + 1]) / di_sum
            dx_values.append(dx)
        else:
            dx_values.append(0.0)
        
        if len(dx_values) >= period:
            if len(dx_values) == period:
                adx[i + 1] = np.mean(dx_values[-period:])
            else:
                adx[i + 1] = (adx[i] * (period - 1) + dx_values[-1]) / period
    
    return adx, di_p, di_m


def _vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """Simple cumulative VWAP (resets conceptually on session — here uses rolling 96 bars ~24h)."""
    n = len(close)
    vwap = np.full(n, close[0] if n > 0 else 0.0)
    window = min(96, n)  # 96 M15 bars = 24 hours
    
    for i in range(n):
        start = max(0, i - window + 1)
        tp = (high[start:i+1] + low[start:i+1] + close[start:i+1]) / 3.0
        vol = volume[start:i+1]
        total_vol = np.sum(vol)
        if total_vol > 0:
            vwap[i] = np.sum(tp * vol) / total_vol
        else:
            vwap[i] = np.mean(tp)
    
    return vwap


def _macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """MACD line, signal line, histogram."""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ═══════════════════════════════════════════════════════════════
# PRICE STRUCTURE ANALYZER
# ═══════════════════════════════════════════════════════════════

def _analyze_price_structure(high: np.ndarray, low: np.ndarray, close: np.ndarray, 
                              lookback: int = 12) -> Tuple[float, int]:
    """
    Analyze recent price structure for higher-highs/higher-lows or lower-lows/lower-highs.
    
    Returns:
        score: -100 to +100 (bearish to bullish)
        swing_count: number of clear swings detected
    """
    n = len(close)
    if n < lookback + 2:
        return 0.0, 0
    
    recent_h = high[-lookback:]
    recent_l = low[-lookback:]
    recent_c = close[-lookback:]
    
    # Split into thirds and compare
    third = lookback // 3
    if third < 2:
        return 0.0, 0
    
    # Compare highs and lows across segments
    seg1_high = np.max(recent_h[:third])
    seg2_high = np.max(recent_h[third:2*third])
    seg3_high = np.max(recent_h[2*third:])
    
    seg1_low = np.min(recent_l[:third])
    seg2_low = np.min(recent_l[third:2*third])
    seg3_low = np.min(recent_l[2*third:])
    
    score = 0.0
    swings = 0
    
    # Higher highs
    if seg3_high > seg2_high > seg1_high:
        score += 40
        swings += 2
    elif seg3_high > seg1_high:
        score += 20
        swings += 1
    
    # Higher lows
    if seg3_low > seg2_low > seg1_low:
        score += 40
        swings += 2
    elif seg3_low > seg1_low:
        score += 20
        swings += 1
    
    # Lower lows
    if seg3_low < seg2_low < seg1_low:
        score -= 40
        swings += 2
    elif seg3_low < seg1_low:
        score -= 20
        swings += 1
    
    # Lower highs
    if seg3_high < seg2_high < seg1_high:
        score -= 40
        swings += 2
    elif seg3_high < seg1_high:
        score -= 20
        swings += 1
    
    # Close position relative to range
    full_range = np.max(recent_h) - np.min(recent_l)
    if full_range > 0:
        close_position = (recent_c[-1] - np.min(recent_l)) / full_range  # 0-1
        # If close is in top 20%, bullish pressure. Bottom 20%, bearish.
        if close_position > 0.8:
            score += 15
        elif close_position < 0.2:
            score -= 15
    
    # Consecutive directional closes
    last_6 = recent_c[-6:]
    bullish_closes = sum(1 for i in range(1, len(last_6)) if last_6[i] > last_6[i-1])
    bearish_closes = sum(1 for i in range(1, len(last_6)) if last_6[i] < last_6[i-1])
    
    if bullish_closes >= 4:
        score += 15
    elif bearish_closes >= 4:
        score -= 15
    
    return np.clip(score, -100, 100), swings


# ═══════════════════════════════════════════════════════════════
# DIRECTION ENGINE
# ═══════════════════════════════════════════════════════════════

class DirectionEngine:
    """
    Real-time direction engine for Titan Forge.
    
    Call update() on every cycle with new candle data.
    Returns DirectionResult with regime, direction, confidence, aggression.
    
    Replaces GENESIS. Switches regime in 3-6 M15 bars (45-90 minutes),
    not weeks like EMA50/200 crossover.
    """
    
    # Minimum bars needed for reliable output
    MIN_BARS = 50
    
    # Regime history for transition detection
    HISTORY_SIZE = 10
    
    # Instrument-specific volatility profiles (ATR multiplier thresholds)
    VOL_PROFILES = {
        "BTCUSD": {"high_mult": 1.8, "low_mult": 0.4, "trend_adx": 22},
        "XAUUSD": {"high_mult": 1.6, "low_mult": 0.5, "trend_adx": 23},
        "GBPJPY": {"high_mult": 1.5, "low_mult": 0.5, "trend_adx": 22},
        "US100":  {"high_mult": 1.5, "low_mult": 0.5, "trend_adx": 23},
        "USOIL":  {"high_mult": 1.6, "low_mult": 0.5, "trend_adx": 22},
        # Defaults for forex majors
        "_default": {"high_mult": 1.4, "low_mult": 0.5, "trend_adx": 22},
    }
    
    def __init__(self):
        self._history: Dict[str, deque] = {}  # symbol → recent regimes
        self._last_result: Dict[str, DirectionResult] = {}
        self._switch_count: Dict[str, int] = {}  # prevent regime flapping
        self._last_switch_time: Dict[str, float] = {}
        
        # Minimum time between regime switches (seconds)
        # 3 M15 bars = 45 minutes = 2700 seconds
        self.MIN_SWITCH_INTERVAL = 2700
        
        # Weight configuration for final score
        self.WEIGHTS = {
            "adx_di": 0.25,       # ADX + DI directional
            "structure": 0.25,    # Price structure (HH/HL, LL/LH)
            "momentum": 0.20,    # RSI + MACD
            "vwap": 0.15,        # VWAP position
            "candle_flow": 0.15, # Recent candle direction flow
        }
    
    def _get_vol_profile(self, symbol: str) -> dict:
        """Get volatility thresholds for instrument."""
        return self.VOL_PROFILES.get(symbol, self.VOL_PROFILES["_default"])
    
    def update(self, symbol: str, 
               o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
               v: Optional[np.ndarray] = None,
               h1_c: Optional[np.ndarray] = None,
               h1_h: Optional[np.ndarray] = None,
               h1_l: Optional[np.ndarray] = None) -> DirectionResult:
        """
        Update direction for one instrument.
        
        Args:
            symbol: Instrument name (e.g., "EURUSD")
            o, h, l, c: M15 candle arrays (most recent data, 100-300 bars)
            v: Volume array (optional, uses uniform if missing)
            h1_c, h1_h, h1_l: H1 candle arrays for confirmation (optional)
        
        Returns:
            DirectionResult with regime, direction, confidence, aggression
        """
        n = len(c)
        
        if v is None or len(v) == 0:
            v = np.ones(n)
        
        if n < self.MIN_BARS:
            return self._default_result(symbol)
        
        # ─── COMPUTE ALL INDICATORS ───
        
        # 1. ADX + DI (primary trend strength)
        adx, di_plus, di_minus = _adx_di(h, l, c, period=14)
        current_adx = adx[-1]
        current_di_plus = di_plus[-1]
        current_di_minus = di_minus[-1]
        
        # 2. ATR for volatility regime
        atr_arr = _atr(h, l, c, period=14)
        current_atr = atr_arr[-1]
        avg_atr_50 = np.mean(atr_arr[-50:]) if n >= 50 else np.mean(atr_arr)
        
        # 3. RSI
        rsi_arr = _rsi(c, period=14)
        current_rsi = rsi_arr[-1]
        
        # 4. MACD
        macd_line, signal_line, macd_hist = _macd(c)
        current_macd_hist = macd_hist[-1]
        prev_macd_hist = macd_hist[-2] if n > 1 else 0
        
        # 5. VWAP
        vwap = _vwap(h, l, c, v)
        current_vwap = vwap[-1]
        
        # 6. EMAs (fast for direction, not for regime switching)
        ema_9 = _ema(c, 9)
        ema_21 = _ema(c, 21)
        ema_50 = _ema(c, 50)
        
        # 7. Price structure
        structure_score, swing_count = _analyze_price_structure(h, l, c, lookback=12)
        
        # ─── SCORE EACH DIMENSION (-100 to +100) ───
        
        # A. ADX + DI score
        adx_di_score = 0.0
        di_diff = current_di_plus - current_di_minus
        if current_adx > 20:
            # ADX says trending — use DI to determine direction
            strength = min((current_adx - 20) / 20, 1.0)  # 0-1 scale for ADX 20-40
            adx_di_score = di_diff * strength * 2  # Scale to roughly -100 to +100
        else:
            # ADX says ranging
            adx_di_score = di_diff * 0.3  # Weak directional signal
        adx_di_score = np.clip(adx_di_score, -100, 100)
        
        # B. Structure score (already computed above, -100 to +100)
        
        # C. Momentum score (RSI + MACD combined)
        momentum_score = 0.0
        # RSI contribution
        if current_rsi > 60:
            momentum_score += (current_rsi - 50) * 1.5  # Up to +75
        elif current_rsi < 40:
            momentum_score -= (50 - current_rsi) * 1.5  # Down to -75
        # MACD histogram direction and acceleration
        if current_macd_hist > 0:
            momentum_score += 25
            if current_macd_hist > prev_macd_hist:  # Accelerating
                momentum_score += 15
        elif current_macd_hist < 0:
            momentum_score -= 25
            if current_macd_hist < prev_macd_hist:  # Accelerating down
                momentum_score -= 15
        momentum_score = np.clip(momentum_score, -100, 100)
        
        # D. VWAP score
        vwap_score = 0.0
        if current_atr > 0:
            vwap_distance = (c[-1] - current_vwap) / current_atr
            vwap_score = np.clip(vwap_distance * 30, -100, 100)
        
        # E. Candle flow score (last 6 candles direction)
        candle_flow = 0.0
        lookback_flow = min(6, n - 1)
        for i in range(-lookback_flow, 0):
            if c[i] > o[i]:  # Bullish candle
                body_pct = (c[i] - o[i]) / current_atr if current_atr > 0 else 0
                candle_flow += min(body_pct * 30, 20)
            elif c[i] < o[i]:  # Bearish candle
                body_pct = (o[i] - c[i]) / current_atr if current_atr > 0 else 0
                candle_flow -= min(body_pct * 30, 20)
        candle_flow = np.clip(candle_flow, -100, 100)
        
        # ─── WEIGHTED COMPOSITE SCORE ───
        
        composite = (
            self.WEIGHTS["adx_di"] * adx_di_score +
            self.WEIGHTS["structure"] * structure_score +
            self.WEIGHTS["momentum"] * momentum_score +
            self.WEIGHTS["vwap"] * vwap_score +
            self.WEIGHTS["candle_flow"] * candle_flow
        )
        
        # ─── H1 CONFIRMATION (if available) ───
        h1_bias = 0.0
        if h1_c is not None and len(h1_c) >= 20:
            h1_ema9 = _ema(h1_c, 9)
            h1_ema21 = _ema(h1_c, 21)
            h1_structure, _ = _analyze_price_structure(h1_h, h1_l, h1_c, lookback=8)
            
            if h1_ema9[-1] > h1_ema21[-1]:
                h1_bias = 20
            elif h1_ema9[-1] < h1_ema21[-1]:
                h1_bias = -20
            
            # H1 structure adds confirmation
            h1_bias += h1_structure * 0.15
            h1_bias = np.clip(h1_bias, -30, 30)
        
        composite += h1_bias
        
        # ─── VOLATILITY REGIME ───
        
        profile = self._get_vol_profile(symbol)
        vol_ratio = current_atr / avg_atr_50 if avg_atr_50 > 0 else 1.0
        
        if vol_ratio > profile["high_mult"]:
            vol_regime = "HIGH"
        elif vol_ratio < profile["low_mult"]:
            vol_regime = "LOW"
        else:
            vol_regime = "NORMAL"
        
        # ─── DETERMINE REGIME ───
        
        trend_threshold = profile["trend_adx"]
        
        if vol_regime == "HIGH" and abs(composite) < 40:
            regime = Regime.HIGH_VOL
            direction = 1 if composite > 0 else (-1 if composite < 0 else 0)
            confidence = min(abs(composite), 50)
        elif vol_regime == "LOW" and current_adx < 15:
            regime = Regime.LOW_VOL
            direction = 0
            confidence = 30
        elif current_adx >= trend_threshold + 10 and abs(composite) >= 50:
            # Strong trend
            if composite > 0:
                regime = Regime.STRONG_TREND_UP
                direction = 1
            else:
                regime = Regime.STRONG_TREND_DOWN
                direction = -1
            confidence = min(abs(composite), 95)
        elif current_adx >= trend_threshold and abs(composite) >= 30:
            # Weak trend
            if composite > 0:
                regime = Regime.WEAK_TREND_UP
                direction = 1
            else:
                regime = Regime.WEAK_TREND_DOWN
                direction = -1
            confidence = min(abs(composite), 75)
        elif abs(composite) < 20 and current_adx < trend_threshold:
            # Clear range
            regime = Regime.RANGE
            direction = 0
            confidence = max(30, 70 - abs(composite))
        else:
            # In between — check for transition
            regime = Regime.RANGE
            direction = 1 if composite > 15 else (-1 if composite < -15 else 0)
            confidence = min(abs(composite), 60)
        
        # ─── TRANSITION DETECTION ───
        
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self.HISTORY_SIZE)
            self._switch_count[symbol] = 0
            self._last_switch_time[symbol] = 0
        
        history = self._history[symbol]
        
        # Check if regime is changing
        if len(history) >= 3:
            last_3 = list(history)[-3:]
            if regime != last_3[-1] and last_3[-1] != last_3[-2]:
                # Regime flapping — mark as transition
                now = time.time()
                if now - self._last_switch_time[symbol] < self.MIN_SWITCH_INTERVAL:
                    regime = Regime.TRANSITION
                    confidence = max(confidence * 0.5, 20)
        
        # Record regime change
        if len(history) > 0 and regime != history[-1]:
            self._last_switch_time[symbol] = time.time()
            self._switch_count[symbol] += 1
        
        history.append(regime)
        
        # ─── STRATEGY FILTER ───
        
        if regime in (Regime.STRONG_TREND_UP, Regime.STRONG_TREND_DOWN):
            strategy_filter = [WITH_TREND, SCALP]
        elif regime in (Regime.WEAK_TREND_UP, Regime.WEAK_TREND_DOWN):
            strategy_filter = [WITH_TREND, SCALP, MEAN_REVERT]
        elif regime == Regime.RANGE:
            strategy_filter = [MEAN_REVERT, SCALP, FADE]
        elif regime == Regime.HIGH_VOL:
            strategy_filter = [SCALP]  # Only quick scalps in high vol
        elif regime == Regime.LOW_VOL:
            strategy_filter = [SCALP, MEAN_REVERT]
        elif regime == Regime.TRANSITION:
            strategy_filter = [SCALP]  # Only scalps during transition
        else:
            strategy_filter = [ALL]
        
        # ─── AGGRESSION MULTIPLIER ───
        
        if regime in (Regime.STRONG_TREND_UP, Regime.STRONG_TREND_DOWN):
            aggression = 1.5 + (confidence / 100) * 1.0  # 1.5x to 2.5x
        elif regime in (Regime.WEAK_TREND_UP, Regime.WEAK_TREND_DOWN):
            aggression = 1.0 + (confidence / 100) * 0.5  # 1.0x to 1.5x
        elif regime == Regime.RANGE:
            aggression = 1.0  # Normal size for mean reversion
        elif regime == Regime.HIGH_VOL:
            aggression = 0.5  # Half size in high vol
        elif regime == Regime.LOW_VOL:
            aggression = 0.8  # Slightly reduced
        elif regime == Regime.TRANSITION:
            aggression = 0.5  # Minimal during transition
        else:
            aggression = 1.0
        
        # ─── BUILD RESULT ───
        
        result = DirectionResult(
            symbol=symbol,
            regime=regime,
            direction=direction,
            confidence=round(confidence, 1),
            aggression=round(aggression, 2),
            strategy_filter=strategy_filter,
            adx=round(current_adx, 1),
            di_plus=round(current_di_plus, 1),
            di_minus=round(current_di_minus, 1),
            trend_score=round(composite, 1),
            momentum_score=round(momentum_score, 1),
            structure_score=round(structure_score, 1),
            vwap_score=round(vwap_score, 1),
            vol_regime=vol_regime,
        )
        
        self._last_result[symbol] = result
        return result
    
    def _default_result(self, symbol: str) -> DirectionResult:
        """Return safe default when not enough data."""
        return DirectionResult(
            symbol=symbol,
            regime=Regime.RANGE,
            direction=0,
            confidence=0,
            aggression=0.5,
            strategy_filter=[SCALP],
        )
    
    def get_last(self, symbol: str) -> Optional[DirectionResult]:
        """Get the most recent direction result for a symbol."""
        return self._last_result.get(symbol)
    
    def should_allow_trade(self, symbol: str, strategy_type: str, trade_direction: int) -> Tuple[bool, str]:
        """
        Check if a trade should be allowed based on current regime.
        
        Args:
            symbol: Instrument
            strategy_type: One of WITH_TREND, MEAN_REVERT, SCALP, FADE
            trade_direction: 1 for LONG, -1 for SHORT
        
        Returns:
            (allowed: bool, reason: str)
        """
        result = self._last_result.get(symbol)
        if result is None:
            return True, "No direction data — allowing"
        
        # Check strategy filter
        if ALL not in result.strategy_filter and strategy_type not in result.strategy_filter:
            return False, f"Strategy {strategy_type} blocked in {result.regime.value}"
        
        # Check direction alignment for trend regimes
        if result.regime in (Regime.STRONG_TREND_UP, Regime.STRONG_TREND_DOWN):
            if result.direction != 0 and trade_direction != result.direction:
                return False, f"Counter-trend blocked: {result.regime.value} is {'UP' if result.direction > 0 else 'DOWN'}"
        
        # In weak trends, allow counter-trend only for mean reversion
        if result.regime in (Regime.WEAK_TREND_UP, Regime.WEAK_TREND_DOWN):
            if trade_direction != result.direction and strategy_type != MEAN_REVERT:
                return False, f"Only MEAN_REVERT allowed counter-trend in {result.regime.value}"
        
        return True, "OK"
    
    def classify_strategy(self, strategy_name: str) -> str:
        """
        Map FORGE strategy names to direction engine categories.
        """
        trend_strategies = {"EMA_BOUNCE", "PREV_DAY_HL", "MOMENTUM", "BREAKOUT", "ORB"}
        revert_strategies = {"MEAN_REVERT", "MEAN_REVERSION", "VWAP_REVERT", "STOCH_REVERSAL", "STOCH_REV"}
        
        upper = strategy_name.upper().replace(" ", "_")
        
        if upper in trend_strategies:
            return WITH_TREND
        elif upper in revert_strategies:
            return MEAN_REVERT
        else:
            return SCALP  # Default to scalp (always allowed)


# ═══════════════════════════════════════════════════════════════
# DAILY P&L GATE (built into same module for deployment simplicity)
# ═══════════════════════════════════════════════════════════════

class DailyPnLGate:
    """
    Tracks daily P&L from start-of-day balance.
    FTMO measures daily loss from SOD balance, NOT from high water mark.
    
    This is the #1 fix that would have saved both account breaches.
    """
    
    def __init__(self, 
                 soft_limit_pct: float = 3.5,   # Stop new trades at -3.5%
                 hard_limit_pct: float = 4.0,   # Close ALL at -4.0%
                 max_risk_budget: float = 4000,  # Max $4,000 total risk at any time
                 max_open: int = 8,              # Hard cap (safety net, not primary limit)
                 ):
        self.soft_limit_pct = soft_limit_pct
        self.hard_limit_pct = hard_limit_pct
        self.max_risk_budget = max_risk_budget
        self.max_open = max_open
        
        self.sod_balance: float = 0.0  # Set at start of day
        self.current_equity: float = 0.0
        self.trades_today: int = 0
        self.realized_pnl_today: float = 0.0
        self.is_locked: bool = False
        self.lock_reason: str = ""
        self._open_symbols: set = set()
        self._open_risk: Dict[str, float] = {}  # symbol → dollar risk of open position
    
    def set_sod_balance(self, balance: float):
        """Call at start of trading day or on bot boot."""
        self.sod_balance = balance
        self.trades_today = 0
        self.realized_pnl_today = 0.0
        self.is_locked = False
        self.lock_reason = ""
        self._open_symbols = set()
        self._open_risk = {}
        logger.info(f"[DAILY_GATE] SOD balance set: ${balance:.2f} | Risk budget: ${self.max_risk_budget:.0f}")
    
    def update(self, equity: float, balance: float, open_positions: int, 
               open_symbols: Optional[set] = None) -> Tuple[bool, bool, str]:
        """
        Check daily limits.
        
        Returns:
            (can_trade: bool, must_close_all: bool, reason: str)
        """
        self.current_equity = equity
        if open_symbols:
            self._open_symbols = open_symbols
        
        if self.sod_balance <= 0:
            self.sod_balance = balance
        
        daily_pnl = equity - self.sod_balance
        daily_pnl_pct = (daily_pnl / self.sod_balance) * 100 if self.sod_balance > 0 else 0
        
        # HARD LIMIT — close everything
        if daily_pnl_pct <= -self.hard_limit_pct:
            self.is_locked = True
            self.lock_reason = f"HARD LIMIT: daily loss {daily_pnl_pct:.1f}% (>{self.hard_limit_pct}%)"
            logger.warning(f"[DAILY_GATE] 🚨 {self.lock_reason}")
            return False, True, self.lock_reason
        
        # SOFT LIMIT — no new trades
        if daily_pnl_pct <= -self.soft_limit_pct:
            self.is_locked = True
            self.lock_reason = f"SOFT LIMIT: daily loss {daily_pnl_pct:.1f}% (>{self.soft_limit_pct}%)"
            logger.warning(f"[DAILY_GATE] ⚠️ {self.lock_reason}")
            return False, False, self.lock_reason
        
        # Hard cap on positions (safety net)
        if open_positions >= self.max_open:
            return False, False, f"MAX_OPEN reached: {open_positions}/{self.max_open}"
        
        if self.is_locked:
            return False, False, f"LOCKED: {self.lock_reason}"
        
        return True, False, f"OK (daily: {daily_pnl_pct:+.1f}%)"
    
    def can_open_symbol(self, symbol: str) -> Tuple[bool, str]:
        """Check if this specific symbol can be opened (no duplicates)."""
        if symbol in self._open_symbols:
            return False, f"DUPLICATE BLOCKED: {symbol} already open"
        return True, "OK"
    
    def can_afford_risk(self, symbol: str, risk_dollars: float) -> Tuple[bool, str]:
        """Check if adding this trade's risk stays within budget."""
        current_total = sum(self._open_risk.values())
        new_total = current_total + risk_dollars
        
        if new_total > self.max_risk_budget:
            return False, (f"RISK BUDGET: ${risk_dollars:.0f} would put total at "
                          f"${new_total:.0f} (budget: ${self.max_risk_budget:.0f})")
        return True, f"OK (${current_total:.0f} + ${risk_dollars:.0f} = ${new_total:.0f} / ${self.max_risk_budget:.0f})"
    
    def register_open(self, symbol: str, risk_dollars: float):
        """Register a newly opened position and its risk."""
        self._open_symbols.add(symbol)
        self._open_risk[symbol] = risk_dollars
        logger.info(f"[DAILY_GATE] Registered {symbol}: ${risk_dollars:.0f} risk | "
                    f"Total: ${sum(self._open_risk.values()):.0f} / ${self.max_risk_budget:.0f}")
    
    def register_close(self, symbol: str, pnl: float):
        """Register a closed position."""
        self._open_symbols.discard(symbol)
        self._open_risk.pop(symbol, None)
        self.trades_today += 1
        self.realized_pnl_today += pnl
        logger.info(f"[DAILY_GATE] Closed {symbol}: ${pnl:+.0f} | "
                    f"Remaining risk: ${sum(self._open_risk.values()):.0f}")
    
    def get_status(self) -> str:
        """Human-readable status."""
        if self.sod_balance <= 0:
            return "NOT INITIALIZED"
        daily_pnl = self.current_equity - self.sod_balance
        pct = (daily_pnl / self.sod_balance) * 100
        return (f"Daily: ${daily_pnl:+.0f} ({pct:+.1f}%) | "
                f"SOD: ${self.sod_balance:.0f} | "
                f"Trades: {self.trades_today} | "
                f"{'🔒 LOCKED' if self.is_locked else '✅ OPEN'}")


# ═══════════════════════════════════════════════════════════════
# INTEGRATION EXAMPLE
# ═══════════════════════════════════════════════════════════════

"""
INTEGRATION INTO FORGE main.py:
================================

# At boot:
from forge_direction_engine import DirectionEngine, DailyPnLGate, Regime

direction_engine = DirectionEngine()
daily_gate = DailyPnLGate(soft_limit_pct=3.5, hard_limit_pct=4.0, max_open=3)

# Set SOD balance on boot:
daily_gate.set_sod_balance(account_info["balance"])

# In the main loop, BEFORE signal generation:
for sym in instruments:
    candles = candle_data[sym]  # M15 candles
    direction = direction_engine.update(
        sym,
        o=candles["o"], h=candles["h"], l=candles["l"], c=candles["c"],
        v=candles.get("v")
    )
    logger.info(f"[DIR] {direction}")

# Check daily gate:
can_trade, must_close, reason = daily_gate.update(
    equity=account_equity,
    balance=account_balance,
    open_positions=len(open_positions),
    open_symbols={p["symbol"] for p in open_positions}
)

if must_close:
    # EMERGENCY: close all positions immediately
    for pos in open_positions:
        close_position(pos["id"])
    continue

if not can_trade:
    logger.info(f"[DAILY_GATE] {reason}")
    continue

# When evaluating each signal:
for signal in signals:
    # Check duplicate symbol
    can_open, dup_reason = daily_gate.can_open_symbol(signal.symbol)
    if not can_open:
        logger.info(f"[GATE] {dup_reason}")
        continue
    
    # Check direction engine
    strat_type = direction_engine.classify_strategy(signal.strategy)
    allowed, dir_reason = direction_engine.should_allow_trade(
        signal.symbol, strat_type, signal.direction
    )
    if not allowed:
        logger.info(f"[DIR] BLOCKED: {signal.symbol} {signal.strategy} — {dir_reason}")
        continue
    
    # Get aggression multiplier for position sizing
    dir_result = direction_engine.get_last(signal.symbol)
    if dir_result:
        lots = base_lots * dir_result.aggression
    
    # Execute trade...
"""
