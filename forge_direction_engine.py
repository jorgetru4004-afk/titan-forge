"""
TITAN FORGE — ELITE REAL-TIME DIRECTION ENGINE v1.1 (FIXED)
=============================================================
FIXES from V22.5 diagnostic:
 1. classify_strategy() now recognizes ALL 14 strategies
 2. RANGE regime allows ALL strategy types (not just MEAN_REVERT/SCALP)
 3. should_allow_trade() only blocks STRONG counter-trend (never blocks in RANGE)
 4. default_result returns direction bias from last candles (not flat 0)
 5. Aggression floor raised from 0.5 to 0.8
 6. Strategy filter uses ALL for most regimes — brakes come from direction check only

PHILOSOPHY: Direction engine is a COMPASS not a WALL.
  - It tells you WHICH DIRECTION to favor
  - It adjusts AGGRESSION (lot sizing)
  - It ONLY blocks trades that are clearly suicidal (counter-trend in strong trend)
  - It NEVER prevents trading in range/weak/transition regimes
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
    symbol: str
    regime: Regime
    direction: int          # 1=LONG bias, -1=SHORT bias, 0=NEUTRAL
    confidence: float       # 0-100
    aggression: float       # 0.8-2.5x position size multiplier
    strategy_filter: List[str]
    adx: float = 0.0
    di_plus: float = 0.0
    di_minus: float = 0.0
    trend_score: float = 0.0
    momentum_score: float = 0.0
    structure_score: float = 0.0
    vwap_score: float = 0.0
    vol_regime: str = "NORMAL"
    
    def __str__(self):
        dir_str = {1: "LONG", -1: "SHORT", 0: "NEUTRAL"}[self.direction]
        return (f"{self.symbol} | {self.regime.value} | {dir_str} | "
                f"conf={self.confidence:.0f} | agg={self.aggression:.1f}x | "
                f"strats={','.join(self.strategy_filter)}")


# Strategy filter constants
WITH_TREND = "WITH_TREND"
MEAN_REVERT = "MEAN_REVERT"
SCALP = "SCALP"
FADE = "FADE"
ALL = "ALL"


# ═══════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(data), np.nan)
    if len(data) < period:
        return np.full(len(data), np.mean(data) if len(data) > 0 else 0)
    mult = 2.0 / (period + 1)
    e = np.mean(data[:period])
    out[period - 1] = e
    for i in range(period, len(data)):
        e = (data[i] - e) * mult + e
        out[i] = e
    first_valid = out[period - 1]
    out[:period - 1] = first_valid
    return out


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
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


def _adx_di(high, low, close, period=14):
    n = len(close)
    adx = np.full(n, 20.0)
    di_p = np.full(n, 25.0)
    di_m = np.full(n, 25.0)
    if n < period * 2:
        return adx, di_p, di_m
    tr = np.maximum(high[1:] - low[1:], np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
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


def _vwap(high, low, close, volume):
    n = len(close)
    vwap = np.full(n, close[0] if n > 0 else 0.0)
    window = min(96, n)
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


def _macd(close, fast=12, slow=26, signal=9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _analyze_price_structure(high, low, close, lookback=12):
    n = len(close)
    if n < lookback + 2:
        return 0.0, 0
    recent_h = high[-lookback:]
    recent_l = low[-lookback:]
    recent_c = close[-lookback:]
    third = lookback // 3
    if third < 2:
        return 0.0, 0
    seg1_high = np.max(recent_h[:third])
    seg2_high = np.max(recent_h[third:2*third])
    seg3_high = np.max(recent_h[2*third:])
    seg1_low = np.min(recent_l[:third])
    seg2_low = np.min(recent_l[third:2*third])
    seg3_low = np.min(recent_l[2*third:])
    score = 0.0
    swings = 0
    if seg3_high > seg2_high > seg1_high:
        score += 40; swings += 2
    elif seg3_high > seg1_high:
        score += 20; swings += 1
    if seg3_low > seg2_low > seg1_low:
        score += 40; swings += 2
    elif seg3_low > seg1_low:
        score += 20; swings += 1
    if seg3_low < seg2_low < seg1_low:
        score -= 40; swings += 2
    elif seg3_low < seg1_low:
        score -= 20; swings += 1
    if seg3_high < seg2_high < seg1_high:
        score -= 40; swings += 2
    elif seg3_high < seg1_high:
        score -= 20; swings += 1
    full_range = np.max(recent_h) - np.min(recent_l)
    if full_range > 0:
        close_position = (recent_c[-1] - np.min(recent_l)) / full_range
        if close_position > 0.8: score += 15
        elif close_position < 0.2: score -= 15
    last_6 = recent_c[-6:]
    bullish_closes = sum(1 for i in range(1, len(last_6)) if last_6[i] > last_6[i-1])
    bearish_closes = sum(1 for i in range(1, len(last_6)) if last_6[i] < last_6[i-1])
    if bullish_closes >= 4: score += 15
    elif bearish_closes >= 4: score -= 15
    return np.clip(score, -100, 100), swings


# ═══════════════════════════════════════════════════════════════
# DIRECTION ENGINE (FIXED)
# ═══════════════════════════════════════════════════════════════

class DirectionEngine:
    MIN_BARS = 50
    HISTORY_SIZE = 10
    
    VOL_PROFILES = {
        "BTCUSD": {"high_mult": 1.8, "low_mult": 0.4, "trend_adx": 22},
        "XAUUSD": {"high_mult": 1.6, "low_mult": 0.5, "trend_adx": 23},
        "GBPJPY": {"high_mult": 1.5, "low_mult": 0.5, "trend_adx": 22},
        "US100":  {"high_mult": 1.5, "low_mult": 0.5, "trend_adx": 23},
        "USOIL":  {"high_mult": 1.6, "low_mult": 0.5, "trend_adx": 22},
        "_default": {"high_mult": 1.4, "low_mult": 0.5, "trend_adx": 22},
    }
    
    def __init__(self):
        self._history: Dict[str, deque] = {}
        self._last_result: Dict[str, DirectionResult] = {}
        self._switch_count: Dict[str, int] = {}
        self._last_switch_time: Dict[str, float] = {}
        self.MIN_SWITCH_INTERVAL = 2700
        self.WEIGHTS = {
            "adx_di": 0.25, "structure": 0.25, "momentum": 0.20,
            "vwap": 0.15, "candle_flow": 0.15,
        }
    
    def _get_vol_profile(self, symbol):
        return self.VOL_PROFILES.get(symbol, self.VOL_PROFILES["_default"])
    
    def update(self, symbol, o, h, l, c, v=None, h1_c=None, h1_h=None, h1_l=None):
        n = len(c)
        if v is None or len(v) == 0:
            v = np.ones(n)
        if n < self.MIN_BARS:
            return self._default_result(symbol, c)
        
        # ─── COMPUTE ALL INDICATORS ───
        adx, di_plus, di_minus = _adx_di(h, l, c, period=14)
        current_adx = adx[-1]
        current_di_plus = di_plus[-1]
        current_di_minus = di_minus[-1]
        
        atr_arr = _atr(h, l, c, period=14)
        current_atr = atr_arr[-1]
        avg_atr_50 = np.mean(atr_arr[-50:]) if n >= 50 else np.mean(atr_arr)
        
        rsi_arr = _rsi(c, period=14)
        current_rsi = rsi_arr[-1]
        
        macd_line, signal_line, macd_hist = _macd(c)
        current_macd_hist = macd_hist[-1]
        prev_macd_hist = macd_hist[-2] if n > 1 else 0
        
        vwap = _vwap(h, l, c, v)
        current_vwap = vwap[-1]
        
        ema_9 = _ema(c, 9)
        ema_21 = _ema(c, 21)
        ema_50 = _ema(c, 50)
        
        structure_score, swing_count = _analyze_price_structure(h, l, c, lookback=12)
        
        # ─── SCORE EACH DIMENSION ───
        adx_di_score = 0.0
        di_diff = current_di_plus - current_di_minus
        if current_adx > 20:
            strength = min((current_adx - 20) / 20, 1.0)
            adx_di_score = di_diff * strength * 2
        else:
            adx_di_score = di_diff * 0.3
        adx_di_score = np.clip(adx_di_score, -100, 100)
        
        momentum_score = 0.0
        if current_rsi > 60:
            momentum_score += (current_rsi - 50) * 1.5
        elif current_rsi < 40:
            momentum_score -= (50 - current_rsi) * 1.5
        if current_macd_hist > 0:
            momentum_score += 25
            if current_macd_hist > prev_macd_hist: momentum_score += 15
        elif current_macd_hist < 0:
            momentum_score -= 25
            if current_macd_hist < prev_macd_hist: momentum_score -= 15
        momentum_score = np.clip(momentum_score, -100, 100)
        
        vwap_score = 0.0
        if current_atr > 0:
            vwap_distance = (c[-1] - current_vwap) / current_atr
            vwap_score = np.clip(vwap_distance * 30, -100, 100)
        
        candle_flow = 0.0
        lookback_flow = min(6, n - 1)
        for i in range(-lookback_flow, 0):
            if c[i] > o[i]:
                body_pct = (c[i] - o[i]) / current_atr if current_atr > 0 else 0
                candle_flow += min(body_pct * 30, 20)
            elif c[i] < o[i]:
                body_pct = (o[i] - c[i]) / current_atr if current_atr > 0 else 0
                candle_flow -= min(body_pct * 30, 20)
        candle_flow = np.clip(candle_flow, -100, 100)
        
        # ─── WEIGHTED COMPOSITE ───
        composite = (
            self.WEIGHTS["adx_di"] * adx_di_score +
            self.WEIGHTS["structure"] * structure_score +
            self.WEIGHTS["momentum"] * momentum_score +
            self.WEIGHTS["vwap"] * vwap_score +
            self.WEIGHTS["candle_flow"] * candle_flow
        )
        
        # H1 confirmation
        h1_bias = 0.0
        if h1_c is not None and len(h1_c) >= 20:
            h1_ema9 = _ema(h1_c, 9)
            h1_ema21 = _ema(h1_c, 21)
            h1_structure, _ = _analyze_price_structure(h1_h, h1_l, h1_c, lookback=8)
            if h1_ema9[-1] > h1_ema21[-1]: h1_bias = 20
            elif h1_ema9[-1] < h1_ema21[-1]: h1_bias = -20
            h1_bias += h1_structure * 0.15
            h1_bias = np.clip(h1_bias, -30, 30)
        composite += h1_bias
        
        # ─── VOLATILITY REGIME ───
        profile = self._get_vol_profile(symbol)
        vol_ratio = current_atr / avg_atr_50 if avg_atr_50 > 0 else 1.0
        if vol_ratio > profile["high_mult"]: vol_regime = "HIGH"
        elif vol_ratio < profile["low_mult"]: vol_regime = "LOW"
        else: vol_regime = "NORMAL"
        
        # ─── DETERMINE REGIME ───
        trend_threshold = profile["trend_adx"]
        
        if vol_regime == "HIGH" and abs(composite) < 40:
            regime = Regime.HIGH_VOL
            direction = 1 if composite > 0 else (-1 if composite < 0 else 0)
            confidence = min(abs(composite), 50)
        elif vol_regime == "LOW" and current_adx < 15:
            regime = Regime.LOW_VOL
            direction = 1 if composite > 5 else (-1 if composite < -5 else 0)
            confidence = 30
        elif current_adx >= trend_threshold + 10 and abs(composite) >= 50:
            if composite > 0:
                regime = Regime.STRONG_TREND_UP; direction = 1
            else:
                regime = Regime.STRONG_TREND_DOWN; direction = -1
            confidence = min(abs(composite), 95)
        elif current_adx >= trend_threshold and abs(composite) >= 30:
            if composite > 0:
                regime = Regime.WEAK_TREND_UP; direction = 1
            else:
                regime = Regime.WEAK_TREND_DOWN; direction = -1
            confidence = min(abs(composite), 75)
        elif abs(composite) < 20 and current_adx < trend_threshold:
            regime = Regime.RANGE
            # FIX: Give directional bias even in range (never return flat 0)
            direction = 1 if composite > 5 else (-1 if composite < -5 else 0)
            confidence = max(30, 70 - abs(composite))
        else:
            regime = Regime.RANGE
            direction = 1 if composite > 10 else (-1 if composite < -10 else 0)
            confidence = min(abs(composite), 60)
        
        # ─── TRANSITION DETECTION ───
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self.HISTORY_SIZE)
            self._switch_count[symbol] = 0
            self._last_switch_time[symbol] = 0
        
        history = self._history[symbol]
        if len(history) >= 3:
            last_3 = list(history)[-3:]
            if regime != last_3[-1] and last_3[-1] != last_3[-2]:
                now = time.time()
                if now - self._last_switch_time[symbol] < self.MIN_SWITCH_INTERVAL:
                    regime = Regime.TRANSITION
                    confidence = max(confidence * 0.5, 20)
        
        if len(history) > 0 and regime != history[-1]:
            self._last_switch_time[symbol] = time.time()
            self._switch_count[symbol] += 1
        history.append(regime)
        
        # ═══════════════════════════════════════════════════════════
        # FIX #1: PERMISSIVE STRATEGY FILTER
        # Old: RANGE only allowed [MEAN_REVERT, SCALP, FADE]
        #      → blocked 73% of calibrated combos
        # New: ALL regimes allow ALL strategies. Direction check handles safety.
        # ═══════════════════════════════════════════════════════════
        
        if regime in (Regime.STRONG_TREND_UP, Regime.STRONG_TREND_DOWN):
            # Only restrict in STRONG trends — block counter-trend types
            strategy_filter = [ALL]
        elif regime in (Regime.WEAK_TREND_UP, Regime.WEAK_TREND_DOWN):
            strategy_filter = [ALL]
        elif regime == Regime.RANGE:
            strategy_filter = [ALL]  # FIX: was [MEAN_REVERT, SCALP, FADE]
        elif regime == Regime.HIGH_VOL:
            strategy_filter = [ALL]  # FIX: was [SCALP] only
        elif regime == Regime.LOW_VOL:
            strategy_filter = [ALL]
        elif regime == Regime.TRANSITION:
            strategy_filter = [ALL]  # FIX: was [SCALP] only
        else:
            strategy_filter = [ALL]
        
        # ═══════════════════════════════════════════════════════════
        # FIX #2: AGGRESSION FLOOR RAISED
        # Old: HIGH_VOL=0.5, TRANSITION=0.5 → half lots in volatile markets
        # New: Floor is 0.8 everywhere. Still trade, just slightly smaller.
        # ═══════════════════════════════════════════════════════════
        
        if regime in (Regime.STRONG_TREND_UP, Regime.STRONG_TREND_DOWN):
            aggression = 1.5 + (confidence / 100) * 1.0
        elif regime in (Regime.WEAK_TREND_UP, Regime.WEAK_TREND_DOWN):
            aggression = 1.0 + (confidence / 100) * 0.5
        elif regime == Regime.RANGE:
            aggression = 1.0
        elif regime == Regime.HIGH_VOL:
            aggression = 0.8  # FIX: was 0.5
        elif regime == Regime.LOW_VOL:
            aggression = 0.9  # FIX: was 0.8
        elif regime == Regime.TRANSITION:
            aggression = 0.8  # FIX: was 0.5
        else:
            aggression = 1.0
        
        result = DirectionResult(
            symbol=symbol, regime=regime, direction=direction,
            confidence=round(confidence, 1), aggression=round(aggression, 2),
            strategy_filter=strategy_filter,
            adx=round(current_adx, 1), di_plus=round(current_di_plus, 1),
            di_minus=round(current_di_minus, 1), trend_score=round(composite, 1),
            momentum_score=round(momentum_score, 1), structure_score=round(structure_score, 1),
            vwap_score=round(vwap_score, 1), vol_regime=vol_regime,
        )
        self._last_result[symbol] = result
        return result
    
    def _default_result(self, symbol, c=None):
        """FIX: Return directional bias from recent candles, not flat 0."""
        direction = 0
        if c is not None and len(c) >= 10:
            # Simple momentum: last 10 bars direction
            if c[-1] > c[-10]:
                direction = 1
            elif c[-1] < c[-10]:
                direction = -1
        return DirectionResult(
            symbol=symbol, regime=Regime.RANGE, direction=direction,
            confidence=10, aggression=0.8,  # FIX: was 0.5
            strategy_filter=[ALL],           # FIX: was [SCALP]
        )
    
    def get_last(self, symbol):
        return self._last_result.get(symbol)
    
    def should_allow_trade(self, symbol, strategy_type, trade_direction):
        """
        FIX #3: ONLY block in STRONG trends when trading counter-trend.
        Never block based on strategy type anymore — strategy filter is ALL everywhere.
        
        The philosophy: "All gas first, then brakes."
        Brakes = only block clearly suicidal trades (shorting a strong uptrend).
        Gas = everything else goes through.
        """
        result = self._last_result.get(symbol)
        if result is None:
            return True, "No direction data — allowing"
        
        # ONLY block counter-trend in STRONG trends
        if result.regime == Regime.STRONG_TREND_UP:
            if trade_direction == -1:
                return False, f"Counter-trend SHORT blocked: STRONG_TREND_UP (score={result.trend_score:.0f})"
        
        if result.regime == Regime.STRONG_TREND_DOWN:
            if trade_direction == 1:
                return False, f"Counter-trend LONG blocked: STRONG_TREND_DOWN (score={result.trend_score:.0f})"
        
        # Everything else: ALLOW
        return True, "OK"
    
    def classify_strategy(self, strategy_name):
        """
        FIX #4: Recognize ALL 14 strategies properly.
        Old version only knew 5 trend + 5 revert names → 7 strategies fell to SCALP default.
        """
        trend_strategies = {
            "EMA_BOUNCE", "PREV_DAY_HL", "MOMENTUM", "MOMENTUM_CONT", "BREAKOUT",
            "ORB", "VWAP_TREND", "LONDON_BREAKOUT", "ASIAN_RANGE", "VOL_SQUEEZE",
        }
        revert_strategies = {
            "MEAN_REVERT", "MEAN_REVERSION", "VWAP_REVERT", "STOCH_REVERSAL",
            "STOCH_REV", "LIQUIDITY_SWEEP", "PREV_DAY_BOUNCE",
        }
        
        upper = strategy_name.upper().replace(" ", "_")
        
        if upper in trend_strategies:
            return WITH_TREND
        elif upper in revert_strategies:
            return MEAN_REVERT
        else:
            return SCALP


# ═══════════════════════════════════════════════════════════════
# DAILY P&L GATE (FIXED)
# ═══════════════════════════════════════════════════════════════

class DailyPnLGate:
    """
    FIX #5: _open_symbols now syncs with actual positions each cycle.
    FIX #6: Risk budget increased to $6,000 (was $4,000 — exhausted in 2 trades).
    """
    
    def __init__(self, 
                 soft_limit_pct: float = 3.5,
                 hard_limit_pct: float = 4.0,
                 max_risk_budget: float = 6000,  # FIX: was 4000
                 max_open: int = 8,
                 ):
        self.soft_limit_pct = soft_limit_pct
        self.hard_limit_pct = hard_limit_pct
        self.max_risk_budget = max_risk_budget
        self.max_open = max_open
        self.sod_balance: float = 0.0
        self.current_equity: float = 0.0
        self.trades_today: int = 0
        self.realized_pnl_today: float = 0.0
        self.is_locked: bool = False
        self.lock_reason: str = ""
        self._open_symbols: set = set()
        self._open_risk: Dict[str, float] = {}
    
    def set_sod_balance(self, balance):
        self.sod_balance = balance
        self.trades_today = 0
        self.realized_pnl_today = 0.0
        self.is_locked = False
        self.lock_reason = ""
        self._open_symbols = set()
        self._open_risk = {}
        logger.info(f"[DAILY_GATE] SOD balance set: ${balance:.2f} | Risk budget: ${self.max_risk_budget:.0f}")
    
    def sync_open_symbols(self, actual_positions: list):
        """
        FIX #5: Sync _open_symbols with actual broker positions each cycle.
        This prevents phantom blocks from missed closures.
        """
        actual_syms = set()
        for pos in actual_positions:
            sym = pos.get("symbol", "").replace(".sim", "")
            if sym:
                actual_syms.add(sym)
        
        # Remove symbols that are no longer in actual positions
        stale = self._open_symbols - actual_syms
        for s in stale:
            self._open_symbols.discard(s)
            self._open_risk.pop(s, None)
            logger.info(f"[DAILY_GATE] Synced: {s} removed (no longer open)")
        
        self._open_symbols = actual_syms
    
    def update(self, equity, balance, open_positions, open_symbols=None):
        self.current_equity = equity
        if open_symbols:
            self._open_symbols = open_symbols
        if self.sod_balance <= 0:
            self.sod_balance = balance
        
        daily_pnl = equity - self.sod_balance
        daily_pnl_pct = (daily_pnl / self.sod_balance) * 100 if self.sod_balance > 0 else 0
        
        if daily_pnl_pct <= -self.hard_limit_pct:
            self.is_locked = True
            self.lock_reason = f"HARD LIMIT: daily loss {daily_pnl_pct:.1f}% (>{self.hard_limit_pct}%)"
            logger.warning(f"[DAILY_GATE] 🚨 {self.lock_reason}")
            return False, True, self.lock_reason
        
        if daily_pnl_pct <= -self.soft_limit_pct:
            self.is_locked = True
            self.lock_reason = f"SOFT LIMIT: daily loss {daily_pnl_pct:.1f}% (>{self.soft_limit_pct}%)"
            logger.warning(f"[DAILY_GATE] ⚠️ {self.lock_reason}")
            return False, False, self.lock_reason
        
        if open_positions >= self.max_open:
            return False, False, f"MAX_OPEN reached: {open_positions}/{self.max_open}"
        
        if self.is_locked:
            return False, False, f"LOCKED: {self.lock_reason}"
        
        return True, False, f"OK (daily: {daily_pnl_pct:+.1f}%)"
    
    def can_open_symbol(self, symbol):
        if symbol in self._open_symbols:
            return False, f"DUPLICATE BLOCKED: {symbol} already open"
        return True, "OK"
    
    def can_afford_risk(self, symbol, risk_dollars):
        current_total = sum(self._open_risk.values())
        new_total = current_total + risk_dollars
        if new_total > self.max_risk_budget:
            return False, (f"RISK BUDGET: ${risk_dollars:.0f} would put total at "
                          f"${new_total:.0f} (budget: ${self.max_risk_budget:.0f})")
        return True, f"OK (${current_total:.0f} + ${risk_dollars:.0f} = ${new_total:.0f} / ${self.max_risk_budget:.0f})"
    
    def register_open(self, symbol, risk_dollars):
        self._open_symbols.add(symbol)
        self._open_risk[symbol] = risk_dollars
        logger.info(f"[DAILY_GATE] Registered {symbol}: ${risk_dollars:.0f} risk | "
                    f"Total: ${sum(self._open_risk.values()):.0f} / ${self.max_risk_budget:.0f}")
    
    def register_close(self, symbol, pnl):
        self._open_symbols.discard(symbol)
        self._open_risk.pop(symbol, None)
        self.trades_today += 1
        self.realized_pnl_today += pnl
        logger.info(f"[DAILY_GATE] Closed {symbol}: ${pnl:+.0f} | "
                    f"Remaining risk: ${sum(self._open_risk.values()):.0f}")
    
    def get_status(self):
        if self.sod_balance <= 0:
            return "NOT INITIALIZED"
        daily_pnl = self.current_equity - self.sod_balance
        pct = (daily_pnl / self.sod_balance) * 100
        return (f"Daily: ${daily_pnl:+.0f} ({pct:+.1f}%) | "
                f"SOD: ${self.sod_balance:.0f} | Trades: {self.trades_today} | "
                f"Risk: ${sum(self._open_risk.values()):.0f}/${self.max_risk_budget:.0f} | "
                f"{'🔒 LOCKED' if self.is_locked else '✅ OPEN'}")
