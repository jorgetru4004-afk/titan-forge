"""
FORGE v22 — NEW INSTRUMENT RESEARCH SCANNER
=============================================
Tests 5 candidate pairs: AUDUSD, EURJPY, CADJPY, AUDNZD, USDCAD
Same methodology as original 14: 8 strategies × 3 regimes × 2 directions
6 months of real Polygon hourly data, walk-forward backtest.

Outputs the optimal config (strategy, direction, trade type) for each pair.
Results plug directly into main.py configs.

Usage:
    set POLYGON_API_KEY=your_key_here
    set PYTHONIOENCODING=utf-8
    python research_new_pairs.py
"""

import os, sys, time, requests
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

# ═══ CANDIDATES ═══
CANDIDATES = {
    "AUDUSD": "C:AUDUSD",
    "EURJPY": "C:EURJPY",
    "CADJPY": "C:CADJPY",
    "AUDNZD": "C:AUDNZD",
    "USDCAD": "C:USDCAD",
}

# ═══ STRATEGIES ═══
STRATEGIES = [
    "GAP_FILL", "MEAN_REVERT", "VWAP_REVERT",
    "EMA_BOUNCE", "PREV_DAY_HL", "ORB",
    "ASIAN_BREAKOUT", "VOL_COMPRESS",
]
DIRECTIONS = ["LONG", "SHORT", "BOTH"]
REGIMES = ["SHORT", "LONG", "NEUTRAL"]
TRADE_TYPES = ["SCALP", "RUNNER"]

# ═══ DATA FETCH — 6 MONTHS ═══

def fetch_candles(symbol, days_back=180):
    """Fetch up to 6 months of hourly candles with pagination."""
    ticker = CANDIDATES.get(symbol)
    if not ticker:
        return None

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)
    all_results = []
    chunk_start = start

    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=35), end)
        url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/hour"
               f"/{chunk_start.strftime('%Y-%m-%d')}/{chunk_end.strftime('%Y-%m-%d')}"
               f"?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_API_KEY}")

        pages = 0
        while url and pages < 20:
            try:
                resp = requests.get(url, timeout=20)
                if resp.status_code == 429:
                    print("(rate limit 12s)", end=" ", flush=True)
                    time.sleep(12)
                    resp = requests.get(url, timeout=20)
                if resp.status_code != 200:
                    break
                data = resp.json()
                all_results.extend(data.get("results", []))
                pages += 1
                nxt = data.get("next_url")
                if nxt:
                    url = nxt + (f"&apiKey={POLYGON_API_KEY}" if "apiKey" not in nxt else "")
                    time.sleep(0.2)
                else:
                    break
            except Exception as e:
                print(f"(err: {e})", end=" ")
                break

        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(0.3)

    if len(all_results) < 50:
        return None

    # Deduplicate
    seen = set()
    deduped = []
    for r in all_results:
        t = r["t"]
        if t not in seen:
            seen.add(t)
            deduped.append(r)
    deduped.sort(key=lambda x: x["t"])

    return {
        "opens": np.array([r["o"] for r in deduped], dtype=float),
        "highs": np.array([r["h"] for r in deduped], dtype=float),
        "lows": np.array([r["l"] for r in deduped], dtype=float),
        "closes": np.array([r["c"] for r in deduped], dtype=float),
        "volumes": np.array([r.get("v", 0) for r in deduped], dtype=float),
        "timestamps": [r["t"] for r in deduped],
        "count": len(deduped),
    }


# ═══ INDICATORS ═══

def compute_atr(h, l, c, p=14):
    if len(c) < 2: return abs(c[-1]) * 0.01
    tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])))
    if len(tr) < p: return np.mean(tr)
    a = np.mean(tr[:p])
    for i in range(p, len(tr)): a = (a*(p-1)+tr[i])/p
    return a

def compute_rsi(c, p=14):
    if len(c) < p+1: return 50.0
    d = np.diff(c); g = np.where(d>0,d,0); lo = np.where(d<0,-d,0)
    ag = np.mean(g[:p]); al = np.mean(lo[:p])
    for i in range(p, len(g)): ag = (ag*(p-1)+g[i])/p; al = (al*(p-1)+lo[i])/p
    if al == 0: return 100.0
    return 100.0 - 100.0/(1.0+ag/al)

def compute_ema(d, p):
    if len(d) < p: return np.mean(d) if len(d) > 0 else 0.0
    m = 2.0/(p+1); e = np.mean(d[:p])
    for i in range(p, len(d)): e = (d[i]-e)*m+e
    return e

def compute_bollinger(c, p=20, m=2.0):
    if len(c) < p:
        mid = np.mean(c); s = np.std(c) if len(c) > 1 else abs(mid)*0.01
        return mid+m*s, mid-m*s, mid
    sma = np.mean(c[-p:]); s = np.std(c[-p:])
    if s == 0: s = abs(sma)*0.001
    return sma+m*s, sma-m*s, sma

def compute_stochastic(h, l, c, kp=14, dp=3):
    if len(c) < kp+dp: return 50.,50.,50.,50.
    kvs = []
    for i in range(kp-1, len(c)):
        hi, lo = np.max(h[i-kp+1:i+1]), np.min(l[i-kp+1:i+1])
        kvs.append(100.*(c[i]-lo)/(hi-lo) if hi != lo else 50.)
    kvs = np.array(kvs)
    if len(kvs) < dp: return kvs[-1], kvs[-1], kvs[-1], kvs[-1]
    return kvs[-1], np.mean(kvs[-dp:]), kvs[-2] if len(kvs) > 1 else kvs[-1], np.mean(kvs[-dp-1:-1]) if len(kvs) > dp else np.mean(kvs[-dp:])

def compute_vwap(h, l, c, v):
    tp = (h+l+c)/3.; cv = np.cumsum(v); ctv = np.cumsum(tp*v)
    if cv[-1] == 0: return c[-1], abs(c[-1])*0.001
    vw = ctv[-1]/cv[-1]; vs = np.std(tp-vw) if len(tp) > 1 else abs(c[-1])*0.001
    return vw, max(vs, abs(c[-1])*0.0001)

def compute_adx(h, l, c, p=14):
    if len(c) < p*2: return 20.,20.,25.,25.
    pdm, mdm, tr = np.zeros(len(h)), np.zeros(len(h)), np.zeros(len(h))
    for i in range(1, len(h)):
        u, dn = h[i]-h[i-1], l[i-1]-l[i]
        pdm[i] = u if u > dn and u > 0 else 0
        mdm[i] = dn if dn > u and dn > 0 else 0
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    atrs = np.mean(tr[1:p+1]); pdms = np.mean(pdm[1:p+1]); mdms = np.mean(mdm[1:p+1])
    dxv = []; pdi = mdi = 0
    for i in range(p+1, len(h)):
        atrs = (atrs*(p-1)+tr[i])/p; pdms = (pdms*(p-1)+pdm[i])/p; mdms = (mdms*(p-1)+mdm[i])/p
        if atrs > 0: pdi = 100.*pdms/atrs; mdi = 100.*mdms/atrs
        ds = pdi+mdi; dxv.append(100.*abs(pdi-mdi)/ds if ds > 0 else 0)
    if len(dxv) < p: a = np.mean(dxv) if dxv else 20.; return a, a, pdi, mdi
    adx = np.mean(dxv[:p])
    for i in range(p, len(dxv)): adx = (adx*(p-1)+dxv[i])/p
    ap = adx
    if len(dxv) > 5:
        ap = np.mean(dxv[:p])
        for i in range(p, len(dxv)-5): ap = (ap*(p-1)+dxv[i])/p
    return adx, ap, pdi, mdi


# ═══ REGIME DETECTION ═══

def detect_regime(h, l, c):
    """Classify current regime: SHORT, LONG, or NEUTRAL."""
    if len(c) < 50:
        return "NEUTRAL"
    adx, _, pdi, mdi = compute_adx(h, l, c)
    ema50 = compute_ema(c, 50)
    ema200 = compute_ema(c, min(200, len(c)))
    bbu, bbl, bbm = compute_bollinger(c)
    bb_width = (bbu - bbl) / bbm if bbm > 0 else 0

    if adx > 25:
        if pdi > mdi and c[-1] > ema50:
            return "LONG"
        elif mdi > pdi and c[-1] < ema50:
            return "SHORT"
    return "NEUTRAL"


# ═══ SIGNAL GENERATION (per strategy) ═══

def generate_signal(strategy, direction, candles, bar_idx):
    """
    Check if a specific strategy fires at a specific bar.
    Returns (fired, entry, sl, tp, confidence) or (False, ...).
    """
    end = bar_idx + 1
    start = max(0, end - 250)
    h = candles["highs"][start:end]
    l = candles["lows"][start:end]
    c = candles["closes"][start:end]
    o = candles["opens"][start:end]
    v = candles["volumes"][start:end]
    n = len(c)
    if n < 30:
        return False, 0, 0, 0, 0

    price = c[-1]
    atr = compute_atr(h, l, c)
    if atr == 0: return False, 0, 0, 0, 0
    rsi = compute_rsi(c)
    sk, sd, _, _ = compute_stochastic(h, l, c)
    ema50 = compute_ema(c, min(50, n))
    bbu, bbl, bbm = compute_bollinger(c)
    vwap, vstd = compute_vwap(h, l, c, v)

    # Session data
    sl_len = min(8, n)
    prev_idx = min(sl_len + 8, n)
    pdh = np.max(h[-prev_idx:-sl_len]) if prev_idx > sl_len else np.max(h[:sl_len])
    pdl = np.min(l[-prev_idx:-sl_len]) if prev_idx > sl_len else np.min(l[:sl_len])
    pdc = c[-sl_len-1] if n > sl_len else c[0]
    session_open = o[-sl_len]
    session_high = np.max(h[-sl_len:])
    session_low = np.min(l[-sl_len:])

    # ORB
    orb_high = h[-sl_len]
    orb_low = l[-sl_len]

    # Asian range
    asian_len = min(7, n)
    asian_high = np.max(h[:asian_len])
    asian_low = np.min(l[:asian_len])

    fired = False
    conf = 0.0
    entry = price
    sl_price = 0.0
    tp_price = 0.0

    # Check direction filter
    is_long = direction in ("LONG", "BOTH")
    is_short = direction in ("SHORT", "BOTH")

    if strategy == "GAP_FILL":
        gap = price - pdc
        if is_long and gap < -atr * 0.3 and rsi < 45:
            fired = True; conf = min(abs(gap) / atr, 1.0) * 0.5
            sl_price = price - atr * 1.5; tp_price = pdc
        elif is_short and gap > atr * 0.3 and rsi > 55:
            fired = True; conf = min(abs(gap) / atr, 1.0) * 0.5
            sl_price = price + atr * 1.5; tp_price = pdc

    elif strategy == "MEAN_REVERT":
        if is_long and price < bbl and rsi < 35:
            fired = True; conf = 0.4
            sl_price = price - atr * 1.5; tp_price = bbm
        elif is_short and price > bbu and rsi > 65:
            fired = True; conf = 0.4
            sl_price = price + atr * 1.5; tp_price = bbm

    elif strategy == "VWAP_REVERT":
        vwap_dist = (price - vwap) / vstd if vstd > 0 else 0
        if is_long and vwap_dist < -1.5 and rsi < 40:
            fired = True; conf = min(abs(vwap_dist) / 3.0, 0.6)
            sl_price = price - atr * 1.5; tp_price = vwap
        elif is_short and vwap_dist > 1.5 and rsi > 60:
            fired = True; conf = min(abs(vwap_dist) / 3.0, 0.6)
            sl_price = price + atr * 1.5; tp_price = vwap

    elif strategy == "EMA_BOUNCE":
        ema_dist = abs(price - ema50) / atr
        if is_long and price > ema50 and ema_dist < 0.5 and rsi > 45 and rsi < 65:
            fired = True; conf = 0.35
            sl_price = ema50 - atr * 0.5; tp_price = price + atr * 2.0
        elif is_short and price < ema50 and ema_dist < 0.5 and rsi > 35 and rsi < 55:
            fired = True; conf = 0.35
            sl_price = ema50 + atr * 0.5; tp_price = price - atr * 2.0

    elif strategy == "PREV_DAY_HL":
        if is_long and price > pdh and price - pdh < atr * 0.5:
            fired = True; conf = 0.4
            sl_price = pdh - atr * 0.5; tp_price = price + atr * 2.5
        elif is_short and price < pdl and pdl - price < atr * 0.5:
            fired = True; conf = 0.4
            sl_price = pdl + atr * 0.5; tp_price = price - atr * 2.5

    elif strategy == "ORB":
        orb_range = orb_high - orb_low
        if orb_range > 0 and sl_len > 1:
            if is_long and price > orb_high and price - orb_high < atr * 0.3:
                fired = True; conf = 0.35
                sl_price = orb_low; tp_price = price + orb_range * 1.5
            elif is_short and price < orb_low and orb_low - price < atr * 0.3:
                fired = True; conf = 0.35
                sl_price = orb_high; tp_price = price - orb_range * 1.5

    elif strategy == "ASIAN_BREAKOUT":
        asian_range = asian_high - asian_low
        if asian_range > 0:
            if is_long and price > asian_high and price - asian_high < atr * 0.5:
                fired = True; conf = 0.3
                sl_price = asian_low; tp_price = price + asian_range
            elif is_short and price < asian_low and asian_low - price < atr * 0.5:
                fired = True; conf = 0.3
                sl_price = asian_high; tp_price = price - asian_range

    elif strategy == "VOL_COMPRESS":
        bb_width = (bbu - bbl) / bbm if bbm > 0 else 0
        if bb_width < 0.02:  # Tight squeeze
            if is_long and sk > 50:
                fired = True; conf = 0.3
                sl_price = bbl; tp_price = price + atr * 2.0
            elif is_short and sk < 50:
                fired = True; conf = 0.3
                sl_price = bbu; tp_price = price - atr * 2.0

    return fired, entry, sl_price, tp_price, conf


# ═══ BACKTEST A SINGLE CONFIG ═══

@dataclass
class TradeResult:
    pnl_r: float
    bars_held: int
    exit_reason: str

def backtest_config(candles, strategy, direction, trade_type, regime_filter):
    """Walk-forward backtest for one strategy/direction/type/regime combo."""
    n = candles["count"]
    start = 60
    trades = []
    cooldown_until = 0

    for bar in range(start, n):
        if bar < cooldown_until:
            continue

        # Detect regime
        end = bar + 1
        s = max(0, end - 250)
        h = candles["highs"][s:end]
        l = candles["lows"][s:end]
        c = candles["closes"][s:end]

        regime = detect_regime(h, l, c)
        if regime != regime_filter:
            continue

        # Check signal
        fired, entry, sl, tp, conf = generate_signal(strategy, direction, candles, bar)
        if not fired or conf < 0.20:
            continue

        # Simulate trade
        r_unit = abs(entry - sl)
        if r_unit == 0:
            continue

        # Determine actual direction of this trade
        is_long = tp > entry

        # Walk forward to find exit
        max_hold = 50
        be_set = False
        current_sl = sl
        exit_price = entry
        exit_reason = "MAX_HOLD"
        bars_held = 0

        for future_bar in range(bar + 1, min(bar + max_hold + 1, n)):
            bars_held += 1
            hi = candles["highs"][future_bar]
            lo = candles["lows"][future_bar]
            cl = candles["closes"][future_bar]

            if is_long:
                cr = (cl - entry) / r_unit
            else:
                cr = (entry - cl) / r_unit

            # SL check
            if is_long and lo <= current_sl:
                exit_price = current_sl
                exit_reason = "BE_STOP" if be_set else "SL_HIT"
                break
            if not is_long and hi >= current_sl:
                exit_price = current_sl
                exit_reason = "BE_STOP" if be_set else "SL_HIT"
                break

            # TP check (SCALP)
            if trade_type == "SCALP":
                if is_long and hi >= tp:
                    exit_price = tp; exit_reason = "TP_HIT"; break
                if not is_long and lo <= tp:
                    exit_price = tp; exit_reason = "TP_HIT"; break

            # Breakeven at +0.5R
            if not be_set and cr >= 0.5:
                current_sl = entry
                be_set = True

            # Runner trailing
            if trade_type == "RUNNER" and be_set and r_unit > 0:
                if is_long:
                    nt = cl - 1.5 * r_unit
                    if nt > current_sl: current_sl = nt
                else:
                    nt = cl + 1.5 * r_unit
                    if nt < current_sl: current_sl = nt
        else:
            exit_price = candles["closes"][min(bar + max_hold, n-1)]

        # Calculate P&L in R
        if is_long:
            pnl_r = (exit_price - entry) / r_unit
        else:
            pnl_r = (entry - exit_price) / r_unit

        trades.append(TradeResult(pnl_r=pnl_r, bars_held=bars_held, exit_reason=exit_reason))
        cooldown_until = bar + max(2, bars_held)  # Cooldown

    return trades


# ═══ MAIN RESEARCH LOOP ═══

def main():
    print("\n" + "=" * 80)
    print("  FORGE v22 — NEW INSTRUMENT RESEARCH SCANNER")
    print("  Candidates: AUDUSD, EURJPY, CADJPY, AUDNZD, USDCAD")
    print("  8 strategies × 3 regimes × 2 directions × 2 types = 96 configs per pair")
    print("=" * 80)

    if not POLYGON_API_KEY:
        print("\n  POLYGON_API_KEY not set!")
        sys.exit(1)

    print(f"\n  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # ═══ FETCH DATA ═══
    print("\n" + "-" * 80)
    print("  Fetching 6 months of hourly data...")
    print("-" * 80)

    all_candles = {}
    for sym, ticker in CANDIDATES.items():
        print(f"  {sym} ({ticker})...", end=" ", flush=True)
        c = fetch_candles(sym, days_back=180)
        if c and c["count"] >= 50:
            all_candles[sym] = c
            print(f"OK — {c['count']} bars ({c['count']/24:.0f} days)")
        else:
            print("FAILED")
        time.sleep(0.3)

    if not all_candles:
        print("\n  No data. Check API key.")
        sys.exit(1)

    # ═══ RESEARCH EACH PAIR ═══
    print("\n" + "=" * 80)
    print("  RUNNING RESEARCH (this takes a few minutes)...")
    print("=" * 80)

    all_results = {}

    for sym, candles in all_candles.items():
        print(f"\n  {'='*60}")
        print(f"  {sym} — {candles['count']} bars")
        print(f"  {'='*60}")

        # Check regime distribution
        regime_counts = {"SHORT": 0, "LONG": 0, "NEUTRAL": 0}
        for bar in range(60, candles["count"], 10):  # Sample every 10 bars
            end = bar + 1
            s = max(0, end - 250)
            regime = detect_regime(candles["highs"][s:end], candles["lows"][s:end], candles["closes"][s:end])
            regime_counts[regime] += 1

        total_samples = sum(regime_counts.values())
        print(f"  Regime distribution:")
        for r, cnt in regime_counts.items():
            pct = cnt / total_samples * 100 if total_samples > 0 else 0
            print(f"    {r}: {pct:.1f}%")

        results = []

        for strategy in STRATEGIES:
            for direction in DIRECTIONS:
                for regime in REGIMES:
                    for trade_type in TRADE_TYPES:
                        trades = backtest_config(candles, strategy, direction, trade_type, regime)

                        if len(trades) < 5:  # Need minimum sample
                            continue

                        total_r = sum(t.pnl_r for t in trades)
                        winners = [t for t in trades if t.pnl_r > 0]
                        losers = [t for t in trades if t.pnl_r < 0]
                        wr = len(winners) / len(trades) * 100
                        pf = abs(sum(t.pnl_r for t in winners)) / abs(sum(t.pnl_r for t in losers)) if losers and sum(t.pnl_r for t in losers) != 0 else 999
                        avg_hold = np.mean([t.bars_held for t in trades])

                        results.append({
                            "strategy": strategy,
                            "direction": direction,
                            "regime": regime,
                            "trade_type": trade_type,
                            "trades": len(trades),
                            "total_r": total_r,
                            "win_rate": wr,
                            "profit_factor": pf,
                            "avg_hold": avg_hold,
                        })

        # Sort by total_r descending
        results.sort(key=lambda x: x["total_r"], reverse=True)
        all_results[sym] = results

        # Show top 10
        print(f"\n  TOP 10 CONFIGS:")
        print(f"  {'Strategy':18s} {'Dir':6s} {'Regime':8s} {'Type':7s} {'Trades':>7s} {'WR':>6s} {'Total R':>9s} {'PF':>6s}")
        print(f"  {'-'*72}")
        for r in results[:10]:
            print(f"  {r['strategy']:18s} {r['direction']:6s} {r['regime']:8s} {r['trade_type']:7s} "
                  f"{r['trades']:7d} {r['win_rate']:5.1f}% {r['total_r']:+8.2f}R {r['profit_factor']:5.2f}")

        # Show bottom 5 (worst)
        print(f"\n  BOTTOM 5 (avoid):")
        for r in results[-5:]:
            if r["total_r"] < 0:
                print(f"  {r['strategy']:18s} {r['direction']:6s} {r['regime']:8s} {r['trade_type']:7s} "
                      f"{r['trades']:7d} {r['win_rate']:5.1f}% {r['total_r']:+8.2f}R {r['profit_factor']:5.2f}")

    # ═══ FINAL RECOMMENDATIONS ═══
    print("\n" + "=" * 80)
    print("  RECOMMENDATIONS — BEST CONFIG PER PAIR")
    print("=" * 80)

    print(f"\n  {'Pair':10s} {'Strategy':18s} {'Dir':6s} {'Regime':8s} {'Type':7s} {'Trades':>7s} {'WR':>6s} {'R':>9s} {'PF':>6s}")
    print(f"  {'-'*80}")

    recommended = {}
    for sym, results in all_results.items():
        if results:
            best = results[0]
            # Only recommend if profitable
            if best["total_r"] > 0:
                recommended[sym] = best
                print(f"  {sym:10s} {best['strategy']:18s} {best['direction']:6s} {best['regime']:8s} "
                      f"{best['trade_type']:7s} {best['trades']:7d} {best['win_rate']:5.1f}% "
                      f"{best['total_r']:+8.2f}R {best['profit_factor']:5.2f}")
            else:
                print(f"  {sym:10s} ❌ NO PROFITABLE CONFIG FOUND")
        else:
            print(f"  {sym:10s} ❌ INSUFFICIENT DATA")

    # ═══ FORGE CONFIG OUTPUT ═══
    print("\n" + "=" * 80)
    print("  FORGE CONFIG — COPY/PASTE INTO main.py")
    print("=" * 80)

    # ATR fallbacks
    print("\n  # ATR_FB additions:")
    for sym in recommended:
        candles = all_candles[sym]
        h, l, c = candles["highs"], candles["lows"], candles["closes"]
        atr = compute_atr(h, l, c)
        print(f'    "{sym}":{atr:.4f},')

    # POLYGON_MAP additions
    print("\n  # POLYGON_MAP additions:")
    for sym in recommended:
        print(f'    "{sym}":"C:{sym}",')

    # ALIASES additions
    print("\n  # ALIASES additions:")
    for sym in recommended:
        print(f'    "{sym}":["{sym}.sim","{sym}"],')

    # Regime configs (NEUTRAL, SHORT, LONG)
    print("\n  # REGIME CONFIG additions (for each regime):")
    for sym, best in recommended.items():
        regime = best["regime"]
        strat = best["strategy"]
        direction = best["direction"]
        ttype = best["trade_type"]
        print(f"  # {sym} [{regime}]: {strat} {direction} {ttype} — "
              f"+{best['total_r']:.1f}R, WR {best['win_rate']:.0f}%, PF {best['profit_factor']:.2f}")

    # Show what to add to calc_lots
    print("\n  # calc_lots additions:")
    for sym in recommended:
        if "JPY" in sym:
            print(f'    # {sym}: JPY pair → sl_dist × 1,000')
        else:
            print(f'    # {sym}: Standard forex → sl_dist × 100,000')

    # Min distance for SL/TP validation
    print("\n  # Min SL/TP distance additions (for hotfix):")
    for sym in recommended:
        if "JPY" in sym:
            print(f'    # {sym}: 0.05 (JPY pair)')
        else:
            print(f'    # {sym}: 0.0005 (standard pair)')

    print("\n" + "=" * 80)
    print("  RESEARCH COMPLETE")
    print("  Next: Add winning configs to main.py, push to Railway")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
