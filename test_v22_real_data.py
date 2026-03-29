"""
FORGE v22 — REAL DATA TEST
============================
Pulls real candle data from Polygon API, computes all indicators,
runs the signal engine, and shows exactly what signals fire.

Usage:
    set POLYGON_API_KEY=your_key_here
    python test_v22_real_data.py

Or on Railway, POLYGON_API_KEY is already in env vars.
"""

import os
import sys
import json
import time
import requests
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forge_instruments_v22 import (
    SETUP_CONFIG, INSTRUMENT_META, get_all_symbols,
    TIME_OF_DAY_EDGES, CORRELATION_GROUPS, MONTHLY_SEASONALITY,
)
from forge_signals_v22 import SignalEngine, MarketSnapshot, Signal
from forge_runner import TradeManager, RunnerDetector
from forge_limit import LimitOrderManager
from forge_correlation import CorrelationGuard


# ─── Polygon API ─────────────────────────────────────────────────────────────

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

# Polygon ticker mapping (their format differs from MetaAPI)
POLYGON_TICKERS = {
    # Forex: C:XXXYYY
    "USDCHF": "C:USDCHF",
    "NZDUSD": "C:NZDUSD",
    "EURGBP": "C:EURGBP",
    "EURUSD": "C:EURUSD",
    "GBPJPY": "C:GBPJPY",
    "USDJPY": "C:USDJPY",
    "GBPUSD": "C:GBPUSD",
    # Indices (Polygon uses I: prefix or specific tickers)
    "GER40":  "I:DAX",
    "UK100":  "I:UKX",
    "US100":  "I:NDX",
    # Commodities
    "XAUUSD": "C:XAUUSD",
    "USOIL":  "C:USDBRO",  # Brent crude proxy — adjust if needed
    # Crypto: X:XXXUSD
    "ETHUSD": "X:ETHUSD",
    "BTCUSD": "X:BTCUSD",
}

# Fallback tickers if primary doesn't work
POLYGON_FALLBACKS = {
    "GER40": "C:EURUSD",    # Fallback: use EURUSD data as proxy
    "UK100": "C:GBPUSD",    # Fallback
    "US100": "X:BTCUSD",    # Fallback
    "USOIL": "C:USDCAD",   # Fallback (oil-correlated)
}


def fetch_polygon_candles(
    symbol: str,
    timeframe: str = "5",       # minutes
    days_back: int = 5,
    limit: int = 200,
) -> Optional[Dict]:
    """Fetch candle data from Polygon API."""
    
    ticker = POLYGON_TICKERS.get(symbol)
    if ticker is None:
        print(f"  ⚠ No Polygon ticker for {symbol}")
        return None

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days_back)

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range"
        f"/{timeframe}/minute"
        f"/{start_date.strftime('%Y-%m-%d')}"
        f"/{end_date.strftime('%Y-%m-%d')}"
        f"?adjusted=true&sort=asc&limit={limit}"
        f"&apiKey={POLYGON_API_KEY}"
    )

    try:
        resp = requests.get(url, timeout=15)
        data = resp.json()

        if data.get("resultsCount", 0) == 0:
            # Try fallback ticker
            fallback = POLYGON_FALLBACKS.get(symbol)
            if fallback:
                print(f"  ⚠ No data for {ticker}, trying fallback {fallback}")
                url = url.replace(ticker, fallback)
                resp = requests.get(url, timeout=15)
                data = resp.json()
                if data.get("resultsCount", 0) == 0:
                    print(f"  ❌ No data for {symbol} (fallback also empty)")
                    return None
            else:
                print(f"  ❌ No data for {symbol}")
                return None

        results = data.get("results", [])
        if len(results) < 50:
            print(f"  ⚠ Only {len(results)} candles for {symbol} (need 50+)")
            return None

        candles = {
            "opens":   np.array([r["o"] for r in results], dtype=float),
            "highs":   np.array([r["h"] for r in results], dtype=float),
            "lows":    np.array([r["l"] for r in results], dtype=float),
            "closes":  np.array([r["c"] for r in results], dtype=float),
            "volumes": np.array([r.get("v", 0) for r in results], dtype=float),
            "timestamps": [r["t"] for r in results],
            "count": len(results),
        }
        return candles

    except Exception as e:
        print(f"  ❌ Polygon error for {symbol}: {e}")
        return None


# ─── Indicator Computation ───────────────────────────────────────────────────

def compute_atr(highs, lows, closes, period=14):
    """Average True Range."""
    if len(closes) < period + 1:
        return 0.0
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1])
        )
    )
    if len(tr) < period:
        return np.mean(tr) if len(tr) > 0 else 0.0
    # Wilder's smoothing
    atr = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr = (atr * (period - 1) + tr[i]) / period
    return atr


def compute_rsi(closes, period=14):
    """Relative Strength Index."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_ema(data, period):
    """Exponential Moving Average."""
    if len(data) < period:
        return np.mean(data) if len(data) > 0 else 0.0
    multiplier = 2.0 / (period + 1)
    ema = np.mean(data[:period])
    for i in range(period, len(data)):
        ema = (data[i] - ema) * multiplier + ema
    return ema


def compute_ema_series(data, period):
    """Full EMA series."""
    ema = np.zeros(len(data))
    if len(data) < period:
        return ema
    ema[period - 1] = np.mean(data[:period])
    multiplier = 2.0 / (period + 1)
    for i in range(period, len(data)):
        ema[i] = (data[i] - ema[i - 1]) * multiplier + ema[i - 1]
    return ema


def compute_bollinger(closes, period=20, std_mult=2.0):
    """Bollinger Bands."""
    if len(closes) < period:
        mid = np.mean(closes)
        return mid + 0.01, mid - 0.01, mid
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    return sma + std_mult * std, sma - std_mult * std, sma


def compute_stochastic(highs, lows, closes, k_period=14, d_period=3):
    """Stochastic Oscillator (%K, %D)."""
    if len(closes) < k_period + d_period:
        return 50.0, 50.0, 50.0, 50.0

    k_values = []
    for i in range(k_period - 1, len(closes)):
        h = np.max(highs[i - k_period + 1:i + 1])
        l = np.min(lows[i - k_period + 1:i + 1])
        if h - l == 0:
            k_values.append(50.0)
        else:
            k_values.append(100.0 * (closes[i] - l) / (h - l))

    k_values = np.array(k_values)

    # %D is SMA of %K
    if len(k_values) < d_period:
        return k_values[-1], k_values[-1], k_values[-1], k_values[-1]

    d_current = np.mean(k_values[-d_period:])
    d_prev = np.mean(k_values[-d_period - 1:-1]) if len(k_values) > d_period else d_current
    k_prev = k_values[-2] if len(k_values) > 1 else k_values[-1]

    return k_values[-1], d_current, k_prev, d_prev


def compute_vwap(highs, lows, closes, volumes):
    """Volume-Weighted Average Price + std deviation."""
    typical = (highs + lows + closes) / 3.0
    cum_vol = np.cumsum(volumes)
    cum_tp_vol = np.cumsum(typical * volumes)

    if cum_vol[-1] == 0:
        return closes[-1], 1.0

    vwap = cum_tp_vol[-1] / cum_vol[-1]
    # VWAP std
    vwap_std = np.std(typical - vwap) if len(typical) > 1 else 1.0
    if vwap_std == 0:
        vwap_std = abs(closes[-1]) * 0.001  # Prevent zero

    return vwap, vwap_std


def compute_adx(highs, lows, closes, period=14):
    """Average Directional Index + DI+ / DI-."""
    if len(closes) < period * 2:
        return 20.0, 20.0, 25.0, 25.0  # adx, adx_prev, plus_di, minus_di

    plus_dm = np.zeros(len(highs))
    minus_dm = np.zeros(len(highs))
    tr = np.zeros(len(highs))

    for i in range(1, len(highs)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm[i] = up if up > down and up > 0 else 0
        minus_dm[i] = down if down > up and down > 0 else 0
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )

    # Wilder smoothing
    atr_s = np.mean(tr[1:period + 1])
    plus_dm_s = np.mean(plus_dm[1:period + 1])
    minus_dm_s = np.mean(minus_dm[1:period + 1])

    dx_values = []
    plus_di_val = 0
    minus_di_val = 0

    for i in range(period + 1, len(highs)):
        atr_s = (atr_s * (period - 1) + tr[i]) / period
        plus_dm_s = (plus_dm_s * (period - 1) + plus_dm[i]) / period
        minus_dm_s = (minus_dm_s * (period - 1) + minus_dm[i]) / period

        if atr_s > 0:
            plus_di_val = 100.0 * plus_dm_s / atr_s
            minus_di_val = 100.0 * minus_dm_s / atr_s
        else:
            plus_di_val = 0
            minus_di_val = 0

        di_sum = plus_di_val + minus_di_val
        if di_sum > 0:
            dx = 100.0 * abs(plus_di_val - minus_di_val) / di_sum
        else:
            dx = 0
        dx_values.append(dx)

    if len(dx_values) < period:
        adx = np.mean(dx_values) if dx_values else 20.0
        return adx, adx, plus_di_val, minus_di_val

    adx = np.mean(dx_values[:period])
    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period

    # ADX 5 bars ago
    adx_prev = adx  # Approximate
    if len(dx_values) > 5:
        adx_prev_series = np.mean(dx_values[:period])
        for i in range(period, len(dx_values) - 5):
            adx_prev_series = (adx_prev_series * (period - 1) + dx_values[i]) / period
        adx_prev = adx_prev_series

    return adx, adx_prev, plus_di_val, minus_di_val


def compute_keltner(closes, highs, lows, ema_period=20, atr_mult=1.5, atr_period=14):
    """Keltner Channels."""
    ema = compute_ema(closes, ema_period)
    atr = compute_atr(highs, lows, closes, atr_period)
    return ema + atr_mult * atr, ema - atr_mult * atr


# ─── Build MarketSnapshot from Real Data ────────────────────────────────────

def build_snapshot(symbol: str, candles: Dict) -> Optional[MarketSnapshot]:
    """Build a complete MarketSnapshot from Polygon candle data."""
    opens = candles["opens"]
    highs = candles["highs"]
    lows = candles["lows"]
    closes = candles["closes"]
    volumes = candles["volumes"]

    if len(closes) < 50:
        return None

    # Compute all indicators
    atr = compute_atr(highs, lows, closes)
    rsi = compute_rsi(closes)
    stoch_k, stoch_d, stoch_k_prev, stoch_d_prev = compute_stochastic(highs, lows, closes)
    ema_50 = compute_ema(closes, 50)
    ema_200 = compute_ema(closes, 200) if len(closes) >= 200 else compute_ema(closes, len(closes))
    bb_upper, bb_lower, bb_middle = compute_bollinger(closes)
    vwap, vwap_std = compute_vwap(highs, lows, closes, volumes)
    adx, adx_prev, plus_di, minus_di = compute_adx(highs, lows, closes)
    keltner_upper, keltner_lower = compute_keltner(closes, highs, lows)

    # Session data (approximate from recent candles)
    # Use last ~78 candles as "today" (5min * 78 ≈ 6.5 hours)
    session_len = min(78, len(closes))
    session_closes = closes[-session_len:]
    session_highs = highs[-session_len:]
    session_lows = lows[-session_len:]

    prev_day_idx = min(session_len + 78, len(closes))
    prev_day_closes = closes[-prev_day_idx:-session_len] if prev_day_idx > session_len else closes[:session_len]

    prev_day_high = np.max(prev_day_closes) if len(prev_day_closes) > 0 else closes[-1]
    prev_day_low = np.min(prev_day_closes) if len(prev_day_closes) > 0 else closes[-1]
    prev_day_close = prev_day_closes[-1] if len(prev_day_closes) > 0 else closes[-1]

    session_open = session_closes[0]
    session_high = np.max(session_highs)
    session_low = np.min(session_lows)

    # ORB (first 6 candles = 30 min at 5min timeframe)
    orb_candles = min(6, len(session_highs))
    orb_high = np.max(session_highs[:orb_candles])
    orb_low = np.min(session_lows[:orb_candles])
    orb_complete = session_len > 6

    # Asian range (approximate — first 84 candles of day at 5min = 7 hours)
    asian_len = min(84, len(highs))
    asian_high = np.max(highs[:asian_len])
    asian_low = np.min(lows[:asian_len])

    current_price = closes[-1]
    spread = atr * 0.05 if atr > 0 else abs(current_price) * 0.0001

    now_utc = datetime.now(timezone.utc)

    return MarketSnapshot(
        symbol=symbol,
        opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes,
        bid=current_price - spread / 2,
        ask=current_price + spread / 2,
        atr=atr, rsi=rsi,
        stoch_k=stoch_k, stoch_d=stoch_d,
        stoch_k_prev=stoch_k_prev, stoch_d_prev=stoch_d_prev,
        ema_50=ema_50, ema_200=ema_200,
        bb_upper=bb_upper, bb_lower=bb_lower, bb_middle=bb_middle,
        vwap=vwap, vwap_std=vwap_std,
        adx=adx, adx_prev=adx_prev,
        plus_di=plus_di, minus_di=minus_di,
        prev_day_high=prev_day_high, prev_day_low=prev_day_low,
        prev_day_close=prev_day_close,
        session_open=session_open, session_high=session_high, session_low=session_low,
        orb_high=orb_high, orb_low=orb_low, orb_complete=orb_complete,
        asian_high=asian_high, asian_low=asian_low, asian_complete=True,
        keltner_upper=keltner_upper, keltner_lower=keltner_lower,
        bars_since_open=session_len,
        current_hour_utc=now_utc.hour,
    )


# ─── Pretty Print ───────────────────────────────────────────────────────────

def print_header():
    print("\n" + "=" * 80)
    print("  FORGE v22 — REAL DATA TEST")
    print("  Philosophy: ALL GAS FIRST THEN BRAKES")
    print("=" * 80)


def print_snapshot_summary(symbol: str, snap: MarketSnapshot):
    """Print key indicator values for a symbol."""
    print(f"\n  {symbol}:")
    print(f"    Price: {snap.closes[-1]:.5f} | ATR: {snap.atr:.5f}")
    print(f"    RSI: {snap.rsi:.1f} | Stoch K/D: {snap.stoch_k:.1f}/{snap.stoch_d:.1f}")
    print(f"    EMA50: {snap.ema_50:.5f} | EMA200: {snap.ema_200:.5f}")
    print(f"    BB: [{snap.bb_lower:.5f} — {snap.bb_middle:.5f} — {snap.bb_upper:.5f}]")
    print(f"    VWAP: {snap.vwap:.5f} ± {snap.vwap_std:.5f}")
    print(f"    ADX: {snap.adx:.1f} (prev: {snap.adx_prev:.1f}) | +DI: {snap.plus_di:.1f} | -DI: {snap.minus_di:.1f}")
    print(f"    Prev Day: H={snap.prev_day_high:.5f} L={snap.prev_day_low:.5f} C={snap.prev_day_close:.5f}")
    print(f"    Session: O={snap.session_open:.5f} H={snap.session_high:.5f} L={snap.session_low:.5f}")
    print(f"    ORB: H={snap.orb_high:.5f} L={snap.orb_low:.5f} ({'complete' if snap.orb_complete else 'pending'})")
    setup = SETUP_CONFIG[symbol]
    print(f"    Strategy: {setup.strategy.value} | Dir: {setup.direction.value} | "
          f"Type: {setup.trade_type.value} | Order: {setup.order_type.value}")


def print_signal(sig: Signal, idx: int):
    """Print a signal in detail."""
    r_ratio = sig.tp_atr_mult / sig.sl_atr_mult if sig.sl_atr_mult > 0 else 0
    print(f"\n  {'🟢' if sig.order_type.value == 'MARKET' else '🔵'} SIGNAL #{idx}: "
          f"{sig.symbol} {sig.direction} — {sig.strategy.value}")
    print(f"    Confidence: {sig.final_confidence:.3f} "
          f"(raw: {sig.raw_confidence:.3f})")
    print(f"    Trade Type: {sig.trade_type.value} | Order: {sig.order_type.value}")
    print(f"    Entry: {sig.entry_price:.5f}")
    print(f"    SL: {sig.sl_price:.5f} ({sig.sl_atr_mult:.1f} ATR)")
    print(f"    TP: {sig.tp_price:.5f} ({sig.tp_atr_mult:.1f} ATR)")
    print(f"    R:R = 1:{r_ratio:.1f}")
    print(f"    Risk: {sig.risk_pct:.1f}%")

    # Context details
    ctx = sig.context
    if ctx:
        ctx_str = " | ".join(f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
                             for k, v in ctx.items())
        print(f"    Context: {ctx_str}")

    # Time-of-day edge?
    if ctx.get("tod_edge"):
        print(f"    ⚡ TIME-OF-DAY EDGE (p={ctx.get('tod_p_value', '?')})")
    if ctx.get("tod_suppressed"):
        print(f"    ⏸ Time-of-day SUPPRESSED (edge exists at different hour)")
    if ctx.get("seasonal_boost"):
        print(f"    📅 SEASONAL BOOST: +{ctx['seasonal_boost']:.0%}")


def print_correlation_check(signals: List[Signal]):
    """Check which signals could trade together."""
    guard = CorrelationGuard()
    active = set()
    allowed_signals = []
    blocked_signals = []

    for sig in signals:
        can, reason = guard.can_trade(sig.symbol, active)
        if can:
            active.add(sig.symbol)
            allowed_signals.append(sig)
        else:
            blocked_signals.append((sig, reason))

    if blocked_signals:
        print(f"\n  ⚠ CORRELATION BLOCKS:")
        for sig, reason in blocked_signals:
            print(f"    ❌ {sig.symbol} {sig.direction}: {reason}")

    return allowed_signals


# ─── Main Test Runner ────────────────────────────────────────────────────────

def main():
    print_header()

    if not POLYGON_API_KEY:
        print("\n  ❌ POLYGON_API_KEY not set!")
        print("  Run: set POLYGON_API_KEY=your_key_here")
        print("  Or on Railway it's already in env vars.")
        sys.exit(1)

    print(f"\n  API Key: {POLYGON_API_KEY[:8]}...")
    print(f"  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Instruments: {len(SETUP_CONFIG)}")

    # ─── Step 1: Fetch data for all instruments ──────────────────────
    print("\n" + "-" * 80)
    print("  STEP 1: Fetching real Polygon data...")
    print("-" * 80)

    snapshots: Dict[str, MarketSnapshot] = {}
    fetch_errors = []

    for symbol in get_all_symbols():
        print(f"  📡 Fetching {symbol}...", end=" ", flush=True)
        candles = fetch_polygon_candles(symbol, timeframe="5", days_back=5, limit=200)

        if candles is None:
            fetch_errors.append(symbol)
            print("FAILED")
            continue

        snap = build_snapshot(symbol, candles)
        if snap is None:
            fetch_errors.append(symbol)
            print("INSUFFICIENT DATA")
            continue

        snapshots[symbol] = snap
        print(f"OK ({candles['count']} candles)")
        time.sleep(0.25)  # Rate limit

    print(f"\n  ✅ Got data for {len(snapshots)}/{len(SETUP_CONFIG)} instruments")
    if fetch_errors:
        print(f"  ❌ Failed: {', '.join(fetch_errors)}")

    if not snapshots:
        print("\n  💀 No data at all. Check your API key and market hours.")
        sys.exit(1)

    # ─── Step 2: Show indicator snapshots ────────────────────────────
    print("\n" + "-" * 80)
    print("  STEP 2: Indicator Snapshots")
    print("-" * 80)

    for symbol, snap in snapshots.items():
        print_snapshot_summary(symbol, snap)

    # ─── Step 3: Run signal engine ───────────────────────────────────
    print("\n" + "-" * 80)
    print("  STEP 3: Running Signal Engine (threshold=0.20)")
    print("-" * 80)

    engine = SignalEngine()
    signals = engine.generate_signals(snapshots)

    if not signals:
        print("\n  😤 NO SIGNALS FIRED.")
        print("  This is unusual with a 0.20 threshold — check market conditions.")
        print("  If markets are closed, indicators may not show tradeable conditions.")
    else:
        print(f"\n  🔥 {len(signals)} SIGNALS FIRED!")
        for i, sig in enumerate(signals, 1):
            print_signal(sig, i)

    # ─── Step 4: Correlation check ───────────────────────────────────
    if signals:
        print("\n" + "-" * 80)
        print("  STEP 4: Correlation Filter")
        print("-" * 80)

        allowed = print_correlation_check(signals)
        print(f"\n  ✅ {len(allowed)} signals pass correlation check")
        if allowed:
            print(f"  Would trade: {', '.join(s.symbol for s in allowed)}")

    # ─── Step 5: Summary ─────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    print(f"  Instruments with data:  {len(snapshots)}")
    print(f"  Signals generated:      {len(signals)}")
    print(f"  Conviction threshold:   0.20")

    if signals:
        scalps = sum(1 for s in signals if s.trade_type.value == "SCALP")
        runners = sum(1 for s in signals if s.trade_type.value == "RUNNER")
        limits = sum(1 for s in signals if s.order_type.value == "LIMIT")
        markets = sum(1 for s in signals if s.order_type.value == "MARKET")
        avg_conf = np.mean([s.final_confidence for s in signals])
        max_conf = max(s.final_confidence for s in signals)

        print(f"  SCALP / RUNNER:         {scalps} / {runners}")
        print(f"  LIMIT / MARKET:         {limits} / {markets}")
        print(f"  Avg confidence:         {avg_conf:.3f}")
        print(f"  Max confidence:         {max_conf:.3f}")
        print(f"  Strategies firing:      {', '.join(set(s.strategy.value for s in signals))}")

        # Time-of-day edge check
        tod_signals = [s for s in signals if s.context.get("tod_edge")]
        if tod_signals:
            print(f"  ⚡ TOD edge signals:    {len(tod_signals)} "
                  f"({', '.join(s.symbol for s in tod_signals)})")

    print(f"\n  Current hour UTC:       {datetime.now(timezone.utc).hour}:00")
    print(f"  Current month:          {datetime.now(timezone.utc).month}")

    # Check if any TOD edges active now
    hour = datetime.now(timezone.utc).hour
    active_edges = TIME_OF_DAY_EDGES.get(hour, [])
    if active_edges:
        print(f"  ⚡ Active TOD edges:    {', '.join(f'{s} {d}' for s, d, _ in active_edges)}")
    else:
        print(f"  No TOD edges at {hour}:00 UTC")

    print("\n" + "=" * 80)
    print("  TEST COMPLETE")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
