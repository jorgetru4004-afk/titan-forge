"""
FORGE v22 — LONG SYSTEM BACKTEST + COMBINED TEST
==================================================
Same 6-month gauntlet we ran on SHORT:
  TEST 1: LONG config standalone (6 months)
  TEST 2: LONG spread+slippage stress
  TEST 3: COMBINED SHORT+LONG (both running simultaneously)
  TEST 4: Monte Carlo on combined

Usage:
    set POLYGON_API_KEY=your_key_here
    set PYTHONIOENCODING=utf-8
    python test_long_system.py > long_results.txt 2>&1
"""

import os, sys, time, random, requests
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forge_instruments_v22 import (
    SETUP_CONFIG, Strategy, TradeType, OrderType, Direction,
    InstrumentSetup, get_all_symbols, TIME_OF_DAY_EDGES,
)
from forge_instruments_long import LONG_SETUP_CONFIG
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


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCH (same as before)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_polygon_candles(symbol, days_back=180):
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


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT
# ═══════════════════════════════════════════════════════════════════════════════

def build_snapshot(symbol, candles, bar_idx, spread_mult=1.0):
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
    price=c[-1]; spread=atr*0.05*spread_mult
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
# TRADE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BT:
    tid:str; sym:str; strat:str; dir:str; ttype:str; cfg:str
    entry:float; sl:float; tp:float; csl:float
    risk:float; atr:float; conf:float; ebar:int
    xbar:int=-1; xprice:float=0.; xreason:str=""
    pnl_r:float=0.; partial:bool=False; be:bool=False
    bars:int=0; mfr:float=0.
    @property
    def ru(self): return abs(self.entry-self.sl)
    def cr(self,p):
        if self.ru==0: return 0.
        return (p-self.entry)/self.ru if self.dir=="LONG" else (self.entry-p)/self.ru


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE (supports config swapping)
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestEngine:
    def __init__(self, config_map, config_name="Standard", bal=10000,
                 spread_mult=1.0, slip_atr=0.0, label="Test"):
        self.config_map = config_map  # {symbol: InstrumentSetup}
        self.config_name = config_name
        self.BAL0=bal; self.balance=bal; self.peak=bal; self.max_dd=0.
        self.spread_mult=spread_mult; self.slip_atr=slip_atr; self.label=label
        self.trades=[]; self.open={}; self.last_bar={}
        self.daily_counts={}; self.equity=[]
        self.corr=CorrelationGuard()

    def run(self, all_candles):
        # Temporarily swap SETUP_CONFIG
        from forge_instruments_v22 import SETUP_CONFIG
        original_config = dict(SETUP_CONFIG)
        SETUP_CONFIG.clear()
        SETUP_CONFIG.update(self.config_map)

        engine = SignalEngine()
        max_bars = max(c["count"] for c in all_candles.values())
        start = 60

        print(f"\n    [{self.label}] Walking {start} to {max_bars} ({max_bars-start} bars)...")

        for bar in range(start, max_bars):
            if bar % 500 == 0:
                pct=(bar-start)/(max_bars-start)*100
                print(f"    [{self.label}] Bar {bar}/{max_bars} ({pct:.0f}%) | "
                      f"${self.balance:,.2f} | {len(self.trades)} trades", flush=True)

            # Manage trades
            for sym in list(self.open.keys()):
                t=self.open[sym]; cd=all_candles.get(sym)
                if not cd or bar>=cd["count"]: continue
                t.bars+=1
                hi,lo,cl=cd["highs"][bar],cd["lows"][bar],cd["closes"][bar]
                r=t.cr(cl)
                if r>t.mfr: t.mfr=r
                if t.dir=="LONG" and lo<=t.csl:
                    self._close(t,bar,t.csl,"BE_STOP" if t.be else "SL_HIT"); continue
                if t.dir=="SHORT" and hi>=t.csl:
                    self._close(t,bar,t.csl,"BE_STOP" if t.be else "SL_HIT"); continue
                if t.ttype=="SCALP":
                    if t.dir=="LONG" and hi>=t.tp:
                        self._close(t,bar,t.tp,"TP_HIT"); continue
                    if t.dir=="SHORT" and lo<=t.tp:
                        self._close(t,bar,t.tp,"TP_HIT"); continue
                if not t.be and r>=0.5: t.csl=t.entry; t.be=True
                if t.ttype=="RUNNER":
                    if not t.partial and r>=1.0:
                        t.partial=True; self.balance+=abs(t.ru*0.5*self._lots(t.risk,t.ru))
                    if t.partial and t.ru>0:
                        if t.dir=="LONG":
                            nt=cl-1.5*t.ru
                            if nt>t.csl: t.csl=nt
                        else:
                            nt=cl+1.5*t.ru
                            if nt<t.csl: t.csl=nt
                    if t.bars>=50: self._close(t,bar,cl,"MAX_HOLD"); continue

            # Build snapshots
            snaps={}
            for sym in self.config_map:
                cd=all_candles.get(sym)
                if cd and bar<cd["count"]:
                    s=build_snapshot(sym,cd,bar,self.spread_mult)
                    if s: snaps[sym]=s
            if not snaps: continue

            # Time
            for sym,cd in all_candles.items():
                if bar<len(cd["timestamps"]):
                    bt=datetime.fromtimestamp(cd["timestamps"][bar]/1000,tz=timezone.utc); break
            else: bt=datetime.now(timezone.utc)

            # Signals
            sigs=engine.generate_signals(snaps,current_time=bt)

            for sig in sigs:
                if len(self.open)>=5: break
                if sig.symbol in self.open: continue
                if bar-self.last_bar.get(sig.symbol,-999)<2: continue
                dk=bt.strftime("%Y-%m-%d")
                if self.daily_counts.get(dk,0)>=12: continue
                ok,_=self.corr.can_trade(sig.symbol,set(self.open.keys()))
                if not ok: continue

                slip=0.
                if self.slip_atr>0:
                    slip=random.uniform(0,self.slip_atr)*sig.atr_value
                    if sig.direction=="LONG": sig.entry_price+=slip
                    else: sig.entry_price-=slip

                t=BT(tid=f"{sig.symbol}-{bar}",sym=sig.symbol,strat=sig.strategy.value,
                      dir=sig.direction,ttype=sig.trade_type.value,cfg=self.config_name,
                      entry=sig.entry_price,sl=sig.sl_price,tp=sig.tp_price,
                      csl=sig.sl_price,risk=sig.risk_pct,atr=sig.atr_value,
                      conf=sig.final_confidence,ebar=bar)
                self.open[sig.symbol]=t; self.trades.append(t)
                self.last_bar[sig.symbol]=bar
                self.daily_counts[dk]=self.daily_counts.get(dk,0)+1

            self.equity.append(self.balance)
            if self.balance<self.BAL0*0.90: break

        # Close remaining
        for sym in list(self.open.keys()):
            t=self.open[sym]; cd=all_candles.get(sym)
            p=cd["closes"][min(max_bars-1,cd["count"]-1)] if cd else t.entry
            self._close(t,max_bars-1,p,"END_OF_TEST")

        # Restore config
        SETUP_CONFIG.clear()
        SETUP_CONFIG.update(original_config)

        return self._report()

    def _lots(self,risk,sl):
        rd=self.balance*risk/100
        if sl==0: return 0.01
        return max(0.01,min(rd/sl,2.0))

    def _close(self,t,bar,xp,reason):
        t.xbar=bar; t.xprice=xp; t.xreason=reason
        if t.ru>0:
            t.pnl_r=(xp-t.entry)/t.ru if t.dir=="LONG" else (t.entry-xp)/t.ru
        if t.ttype=="RUNNER" and t.partial:
            dp=t.pnl_r*0.5*t.ru*self._lots(t.risk,t.ru)
        else:
            dp=t.pnl_r*t.ru*self._lots(t.risk,t.ru)
        self.balance+=dp
        if self.balance>self.peak: self.peak=self.balance
        dd=(self.peak-self.balance)/self.peak*100
        if dd>self.max_dd: self.max_dd=dd
        if t.sym in self.open: del self.open[t.sym]

    def _report(self):
        closed=[t for t in self.trades if t.xbar>=0]
        if not closed: return {"label":self.label,"error":"No trades"}
        w=[t for t in closed if t.pnl_r>0]; l=[t for t in closed if t.pnl_r<0]
        be=[t for t in closed if t.pnl_r==0]
        tr=sum(t.pnl_r for t in closed)
        mcl=0; cur=0
        for t in closed:
            if t.pnl_r<0: cur+=1; mcl=max(mcl,cur)
            else: cur=0
        sb={}
        for t in closed:
            s=t.strat
            if s not in sb: sb[s]={"n":0,"w":0,"r":0.}
            sb[s]["n"]+=1; sb[s]["r"]+=t.pnl_r
            if t.pnl_r>0: sb[s]["w"]+=1
        yb={}
        for t in closed:
            s=t.sym
            if s not in yb: yb[s]={"n":0,"w":0,"r":0.,"dir_long":0,"dir_short":0}
            yb[s]["n"]+=1; yb[s]["r"]+=t.pnl_r
            if t.pnl_r>0: yb[s]["w"]+=1
            if t.dir=="LONG": yb[s]["dir_long"]+=1
            else: yb[s]["dir_short"]+=1
        xr={}
        for t in closed: xr[t.xreason]=xr.get(t.xreason,0)+1
        bars_walked=len(self.equity)
        est_months=bars_walked/120/4.3
        if est_months<=0: est_months=1
        return {
            "label":self.label,"total_trades":len(closed),
            "winners":len(w),"losers":len(l),"scratches":len(be),
            "win_rate":len(w)/len(closed)*100,
            "total_r":tr,
            "avg_w":np.mean([t.pnl_r for t in w]) if w else 0,
            "avg_l":np.mean([t.pnl_r for t in l]) if l else 0,
            "pf":abs(sum(t.pnl_r for t in w))/abs(sum(t.pnl_r for t in l)) if l and sum(t.pnl_r for t in l)!=0 else 999,
            "avg_hold":np.mean([t.bars for t in closed]),
            "max_dd":self.max_dd,"final":self.balance,
            "pnl_d":self.balance-self.BAL0,
            "pnl_p":(self.balance-self.BAL0)/self.BAL0*100,
            "max_consec_loss":mcl,
            "monthly_r":tr/est_months,"est_months":est_months,
            "bars_walked":bars_walked,
            "strats":sb,"syms":yb,"exits":xr,
            "trades":closed,
            "scalp_n":sum(1 for t in closed if t.ttype=="SCALP"),
            "scalp_r":sum(t.pnl_r for t in closed if t.ttype=="SCALP"),
            "runner_n":sum(1 for t in closed if t.ttype=="RUNNER"),
            "runner_r":sum(t.pnl_r for t in closed if t.ttype=="RUNNER"),
            "long_n":sum(1 for t in closed if t.dir=="LONG"),
            "long_r":sum(t.pnl_r for t in closed if t.dir=="LONG"),
            "short_n":sum(1 for t in closed if t.dir=="SHORT"),
            "short_r":sum(t.pnl_r for t in closed if t.dir=="SHORT"),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MONTE CARLO
# ═══════════════════════════════════════════════════════════════════════════════

def monte_carlo(pnls, n=1000, bal=10000):
    results=[]; dds=[]; busts=0; passes=0
    for _ in range(n):
        sh=pnls.copy(); random.shuffle(sh)
        b=bal; pk=bal; mdd=0
        for r in sh:
            b+=r*b*0.015  # 1.5% risk
            if b>pk: pk=b
            dd=(pk-b)/pk*100
            if dd>mdd: mdd=dd
            if dd>=10: busts+=1; break
        dds.append(mdd)
        fp=(b-bal)/bal*100
        results.append(fp)
        if fp>=10 and mdd<10: passes+=1
    return {
        "n":n,"avg":np.mean(results),"med":np.median(results),
        "worst":np.min(results),"best":np.max(results),"std":np.std(results),
        "p5":np.percentile(results,5),"p25":np.percentile(results,25),
        "p75":np.percentile(results,75),"p95":np.percentile(results,95),
        "avg_dd":np.mean(dds),"p95_dd":np.percentile(dds,95),"worst_dd":np.max(dds),
        "bust":busts/n*100,"ftmo_pass":passes/n*100,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def show(r, trades=True):
    if "error" in r: print(f"\n  [{r['label']}] ERROR: {r['error']}"); return
    print(f"\n  {'='*75}")
    print(f"  {r['label'].upper()}")
    print(f"  {'='*75}")
    print(f"  Trades: {r['total_trades']} | W:{r['winners']} L:{r['losers']} BE:{r['scratches']} | WR: {r['win_rate']:.1f}%")
    print(f"  Total R: {r['total_r']:+.2f}R | PF: {r['pf']:.2f} | Avg W: {r['avg_w']:+.2f}R | Avg L: {r['avg_l']:+.2f}R")
    print(f"  Max DD: {r['max_dd']:.2f}% | Final: ${r['final']:,.2f} ({r['pnl_p']:+.1f}%)")
    print(f"  Max consec losses: {r['max_consec_loss']} | Avg hold: {r['avg_hold']:.1f} bars")
    print(f"  LONG:  {r['long_n']} trades {r['long_r']:+.2f}R | SHORT: {r['short_n']} trades {r['short_r']:+.2f}R")
    print(f"  SCALP: {r['scalp_n']} trades {r['scalp_r']:+.2f}R | RUNNER: {r['runner_n']} trades {r['runner_r']:+.2f}R")
    print(f"  Monthly R: {r['monthly_r']:+.2f}R | Est months: {r['est_months']:.1f}")

    print(f"\n  Strategy breakdown:")
    for s,st in sorted(r['strats'].items(),key=lambda x:x[1]['r'],reverse=True):
        wr=st['w']/st['n']*100 if st['n']>0 else 0
        print(f"    {s:20s}: {st['n']:4d} trades | WR: {wr:5.1f}% | {st['r']:+8.2f}R")

    print(f"\n  Symbol breakdown:")
    for s,st in sorted(r['syms'].items(),key=lambda x:x[1]['r'],reverse=True):
        wr=st['w']/st['n']*100 if st['n']>0 else 0
        ld = f"L:{st.get('dir_long',0)} S:{st.get('dir_short',0)}"
        print(f"    {s:10s}: {st['n']:4d} trades | WR: {wr:5.1f}% | {st['r']:+8.2f}R | {ld}")

    print(f"\n  Exit reasons:")
    for reason,count in sorted(r['exits'].items(),key=lambda x:x[1],reverse=True):
        print(f"    {reason:20s}: {count}")

    if trades:
        tl=r['trades']; n=min(15,len(tl))
        print(f"\n  Last {n} trades:")
        print(f"  {'Sym':10s} {'Dir':5s} {'Strat':20s} {'Type':7s} {'P&L':>7s} {'Hold':>5s} {'Reason':15s}")
        print(f"  {'-'*72}")
        for t in tl[-n:]:
            print(f"  {t.sym:10s} {t.dir:5s} {t.strat:20s} {t.ttype:7s} "
                  f"{t.pnl_r:+6.2f}R {t.bars:4d}b {t.xreason:15s}")

def show_mc(mc):
    print(f"\n  {'='*75}")
    print(f"  MONTE CARLO ({mc['n']} sims, 1.5% risk)")
    print(f"  {'='*75}")
    print(f"  P&L: Worst:{mc['worst']:+.1f}% P5:{mc['p5']:+.1f}% Median:{mc['med']:+.1f}% "
          f"Mean:{mc['avg']:+.1f}% P95:{mc['p95']:+.1f}% Best:{mc['best']:+.1f}%")
    print(f"  DD:  Avg:{mc['avg_dd']:.1f}% P95:{mc['p95_dd']:.1f}% Worst:{mc['worst_dd']:.1f}%")
    print(f"  Bust rate: {mc['bust']:.1f}% | FTMO pass rate: {mc['ftmo_pass']:.1f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 80)
    print("  FORGE v22 — LONG SYSTEM + COMBINED BACKTEST")
    print("  6 months real data | LONG standalone | SHORT+LONG combined")
    print("=" * 80)

    if not POLYGON_API_KEY: print("\n  POLYGON_API_KEY not set!"); sys.exit(1)

    # Show configs
    print(f"\n  SHORT config: {len(SETUP_CONFIG)} instruments")
    print(f"  LONG config:  {len(LONG_SETUP_CONFIG)} instruments")

    # Fetch data
    print("\n" + "-" * 80)
    print("  Fetching 6 months of hourly data...")
    print("-" * 80)
    all_symbols = set(list(SETUP_CONFIG.keys()) + list(LONG_SETUP_CONFIG.keys()))
    all_candles = {}
    for sym in sorted(all_symbols):
        t=POLYGON_TICKERS.get(sym,"?")
        print(f"  {sym} ({t})...", end=" ", flush=True)
        c=fetch_polygon_candles(sym)
        if c and c["count"]>=50:
            all_candles[sym]=c
            print(f"OK - {c['count']} bars")
        else: print("SKIP")
        time.sleep(0.3)
    print(f"\n  Loaded: {len(all_candles)} instruments")

    # ═══════ TEST 1: LONG STANDALONE ═══════
    print("\n" + "#" * 80)
    print("  TEST 1: LONG CONFIG STANDALONE (6 months)")
    print("#" * 80)
    bt1=BacktestEngine(config_map=dict(LONG_SETUP_CONFIG), config_name="LONG",
                        label="LONG Standalone 6mo")
    r1=bt1.run(deepcopy(all_candles))
    show(r1)

    # ═══════ TEST 2: LONG STRESS ═══════
    print("\n" + "#" * 80)
    print("  TEST 2: LONG STRESS (3x spread + slippage)")
    print("#" * 80)
    bt2=BacktestEngine(config_map=dict(LONG_SETUP_CONFIG), config_name="LONG",
                        spread_mult=3.0, slip_atr=0.3,
                        label="LONG Stress 6mo")
    r2=bt2.run(deepcopy(all_candles))
    show(r2, trades=False)

    # ═══════ TEST 3: SHORT STANDALONE (baseline comparison) ═══════
    print("\n" + "#" * 80)
    print("  TEST 3: SHORT CONFIG (baseline for comparison)")
    print("#" * 80)
    bt3=BacktestEngine(config_map=dict(SETUP_CONFIG), config_name="SHORT",
                        label="SHORT Baseline 6mo")
    r3=bt3.run(deepcopy(all_candles))
    show(r3, trades=False)

    # ═══════ TEST 4: COMBINED SHORT+LONG ═══════
    # Merge configs: for each symbol, use BOTH direction if both configs have it
    # with the SHORT strategy as primary. This is the "BOTH-direction" config.
    print("\n" + "#" * 80)
    print("  TEST 4: COMBINED (SHORT strategies + LONG strategies)")
    print("  Each instrument gets its SHORT strategy + LONG strategy")
    print("#" * 80)

    # Build combined: for instruments in both configs, set direction=BOTH
    # For instruments in only one config, keep that direction
    # Strategy: use the SHORT config's strategy but allow BOTH directions
    # (since the signal engine generates signals based on the setup's direction)
    # Actually, we need to pick: if instrument has both SHORT and LONG strategy,
    # which strategy do we use? They might be different.
    # Solution: use the strategy that had higher total R across both directions.
    combined_config = {}
    for sym in all_symbols:
        short_setup = SETUP_CONFIG.get(sym)
        long_setup = LONG_SETUP_CONFIG.get(sym)

        if short_setup and long_setup:
            # Both exist — create BOTH direction setup with the higher-R strategy
            # For now, use SHORT strategy as base since it's proven in production
            combined_config[sym] = InstrumentSetup(
                symbol=sym,
                strategy=short_setup.strategy,
                direction=Direction.BOTH,
                sl_atr=short_setup.sl_atr,
                tp_atr=short_setup.tp_atr,
                risk_pct=1.5,
                trade_type=short_setup.trade_type,
                order_type=short_setup.order_type,
                expectancy=short_setup.expectancy,
                win_rate=short_setup.win_rate,
                profit_factor=short_setup.profit_factor,
            )
        elif short_setup:
            combined_config[sym] = short_setup
        elif long_setup:
            combined_config[sym] = long_setup

    bt4=BacktestEngine(config_map=combined_config, config_name="COMBINED",
                        label="COMBINED SHORT+LONG 6mo")
    r4=bt4.run(deepcopy(all_candles))
    show(r4)

    # ═══════ TEST 5: MONTE CARLO ═══════
    print("\n" + "#" * 80)
    print("  TEST 5: MONTE CARLO (1000 sims, 1.5% risk)")
    print("#" * 80)

    for label, report in [("LONG", r1), ("SHORT", r3), ("COMBINED", r4)]:
        if "error" not in report:
            pnls = [t.pnl_r for t in report["trades"]]
            if pnls:
                mc = monte_carlo(pnls, n=1000)
                print(f"\n  {label}:")
                show_mc(mc)

    # ═══════ COMPARISON ═══════
    print("\n" + "=" * 80)
    print("  COMPARISON TABLE")
    print("=" * 80)
    print(f"\n  {'Test':35s} {'Trades':>7s} {'WR':>6s} {'R':>9s} {'PF':>6s} {'DD':>7s} {'L/S':>10s}")
    print(f"  {'-'*82}")
    for r in [r1, r2, r3, r4]:
        if "error" in r: continue
        ls = f"{r['long_n']}L/{r['short_n']}S"
        print(f"  {r['label']:35s} {r['total_trades']:7d} {r['win_rate']:5.1f}% "
              f"{r['total_r']:+8.2f}R {r['pf']:5.2f} {r['max_dd']:6.2f}% {ls:>10s}")

    # ═══════ VERDICT ═══════
    print("\n" + "=" * 80)
    print("  VERDICT")
    print("=" * 80)
    for label, r in [("LONG standalone", r1), ("SHORT baseline", r3), ("COMBINED", r4)]:
        if "error" in r: continue
        ok = r["total_r"] > 0 and r["max_dd"] < 10
        print(f"  {label:20s}: {'PASS' if ok else 'FAIL'} | {r['total_r']:+.2f}R | "
              f"DD: {r['max_dd']:.2f}% | {r['total_trades']} trades")

    print("\n" + "=" * 80)
    print("  TESTS COMPLETE")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
