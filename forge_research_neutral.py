"""
FORGE v22 — NEUTRAL / CHOPPY REGIME RESEARCH
==============================================
Finds strategies that work when markets are RANGING — low ADX, tight BBs,
no clear trend. These strategies will activate when FORGE detects neutral
conditions on a per-instrument basis.

Approach:
  - Pull 9 months of data (same 3 periods as LONG research)
  - For each bar, compute ADX. If ADX < 20 = NEUTRAL regime
  - Only test trades that fired DURING neutral bars
  - Find which strategy+direction combos win in neutral conditions

Usage:
    set POLYGON_API_KEY=your_key_here
    set PYTHONIOENCODING=utf-8
    python forge_research_neutral.py > neutral_results.txt 2>&1
"""

import os, sys, time, requests, re
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from dataclasses import dataclass
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forge_instruments_v22 import (
    Strategy, TradeType, OrderType, Direction, InstrumentSetup,
    get_all_symbols,
)
from forge_signals_v22 import SignalEngine, MarketSnapshot, Signal
from forge_correlation import CorrelationGuard

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

POLYGON_TICKERS = {
    "USDCHF": "C:USDCHF", "NZDUSD": "C:NZDUSD", "EURGBP": "C:EURGBP",
    "EURUSD": "C:EURUSD", "GBPJPY": "C:GBPJPY", "USDJPY": "C:USDJPY",
    "GBPUSD": "C:GBPUSD", "XAUUSD": "C:XAUUSD",
    "GER40": "EWG", "UK100": "EWU", "US100": "QQQ", "USOIL": "USO",
    "ETHUSD": "X:ETHUSD", "BTCUSD": "X:BTCUSD",
}

ALL_STRATEGIES = [
    Strategy.MEAN_REVERT, Strategy.VWAP_REVERT, Strategy.STOCH_REVERSAL,
    Strategy.EMA_BOUNCE, Strategy.PREV_DAY_HL, Strategy.ORB,
    Strategy.GAP_FILL, Strategy.CONFLUENCE,
]

STRATEGY_PARAMS = {
    Strategy.MEAN_REVERT:    {"sl": 0.5, "tp": 1.5, "type": "SCALP"},
    Strategy.VWAP_REVERT:    {"sl": 0.5, "tp": 4.0, "type": "SCALP"},
    Strategy.STOCH_REVERSAL: {"sl": 0.5, "tp": 1.0, "type": "SCALP"},
    Strategy.EMA_BOUNCE:     {"sl": 1.0, "tp": 3.0, "type": "SCALP"},
    Strategy.PREV_DAY_HL:    {"sl": 0.5, "tp": 1.5, "type": "RUNNER"},
    Strategy.ORB:            {"sl": 0.8, "tp": 1.5, "type": "RUNNER"},
    Strategy.GAP_FILL:       {"sl": 2.5, "tp": 3.0, "type": "SCALP"},
    Strategy.CONFLUENCE:     {"sl": 0.8, "tp": 1.5, "type": "RUNNER"},
}


def fetch_polygon_candles(symbol, days_back=270):
    ticker = POLYGON_TICKERS.get(symbol)
    if not ticker: return None
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
                if resp.status_code == 429: time.sleep(12); resp = requests.get(url, timeout=20)
                if resp.status_code != 200: break
                data = resp.json()
                all_results.extend(data.get("results", []))
                pages += 1
                nxt = data.get("next_url")
                if nxt:
                    url = nxt + (f"&apiKey={POLYGON_API_KEY}" if "apiKey" not in nxt else "")
                    time.sleep(0.2)
                else: break
            except: break
        chunk_start = chunk_end + timedelta(days=1)
        time.sleep(0.3)
    if len(all_results) < 50: return None
    seen = set(); deduped = []
    for r in all_results:
        if r["t"] not in seen: seen.add(r["t"]); deduped.append(r)
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


# ═══════════════════════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_atr(h,l,c,p=14):
    if len(c)<2: return abs(c[-1])*0.01
    tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1])))
    if len(tr)<p: return np.mean(tr)
    a=np.mean(tr[:p])
    for i in range(p,len(tr)): a=(a*(p-1)+tr[i])/p
    return a

def compute_rsi(c,p=14):
    if len(c)<p+1: return 50.0
    d=np.diff(c); g=np.where(d>0,d,0); lo=np.where(d<0,-d,0)
    ag=np.mean(g[:p]); al=np.mean(lo[:p])
    for i in range(p,len(g)): ag=(ag*(p-1)+g[i])/p; al=(al*(p-1)+lo[i])/p
    if al==0: return 100.0
    return 100.0-100.0/(1.0+ag/al)

def compute_ema(d,p):
    if len(d)<p: return np.mean(d) if len(d)>0 else 0.0
    m=2.0/(p+1); e=np.mean(d[:p])
    for i in range(p,len(d)): e=(d[i]-e)*m+e
    return e

def compute_bollinger(c,p=20,m=2.0):
    if len(c)<p:
        mid=np.mean(c); s=np.std(c) if len(c)>1 else abs(mid)*0.01
        return mid+m*s,mid-m*s,mid
    sma=np.mean(c[-p:]); s=np.std(c[-p:])
    if s==0: s=abs(sma)*0.001
    return sma+m*s,sma-m*s,sma

def compute_stochastic(h,l,c,kp=14,dp=3):
    if len(c)<kp+dp: return 50.,50.,50.,50.
    kvs=[]
    for i in range(kp-1,len(c)):
        hi,lo=np.max(h[i-kp+1:i+1]),np.min(l[i-kp+1:i+1])
        kvs.append(100.*(c[i]-lo)/(hi-lo) if hi!=lo else 50.)
    kvs=np.array(kvs)
    if len(kvs)<dp: return kvs[-1],kvs[-1],kvs[-1],kvs[-1]
    return kvs[-1],np.mean(kvs[-dp:]),kvs[-2] if len(kvs)>1 else kvs[-1],np.mean(kvs[-dp-1:-1]) if len(kvs)>dp else np.mean(kvs[-dp:])

def compute_vwap(h,l,c,v):
    tp=(h+l+c)/3.; cv=np.cumsum(v); ctv=np.cumsum(tp*v)
    if cv[-1]==0: return c[-1],abs(c[-1])*0.001
    vw=ctv[-1]/cv[-1]; vs=np.std(tp-vw) if len(tp)>1 else abs(c[-1])*0.001
    return vw,max(vs,abs(c[-1])*0.0001)

def compute_adx(h,l,c,p=14):
    if len(c)<p*2: return 20.,20.,25.,25.
    pdm,mdm,tr=np.zeros(len(h)),np.zeros(len(h)),np.zeros(len(h))
    for i in range(1,len(h)):
        u,dn=h[i]-h[i-1],l[i-1]-l[i]
        pdm[i]=u if u>dn and u>0 else 0; mdm[i]=dn if dn>u and dn>0 else 0
        tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
    atrs=np.mean(tr[1:p+1]); pdms=np.mean(pdm[1:p+1]); mdms=np.mean(mdm[1:p+1])
    dxv=[]; pdi=mdi=0
    for i in range(p+1,len(h)):
        atrs=(atrs*(p-1)+tr[i])/p; pdms=(pdms*(p-1)+pdm[i])/p; mdms=(mdms*(p-1)+mdm[i])/p
        if atrs>0: pdi=100.*pdms/atrs; mdi=100.*mdms/atrs
        ds=pdi+mdi; dxv.append(100.*abs(pdi-mdi)/ds if ds>0 else 0)
    if len(dxv)<p: a=np.mean(dxv) if dxv else 20.; return a,a,pdi,mdi
    adx=np.mean(dxv[:p])
    for i in range(p,len(dxv)): adx=(adx*(p-1)+dxv[i])/p
    ap=adx
    if len(dxv)>5:
        ap=np.mean(dxv[:p])
        for i in range(p,len(dxv)-5): ap=(ap*(p-1)+dxv[i])/p
    return adx,ap,pdi,mdi

def compute_keltner(c,h,l):
    e=compute_ema(c,20); a=compute_atr(h,l,c)
    return e+1.5*a,e-1.5*a

def compute_bb_width(c,p=20):
    if len(c)<p: return 999.
    sma=np.mean(c[-p:]); s=np.std(c[-p:])
    if sma==0: return 999.
    return (s*4)/sma*100  # BB width as % of price


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_regime(h, l, c):
    """Returns 'BEAR', 'BULL', or 'NEUTRAL' based on ADX + EMA"""
    adx, _, pdi, mdi = compute_adx(h, l, c)
    e50 = compute_ema(c, min(50, len(c)))
    e200 = compute_ema(c, min(200, len(c)))
    bb_w = compute_bb_width(c)
    
    # NEUTRAL: low ADX or tight BB squeeze
    if adx < 20 or bb_w < 1.5:
        return "NEUTRAL", adx, bb_w
    
    # BEAR: trending down
    if adx > 25 and e50 < e200:
        return "BEAR", adx, bb_w
    
    # BULL: trending up
    if adx > 25 and e50 > e200:
        return "BULL", adx, bb_w
    
    return "NEUTRAL", adx, bb_w


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT
# ═══════════════════════════════════════════════════════════════════════════════

def build_snapshot(symbol, candles, bar_idx):
    end=bar_idx+1; start=max(0,end-250)
    o=candles["opens"][start:end]; h=candles["highs"][start:end]
    l=candles["lows"][start:end]; c=candles["closes"][start:end]
    v=candles["volumes"][start:end]; n=len(c)
    if n<30: return None
    atr=compute_atr(h,l,c)
    if atr==0: atr=abs(c[-1])*0.001
    rsi=compute_rsi(c); sk,sd,skp,sdp=compute_stochastic(h,l,c)
    e50=compute_ema(c,min(50,n)); e200=compute_ema(c,min(200,n))
    bbu,bbl,bbm=compute_bollinger(c); vwap,vstd=compute_vwap(h,l,c,v)
    adx,adxp,pdi,mdi=compute_adx(h,l,c); ku,kl=compute_keltner(c,h,l)
    sl=min(8,n); pi=min(sl+8,n)
    pdh=np.max(h[-pi:-sl]) if pi>sl else np.max(h[:sl])
    pdl=np.min(l[-pi:-sl]) if pi>sl else np.min(l[:sl])
    pdc=c[-sl-1] if n>sl else c[0]
    price=c[-1]; spread=atr*0.05
    hour=12
    ts=candles["timestamps"][bar_idx] if bar_idx<len(candles["timestamps"]) else None
    if ts: hour=datetime.fromtimestamp(ts/1000,tz=timezone.utc).hour
    return MarketSnapshot(
        symbol=symbol,opens=o,highs=h,lows=l,closes=c,volumes=v,
        bid=price-spread/2,ask=price+spread/2,
        atr=atr,rsi=rsi,stoch_k=sk,stoch_d=sd,stoch_k_prev=skp,stoch_d_prev=sdp,
        ema_50=e50,ema_200=e200,bb_upper=bbu,bb_lower=bbl,bb_middle=bbm,
        vwap=vwap,vwap_std=vstd,adx=adx,adx_prev=adxp,plus_di=pdi,minus_di=mdi,
        prev_day_high=pdh,prev_day_low=pdl,prev_day_close=pdc,
        session_open=o[-sl],session_high=np.max(h[-sl:]),session_low=np.min(l[-sl:]),
        orb_high=h[-sl],orb_low=l[-sl],orb_complete=True,
        asian_high=np.max(h[:min(7,n)]),asian_low=np.min(l[:min(7,n)]),asian_complete=True,
        keltner_upper=ku,keltner_lower=kl,bars_since_open=sl,current_hour_utc=hour,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STRATEGY TESTER (REGIME-FILTERED)
# ═══════════════════════════════════════════════════════════════════════════════

def test_strategy_regime_filtered(symbol, strategy, direction, candles, sl_atr, tp_atr, trade_type, regime_filter="NEUTRAL"):
    """Only take trades when the bar's regime matches regime_filter."""
    
    setup = InstrumentSetup(
        symbol=symbol, strategy=strategy,
        direction=Direction(direction), sl_atr=sl_atr, tp_atr=tp_atr,
        risk_pct=1.5, trade_type=TradeType(trade_type),
        order_type=OrderType("LIMIT" if trade_type == "SCALP" else "MARKET"),
        expectancy=0, win_rate=0, profit_factor=0,
    )
    
    from forge_instruments_v22 import SETUP_CONFIG
    original = SETUP_CONFIG.get(symbol)
    SETUP_CONFIG[symbol] = setup
    
    engine = SignalEngine()
    trades = []
    open_trade = None
    cooldown = 0
    n = candles["count"]
    
    regime_bars = 0
    total_bars = 0
    
    for bar in range(60, n):
        total_bars += 1
        
        # Detect regime at this bar
        end = bar + 1; start = max(0, end - 250)
        h = candles["highs"][start:end]
        l = candles["lows"][start:end]
        c = candles["closes"][start:end]
        
        regime, adx_val, bb_w = detect_regime(h, l, c)
        if regime == regime_filter:
            regime_bars += 1
        
        # Manage open trade (always manage, regardless of regime)
        if open_trade is not None:
            open_trade["bars"] += 1
            hi = candles["highs"][bar]
            lo = candles["lows"][bar]
            cl = candles["closes"][bar]
            r_unit = abs(open_trade["entry"] - open_trade["sl"])
            if r_unit == 0: r_unit = 0.0001
            
            cur_r = (cl - open_trade["entry"]) / r_unit if open_trade["dir"] == "LONG" else (open_trade["entry"] - cl) / r_unit
            
            if not open_trade["be"] and cur_r >= 0.5:
                open_trade["csl"] = open_trade["entry"]; open_trade["be"] = True
            
            hit_sl = (open_trade["dir"] == "LONG" and lo <= open_trade["csl"]) or \
                     (open_trade["dir"] == "SHORT" and hi >= open_trade["csl"])
            if hit_sl:
                pnl = 0.0 if open_trade["be"] else -1.0
                trades.append({"pnl_r": pnl, "bars": open_trade["bars"],
                              "reason": "BE" if open_trade["be"] else "SL"})
                open_trade = None; cooldown = 2; continue
            
            hit_tp = (open_trade["dir"] == "LONG" and hi >= open_trade["tp"]) or \
                     (open_trade["dir"] == "SHORT" and lo <= open_trade["tp"])
            if hit_tp:
                pnl = tp_atr / sl_atr
                trades.append({"pnl_r": pnl, "bars": open_trade["bars"], "reason": "TP"})
                open_trade = None; cooldown = 2; continue
            
            if open_trade["bars"] >= 30:
                trades.append({"pnl_r": cur_r, "bars": open_trade["bars"], "reason": "MAX"})
                open_trade = None; cooldown = 2; continue
            continue
        
        if cooldown > 0: cooldown -= 1; continue
        
        # ONLY enter trades during the target regime
        if regime != regime_filter:
            continue
        
        snap = build_snapshot(symbol, candles, bar)
        if snap is None: continue
        
        ts = candles["timestamps"][bar]
        bar_time = datetime.fromtimestamp(ts/1000, tz=timezone.utc)
        
        sigs = engine.generate_signals({symbol: snap}, current_time=bar_time)
        if sigs:
            sig = sigs[0]
            open_trade = {
                "dir": sig.direction, "entry": sig.entry_price,
                "sl": sig.sl_price, "tp": sig.tp_price,
                "csl": sig.sl_price, "bars": 0, "be": False,
            }
    
    if original: SETUP_CONFIG[symbol] = original
    elif symbol in SETUP_CONFIG: del SETUP_CONFIG[symbol]
    
    return trades, regime_bars, total_bars


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 80)
    print("  FORGE v22 — NEUTRAL REGIME RESEARCH")
    print("  Finding strategies that work in CHOPPY / RANGING markets")
    print("  ADX < 20 or BB squeeze = NEUTRAL regime")
    print("=" * 80)

    if not POLYGON_API_KEY: print("\n  POLYGON_API_KEY not set!"); sys.exit(1)

    symbols = get_all_symbols()
    print(f"\n  Instruments: {len(symbols)}")
    print(f"  Strategies: {len(ALL_STRATEGIES)}")
    print(f"  Testing on: 9 months of data, NEUTRAL bars only")

    # Fetch all data (9 months)
    print(f"\n  Fetching 9 months of hourly data...")
    all_candles = {}
    for sym in symbols:
        t = POLYGON_TICKERS.get(sym, "?")
        print(f"    {sym} ({t})...", end=" ", flush=True)
        c = fetch_polygon_candles(sym, days_back=270)
        if c and c["count"] >= 100:
            all_candles[sym] = c
            print(f"OK - {c['count']} bars")
        else:
            print(f"SKIP")
        time.sleep(0.3)
    
    print(f"  Loaded: {len(all_candles)} instruments")

    # First, show regime distribution per instrument
    print(f"\n{'=' * 80}")
    print(f"  REGIME DISTRIBUTION (how often is each instrument in NEUTRAL?)")
    print(f"{'=' * 80}")
    print(f"  {'Symbol':10s} {'NEUTRAL%':>9s} {'BULL%':>7s} {'BEAR%':>7s} {'Bars':>6s}")
    print(f"  {'-'*45}")
    
    for sym, cd in sorted(all_candles.items()):
        regimes = {"NEUTRAL": 0, "BULL": 0, "BEAR": 0}
        for bar in range(60, cd["count"]):
            end = bar + 1; start = max(0, end - 250)
            h = cd["highs"][start:end]; l = cd["lows"][start:end]; c = cd["closes"][start:end]
            r, _, _ = detect_regime(h, l, c)
            regimes[r] += 1
        total = sum(regimes.values())
        if total > 0:
            print(f"  {sym:10s} {regimes['NEUTRAL']/total*100:8.1f}% "
                  f"{regimes['BULL']/total*100:6.1f}% "
                  f"{regimes['BEAR']/total*100:6.1f}% {total:5d}")

    # Test every combo in NEUTRAL regime
    print(f"\n{'=' * 80}")
    print(f"  NEUTRAL REGIME STRATEGY RESULTS")
    print(f"{'=' * 80}")
    
    all_results = []
    
    for sym, candles in sorted(all_candles.items()):
        for strategy in ALL_STRATEGIES:
            params = STRATEGY_PARAMS[strategy]
            for direction in ["LONG", "SHORT"]:
                trades, regime_bars, total_bars = test_strategy_regime_filtered(
                    symbol=sym, strategy=strategy, direction=direction,
                    candles=candles, sl_atr=params["sl"], tp_atr=params["tp"],
                    trade_type=params["type"], regime_filter="NEUTRAL"
                )
                
                if len(trades) >= 3:
                    w = [t for t in trades if t["pnl_r"] > 0]
                    l = [t for t in trades if t["pnl_r"] < 0]
                    total_r = sum(t["pnl_r"] for t in trades)
                    wr = len(w) / len(trades) * 100
                    pf = abs(sum(t["pnl_r"] for t in w)) / abs(sum(t["pnl_r"] for t in l)) if l and sum(t["pnl_r"] for t in l) != 0 else 999
                    
                    all_results.append({
                        "symbol": sym, "strategy": strategy.value,
                        "direction": direction, "trades": len(trades),
                        "winners": len(w), "losers": len(l),
                        "wr": wr, "total_r": total_r, "pf": pf,
                        "neutral_pct": regime_bars / total_bars * 100 if total_bars > 0 else 0,
                    })

    # Show results sorted by R
    profitable = [r for r in all_results if r["total_r"] > 0]
    profitable.sort(key=lambda x: x["total_r"], reverse=True)
    
    print(f"\n  --- PROFITABLE NEUTRAL COMBOS (sorted by R) ---")
    print(f"  {'Symbol':10s} {'Strategy':20s} {'Dir':5s} {'Trades':>7s} {'WR':>6s} {'R':>8s} {'PF':>6s}")
    print(f"  {'-'*65}")
    for r in profitable:
        print(f"  {r['symbol']:10s} {r['strategy']:20s} {r['direction']:5s} "
              f"{r['trades']:7d} {r['wr']:5.1f}% {r['total_r']:+7.2f}R {r['pf']:5.2f}")

    # Find best NEUTRAL strategy per instrument
    print(f"\n{'=' * 80}")
    print(f"  BEST NEUTRAL STRATEGY PER INSTRUMENT")
    print(f"{'=' * 80}")
    
    best_per_sym = {}
    for r in profitable:
        key = r["symbol"]
        if key not in best_per_sym or r["total_r"] > best_per_sym[key]["total_r"]:
            best_per_sym[key] = r
    
    print(f"\n  {'Symbol':10s} {'Strategy':20s} {'Dir':5s} {'Trades':>7s} {'WR':>6s} {'R':>8s} {'PF':>6s}")
    print(f"  {'-'*65}")
    for sym in sorted(best_per_sym.keys()):
        r = best_per_sym[sym]
        print(f"  {r['symbol']:10s} {r['strategy']:20s} {r['direction']:5s} "
              f"{r['trades']:7d} {r['wr']:5.1f}% {r['total_r']:+7.2f}R {r['pf']:5.2f}")

    # Show BOTH-direction combos (same strategy works LONG and SHORT in neutral)
    print(f"\n{'=' * 80}")
    print(f"  BOTH-DIRECTION NEUTRAL COMBOS (same strategy, both sides profitable)")
    print(f"{'=' * 80}")
    
    by_combo = {}
    for r in profitable:
        key = f"{r['symbol']}|{r['strategy']}"
        if key not in by_combo: by_combo[key] = {}
        by_combo[key][r["direction"]] = r
    
    print(f"\n  {'Symbol':10s} {'Strategy':20s} {'LONG R':>8s} {'SHORT R':>9s} {'Combined':>10s}")
    print(f"  {'-'*60}")
    both_combos = []
    for key, dirs in sorted(by_combo.items()):
        if "LONG" in dirs and "SHORT" in dirs:
            combined = dirs["LONG"]["total_r"] + dirs["SHORT"]["total_r"]
            both_combos.append({
                "symbol": dirs["LONG"]["symbol"],
                "strategy": dirs["LONG"]["strategy"],
                "long_r": dirs["LONG"]["total_r"],
                "short_r": dirs["SHORT"]["total_r"],
                "combined": combined,
            })
    
    both_combos.sort(key=lambda x: x["combined"], reverse=True)
    for c in both_combos:
        print(f"  {c['symbol']:10s} {c['strategy']:20s} "
              f"{c['long_r']:+7.2f}R {c['short_r']:+8.2f}R {c['combined']:+9.2f}R")

    # Summary
    print(f"\n{'=' * 80}")
    print(f"  NEUTRAL RESEARCH SUMMARY")
    print(f"{'=' * 80}")
    print(f"\n  Total combos tested: {len(all_results)}")
    print(f"  Profitable NEUTRAL combos: {len(profitable)}")
    print(f"  Instruments with NEUTRAL edge: {len(best_per_sym)}")
    print(f"  Both-direction NEUTRAL combos: {len(both_combos)}")
    
    if best_per_sym:
        print(f"\n  >>> NEUTRAL EDGES FOUND — {len(best_per_sym)} instruments have profitable neutral strategies <<<")
    
    print(f"\n{'=' * 80}")
    print(f"  RESEARCH COMPLETE")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()
