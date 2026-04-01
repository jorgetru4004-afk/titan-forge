"""
TITAN FORGE V22.5 — COMPLETE STRATEGY ARSENAL
================================================
Every strategy type for every market condition.
Each function takes OHLCV arrays + indicators and returns signal arrays.
Signal: 1=LONG, -1=SHORT, 0=NO SIGNAL

Categories:
  COUNTER-TREND:  MEAN_REVERT, STOCH_REVERSAL, VWAP_REVERT
  TREND-FOLLOW:   EMA_BOUNCE, MOMENTUM_CONT, BREAKOUT, VWAP_TREND
  SESSION:        LONDON_BREAKOUT, ORB, ASIAN_RANGE
  VOLATILITY:     VOL_SQUEEZE
  LIQUIDITY:      LIQUIDITY_SWEEP, PREV_DAY_BOUNCE
  SCALP:          FIXED_TARGET (separate layer, not signal-based)

Each strategy has:
  - signal function (returns array of -1/0/1)
  - regime_type: which regimes it should run in
  - direction_type: WITH_TREND, MEAN_REVERT, or SCALP
"""

import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

# ═══════════════════════════════════════════════════════════════
# SHARED INSTRUMENT CONFIG
# ═══════════════════════════════════════════════════════════════

# Dollar value per price unit at 2 standard lots
DOLLAR_PER_UNIT = {
    "EURUSD": 200000, "GBPUSD": 200000, "USDJPY": 1333, "USDCHF": 200000,
    "EURGBP": 200000, "GBPJPY": 1333, "NZDUSD": 200000, "XAUUSD": 200,
    "US100": 20, "USOIL": 2000, "BTCUSD": 2,
    "AUDNZD": 200000, "AUDUSD": 200000, "EURJPY": 1333,
}

# Realistic spreads in price units
SPREAD = {
    "EURUSD": 0.00012, "GBPUSD": 0.00015, "USDJPY": 0.013, "USDCHF": 0.00015,
    "EURGBP": 0.00018, "GBPJPY": 0.025, "NZDUSD": 0.00018, "XAUUSD": 0.30,
    "US100": 1.5, "USOIL": 0.04, "BTCUSD": 50.0,
    "AUDNZD": 0.00025, "AUDUSD": 0.00015, "EURJPY": 0.02,
}

# ═══════════════════════════════════════════════════════════════
# INDICATOR HELPERS (shared across all strategies)
# ═══════════════════════════════════════════════════════════════

def ema(data, period):
    out = np.full(len(data), np.nan)
    if len(data) < period:
        return np.full(len(data), np.mean(data) if len(data) > 0 else 0)
    m = 2.0 / (period + 1)
    e = np.mean(data[:period])
    out[period - 1] = e
    for i in range(period, len(data)):
        e = (data[i] - e) * m + e
        out[i] = e
    out[:period - 1] = out[period - 1] if not np.isnan(out[period - 1]) else np.mean(data[:period])
    return np.nan_to_num(out, nan=np.mean(data[:period]))

def atr(h, l, c, p=14):
    if len(c) < 2:
        return np.full(len(c), abs(h[0] - l[0]) if len(c) > 0 else 1.0)
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    if len(tr) < p:
        return np.full(len(c), np.mean(tr))
    out = np.full(len(c), np.mean(tr[:p]))
    a = np.mean(tr[:p])
    out[p] = a
    for i in range(p, len(tr)):
        a = (a * (p - 1) + tr[i]) / p
        out[i + 1] = a
    return out

def rsi(c, p=14):
    out = np.full(len(c), 50.0)
    if len(c) < p + 1:
        return out
    d = np.diff(c)
    g = np.where(d > 0, d, 0)
    lo = np.where(d < 0, -d, 0)
    ag = np.mean(g[:p])
    al = np.mean(lo[:p])
    for i in range(p, len(g)):
        ag = (ag * (p - 1) + g[i]) / p
        al = (al * (p - 1) + lo[i]) / p
        out[i + 1] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out

def stoch(h, l, c, kp=14, dp=3):
    k = np.full(len(c), 50.0)
    for i in range(kp - 1, len(c)):
        hi = np.max(h[i - kp + 1:i + 1])
        lo = np.min(l[i - kp + 1:i + 1])
        k[i] = 100 * (c[i] - lo) / (hi - lo) if hi != lo else 50.0
    d = np.full(len(c), 50.0)
    for i in range(kp + dp - 2, len(c)):
        d[i] = np.mean(k[i - dp + 1:i + 1])
    return k, d

def bb(c, p=20, mult=2.0):
    mid = np.full(len(c), np.mean(c))
    upper = mid.copy()
    lower = mid.copy()
    for i in range(p, len(c)):
        s = c[i - p:i]
        m = np.mean(s)
        sd = np.std(s)
        mid[i] = m
        upper[i] = m + mult * sd
        lower[i] = m - mult * sd
    return upper, lower, mid

def vwap_rolling(h, l, c, v, window=96):
    n = len(c)
    vw = np.full(n, c[0] if n > 0 else 0.0)
    for i in range(n):
        s = max(0, i - window + 1)
        tp = (h[s:i + 1] + l[s:i + 1] + c[s:i + 1]) / 3.0
        vol = v[s:i + 1]
        tv = np.sum(vol)
        vw[i] = np.sum(tp * vol) / tv if tv > 0 else np.mean(tp)
    return vw

def macd(c, fast=12, slow=26, sig=9):
    ef = ema(c, fast)
    es = ema(c, slow)
    line = ef - es
    signal = ema(line, sig)
    hist = line - signal
    return line, signal, hist

def adx_calc(h, l, c, p=14):
    n = len(c)
    adx_out = np.full(n, 20.0)
    dip = np.full(n, 25.0)
    dim = np.full(n, 25.0)
    if n < p * 2:
        return adx_out, dip, dim
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    up = h[1:] - h[:-1]
    dn = l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_s = np.mean(tr[:p])
    pdm_s = np.mean(pdm[:p])
    mdm_s = np.mean(mdm[:p])
    dxs = []
    for i in range(p, len(tr)):
        atr_s = atr_s - atr_s / p + tr[i]
        pdm_s = pdm_s - pdm_s / p + pdm[i]
        mdm_s = mdm_s - mdm_s / p + mdm[i]
        if atr_s > 0:
            dip[i + 1] = 100.0 * pdm_s / atr_s
            dim[i + 1] = 100.0 * mdm_s / atr_s
        ds = dip[i + 1] + dim[i + 1]
        dx = 100.0 * abs(dip[i + 1] - dim[i + 1]) / ds if ds > 0 else 0
        dxs.append(dx)
        if len(dxs) >= p:
            if len(dxs) == p:
                adx_out[i + 1] = np.mean(dxs[-p:])
            else:
                adx_out[i + 1] = (adx_out[i] * (p - 1) + dxs[-1]) / p
    return adx_out, dip, dim


@dataclass
class StrategyMeta:
    """Metadata for each strategy."""
    name: str
    direction_type: str   # WITH_TREND, MEAN_REVERT, SCALP
    regime_types: list     # Which regimes it runs in
    description: str


# ═══════════════════════════════════════════════════════════════
# COUNTER-TREND STRATEGIES (existing, refined)
# ═══════════════════════════════════════════════════════════════

def strat_mean_revert(o, h, l, c, v, **kw):
    """RSI extreme + BB band touch. Classic mean reversion."""
    n = len(c)
    sigs = np.zeros(n)
    rsi_arr = rsi(c, 14)
    bb_u, bb_l, _ = bb(c, 20)
    atr_arr = atr(h, l, c)
    
    for i in range(2, n):
        if atr_arr[i] <= 0:
            continue
        # Rejection candle confirmation
        body = abs(c[i] - o[i])
        if body < atr_arr[i] * 0.05:
            continue
        
        if rsi_arr[i] < 30 and c[i] <= bb_l[i]:
            # Lower wick should be longer than body (rejection)
            lower_wick = min(o[i], c[i]) - l[i]
            if lower_wick > body * 0.5:
                sigs[i] = 1
        elif rsi_arr[i] > 70 and c[i] >= bb_u[i]:
            upper_wick = h[i] - max(o[i], c[i])
            if upper_wick > body * 0.5:
                sigs[i] = -1
    return sigs

MEAN_REVERT_META = StrategyMeta("MEAN_REVERT", "MEAN_REVERT", ["RANGE", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "RSI extreme + BB touch with rejection candle")


def strat_stoch_reversal(o, h, l, c, v, **kw):
    """Stochastic K/D crossover in extreme zones."""
    n = len(c)
    sigs = np.zeros(n)
    sk, sd = stoch(h, l, c, 14, 3)
    
    for i in range(2, n):
        # Bullish: K crosses above D below 25
        if sk[i - 1] < sd[i - 1] and sk[i] > sd[i] and sk[i] < 25:
            sigs[i] = 1
        # Bearish: K crosses below D above 75
        elif sk[i - 1] > sd[i - 1] and sk[i] < sd[i] and sk[i] > 75:
            sigs[i] = -1
    return sigs

STOCH_REVERSAL_META = StrategyMeta("STOCH_REVERSAL", "MEAN_REVERT", ["RANGE", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "Stochastic crossover in extreme zones")


def strat_vwap_revert(o, h, l, c, v, **kw):
    """Price extreme deviation from VWAP with rejection candle."""
    n = len(c)
    sigs = np.zeros(n)
    vw = vwap_rolling(h, l, c, v if v is not None else np.ones(n))
    atr_arr = atr(h, l, c)
    
    for i in range(2, n):
        if atr_arr[i] <= 0:
            continue
        dist = (c[i] - vw[i]) / atr_arr[i]
        body = abs(c[i] - o[i])
        if body < 1e-10:
            body = 1e-10
        
        if dist < -2.0:
            lwick = min(o[i], c[i]) - l[i]
            if lwick > body * 1.5:
                sigs[i] = 1
        elif dist > 2.0:
            uwick = h[i] - max(o[i], c[i])
            if uwick > body * 1.5:
                sigs[i] = -1
    return sigs

VWAP_REVERT_META = StrategyMeta("VWAP_REVERT", "MEAN_REVERT", ["RANGE", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "VWAP 2-ATR deviation with rejection wick")


# ═══════════════════════════════════════════════════════════════
# TREND-FOLLOWING STRATEGIES (new)
# ═══════════════════════════════════════════════════════════════

def strat_ema_bounce(o, h, l, c, v, **kw):
    """Price pulls back to EMA9 or EMA21 in a trend and bounces."""
    n = len(c)
    sigs = np.zeros(n)
    ema9 = ema(c, 9)
    ema21 = ema(c, 21)
    ema50 = ema(c, 50)
    atr_arr = atr(h, l, c)
    adx_arr, _, _ = adx_calc(h, l, c)
    
    for i in range(3, n):
        if atr_arr[i] <= 0 or adx_arr[i] < 20:
            continue
        
        # Uptrend: EMA9 > EMA21 > EMA50
        if ema9[i] > ema21[i] > ema50[i]:
            # Price touches or dips below EMA9/21 then closes above
            touched_ema = l[i] <= ema9[i] * 1.001 or l[i] <= ema21[i] * 1.001
            closed_above = c[i] > ema9[i]
            bullish_candle = c[i] > o[i]
            
            if touched_ema and closed_above and bullish_candle:
                sigs[i] = 1
        
        # Downtrend: EMA9 < EMA21 < EMA50
        elif ema9[i] < ema21[i] < ema50[i]:
            touched_ema = h[i] >= ema9[i] * 0.999 or h[i] >= ema21[i] * 0.999
            closed_below = c[i] < ema9[i]
            bearish_candle = c[i] < o[i]
            
            if touched_ema and closed_below and bearish_candle:
                sigs[i] = -1
    return sigs

EMA_BOUNCE_META = StrategyMeta("EMA_BOUNCE", "WITH_TREND", ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "Pullback to EMA in established trend")


def strat_momentum_continuation(o, h, l, c, v, **kw):
    """MACD histogram accelerating + RSI in trend zone (not extreme)."""
    n = len(c)
    sigs = np.zeros(n)
    _, _, macd_hist = macd(c)
    rsi_arr = rsi(c, 14)
    adx_arr, dip, dim = adx_calc(h, l, c)
    ema21 = ema(c, 21)
    
    for i in range(3, n):
        if adx_arr[i] < 22:
            continue
        
        # Bullish momentum: MACD hist positive + accelerating + RSI 50-70
        if (macd_hist[i] > 0 and macd_hist[i] > macd_hist[i - 1] and
            50 <= rsi_arr[i] <= 70 and dip[i] > dim[i] and c[i] > ema21[i]):
            # Confirm with bullish candle
            if c[i] > o[i] and (c[i] - o[i]) > (h[i] - l[i]) * 0.4:
                sigs[i] = 1
        
        # Bearish momentum: MACD hist negative + accelerating down + RSI 30-50
        elif (macd_hist[i] < 0 and macd_hist[i] < macd_hist[i - 1] and
              30 <= rsi_arr[i] <= 50 and dim[i] > dip[i] and c[i] < ema21[i]):
            if c[i] < o[i] and (o[i] - c[i]) > (h[i] - l[i]) * 0.4:
                sigs[i] = -1
    return sigs

MOMENTUM_CONT_META = StrategyMeta("MOMENTUM_CONT", "WITH_TREND", ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "MACD acceleration + RSI in trend zone")


def strat_breakout(o, h, l, c, v, **kw):
    """Break above/below previous session high/low (24-bar lookback for M15)."""
    n = len(c)
    sigs = np.zeros(n)
    atr_arr = atr(h, l, c)
    lookback = kw.get("lookback", 96)  # 96 M15 bars = 24 hours
    
    for i in range(lookback + 1, n):
        if atr_arr[i] <= 0:
            continue
        
        prev_high = np.max(h[i - lookback:i])
        prev_low = np.min(l[i - lookback:i])
        
        # Breakout above with strong candle
        if c[i] > prev_high and c[i - 1] <= prev_high:
            body = c[i] - o[i]
            candle_range = h[i] - l[i]
            if candle_range > 0 and body > candle_range * 0.5:  # Strong close
                sigs[i] = 1
        
        # Breakdown below with strong candle
        elif c[i] < prev_low and c[i - 1] >= prev_low:
            body = o[i] - c[i]
            candle_range = h[i] - l[i]
            if candle_range > 0 and body > candle_range * 0.5:
                sigs[i] = -1
    return sigs

BREAKOUT_META = StrategyMeta("BREAKOUT", "WITH_TREND", ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "LOW_VOL"], "Session high/low breakout with momentum confirmation")


def strat_vwap_trend(o, h, l, c, v, **kw):
    """In a trend, price pulls back to VWAP and bounces WITH the trend."""
    n = len(c)
    sigs = np.zeros(n)
    vw = vwap_rolling(h, l, c, v if v is not None else np.ones(n))
    ema21 = ema(c, 21)
    ema50 = ema(c, 50)
    atr_arr = atr(h, l, c)
    adx_arr, _, _ = adx_calc(h, l, c)
    
    for i in range(3, n):
        if atr_arr[i] <= 0 or adx_arr[i] < 20:
            continue
        
        vwap_dist = abs(c[i] - vw[i]) / atr_arr[i]
        
        # Uptrend: EMA21 > EMA50, price near VWAP, bounces up
        if ema21[i] > ema50[i] and vwap_dist < 0.5:
            # Price touched VWAP from above and bounced
            if l[i] <= vw[i] * 1.001 and c[i] > vw[i] and c[i] > o[i]:
                sigs[i] = 1
        
        # Downtrend: EMA21 < EMA50, price near VWAP, bounces down
        elif ema21[i] < ema50[i] and vwap_dist < 0.5:
            if h[i] >= vw[i] * 0.999 and c[i] < vw[i] and c[i] < o[i]:
                sigs[i] = -1
    return sigs

VWAP_TREND_META = StrategyMeta("VWAP_TREND", "WITH_TREND", ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "VWAP pullback bounce in trend direction")


# ═══════════════════════════════════════════════════════════════
# SESSION/TIME-BASED STRATEGIES (new)
# ═══════════════════════════════════════════════════════════════

def strat_london_breakout(o, h, l, c, v, timestamps=None, **kw):
    """
    Asian session builds range (00:00-07:00 UTC), London breaks it (07:00-10:00 UTC).
    Since we may not have timestamps, use bar index proxy:
    On M15 data, Asian session = bars 0-28 (7 hours), London = bars 28-40.
    
    For backtesting without timestamps, we look for 28-bar consolidation then breakout.
    """
    n = len(c)
    sigs = np.zeros(n)
    asian_bars = 28   # 7 hours on M15
    london_window = 12  # 3 hours after Asian close
    atr_arr = atr(h, l, c)
    
    for i in range(asian_bars + 1, n):
        if atr_arr[i] <= 0:
            continue
        
        # Define "Asian range" as the last 28 bars
        asian_high = np.max(h[i - asian_bars:i])
        asian_low = np.min(l[i - asian_bars:i])
        asian_range = asian_high - asian_low
        
        # Range must be reasonable (0.5-3x ATR = consolidation, not already trending)
        if asian_range < atr_arr[i] * 0.5 or asian_range > atr_arr[i] * 3.0:
            continue
        
        # Breakout above Asian high
        if c[i] > asian_high and c[i - 1] <= asian_high:
            body = c[i] - o[i]
            if body > 0 and body > asian_range * 0.3:
                sigs[i] = 1
        
        # Breakdown below Asian low
        elif c[i] < asian_low and c[i - 1] >= asian_low:
            body = o[i] - c[i]
            if body > 0 and body > asian_range * 0.3:
                sigs[i] = -1
    return sigs

LONDON_BREAKOUT_META = StrategyMeta("LONDON_BREAKOUT", "WITH_TREND", ["RANGE", "LOW_VOL", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "Asian range breakout at London open")


def strat_orb(o, h, l, c, v, **kw):
    """
    Opening Range Breakout. First 2 bars (30 min on M15) form range.
    Trade breakout of that range in next 8 bars (2 hours).
    Uses rolling 2-bar range as proxy without exact session times.
    """
    n = len(c)
    sigs = np.zeros(n)
    atr_arr = atr(h, l, c)
    orb_bars = 2  # 30 min opening range on M15
    valid_window = 8  # Trade within 2 hours of range forming
    
    for i in range(orb_bars + 1, n):
        if atr_arr[i] <= 0:
            continue
        
        # Look at previous 2 bars as "opening range"
        range_high = np.max(h[i - orb_bars:i])
        range_low = np.min(l[i - orb_bars:i])
        orb_range = range_high - range_low
        
        # Range must be tight (consolidation)
        if orb_range > atr_arr[i] * 1.5 or orb_range < atr_arr[i] * 0.2:
            continue
        
        # Breakout with conviction
        if c[i] > range_high and c[i] > o[i]:
            body_pct = (c[i] - o[i]) / (h[i] - l[i]) if h[i] != l[i] else 0
            if body_pct > 0.5:
                sigs[i] = 1
        elif c[i] < range_low and c[i] < o[i]:
            body_pct = (o[i] - c[i]) / (h[i] - l[i]) if h[i] != l[i] else 0
            if body_pct > 0.5:
                sigs[i] = -1
    return sigs

ORB_META = StrategyMeta("ORB", "WITH_TREND", ["RANGE", "LOW_VOL", "WEAK_TREND_UP", "WEAK_TREND_DOWN", "STRONG_TREND_UP", "STRONG_TREND_DOWN"], "Opening range breakout (30-min range)")


def strat_asian_range(o, h, l, c, v, **kw):
    """
    Mark 6-hour consolidation range, trade break in either direction.
    Similar to London breakout but uses 24-bar (6hr) lookback.
    """
    n = len(c)
    sigs = np.zeros(n)
    atr_arr = atr(h, l, c)
    range_bars = 24  # 6 hours on M15
    
    for i in range(range_bars + 1, n):
        if atr_arr[i] <= 0:
            continue
        
        session_high = np.max(h[i - range_bars:i])
        session_low = np.min(l[i - range_bars:i])
        session_range = session_high - session_low
        
        # Must be consolidation not chaos
        if session_range > atr_arr[i] * 4.0 or session_range < atr_arr[i] * 0.5:
            continue
        
        if c[i] > session_high and c[i - 1] <= session_high and c[i] > o[i]:
            sigs[i] = 1
        elif c[i] < session_low and c[i - 1] >= session_low and c[i] < o[i]:
            sigs[i] = -1
    return sigs

ASIAN_RANGE_META = StrategyMeta("ASIAN_RANGE", "WITH_TREND", ["RANGE", "LOW_VOL"], "6-hour session range breakout")


# ═══════════════════════════════════════════════════════════════
# VOLATILITY STRATEGIES (new)
# ═══════════════════════════════════════════════════════════════

def strat_vol_squeeze(o, h, l, c, v, **kw):
    """
    Bollinger Band squeeze (bands compress inside Keltner Channel).
    When BB width drops below threshold, expect expansion.
    Enter on first directional candle after squeeze.
    """
    n = len(c)
    sigs = np.zeros(n)
    bb_u, bb_l, bb_m = bb(c, 20, 2.0)
    atr_arr = atr(h, l, c, 20)
    
    for i in range(25, n):
        if atr_arr[i] <= 0:
            continue
        
        # BB width as percentage of price
        bb_width = (bb_u[i] - bb_l[i]) / c[i] if c[i] > 0 else 0
        
        # Keltner channel width (1.5x ATR)
        kc_width = (3.0 * atr_arr[i]) / c[i] if c[i] > 0 else 0
        
        # Squeeze: BB inside KC
        in_squeeze = bb_width < kc_width
        
        # Previous bar was in squeeze, current bar breaks out
        prev_bb_width = (bb_u[i - 1] - bb_l[i - 1]) / c[i - 1] if c[i - 1] > 0 else 0
        prev_kc_width = (3.0 * atr_arr[i - 1]) / c[i - 1] if c[i - 1] > 0 else 0
        was_squeeze = prev_bb_width < prev_kc_width
        
        if was_squeeze:
            # Expansion breakout
            body = abs(c[i] - o[i])
            candle_range = h[i] - l[i]
            
            if candle_range > 0 and body > candle_range * 0.6:
                if c[i] > o[i] and c[i] > bb_m[i]:
                    sigs[i] = 1
                elif c[i] < o[i] and c[i] < bb_m[i]:
                    sigs[i] = -1
    return sigs

VOL_SQUEEZE_META = StrategyMeta("VOL_SQUEEZE", "WITH_TREND", ["LOW_VOL", "RANGE"], "Bollinger/Keltner squeeze breakout")


# ═══════════════════════════════════════════════════════════════
# LIQUIDITY/STRUCTURE STRATEGIES (new)
# ═══════════════════════════════════════════════════════════════

def strat_liquidity_sweep(o, h, l, c, v, **kw):
    """
    Price spikes through a recent high/low (stop hunt), then reverses.
    The wick goes beyond the level but the close is back inside.
    This is how smart money enters — they trigger retail stops then reverse.
    """
    n = len(c)
    sigs = np.zeros(n)
    atr_arr = atr(h, l, c)
    lookback = 48  # 12 hours on M15
    
    for i in range(lookback + 1, n):
        if atr_arr[i] <= 0:
            continue
        
        prev_high = np.max(h[i - lookback:i - 1])  # Exclude current and previous bar
        prev_low = np.min(l[i - lookback:i - 1])
        
        # Bullish sweep: wick below previous low but close above it
        if l[i] < prev_low and c[i] > prev_low:
            sweep_depth = prev_low - l[i]
            # Wick must be meaningful but not too deep
            if 0.2 * atr_arr[i] < sweep_depth < 1.5 * atr_arr[i]:
                # Must close in upper half of candle
                if c[i] > (h[i] + l[i]) / 2:
                    sigs[i] = 1
        
        # Bearish sweep: wick above previous high but close below it
        elif h[i] > prev_high and c[i] < prev_high:
            sweep_depth = h[i] - prev_high
            if 0.2 * atr_arr[i] < sweep_depth < 1.5 * atr_arr[i]:
                if c[i] < (h[i] + l[i]) / 2:
                    sigs[i] = -1
    return sigs

LIQUIDITY_SWEEP_META = StrategyMeta("LIQUIDITY_SWEEP", "MEAN_REVERT", ["RANGE", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "Stop hunt reversal at liquidity levels")


def strat_prev_day_bounce(o, h, l, c, v, **kw):
    """
    Bounce off previous day's high, low, or close.
    Different from PREV_DAY_HL breakout — this trades the REJECTION.
    """
    n = len(c)
    sigs = np.zeros(n)
    atr_arr = atr(h, l, c)
    day_bars = 96  # 24 hours on M15
    
    for i in range(day_bars + 2, n):
        if atr_arr[i] <= 0:
            continue
        
        pd_high = np.max(h[i - day_bars:i - 1])
        pd_low = np.min(l[i - day_bars:i - 1])
        pd_close = c[i - day_bars]
        
        tolerance = atr_arr[i] * 0.3  # Within 0.3 ATR of level
        
        # Bounce off previous day low (support)
        if abs(l[i] - pd_low) < tolerance and c[i] > o[i]:
            lower_wick = min(o[i], c[i]) - l[i]
            body = abs(c[i] - o[i])
            if lower_wick > body:  # Rejection wick
                sigs[i] = 1
        
        # Bounce off previous day high (resistance)
        elif abs(h[i] - pd_high) < tolerance and c[i] < o[i]:
            upper_wick = h[i] - max(o[i], c[i])
            body = abs(c[i] - o[i])
            if upper_wick > body:
                sigs[i] = -1
        
        # Bounce off previous day close (pivot)
        elif abs(l[i] - pd_close) < tolerance and c[i] > pd_close and c[i] > o[i]:
            sigs[i] = 1
        elif abs(h[i] - pd_close) < tolerance and c[i] < pd_close and c[i] < o[i]:
            sigs[i] = -1
    
    return sigs

PREV_DAY_BOUNCE_META = StrategyMeta("PREV_DAY_BOUNCE", "MEAN_REVERT", ["RANGE", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "Rejection bounce at previous day's levels")


def strat_prev_day_hl(o, h, l, c, v, **kw):
    """Previous day high/low breakout (existing, kept for completeness)."""
    n = len(c)
    sigs = np.zeros(n)
    day_bars = 96
    
    for i in range(day_bars + 1, n):
        pdh = np.max(h[i - day_bars:i])
        pdl = np.min(l[i - day_bars:i])
        if c[i] > pdh and c[i - 1] <= pdh:
            sigs[i] = 1
        elif c[i] < pdl and c[i - 1] >= pdl:
            sigs[i] = -1
    return sigs

PREV_DAY_HL_META = StrategyMeta("PREV_DAY_HL", "WITH_TREND", ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "WEAK_TREND_UP", "WEAK_TREND_DOWN"], "Previous day high/low breakout")


# ═══════════════════════════════════════════════════════════════
# MASTER REGISTRY
# ═══════════════════════════════════════════════════════════════

ALL_STRATEGIES = {
    # Counter-trend
    "MEAN_REVERT": (strat_mean_revert, MEAN_REVERT_META),
    "STOCH_REVERSAL": (strat_stoch_reversal, STOCH_REVERSAL_META),
    "VWAP_REVERT": (strat_vwap_revert, VWAP_REVERT_META),
    
    # Trend-following
    "EMA_BOUNCE": (strat_ema_bounce, EMA_BOUNCE_META),
    "MOMENTUM_CONT": (strat_momentum_continuation, MOMENTUM_CONT_META),
    "BREAKOUT": (strat_breakout, BREAKOUT_META),
    "VWAP_TREND": (strat_vwap_trend, VWAP_TREND_META),
    
    # Session/time-based
    "LONDON_BREAKOUT": (strat_london_breakout, LONDON_BREAKOUT_META),
    "ORB": (strat_orb, ORB_META),
    "ASIAN_RANGE": (strat_asian_range, ASIAN_RANGE_META),
    
    # Volatility
    "VOL_SQUEEZE": (strat_vol_squeeze, VOL_SQUEEZE_META),
    
    # Liquidity/structure
    "LIQUIDITY_SWEEP": (strat_liquidity_sweep, LIQUIDITY_SWEEP_META),
    "PREV_DAY_BOUNCE": (strat_prev_day_bounce, PREV_DAY_BOUNCE_META),
    "PREV_DAY_HL": (strat_prev_day_hl, PREV_DAY_HL_META),
}

# Group by type for easy access
COUNTER_TREND_STRATS = ["MEAN_REVERT", "STOCH_REVERSAL", "VWAP_REVERT"]
TREND_FOLLOW_STRATS = ["EMA_BOUNCE", "MOMENTUM_CONT", "BREAKOUT", "VWAP_TREND"]
SESSION_STRATS = ["LONDON_BREAKOUT", "ORB", "ASIAN_RANGE"]
VOLATILITY_STRATS = ["VOL_SQUEEZE"]
LIQUIDITY_STRATS = ["LIQUIDITY_SWEEP", "PREV_DAY_BOUNCE", "PREV_DAY_HL"]


def get_strategies_for_regime(regime: str) -> list:
    """Return list of strategy names appropriate for this regime."""
    result = []
    for name, (func, meta) in ALL_STRATEGIES.items():
        if regime in meta.regime_types:
            result.append(name)
    return result


def get_strategy_direction_type(name: str) -> str:
    """Return WITH_TREND, MEAN_REVERT, or SCALP for a strategy."""
    if name in ALL_STRATEGIES:
        return ALL_STRATEGIES[name][1].direction_type
    return "SCALP"


def print_strategy_matrix():
    """Print which strategies run in which regimes."""
    regimes = ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "WEAK_TREND_UP", 
               "WEAK_TREND_DOWN", "RANGE", "HIGH_VOL", "LOW_VOL"]
    
    print(f"\n{'Strategy':<20} {'Type':<12} ", end="")
    for r in regimes:
        print(f"{r[:8]:>9}", end="")
    print()
    print("─" * 100)
    
    for name, (func, meta) in ALL_STRATEGIES.items():
        print(f"{name:<20} {meta.direction_type:<12} ", end="")
        for r in regimes:
            mark = "  ✅" if r in meta.regime_types else "  ·"
            print(f"{mark:>9}", end="")
        print()


if __name__ == "__main__":
    print("═" * 60)
    print("  TITAN FORGE V22.5 — STRATEGY ARSENAL")
    print("═" * 60)
    print(f"\n  Total strategies: {len(ALL_STRATEGIES)}")
    print(f"  Counter-trend:    {len(COUNTER_TREND_STRATS)} — {', '.join(COUNTER_TREND_STRATS)}")
    print(f"  Trend-following:  {len(TREND_FOLLOW_STRATS)} — {', '.join(TREND_FOLLOW_STRATS)}")
    print(f"  Session-based:    {len(SESSION_STRATS)} — {', '.join(SESSION_STRATS)}")
    print(f"  Volatility:       {len(VOLATILITY_STRATS)} — {', '.join(VOLATILITY_STRATS)}")
    print(f"  Liquidity:        {len(LIQUIDITY_STRATS)} — {', '.join(LIQUIDITY_STRATS)}")
    
    print_strategy_matrix()
    
    print(f"\n  Regime coverage:")
    for regime in ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "WEAK_TREND_UP", 
                    "WEAK_TREND_DOWN", "RANGE", "HIGH_VOL", "LOW_VOL"]:
        strats = get_strategies_for_regime(regime)
        print(f"    {regime:<20} → {len(strats)} strategies: {', '.join(strats)}")
