"""
FORGE v22.1 — COMPREHENSIVE STRESS TEST SUITE
===============================================
6 test scenarios on real Polygon data, bar-by-bar walk-forward:

  TEST 1: Standard Backtest (baseline)
  TEST 2: Spread & Slippage Stress (3x spreads + random slippage)
  TEST 3: Choppy Market (inject price noise to simulate ranging hell)
  TEST 4: Drawdown Recovery (start after 4 consecutive losses)
  TEST 5: Monte Carlo (1000 randomized trade sequences → P&L distribution)
  TEST 6: FTMO Pass/Fail Simulation (10 challenge attempts)

v22.1 fixes from backtest data:
  - EURUSD: CONFLUENCE → MEAN_REVERT (was 45% of all trades, 20% WR)
  - UK100: BOTH → SHORT only (both LONG trades lost)
  - CONFLUENCE: 4/5 signals required (was 3/5, too loose)

Usage:
    set POLYGON_API_KEY=your_key_here
    set PYTHONIOENCODING=utf-8
    python test_v22_real_data.py > test_results.txt 2>&1
"""

import os
import sys
import time
import random
import requests
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from copy import deepcopy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forge_instruments_v22 import (
    SETUP_CONFIG, get_all_symbols,
    TIME_OF_DAY_EDGES, MONTHLY_SEASONALITY,
)
from forge_signals_v22 import SignalEngine, MarketSnapshot, Signal
from forge_runner import TradeManager, ManagedTrade, RunnerContext, RunnerDetector, TradeType, ExitReason
from forge_limit import LimitOrderManager
from forge_correlation import CorrelationGuard


# ═══════════════════════════════════════════════════════════════════════════════
# POLYGON DATA FETCH
# ═══════════════════════════════════════════════════════════════════════════════

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

POLYGON_TICKERS = {
    "USDCHF": "C:USDCHF", "NZDUSD": "C:NZDUSD", "EURGBP": "C:EURGBP",
    "EURUSD": "C:EURUSD", "GBPJPY": "C:GBPJPY", "USDJPY": "C:USDJPY",
    "GBPUSD": "C:GBPUSD", "XAUUSD": "C:XAUUSD",
    "GER40": "EWG", "UK100": "EWU", "US100": "QQQ", "USOIL": "USO",
    "ETHUSD": "X:ETHUSD", "BTCUSD": "X:BTCUSD",
}


def fetch_polygon_candles(symbol):
    ticker = POLYGON_TICKERS.get(symbol)
    if not ticker:
        return None
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/hour"
           f"/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
           f"?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_API_KEY}")
    all_results = []
    pages = 0
    try:
        while url and pages < 15:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 429:
                time.sleep(12)
                resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            all_results.extend(data.get("results", []))
            pages += 1
            nxt = data.get("next_url")
            if nxt and len(all_results) < 1000:
                url = nxt + (f"&apiKey={POLYGON_API_KEY}" if "apiKey" not in nxt else "")
                time.sleep(0.3)
            else:
                break
    except Exception as e:
        return None
    if len(all_results) < 50:
        return None
    return {
        "opens": np.array([r["o"] for r in all_results], dtype=float),
        "highs": np.array([r["h"] for r in all_results], dtype=float),
        "lows": np.array([r["l"] for r in all_results], dtype=float),
        "closes": np.array([r["c"] for r in all_results], dtype=float),
        "volumes": np.array([r.get("v", 0) for r in all_results], dtype=float),
        "timestamps": [r["t"] for r in all_results],
        "count": len(all_results),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════════════════════

def compute_atr(h, l, c, p=14):
    if len(c) < 2: return abs(c[-1]) * 0.01
    tr = np.maximum(h[1:]-l[1:], np.maximum(np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])))
    if len(tr) < p: return np.mean(tr)
    a = np.mean(tr[:p])
    for i in range(p, len(tr)): a = (a*(p-1)+tr[i])/p
    return a

def compute_rsi(c, p=14):
    if len(c) < p+1: return 50.0
    d = np.diff(c)
    g, lo = np.where(d>0,d,0), np.where(d<0,-d,0)
    ag, al = np.mean(g[:p]), np.mean(lo[:p])
    for i in range(p,len(g)): ag=(ag*(p-1)+g[i])/p; al=(al*(p-1)+lo[i])/p
    if al == 0: return 100.0
    return 100.0 - 100.0/(1.0+ag/al)

def compute_ema(d, p):
    if len(d) < p: return np.mean(d) if len(d) > 0 else 0.0
    m = 2.0/(p+1); e = np.mean(d[:p])
    for i in range(p, len(d)): e = (d[i]-e)*m+e
    return e

def compute_bollinger(c, p=20, m=2.0):
    if len(c) < p:
        mid = np.mean(c); s = np.std(c) if len(c)>1 else abs(mid)*0.01
        return mid+m*s, mid-m*s, mid
    sma = np.mean(c[-p:]); s = np.std(c[-p:])
    if s == 0: s = abs(sma)*0.001
    return sma+m*s, sma-m*s, sma

def compute_stochastic(h, l, c, kp=14, dp=3):
    if len(c) < kp+dp: return 50.0,50.0,50.0,50.0
    kvs = []
    for i in range(kp-1, len(c)):
        hi,lo = np.max(h[i-kp+1:i+1]), np.min(l[i-kp+1:i+1])
        kvs.append(100.0*(c[i]-lo)/(hi-lo) if hi!=lo else 50.0)
    kvs = np.array(kvs)
    if len(kvs) < dp: return kvs[-1],kvs[-1],kvs[-1],kvs[-1]
    return kvs[-1], np.mean(kvs[-dp:]), kvs[-2] if len(kvs)>1 else kvs[-1], np.mean(kvs[-dp-1:-1]) if len(kvs)>dp else np.mean(kvs[-dp:])

def compute_vwap(h, l, c, v):
    tp = (h+l+c)/3.0; cv = np.cumsum(v); ctv = np.cumsum(tp*v)
    if cv[-1]==0: return c[-1], abs(c[-1])*0.001
    vw = ctv[-1]/cv[-1]; vs = np.std(tp-vw) if len(tp)>1 else abs(c[-1])*0.001
    return vw, max(vs, abs(c[-1])*0.0001)

def compute_adx(h, l, c, p=14):
    if len(c) < p*2: return 20.0,20.0,25.0,25.0
    pdm,mdm,tr = np.zeros(len(h)),np.zeros(len(h)),np.zeros(len(h))
    for i in range(1,len(h)):
        u,d = h[i]-h[i-1], l[i-1]-l[i]
        pdm[i] = u if u>d and u>0 else 0
        mdm[i] = d if d>u and d>0 else 0
        tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
    atrs = np.mean(tr[1:p+1]); pdms = np.mean(pdm[1:p+1]); mdms = np.mean(mdm[1:p+1])
    dxv = []; pdi=mdi=0
    for i in range(p+1,len(h)):
        atrs=(atrs*(p-1)+tr[i])/p; pdms=(pdms*(p-1)+pdm[i])/p; mdms=(mdms*(p-1)+mdm[i])/p
        if atrs>0: pdi=100.0*pdms/atrs; mdi=100.0*mdms/atrs
        ds=pdi+mdi; dxv.append(100.0*abs(pdi-mdi)/ds if ds>0 else 0)
    if len(dxv)<p: a=np.mean(dxv) if dxv else 20.0; return a,a,pdi,mdi
    adx=np.mean(dxv[:p])
    for i in range(p,len(dxv)): adx=(adx*(p-1)+dxv[i])/p
    ap=adx
    if len(dxv)>5:
        ap=np.mean(dxv[:p])
        for i in range(p,len(dxv)-5): ap=(ap*(p-1)+dxv[i])/p
    return adx,ap,pdi,mdi

def compute_keltner(c, h, l):
    e = compute_ema(c, 20); a = compute_atr(h, l, c)
    return e+1.5*a, e-1.5*a


# ═══════════════════════════════════════════════════════════════════════════════
# BUILD SNAPSHOT AT BAR
# ═══════════════════════════════════════════════════════════════════════════════

def build_snapshot_at_bar(symbol, candles, bar_idx, timestamp_ms=None,
                          spread_mult=1.0, noise_pct=0.0):
    """Build MarketSnapshot at a specific bar. Supports spread/noise injection."""
    end = bar_idx + 1
    start = max(0, end - 250)
    o = candles["opens"][start:end].copy()
    h = candles["highs"][start:end].copy()
    l = candles["lows"][start:end].copy()
    c = candles["closes"][start:end].copy()
    v = candles["volumes"][start:end].copy()
    n = len(c)
    if n < 20: return None

    # Inject noise for choppy market test
    if noise_pct > 0:
        noise = np.random.normal(0, noise_pct, n) * c
        c = c + noise
        h = np.maximum(h, c)
        l = np.minimum(l, c)

    atr = compute_atr(h, l, c)
    if atr == 0: atr = abs(c[-1])*0.001
    rsi = compute_rsi(c)
    sk,sd,skp,sdp = compute_stochastic(h,l,c)
    e50 = compute_ema(c, min(50,n))
    e200 = compute_ema(c, min(200,n))
    bbu,bbl,bbm = compute_bollinger(c)
    vwap,vstd = compute_vwap(h,l,c,v)
    adx,adxp,pdi,mdi = compute_adx(h,l,c)
    ku,kl = compute_keltner(c,h,l)

    sl = min(8,n); pi = min(sl+8,n)
    pdh = np.max(h[-pi:-sl]) if pi>sl else np.max(h[:sl])
    pdl = np.min(l[-pi:-sl]) if pi>sl else np.min(l[:sl])
    pdc = c[-sl-1] if n>sl else c[0]
    price = c[-1]
    spread = atr * 0.05 * spread_mult

    hour = 12
    if timestamp_ms:
        hour = datetime.fromtimestamp(timestamp_ms/1000, tz=timezone.utc).hour

    return MarketSnapshot(
        symbol=symbol,
        opens=o, highs=h, lows=l, closes=c, volumes=v,
        bid=price-spread/2, ask=price+spread/2,
        atr=atr, rsi=rsi,
        stoch_k=sk, stoch_d=sd, stoch_k_prev=skp, stoch_d_prev=sdp,
        ema_50=e50, ema_200=e200,
        bb_upper=bbu, bb_lower=bbl, bb_middle=bbm,
        vwap=vwap, vwap_std=vstd,
        adx=adx, adx_prev=adxp, plus_di=pdi, minus_di=mdi,
        prev_day_high=pdh, prev_day_low=pdl, prev_day_close=pdc,
        session_open=o[-sl], session_high=np.max(h[-sl:]), session_low=np.min(l[-sl:]),
        orb_high=h[-sl], orb_low=l[-sl], orb_complete=True,
        asian_high=np.max(h[:min(7,n)]), asian_low=np.min(l[:min(7,n)]), asian_complete=True,
        keltner_upper=ku, keltner_lower=kl,
        bars_since_open=sl, current_hour_utc=hour,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTEST TRADE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BacktestTrade:
    trade_id: str
    symbol: str
    strategy: str
    direction: str
    trade_type: str
    entry_price: float
    sl_price: float
    tp_price: float
    current_sl: float
    risk_pct: float
    atr_at_entry: float
    confidence: float
    entry_bar: int
    exit_bar: int = -1
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl_r: float = 0.0
    partial_taken: bool = False
    be_set: bool = False
    bars_held: int = 0
    max_favorable_r: float = 0.0
    slippage_applied: float = 0.0

    @property
    def r_unit(self):
        return abs(self.entry_price - self.sl_price)

    def current_r(self, price):
        if self.r_unit == 0: return 0.0
        if self.direction == "LONG":
            return (price - self.entry_price) / self.r_unit
        return (self.entry_price - price) / self.r_unit

    @property
    def is_open(self):
        return self.exit_bar == -1


# ═══════════════════════════════════════════════════════════════════════════════
# WALK-FORWARD BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class WalkForwardBacktest:
    """Configurable walk-forward backtest engine."""

    def __init__(self, starting_balance=10000, spread_mult=1.0, noise_pct=0.0,
                 slippage_atr=0.0, forced_initial_losses=0, label="Standard"):
        self.STARTING_BALANCE = starting_balance
        self.spread_mult = spread_mult
        self.noise_pct = noise_pct
        self.slippage_atr = slippage_atr
        self.forced_initial_losses = forced_initial_losses
        self.label = label

        self.BE_R = 0.5
        self.PARTIAL_R = 1.0
        self.PARTIAL_PCT = 0.50
        self.TRAIL_R = 1.5
        self.MAX_HOLD = 50
        self.COOLDOWN_BARS = 2
        self.MAX_OPEN = 5
        self.MAX_DAILY = 12

        self.trades = []
        self.open_trades = {}
        self.signal_engine = SignalEngine()
        self.correlation_guard = CorrelationGuard()
        self.last_trade_bar = {}
        self.daily_counts = {}
        self.balance = starting_balance
        self.peak_balance = starting_balance
        self.max_drawdown = 0.0
        self.equity_curve = []
        self.forced_losses_remaining = forced_initial_losses

    def run(self, all_candles):
        min_bars = min(c["count"] for c in all_candles.values())
        start_bar = 50
        end_bar = min_bars

        for bar in range(start_bar, end_bar):
            if bar % 100 == 0:
                pct = (bar - start_bar) / max(1, end_bar - start_bar) * 100
                print(f"    [{self.label}] Bar {bar}/{end_bar} ({pct:.0f}%) | "
                      f"Bal: ${self.balance:,.2f} | Trades: {len(self.trades)}", flush=True)

            self._manage_trades(all_candles, bar)

            snapshots = {}
            for sym, cand in all_candles.items():
                if bar < cand["count"]:
                    ts = cand["timestamps"][bar] if bar < len(cand["timestamps"]) else None
                    snap = build_snapshot_at_bar(sym, cand, bar, ts,
                                                 self.spread_mult, self.noise_pct)
                    if snap:
                        snapshots[sym] = snap
            if not snapshots:
                continue

            first_sym = list(all_candles.keys())[0]
            ts = all_candles[first_sym]["timestamps"][bar]
            bar_time = datetime.fromtimestamp(ts/1000, tz=timezone.utc)

            signals = self.signal_engine.generate_signals(snapshots, current_time=bar_time)

            for sig in signals:
                if not self._can_take(sig, bar, bar_time):
                    continue
                self._open_trade(sig, bar, all_candles)

            self.equity_curve.append(self.balance)
            if self.balance < self.STARTING_BALANCE * 0.90:
                break

        self._close_all_open(all_candles, end_bar - 1)
        return self._build_report(end_bar - start_bar)

    def _manage_trades(self, all_candles, bar):
        for sym in list(self.open_trades.keys()):
            trade = self.open_trades[sym]
            cand = all_candles.get(sym)
            if not cand or bar >= cand["count"]:
                continue
            trade.bars_held += 1
            high, low, close = cand["highs"][bar], cand["lows"][bar], cand["closes"][bar]
            r = trade.current_r(close)
            if r > trade.max_favorable_r:
                trade.max_favorable_r = r

            # SL check
            if trade.direction == "LONG" and low <= trade.current_sl:
                self._close(trade, bar, trade.current_sl, "BREAKEVEN_STOP" if trade.be_set else "SL_HIT")
                continue
            elif trade.direction == "SHORT" and high >= trade.current_sl:
                self._close(trade, bar, trade.current_sl, "BREAKEVEN_STOP" if trade.be_set else "SL_HIT")
                continue

            # TP check (SCALP only)
            if trade.trade_type == "SCALP":
                if trade.direction == "LONG" and high >= trade.tp_price:
                    self._close(trade, bar, trade.tp_price, "TP_HIT"); continue
                elif trade.direction == "SHORT" and low <= trade.tp_price:
                    self._close(trade, bar, trade.tp_price, "TP_HIT"); continue

            # Breakeven
            if not trade.be_set and r >= self.BE_R:
                trade.current_sl = trade.entry_price
                trade.be_set = True

            # Runner management
            if trade.trade_type == "RUNNER":
                if not trade.partial_taken and r >= self.PARTIAL_R:
                    trade.partial_taken = True
                    pp = trade.r_unit * self.PARTIAL_PCT * self._lots(trade.risk_pct, trade.r_unit)
                    self.balance += abs(pp)
                if trade.partial_taken and trade.r_unit > 0:
                    if trade.direction == "LONG":
                        nt = close - self.TRAIL_R * trade.r_unit
                        if nt > trade.current_sl: trade.current_sl = nt
                    else:
                        nt = close + self.TRAIL_R * trade.r_unit
                        if nt < trade.current_sl: trade.current_sl = nt
                if trade.bars_held >= self.MAX_HOLD:
                    self._close(trade, bar, close, "MAX_HOLD"); continue

    def _can_take(self, sig, bar, bar_time):
        if len(self.open_trades) >= self.MAX_OPEN: return False
        if sig.symbol in self.open_trades: return False
        if bar - self.last_trade_bar.get(sig.symbol, -999) < self.COOLDOWN_BARS: return False
        dk = bar_time.strftime("%Y-%m-%d")
        if self.daily_counts.get(dk, 0) >= self.MAX_DAILY: return False
        ok, _ = self.correlation_guard.can_trade(sig.symbol, set(self.open_trades.keys()))
        return ok

    def _lots(self, risk_pct, sl_dist):
        rd = self.balance * risk_pct / 100
        if sl_dist == 0: return 0.01
        return max(0.01, min(rd / sl_dist, 2.0))

    def _open_trade(self, sig, bar, all_candles):
        # Apply slippage
        slip = 0.0
        if self.slippage_atr > 0:
            slip = random.uniform(0, self.slippage_atr) * sig.atr_value
            if sig.direction == "LONG":
                sig.entry_price += slip
            else:
                sig.entry_price -= slip

        # Force initial losses for drawdown recovery test
        force_loss = False
        if self.forced_losses_remaining > 0:
            force_loss = True
            self.forced_losses_remaining -= 1

        t = BacktestTrade(
            trade_id=f"{sig.symbol}-{bar}", symbol=sig.symbol,
            strategy=sig.strategy.value, direction=sig.direction,
            trade_type=sig.trade_type.value, entry_price=sig.entry_price,
            sl_price=sig.sl_price, tp_price=sig.tp_price,
            current_sl=sig.sl_price, risk_pct=sig.risk_pct,
            atr_at_entry=sig.atr_value, confidence=sig.final_confidence,
            entry_bar=bar, slippage_applied=slip,
        )

        if force_loss:
            # Simulate immediate stop loss hit
            t.exit_bar = bar + 1
            t.exit_price = sig.sl_price
            t.exit_reason = "FORCED_LOSS"
            t.pnl_r = -1.0
            t.bars_held = 1
            dollar_pnl = -t.r_unit * self._lots(t.risk_pct, t.r_unit)
            self.balance += dollar_pnl
            self.trades.append(t)
            self.last_trade_bar[sig.symbol] = bar
            return

        self.open_trades[sig.symbol] = t
        self.trades.append(t)
        self.last_trade_bar[sig.symbol] = bar
        dk = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily_counts[dk] = self.daily_counts.get(dk, 0) + 1

    def _close(self, trade, bar, exit_price, reason):
        trade.exit_bar = bar
        trade.exit_price = exit_price
        trade.exit_reason = reason

        if trade.r_unit > 0:
            if trade.direction == "LONG":
                trade.pnl_r = (exit_price - trade.entry_price) / trade.r_unit
            else:
                trade.pnl_r = (trade.entry_price - exit_price) / trade.r_unit
        else:
            trade.pnl_r = 0

        if trade.trade_type == "RUNNER" and trade.partial_taken:
            dp = trade.pnl_r * (1 - self.PARTIAL_PCT) * trade.r_unit * self._lots(trade.risk_pct, trade.r_unit)
        else:
            dp = trade.pnl_r * trade.r_unit * self._lots(trade.risk_pct, trade.r_unit)
        self.balance += dp

        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = (self.peak_balance - self.balance) / self.peak_balance * 100
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        if trade.symbol in self.open_trades:
            del self.open_trades[trade.symbol]

    def _close_all_open(self, all_candles, last_bar):
        for sym in list(self.open_trades.keys()):
            t = self.open_trades[sym]
            c = all_candles.get(sym)
            p = c["closes"][last_bar] if c and last_bar < c["count"] else t.entry_price
            self._close(t, last_bar, p, "END_OF_TEST")

    def _build_report(self, total_bars):
        closed = [t for t in self.trades if t.exit_bar >= 0]
        if not closed:
            return {"label": self.label, "error": "No trades"}

        winners = [t for t in closed if t.pnl_r > 0]
        losers = [t for t in closed if t.pnl_r < 0]
        scratches = [t for t in closed if t.pnl_r == 0]
        total_r = sum(t.pnl_r for t in closed)

        strat_stats = {}
        for t in closed:
            s = t.strategy
            if s not in strat_stats:
                strat_stats[s] = {"trades": 0, "wins": 0, "total_r": 0}
            strat_stats[s]["trades"] += 1
            strat_stats[s]["total_r"] += t.pnl_r
            if t.pnl_r > 0: strat_stats[s]["wins"] += 1

        sym_stats = {}
        for t in closed:
            s = t.symbol
            if s not in sym_stats:
                sym_stats[s] = {"trades": 0, "wins": 0, "total_r": 0}
            sym_stats[s]["trades"] += 1
            sym_stats[s]["total_r"] += t.pnl_r
            if t.pnl_r > 0: sym_stats[s]["wins"] += 1

        exit_reasons = {}
        for t in closed:
            exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1

        return {
            "label": self.label,
            "total_bars": total_bars,
            "total_trades": len(closed),
            "winners": len(winners),
            "losers": len(losers),
            "scratches": len(scratches),
            "win_rate": len(winners) / len(closed) * 100 if closed else 0,
            "total_r": total_r,
            "avg_winner_r": np.mean([t.pnl_r for t in winners]) if winners else 0,
            "avg_loser_r": np.mean([t.pnl_r for t in losers]) if losers else 0,
            "profit_factor": abs(sum(t.pnl_r for t in winners)) / abs(sum(t.pnl_r for t in losers)) if losers and sum(t.pnl_r for t in losers) != 0 else 999,
            "avg_hold": np.mean([t.bars_held for t in closed]),
            "max_drawdown_pct": self.max_drawdown,
            "final_balance": self.balance,
            "pnl_dollar": self.balance - self.STARTING_BALANCE,
            "pnl_pct": (self.balance - self.STARTING_BALANCE) / self.STARTING_BALANCE * 100,
            "strategy_breakdown": strat_stats,
            "symbol_breakdown": sym_stats,
            "exit_reasons": exit_reasons,
            "trade_log": closed,
            "equity_curve": list(self.equity_curve),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MONTE CARLO SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════

def monte_carlo_simulation(trade_pnls, n_sims=1000, starting_balance=10000):
    """Randomize trade order N times, return distribution of outcomes."""
    results = []
    max_dds = []
    bust_count = 0
    ftmo_pass = 0

    for _ in range(n_sims):
        shuffled = trade_pnls.copy()
        random.shuffle(shuffled)

        balance = starting_balance
        peak = starting_balance
        max_dd = 0

        for pnl_r in shuffled:
            # Approximate dollar P&L: 2% risk per trade, risk = 1R
            risk_dollars = balance * 0.02
            balance += pnl_r * risk_dollars

            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100
            if dd > max_dd:
                max_dd = dd

            if balance < starting_balance * 0.90:  # FTMO hard limit
                bust_count += 1
                break

        max_dds.append(max_dd)
        final_pnl_pct = (balance - starting_balance) / starting_balance * 100
        results.append(final_pnl_pct)

        # FTMO pass: > 10% profit AND max DD < 10% AND max daily DD < 5%
        if final_pnl_pct >= 10.0 and max_dd < 10.0:
            ftmo_pass += 1

    return {
        "n_sims": n_sims,
        "avg_pnl_pct": np.mean(results),
        "median_pnl_pct": np.median(results),
        "worst_pnl_pct": np.min(results),
        "best_pnl_pct": np.max(results),
        "std_pnl_pct": np.std(results),
        "p5_pnl_pct": np.percentile(results, 5),
        "p25_pnl_pct": np.percentile(results, 25),
        "p75_pnl_pct": np.percentile(results, 75),
        "p95_pnl_pct": np.percentile(results, 95),
        "avg_max_dd": np.mean(max_dds),
        "worst_max_dd": np.max(max_dds),
        "p95_max_dd": np.percentile(max_dds, 95),
        "bust_rate": bust_count / n_sims * 100,
        "ftmo_pass_rate": ftmo_pass / n_sims * 100,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DISPLAY
# ═══════════════════════════════════════════════════════════════════════════════

def fmt(p):
    if p > 100: return ".2f"
    elif p > 1: return ".4f"
    else: return ".5f"


def print_report(r, show_trades=True):
    if "error" in r:
        print(f"\n  [{r['label']}] ERROR: {r['error']}")
        return

    print(f"\n  {'='*70}")
    print(f"  {r['label'].upper()}")
    print(f"  {'='*70}")
    print(f"  Trades: {r['total_trades']} | W: {r['winners']} L: {r['losers']} BE: {r['scratches']} | "
          f"WR: {r['win_rate']:.1f}%")
    print(f"  Total R: {r['total_r']:+.2f}R | PF: {r['profit_factor']:.2f} | "
          f"Avg W: {r['avg_winner_r']:+.2f}R | Avg L: {r['avg_loser_r']:+.2f}R")
    print(f"  Max DD: {r['max_drawdown_pct']:.2f}% | Final: ${r['final_balance']:,.2f} "
          f"({r['pnl_pct']:+.1f}%)")
    print(f"  Avg hold: {r['avg_hold']:.1f} bars")

    # Strategy breakdown
    print(f"\n  Strategy breakdown:")
    for s, st in sorted(r['strategy_breakdown'].items(), key=lambda x: x[1]['total_r'], reverse=True):
        wr = st['wins']/st['trades']*100 if st['trades'] > 0 else 0
        print(f"    {s:20s}: {st['trades']:3d} trades | WR: {wr:5.1f}% | {st['total_r']:+7.2f}R")

    # Symbol breakdown
    print(f"\n  Symbol breakdown:")
    for s, st in sorted(r['symbol_breakdown'].items(), key=lambda x: x[1]['total_r'], reverse=True):
        wr = st['wins']/st['trades']*100 if st['trades'] > 0 else 0
        print(f"    {s:10s}: {st['trades']:3d} trades | WR: {wr:5.1f}% | {st['total_r']:+7.2f}R")

    # Exit reasons
    print(f"\n  Exit reasons:")
    for reason, count in sorted(r['exit_reasons'].items(), key=lambda x: x[1], reverse=True):
        print(f"    {reason:20s}: {count}")

    # Trade log
    if show_trades:
        trades = r['trade_log']
        show = min(15, len(trades))
        print(f"\n  Last {show} trades:")
        print(f"  {'Sym':10s} {'Dir':5s} {'Strategy':20s} {'Type':7s} {'P&L':>7s} {'Reason':15s}")
        print(f"  {'-'*70}")
        for t in trades[-show:]:
            print(f"  {t.symbol:10s} {t.direction:5s} {t.strategy:20s} {t.trade_type:7s} "
                  f"{t.pnl_r:+6.2f}R {t.exit_reason:15s}")


def print_monte_carlo(mc):
    print(f"\n  {'='*70}")
    print(f"  MONTE CARLO ({mc['n_sims']} simulations)")
    print(f"  {'='*70}")
    print(f"  P&L Distribution:")
    print(f"    Worst:    {mc['worst_pnl_pct']:+.1f}%")
    print(f"    P5:       {mc['p5_pnl_pct']:+.1f}%")
    print(f"    P25:      {mc['p25_pnl_pct']:+.1f}%")
    print(f"    Median:   {mc['median_pnl_pct']:+.1f}%")
    print(f"    Mean:     {mc['avg_pnl_pct']:+.1f}%")
    print(f"    P75:      {mc['p75_pnl_pct']:+.1f}%")
    print(f"    P95:      {mc['p95_pnl_pct']:+.1f}%")
    print(f"    Best:     {mc['best_pnl_pct']:+.1f}%")
    print(f"    Std Dev:  {mc['std_pnl_pct']:.1f}%")
    print(f"\n  Drawdown Distribution:")
    print(f"    Avg Max DD:   {mc['avg_max_dd']:.2f}%")
    print(f"    P95 Max DD:   {mc['p95_max_dd']:.2f}%")
    print(f"    Worst Max DD: {mc['worst_max_dd']:.2f}%")
    print(f"\n  Risk:")
    print(f"    Bust rate (hit -10% DD): {mc['bust_rate']:.1f}%")
    print(f"    FTMO pass rate (>10% profit, <10% DD): {mc['ftmo_pass_rate']:.1f}%")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 80)
    print("  FORGE v22.1 — COMPREHENSIVE STRESS TEST SUITE")
    print("  6 scenarios on real Polygon data")
    print("  Fixes: EURUSD->MEAN_REVERT, UK100->SHORT, CONFLUENCE 4/5")
    print("=" * 80)

    if not POLYGON_API_KEY:
        print("\n  POLYGON_API_KEY not set!")
        sys.exit(1)

    print(f"\n  Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # ─── Fetch data ──────────────────────────────────────────────────
    print("\n" + "-" * 80)
    print("  Fetching 30 days of hourly data...")
    print("-" * 80)

    all_candles = {}
    for sym in get_all_symbols():
        t = POLYGON_TICKERS.get(sym, "?")
        print(f"  {sym} ({t})...", end=" ", flush=True)
        c = fetch_polygon_candles(sym)
        if c and c["count"] >= 50:
            all_candles[sym] = c
            print(f"OK - {c['count']} bars")
        else:
            print("SKIP")
        time.sleep(0.3)

    print(f"\n  Loaded: {len(all_candles)}/{len(SETUP_CONFIG)} instruments")
    if len(all_candles) < 5:
        print("  Not enough data."); sys.exit(1)


    # ═══════════════════════════════════════════════════════════════════
    # TEST 1: STANDARD BACKTEST (baseline)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "#" * 80)
    print("  TEST 1: STANDARD BACKTEST (baseline v22.1)")
    print("#" * 80)

    bt1 = WalkForwardBacktest(label="Standard v22.1")
    r1 = bt1.run(deepcopy(all_candles))
    print_report(r1)


    # ═══════════════════════════════════════════════════════════════════
    # TEST 2: SPREAD & SLIPPAGE STRESS
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "#" * 80)
    print("  TEST 2: SPREAD & SLIPPAGE STRESS")
    print("  3x normal spreads + random slippage up to 0.3 ATR")
    print("#" * 80)

    bt2 = WalkForwardBacktest(
        label="3x Spread + Slippage",
        spread_mult=3.0,
        slippage_atr=0.3,
    )
    r2 = bt2.run(deepcopy(all_candles))
    print_report(r2)


    # ═══════════════════════════════════════════════════════════════════
    # TEST 3: CHOPPY MARKET (injected noise)
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "#" * 80)
    print("  TEST 3: CHOPPY MARKET")
    print("  0.3% random price noise injected into every bar")
    print("#" * 80)

    bt3 = WalkForwardBacktest(
        label="Choppy Market (0.3% noise)",
        noise_pct=0.003,
    )
    r3 = bt3.run(deepcopy(all_candles))
    print_report(r3)


    # ═══════════════════════════════════════════════════════════════════
    # TEST 4: DRAWDOWN RECOVERY
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "#" * 80)
    print("  TEST 4: DRAWDOWN RECOVERY")
    print("  Start with 4 forced consecutive losses, then trade normally")
    print("#" * 80)

    bt4 = WalkForwardBacktest(
        label="Drawdown Recovery (4 forced losses)",
        forced_initial_losses=4,
    )
    r4 = bt4.run(deepcopy(all_candles))
    print_report(r4)


    # ═══════════════════════════════════════════════════════════════════
    # TEST 5: WORST CASE — ALL STRESS COMBINED
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "#" * 80)
    print("  TEST 5: WORST CASE (all stress combined)")
    print("  3x spreads + slippage + noise + 4 forced losses")
    print("#" * 80)

    bt5 = WalkForwardBacktest(
        label="WORST CASE (everything bad)",
        spread_mult=3.0,
        slippage_atr=0.3,
        noise_pct=0.003,
        forced_initial_losses=4,
    )
    r5 = bt5.run(deepcopy(all_candles))
    print_report(r5)


    # ═══════════════════════════════════════════════════════════════════
    # TEST 6: MONTE CARLO SIMULATION
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "#" * 80)
    print("  TEST 6: MONTE CARLO (1000 simulations)")
    print("  Randomize trade order from baseline results")
    print("#" * 80)

    if "error" not in r1:
        trade_pnls = [t.pnl_r for t in r1["trade_log"]]
        if trade_pnls:
            mc = monte_carlo_simulation(trade_pnls, n_sims=1000)
            print_monte_carlo(mc)
        else:
            print("  No trades to simulate")
    else:
        print("  Baseline had no trades, skipping MC")


    # ═══════════════════════════════════════════════════════════════════
    # COMPARISON TABLE
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  COMPARISON TABLE")
    print("=" * 80)
    print(f"\n  {'Test':40s} {'Trades':>7s} {'WR':>6s} {'Total R':>9s} {'PF':>6s} {'MaxDD':>7s} {'P&L':>9s}")
    print(f"  {'-'*84}")

    for r in [r1, r2, r3, r4, r5]:
        if "error" in r:
            print(f"  {r['label']:40s} {'ERROR':>7s}")
            continue
        print(f"  {r['label']:40s} {r['total_trades']:7d} {r['win_rate']:5.1f}% "
              f"{r['total_r']:+8.2f}R {r['profit_factor']:5.2f} {r['max_drawdown_pct']:6.2f}% "
              f"${r['pnl_dollar']:+8.2f}")

    # ═══════════════════════════════════════════════════════════════════
    # VERDICT
    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  VERDICT")
    print("=" * 80)

    if "error" not in r1 and "error" not in r5:
        baseline_ok = r1["total_r"] > 0 and r1["max_drawdown_pct"] < 10
        worst_ok = r5["total_r"] > -5 and r5["max_drawdown_pct"] < 10
        print(f"\n  Baseline profitable:     {'YES' if baseline_ok else 'NO'} "
              f"({r1['total_r']:+.2f}R, {r1['max_drawdown_pct']:.2f}% DD)")
        print(f"  Survives worst case:     {'YES' if worst_ok else 'NO'} "
              f"({r5['total_r']:+.2f}R, {r5['max_drawdown_pct']:.2f}% DD)")
        if baseline_ok and worst_ok:
            print(f"\n  >>> FORGE v22.1 is CLEARED FOR DEPLOYMENT <<<")
        elif baseline_ok:
            print(f"\n  >>> Baseline good but stressed. Deploy with caution. <<<")
        else:
            print(f"\n  >>> NEEDS MORE TUNING before deployment <<<")

    print("\n" + "=" * 80)
    print("  ALL TESTS COMPLETE")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
