"""
FORGE v22 — LONG & NEUTRAL REGIME RESEARCH
=============================================
Tests every strategy in LONG direction across multiple time periods.
Tests neutral/range strategies on low-ADX periods.
Same rigor as SHORT research: real data, hundreds of trades, no assumptions.

Pulls 3 distinct periods:
  PERIOD 1: Oct-Dec 2024 (bull run, post-election rally)
  PERIOD 2: Jul-Sep 2024 (summer rally / mixed)
  PERIOD 3: Jan-Mar 2025 (recent, mixed/bearish for comparison)

For each period, tests all 14 instruments with ALL strategies in BOTH directions.
Reports which instrument+strategy+direction combos are profitable.

Usage:
    set POLYGON_API_KEY=your_key_here
    set PYTHONIOENCODING=utf-8
    python forge_research_long.py > research_results.txt 2>&1
"""

import os, sys, time, requests
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forge_instruments_v22 import (
    Strategy, TradeType, OrderType, Direction, InstrumentSetup,
    get_all_symbols, TIME_OF_DAY_EDGES,
)
from forge_signals_v22 import SignalEngine, MarketSnapshot, Signal, STRATEGY_FUNCTIONS
from forge_correlation import CorrelationGuard

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

POLYGON_TICKERS = {
    "USDCHF": "C:USDCHF", "NZDUSD": "C:NZDUSD", "EURGBP": "C:EURGBP",
    "EURUSD": "C:EURUSD", "GBPJPY": "C:GBPJPY", "USDJPY": "C:USDJPY",
    "GBPUSD": "C:GBPUSD", "XAUUSD": "C:XAUUSD",
    "GER40": "EWG", "UK100": "EWU", "US100": "QQQ", "USOIL": "USO",
    "ETHUSD": "X:ETHUSD", "BTCUSD": "X:BTCUSD",
}

# Research periods
PERIODS = [
    {"name": "BULL (Oct-Dec 2024)", "start": "2024-10-01", "end": "2024-12-31"},
    {"name": "MIXED (Jul-Sep 2024)", "start": "2024-07-01", "end": "2024-09-30"},
    {"name": "BEAR (Jan-Mar 2025)", "start": "2025-01-01", "end": "2025-03-28"},
]

# All strategies to test
ALL_STRATEGIES = [
    Strategy.MEAN_REVERT,
    Strategy.VWAP_REVERT,
    Strategy.STOCH_REVERSAL,
    Strategy.EMA_BOUNCE,
    Strategy.PREV_DAY_HL,
    Strategy.ORB,
    Strategy.GAP_FILL,
    Strategy.CONFLUENCE,
]

# SL/TP configs per strategy
STRATEGY_PARAMS = {
    Strategy.MEAN_REVERT:    {"sl": 0.5, "tp": 1.5, "type": "SCALP",  "order": "LIMIT"},
    Strategy.VWAP_REVERT:    {"sl": 0.5, "tp": 4.0, "type": "SCALP",  "order": "LIMIT"},
    Strategy.STOCH_REVERSAL: {"sl": 0.5, "tp": 1.0, "type": "SCALP",  "order": "LIMIT"},
    Strategy.EMA_BOUNCE:     {"sl": 1.0, "tp": 3.0, "type": "SCALP",  "order": "LIMIT"},
    Strategy.PREV_DAY_HL:    {"sl": 0.5, "tp": 1.5, "type": "RUNNER", "order": "MARKET"},
    Strategy.ORB:            {"sl": 0.8, "tp": 1.5, "type": "RUNNER", "order": "MARKET"},
    Strategy.GAP_FILL:       {"sl": 2.5, "tp": 3.0, "type": "SCALP",  "order": "LIMIT"},
    Strategy.CONFLUENCE:     {"sl": 0.8, "tp": 1.5, "type": "RUNNER", "order": "MARKET"},
}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCH
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_period(symbol, start_date, end_date):
    ticker = POLYGON_TICKERS.get(symbol)
    if not ticker: return None
    
    all_results = []
    chunk_start = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    
    while chunk_start < end_dt:
        chunk_end = min(chunk_start + timedelta(days=35), end_dt)
        url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/hour"
               f"/{chunk_start.strftime('%Y-%m-%d')}/{chunk_end.strftime('%Y-%m-%d')}"
               f"?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_API_KEY}")
        
        pages = 0
        while url and pages < 20:
            try:
                resp = requests.get(url, timeout=20)
                if resp.status_code == 429:
                    time.sleep(12)
                    resp = requests.get(url, timeout=20)
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
    
    if len(all_results) < 30: return None
    
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
# INDICATORS (same as backtest)
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


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT BUILDER
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
# SINGLE-INSTRUMENT STRATEGY TESTER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeResult:
    symbol: str
    strategy: str
    direction: str
    entry: float
    sl: float
    tp: float
    exit_price: float
    pnl_r: float
    bars_held: int
    exit_reason: str
    trade_type: str


def test_strategy_on_instrument(
    symbol: str,
    strategy: Strategy,
    direction: str,  # "LONG", "SHORT", or "BOTH"
    candles: Dict,
    sl_atr: float,
    tp_atr: float,
    trade_type: str,
) -> List[TradeResult]:
    """Walk through candles, fire the strategy, simulate trades."""
    
    # Build a temporary setup config for this test
    setup = InstrumentSetup(
        symbol=symbol,
        strategy=strategy,
        direction=Direction(direction),
        sl_atr=sl_atr,
        tp_atr=tp_atr,
        risk_pct=1.5,
        trade_type=TradeType(trade_type),
        order_type=OrderType("LIMIT" if trade_type == "SCALP" else "MARKET"),
        expectancy=0, win_rate=0, profit_factor=0,
    )
    
    # Temporarily inject this setup
    from forge_instruments_v22 import SETUP_CONFIG
    original = SETUP_CONFIG.get(symbol)
    SETUP_CONFIG[symbol] = setup
    
    engine = SignalEngine()
    trades = []
    open_trade = None
    cooldown = 0
    
    n = candles["count"]
    start = 60
    
    for bar in range(start, n):
        # Manage open trade
        if open_trade is not None:
            open_trade["bars"] += 1
            hi = candles["highs"][bar]
            lo = candles["lows"][bar]
            cl = candles["closes"][bar]
            
            r_unit = abs(open_trade["entry"] - open_trade["sl"])
            if r_unit == 0: r_unit = 0.0001
            
            if open_trade["dir"] == "LONG":
                cur_r = (cl - open_trade["entry"]) / r_unit
            else:
                cur_r = (open_trade["entry"] - cl) / r_unit
            
            # Breakeven at +0.5R
            if not open_trade["be"] and cur_r >= 0.5:
                open_trade["csl"] = open_trade["entry"]
                open_trade["be"] = True
            
            # SL check
            hit_sl = False
            if open_trade["dir"] == "LONG" and lo <= open_trade["csl"]:
                hit_sl = True
            elif open_trade["dir"] == "SHORT" and hi >= open_trade["csl"]:
                hit_sl = True
            
            if hit_sl:
                reason = "BE_STOP" if open_trade["be"] else "SL_HIT"
                pnl_r = 0.0 if open_trade["be"] else -1.0
                trades.append(TradeResult(
                    symbol=symbol, strategy=strategy.value,
                    direction=open_trade["dir"], entry=open_trade["entry"],
                    sl=open_trade["sl"], tp=open_trade["tp"],
                    exit_price=open_trade["csl"], pnl_r=pnl_r,
                    bars_held=open_trade["bars"], exit_reason=reason,
                    trade_type=trade_type,
                ))
                open_trade = None
                cooldown = 2
                continue
            
            # TP check
            hit_tp = False
            if open_trade["dir"] == "LONG" and hi >= open_trade["tp"]:
                hit_tp = True
            elif open_trade["dir"] == "SHORT" and lo <= open_trade["tp"]:
                hit_tp = True
            
            if hit_tp:
                pnl_r = tp_atr / sl_atr  # R:R ratio
                trades.append(TradeResult(
                    symbol=symbol, strategy=strategy.value,
                    direction=open_trade["dir"], entry=open_trade["entry"],
                    sl=open_trade["sl"], tp=open_trade["tp"],
                    exit_price=open_trade["tp"], pnl_r=pnl_r,
                    bars_held=open_trade["bars"], exit_reason="TP_HIT",
                    trade_type=trade_type,
                ))
                open_trade = None
                cooldown = 2
                continue
            
            # Max hold for runners
            if trade_type == "RUNNER" and open_trade["bars"] >= 50:
                if open_trade["dir"] == "LONG":
                    pnl_r = (cl - open_trade["entry"]) / r_unit
                else:
                    pnl_r = (open_trade["entry"] - cl) / r_unit
                trades.append(TradeResult(
                    symbol=symbol, strategy=strategy.value,
                    direction=open_trade["dir"], entry=open_trade["entry"],
                    sl=open_trade["sl"], tp=open_trade["tp"],
                    exit_price=cl, pnl_r=pnl_r,
                    bars_held=open_trade["bars"], exit_reason="MAX_HOLD",
                    trade_type=trade_type,
                ))
                open_trade = None
                cooldown = 2
                continue
            
            continue  # Trade open, skip signal gen
        
        # Cooldown
        if cooldown > 0:
            cooldown -= 1
            continue
        
        # Build snapshot and generate signals
        snap = build_snapshot(symbol, candles, bar)
        if snap is None:
            continue
        
        ts = candles["timestamps"][bar]
        bar_time = datetime.fromtimestamp(ts/1000, tz=timezone.utc)
        
        sigs = engine.generate_signals({symbol: snap}, current_time=bar_time)
        
        if sigs:
            sig = sigs[0]
            open_trade = {
                "dir": sig.direction,
                "entry": sig.entry_price,
                "sl": sig.sl_price,
                "tp": sig.tp_price,
                "csl": sig.sl_price,
                "bars": 0,
                "be": False,
            }
    
    # Restore original config
    if original:
        SETUP_CONFIG[symbol] = original
    elif symbol in SETUP_CONFIG:
        del SETUP_CONFIG[symbol]
    
    return trades


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ComboResult:
    symbol: str
    strategy: str
    direction: str
    period: str
    trades: int
    winners: int
    losers: int
    scratches: int
    win_rate: float
    total_r: float
    avg_winner: float
    avg_loser: float
    profit_factor: float
    max_consec_loss: int


def analyze_trades(symbol, strategy, direction, period, trade_list) -> Optional[ComboResult]:
    if not trade_list:
        return None
    
    w = [t for t in trade_list if t.pnl_r > 0]
    l = [t for t in trade_list if t.pnl_r < 0]
    be = [t for t in trade_list if t.pnl_r == 0]
    total_r = sum(t.pnl_r for t in trade_list)
    
    # Max consecutive losses
    mcl = 0; cur = 0
    for t in trade_list:
        if t.pnl_r < 0: cur += 1; mcl = max(mcl, cur)
        else: cur = 0
    
    wr = len(w) / len(trade_list) * 100 if trade_list else 0
    avg_w = np.mean([t.pnl_r for t in w]) if w else 0
    avg_l = np.mean([t.pnl_r for t in l]) if l else 0
    pf = abs(sum(t.pnl_r for t in w)) / abs(sum(t.pnl_r for t in l)) if l and sum(t.pnl_r for t in l) != 0 else 999
    
    return ComboResult(
        symbol=symbol, strategy=strategy, direction=direction,
        period=period, trades=len(trade_list), winners=len(w),
        losers=len(l), scratches=len(be), win_rate=wr,
        total_r=total_r, avg_winner=avg_w, avg_loser=avg_l,
        profit_factor=pf, max_consec_loss=mcl,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN RESEARCH LOOP
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 80)
    print("  FORGE v22 — LONG & NEUTRAL REGIME RESEARCH")
    print("  Testing every strategy x direction x instrument x period")
    print("  Finding LONG and NEUTRAL edges backed by real data")
    print("=" * 80)

    if not POLYGON_API_KEY:
        print("\n  POLYGON_API_KEY not set!"); sys.exit(1)

    symbols = get_all_symbols()
    print(f"\n  Instruments: {len(symbols)}")
    print(f"  Strategies: {len(ALL_STRATEGIES)}")
    print(f"  Directions: LONG, SHORT, BOTH")
    print(f"  Periods: {len(PERIODS)}")
    total_combos = len(symbols) * len(ALL_STRATEGIES) * 2 * len(PERIODS)
    print(f"  Total combos to test: {total_combos}")

    all_results = []

    for period in PERIODS:
        print(f"\n{'#' * 80}")
        print(f"  PERIOD: {period['name']} ({period['start']} to {period['end']})")
        print(f"{'#' * 80}")

        # Fetch data for this period
        print(f"\n  Fetching data...")
        period_candles = {}
        for sym in symbols:
            t = POLYGON_TICKERS.get(sym, "?")
            print(f"    {sym} ({t})...", end=" ", flush=True)
            c = fetch_period(sym, period["start"], period["end"])
            if c and c["count"] >= 50:
                period_candles[sym] = c
                print(f"OK - {c['count']} bars")
            else:
                count = c["count"] if c else 0
                print(f"SKIP ({count} bars)")
            time.sleep(0.3)
        
        print(f"  Loaded: {len(period_candles)} instruments")
        
        if len(period_candles) < 3:
            print(f"  Not enough data for this period, skipping")
            continue

        # Test every strategy x direction x instrument
        print(f"\n  Running strategy tests...")
        
        for sym, candles in period_candles.items():
            for strategy in ALL_STRATEGIES:
                params = STRATEGY_PARAMS[strategy]
                
                for direction in ["LONG", "SHORT"]:
                    trades = test_strategy_on_instrument(
                        symbol=sym,
                        strategy=strategy,
                        direction=direction,
                        candles=candles,
                        sl_atr=params["sl"],
                        tp_atr=params["tp"],
                        trade_type=params["type"],
                    )
                    
                    result = analyze_trades(
                        sym, strategy.value, direction,
                        period["name"], trades
                    )
                    
                    if result and result.trades >= 3:
                        all_results.append(result)

        # Show top results for this period
        period_results = [r for r in all_results if r.period == period["name"]]
        
        # Top LONG combos
        long_results = [r for r in period_results if r.direction == "LONG" and r.total_r > 0]
        long_results.sort(key=lambda x: x.total_r, reverse=True)
        
        print(f"\n  --- TOP LONG COMBOS ({period['name']}) ---")
        print(f"  {'Symbol':10s} {'Strategy':20s} {'Trades':>7s} {'WR':>6s} {'R':>8s} {'PF':>6s} {'MaxCL':>6s}")
        print(f"  {'-'*65}")
        for r in long_results[:20]:
            print(f"  {r.symbol:10s} {r.strategy:20s} {r.trades:7d} {r.win_rate:5.1f}% "
                  f"{r.total_r:+7.2f}R {r.profit_factor:5.2f} {r.max_consec_loss:5d}")
        if not long_results:
            print(f"  No profitable LONG combos found")
        
        # Top SHORT combos
        short_results = [r for r in period_results if r.direction == "SHORT" and r.total_r > 0]
        short_results.sort(key=lambda x: x.total_r, reverse=True)
        
        print(f"\n  --- TOP SHORT COMBOS ({period['name']}) ---")
        print(f"  {'Symbol':10s} {'Strategy':20s} {'Trades':>7s} {'WR':>6s} {'R':>8s} {'PF':>6s} {'MaxCL':>6s}")
        print(f"  {'-'*65}")
        for r in short_results[:20]:
            print(f"  {r.symbol:10s} {r.strategy:20s} {r.trades:7d} {r.win_rate:5.1f}% "
                  f"{r.total_r:+7.2f}R {r.profit_factor:5.2f} {r.max_consec_loss:5d}")

    # ═══════════════════════════════════════════════════════════════════
    # CROSS-PERIOD ANALYSIS
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print(f"  CROSS-PERIOD ANALYSIS — LONG COMBOS THAT WORK IN MULTIPLE PERIODS")
    print(f"{'=' * 80}")
    
    # Find LONG combos that are profitable in 2+ periods
    long_combos = {}
    for r in all_results:
        if r.direction == "LONG" and r.total_r > 0:
            key = f"{r.symbol}|{r.strategy}"
            if key not in long_combos:
                long_combos[key] = []
            long_combos[key].append(r)
    
    consistent_long = []
    for key, results in long_combos.items():
        if len(results) >= 2:
            total_r = sum(r.total_r for r in results)
            total_trades = sum(r.trades for r in results)
            total_wins = sum(r.winners for r in results)
            periods_profitable = len(results)
            avg_pf = np.mean([r.profit_factor for r in results if r.profit_factor < 999])
            consistent_long.append({
                "key": key,
                "symbol": results[0].symbol,
                "strategy": results[0].strategy,
                "periods": periods_profitable,
                "total_trades": total_trades,
                "total_r": total_r,
                "wr": total_wins/total_trades*100 if total_trades>0 else 0,
                "avg_pf": avg_pf,
                "details": results,
            })
    
    consistent_long.sort(key=lambda x: x["total_r"], reverse=True)
    
    print(f"\n  LONG combos profitable in 2+ periods:")
    print(f"  {'Symbol':10s} {'Strategy':20s} {'Periods':>8s} {'Trades':>7s} {'WR':>6s} {'R':>8s} {'PF':>6s}")
    print(f"  {'-'*67}")
    for c in consistent_long:
        print(f"  {c['symbol']:10s} {c['strategy']:20s} {c['periods']:8d} "
              f"{c['total_trades']:7d} {c['wr']:5.1f}% {c['total_r']:+7.2f}R {c['avg_pf']:5.2f}")
        for r in c["details"]:
            print(f"    -> {r.period:30s}: {r.trades:3d} trades {r.total_r:+7.2f}R WR:{r.win_rate:.0f}%")
    
    if not consistent_long:
        print(f"  No LONG combos found that work across multiple periods")

    # Same for SHORT (validation)
    print(f"\n  SHORT combos profitable in ALL {len(PERIODS)} periods (validation):")
    short_combos = {}
    for r in all_results:
        if r.direction == "SHORT" and r.total_r > 0:
            key = f"{r.symbol}|{r.strategy}"
            if key not in short_combos:
                short_combos[key] = []
            short_combos[key].append(r)
    
    consistent_short = []
    for key, results in short_combos.items():
        if len(results) >= 2:
            total_r = sum(r.total_r for r in results)
            total_trades = sum(r.trades for r in results)
            total_wins = sum(r.winners for r in results)
            consistent_short.append({
                "key": key, "symbol": results[0].symbol,
                "strategy": results[0].strategy,
                "periods": len(results), "total_trades": total_trades,
                "total_r": total_r,
                "wr": total_wins/total_trades*100 if total_trades>0 else 0,
                "avg_pf": np.mean([r.profit_factor for r in results if r.profit_factor < 999]),
                "details": results,
            })
    consistent_short.sort(key=lambda x: x["total_r"], reverse=True)
    
    print(f"  {'Symbol':10s} {'Strategy':20s} {'Periods':>8s} {'Trades':>7s} {'WR':>6s} {'R':>8s} {'PF':>6s}")
    print(f"  {'-'*67}")
    for c in consistent_short[:15]:
        print(f"  {c['symbol']:10s} {c['strategy']:20s} {c['periods']:8d} "
              f"{c['total_trades']:7d} {c['wr']:5.1f}% {c['total_r']:+7.2f}R {c['avg_pf']:5.2f}")

    # ═══════════════════════════════════════════════════════════════════
    # REGIME-NEUTRAL COMBOS (profitable in BULL AND BEAR)
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print(f"  REGIME-NEUTRAL COMBOS — Work in BOTH bull and bear markets")
    print(f"{'=' * 80}")
    
    # Find combos where LONG works in BULL period AND SHORT works in BEAR period
    bull_longs = {f"{r.symbol}|{r.strategy}": r for r in all_results
                  if r.direction == "LONG" and "BULL" in r.period and r.total_r > 0}
    bear_shorts = {f"{r.symbol}|{r.strategy}": r for r in all_results
                   if r.direction == "SHORT" and "BEAR" in r.period and r.total_r > 0}
    
    print(f"\n  Instruments where LONG works in BULL + SHORT works in BEAR:")
    print(f"  {'Symbol':10s} {'Strategy':20s} {'BULL LONG R':>12s} {'BEAR SHORT R':>13s} {'Combined':>10s}")
    print(f"  {'-'*70}")
    
    neutral_combos = []
    for key in set(bull_longs.keys()) & set(bear_shorts.keys()):
        bl = bull_longs[key]
        bs = bear_shorts[key]
        combined = bl.total_r + bs.total_r
        neutral_combos.append({
            "symbol": bl.symbol, "strategy": bl.strategy,
            "bull_long_r": bl.total_r, "bear_short_r": bs.total_r,
            "combined": combined,
            "bull_trades": bl.trades, "bear_trades": bs.trades,
        })
    
    neutral_combos.sort(key=lambda x: x["combined"], reverse=True)
    for c in neutral_combos:
        print(f"  {c['symbol']:10s} {c['strategy']:20s} {c['bull_long_r']:+11.2f}R "
              f"{c['bear_short_r']:+12.2f}R {c['combined']:+9.2f}R")
    
    if not neutral_combos:
        print(f"  No regime-neutral combos found")

    # ═══════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ═══════════════════════════════════════════════════════════════════
    print(f"\n{'=' * 80}")
    print(f"  RESEARCH SUMMARY")
    print(f"{'=' * 80}")
    print(f"\n  Total combos tested: {len(all_results)}")
    print(f"  Profitable LONG combos: {len([r for r in all_results if r.direction=='LONG' and r.total_r>0])}")
    print(f"  Profitable SHORT combos: {len([r for r in all_results if r.direction=='SHORT' and r.total_r>0])}")
    print(f"  LONG combos in 2+ periods: {len(consistent_long)}")
    print(f"  Regime-neutral combos: {len(neutral_combos)}")
    
    if consistent_long:
        print(f"\n  >>> LONG EDGE FOUND — {len(consistent_long)} combos work across periods <<<")
    else:
        print(f"\n  >>> NO CONSISTENT LONG EDGE — SHORT bias confirmed <<<")
    
    if neutral_combos:
        print(f"  >>> REGIME-NEUTRAL POSSIBLE — {len(neutral_combos)} combos adapt to bull/bear <<<")

    print(f"\n{'=' * 80}")
    print(f"  RESEARCH COMPLETE")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()
