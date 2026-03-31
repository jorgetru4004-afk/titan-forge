"""
FORGE V21 -- HISTORICAL REPLAY (FIXED)
========================================
Feeds real Polygon M1 candles through v21 signal generators and
Bayesian engine. Shows what FORGE would have actually done.

FIXES from previous version:
  - Picks ONE best signal per candle (highest posterior), not all
  - 25 trade daily cap (realistic for live FORGE)
  - 30-candle cooldown per setup (matches real 180s cooldown)
  - EXPIRED with positive P&L counts as WIN
  - No new trades in last 30 min of RTH
  - Proper lot sizing from conviction level

Usage:
  set PYTHONIOENCODING=utf-8
  set POLYGON_API_KEY=your_key
  python forge_replay.py

Jorge Trujillo -- Founder | Claude -- AI Architect | March 2026
"""

import json, math, os, random, ssl, urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from datetime import time as dtime

random.seed(42)
POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
QQQ_TO_NQ = 41.2
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

LIMIT_SETUPS = {"VWAP-01","VWAP-02","LVL-01","LVL-02","IB-02","VOL-05","VOL-06"}

SETUP_CONFIG = {
    "ORD-02": {"wr": 0.52}, "IB-01": {"wr": 0.53}, "OD-01": {"wr": 0.51},
    "GAP-02": {"wr": 0.50}, "ICT-01": {"wr": 0.50}, "ICT-02": {"wr": 0.48},
    "ICT-03": {"wr": 0.50}, "VOL-03": {"wr": 0.50}, "VOL-05": {"wr": 0.52},
    "VOL-06": {"wr": 0.50}, "VWAP-01": {"wr": 0.52}, "VWAP-02": {"wr": 0.50},
    "VWAP-03": {"wr": 0.51}, "LVL-01": {"wr": 0.53}, "LVL-02": {"wr": 0.51},
    "IB-02": {"wr": 0.52},
}

REGIME_MULT = {
    "ORD-02": {"TREND":1.20,"CHOP":0.70,"NORMAL":1.0},
    "IB-01": {"TREND":1.20,"CHOP":0.70,"NORMAL":1.0},
    "OD-01": {"TREND":1.25,"CHOP":0.60,"NORMAL":1.0},
    "GAP-02": {"TREND":1.15,"CHOP":0.70,"NORMAL":1.0},
    "VOL-03": {"TREND":1.25,"CHOP":0.65,"NORMAL":1.0},
    "VWAP-03": {"TREND":1.15,"CHOP":0.80,"NORMAL":1.0},
    "VOL-05": {"TREND":0.60,"CHOP":1.25,"NORMAL":1.0},
    "VOL-06": {"TREND":0.70,"CHOP":1.20,"NORMAL":1.0},
    "IB-02": {"TREND":0.30,"CHOP":1.20,"NORMAL":0.85},
    "LVL-02": {"TREND":0.80,"CHOP":1.15,"NORMAL":1.0},
    "ICT-01": {"TREND":1.10,"CHOP":0.85,"NORMAL":1.0},
    "ICT-02": {"TREND":1.05,"CHOP":0.85,"NORMAL":1.0},
    "ICT-03": {"TREND":1.05,"CHOP":0.85,"NORMAL":1.0},
    "VWAP-01": {"TREND":0.80,"CHOP":1.15,"NORMAL":1.0},
    "VWAP-02": {"TREND":0.80,"CHOP":1.15,"NORMAL":1.0},
    "LVL-01": {"TREND":0.90,"CHOP":1.10,"NORMAL":1.0},
}

@dataclass
class Candle:
    timestamp: float; open: float; high: float; low: float; close: float
    volume: float = 0.0
    @property
    def dt(self): return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

def fetch_polygon_history(ticker="QQQ", days=10):
    if not POLYGON_KEY:
        print("[ERROR] No POLYGON_API_KEY set"); return []
    all_c = []; end = date.today(); cur = end - timedelta(days=days)
    while cur < end:
        ce = min(cur + timedelta(days=5), end)
        url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/"
               f"{cur.isoformat()}/{ce.isoformat()}?adjusted=true&sort=asc&limit=50000"
               f"&apiKey={POLYGON_KEY}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NEXUS/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                data = json.loads(resp.read().decode())
                for b in data.get("results", []):
                    all_c.append(Candle(b["t"]/1000.0, b["o"]*QQQ_TO_NQ, b["h"]*QQQ_TO_NQ,
                                        b["l"]*QQQ_TO_NQ, b["c"]*QQQ_TO_NQ, b.get("v",0)))
        except Exception as e:
            print(f"[POLYGON] {cur}-{ce}: {e}")
        cur = ce + timedelta(days=1)
    print(f"[DATA] {len(all_c)} M1 candles loaded")
    return all_c

def utc_to_et(dt_utc):
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo("America/New_York"))
    except:
        return dt_utc - timedelta(hours=4)

def get_session(et_time):
    if dtime(9,30) <= et_time < dtime(16,0): return "RTH"
    if dtime(17,0) <= et_time < dtime(18,0): return "BREAK"
    return "OTHER"

class ReplayTracker:
    def __init__(self):
        self.price_history = []; self.close_prices = []; self.open_price = 0
        self.session_high = 0; self.session_low = float("inf")
        self.orb_high = 0; self.orb_low = float("inf"); self.orb_locked = False
        self.ib_high = 0; self.ib_low = float("inf"); self.ib_locked = False
        self.ib_direction = None; self.vwap = 0; self._vwap_vol = 0; self._vwap_pv = 0

    def update(self, candle, et_time, session):
        self.price_history.append(candle.close)
        self.close_prices.append(candle.close)
        if len(self.price_history) > 120: self.price_history = self.price_history[-120:]
        if len(self.close_prices) > 20: self.close_prices = self.close_prices[-20:]
        if session == "RTH":
            if self.open_price == 0: self.open_price = candle.open
            self.session_high = max(self.session_high, candle.high)
            self.session_low = min(self.session_low, candle.low)
            self._vwap_vol += candle.volume + 1
            self._vwap_pv += candle.close * (candle.volume + 1)
            self.vwap = self._vwap_pv / self._vwap_vol if self._vwap_vol > 0 else candle.close
            mins = (et_time.hour - 9) * 60 + et_time.minute - 30
            if mins <= 5 and not self.orb_locked:
                self.orb_high = max(self.orb_high, candle.high)
                self.orb_low = min(self.orb_low, candle.low)
            elif mins > 5: self.orb_locked = True
            if mins <= 30 and not self.ib_locked:
                self.ib_high = max(self.ib_high, candle.high)
                self.ib_low = min(self.ib_low, candle.low)
            elif mins > 30 and not self.ib_locked:
                self.ib_locked = True
                mid = (self.ib_high + self.ib_low) / 2
                self.ib_direction = "long" if candle.close > mid else "short"

class ReplayContext:
    def __init__(self):
        self.regime = "NORMAL"; self.regime_bias = "neutral"; self.atr_consumed_pct = 0.0

def generate_signals(price, tracker, ctx, atr, et_time, pdh, pdl, prev_close):
    signals = []; vwap = tracker.vwap or tracker.open_price or price
    sd = max(atr * 0.3, 15)
    def _add(sid, d, sl, tp, reason):
        rm = REGIME_MULT.get(sid, {}).get(ctx.regime, 1.0)
        if rm <= 0.0: return
        signals.append({"setup_id":sid,"direction":d,"entry":round(price,2),
                        "sl":round(sl,2),"tp":round(tp,2),"reason":reason,"regime_mult":rm,
                        "order_type":"LIMIT" if sid in LIMIT_SETUPS else "MARKET"})

    if tracker.orb_locked:
        if price > tracker.orb_high + 2:
            _add("ORD-02","long",price-sd,price+sd*2,"ORB long")
        elif tracker.orb_low < float("inf") and price < tracker.orb_low - 2:
            _add("ORD-02","short",price+sd,price-sd*2,"ORB short")
    if tracker.ib_locked:
        if price > tracker.ib_high + 2:
            ib_r = max(tracker.ib_high - tracker.ib_low, 20) if tracker.ib_low < float("inf") else 40
            _add("IB-01","long",price-ib_r*0.5,price+ib_r,"IB break long")
        elif tracker.ib_low < float("inf") and price < tracker.ib_low - 2:
            ib_r = max(tracker.ib_high - tracker.ib_low, 20)
            _add("IB-01","short",price+ib_r*0.5,price-ib_r,"IB break short")
    if tracker.ib_locked:
        if abs(price - tracker.ib_high) < 3:
            _add("IB-02","short",price+15,price-25,"IB scalp short")
        elif tracker.ib_low < float("inf") and abs(price - tracker.ib_low) < 3:
            _add("IB-02","long",price-15,price+25,"IB scalp long")
    if tracker.open_price > 0:
        mp = (price - tracker.open_price) / tracker.open_price
        if abs(mp) >= 0.002:
            d = "long" if mp > 0 else "short"
            sld = abs(price - tracker.open_price) + 5
            _add("OD-01",d,price-sld if d=="long" else price+sld,
                 price+sld*1.5 if d=="long" else price-sld*1.5,f"OD {d}")
    if prev_close > 0 and tracker.open_price > 0:
        gp = (tracker.open_price - prev_close) / prev_close
        if abs(gp) >= 0.003:
            d = "long" if gp > 0 else "short"
            _add("GAP-02",d,price-sd if d=="long" else price+sd,
                 price+sd*2 if d=="long" else price-sd*2,f"Gap {d}")
    if vwap > 0 and 0 < price - vwap < 5:
        _add("VWAP-01","long",vwap-15,price+max(20,atr*0.3),"VWAP bounce")
    if vwap > 0 and 0 < vwap - price < 5:
        _add("VWAP-02","short",vwap+15,price-max(20,atr*0.3),"VWAP reject")
    if vwap > 0 and price > vwap * 1.003:
        _add("VOL-03","long",price-sd,price+sd*2,"Trend long")
    elif vwap > 0 and price < vwap * 0.997:
        _add("VOL-03","short",price+sd,price-sd*2,"Trend short")
    if vwap > 0 and atr > 0 and abs(price - vwap) / atr >= 0.5:
        d = "short" if price > vwap else "long"
        _add("VOL-05",d,price-sd if d=="long" else price+sd,vwap,f"MeanRev {d}")
    if pdh > 0 and abs(price - pdh) < 10:
        d = "short" if price < pdh else "long"
        _add("LVL-01",d,pdh+20 if d=="short" else pdh-5,
             price-40 if d=="short" else price+40,f"PDH {d}")
    if pdl > 0 and abs(price - pdl) < 10:
        d = "long" if price > pdl else "short"
        _add("LVL-01",d,pdl-20 if d=="long" else pdl+5,
             price+40 if d=="long" else price-40,f"PDL {d}")
    nearest = round(price / 100) * 100
    if abs(price - nearest) < 5:
        d = "short" if price > nearest else "long"
        _add("LVL-02",d,price+15 if d=="short" else price-15,
             price-25 if d=="short" else price+25,f"Round {nearest:.0f}")
    if vwap > 0 and price > vwap:
        _add("VWAP-03","long",vwap-5,price+atr*0.4,"VWAP momentum")
    if tracker.open_price > 0 and abs(price - tracker.open_price) >= atr * 0.3:
        d = "short" if price > tracker.open_price else "long"
        _add("VOL-06",d,price+sd if d=="short" else price-sd,
             price-sd*1.6 if d=="short" else price+sd*1.6,f"Noon {d}")
    if vwap > 0 and len(tracker.price_history) >= 10:
        roc = (tracker.price_history[-1] - tracker.price_history[-5]) / tracker.price_history[-5] if tracker.price_history[-5] > 0 else 0
        if abs(roc) > 0.001 and price > vwap:
            _add("ICT-01","long",price-sd,price+sd*2,"VWAP+momentum")
    if len(tracker.close_prices) >= 4:
        c1,c2,c3,c4 = tracker.close_prices[-4:]
        if c1<c2 and c3>c2 and c4>c3: _add("ICT-02","long",price-sd,price+sd*1.8,"FVG bull")
        elif c1>c2 and c3<c2 and c4<c3: _add("ICT-02","short",price+sd,price-sd*1.8,"FVG bear")
    if pdl > 0 and tracker.session_low < pdl and price > pdl:
        _add("ICT-03","long",tracker.session_low,price+(price-tracker.session_low)*2,"Swept PDL")
    if pdh > 0 and tracker.session_high > pdh and price < pdh:
        _add("ICT-03","short",tracker.session_high,price-(tracker.session_high-price)*2,"Swept PDH")
    return signals

def compute_conviction(signal, tracker, ctx, atr):
    prior = SETUP_CONFIG.get(signal["setup_id"],{}).get("wr",0.50)
    d = signal["direction"]; price = signal["entry"]
    vwap = tracker.vwap or tracker.open_price or price
    odds = prior / (1.0 - prior); lr = 1.0; dc = 0; dt = 0

    dt += 1
    if ctx.regime == "TREND" and d == ctx.regime_bias: lr *= 2.5; dc += 1
    elif ctx.regime_bias != "neutral" and d != ctx.regime_bias and ctx.regime == "TREND": lr *= 0.4

    dt += 1
    if vwap > 0:
        if (d=="long" and price>vwap) or (d=="short" and price<vwap): lr *= 1.5; dc += 1
        else: lr *= 0.67

    dt += 1
    if len(tracker.price_history) >= 10:
        roc = (tracker.price_history[-1]-tracker.price_history[-5])/tracker.price_history[-5] if tracker.price_history[-5]>0 else 0
        if (d=="long" and roc>0.001) or (d=="short" and roc<-0.001): lr *= 1.8; dc += 1
        elif (d=="long" and roc<-0.002) or (d=="short" and roc>0.002): lr *= 0.56

    dt += 1
    if ctx.atr_consumed_pct < 0.40: lr *= 1.3; dc += 1
    elif ctx.atr_consumed_pct > 0.80: lr *= 0.50

    if tracker.ib_locked and tracker.ib_direction:
        dt += 1
        if tracker.ib_direction == d: lr *= 1.5; dc += 1
        else: lr *= 0.67

    if len(tracker.price_history) >= 30:
        dt += 1
        seg = tracker.price_history[-30:]
        highs = [seg[i] for i in range(1,len(seg)-1) if seg[i]>seg[i-1] and seg[i]>seg[i+1]]
        lows = [seg[i] for i in range(1,len(seg)-1) if seg[i]<seg[i-1] and seg[i]<seg[i+1]]
        if len(highs)>=2 and len(lows)>=2:
            if d=="long" and highs[-1]>highs[-2] and lows[-1]>lows[-2]: lr *= 1.7; dc += 1
            elif d=="short" and highs[-1]<highs[-2] and lows[-1]<lows[-2]: lr *= 1.7; dc += 1

    lr *= signal.get("regime_mult", 1.0)
    post = max(0.05, min(0.98, (odds*lr) / (1.0 + odds*lr)))

    if post >= 0.88 and dc >= 5: lv = "ELITE"
    elif post >= 0.78 and dc >= 4: lv = "HIGH"
    elif post >= 0.65 and dc >= 3: lv = "STANDARD"
    elif post >= 0.52: lv = "REDUCED"
    elif post >= 0.40 and dc >= 2: lv = "SCALP"
    else: lv = "REJECT"
    return post, lv, dc, dt

def compute_lot_size(post, level):
    cv = {"ELITE":1.5,"HIGH":1.2,"STANDARD":1.0,"REDUCED":0.7,"SCALP":0.5}.get(level,0.3)
    sm = {"ELITE":1.0,"HIGH":0.75,"STANDARD":0.50,"REDUCED":0.30,"SCALP":0.25}.get(level,0.25)
    lot = 0.20 * cv * sm
    return min(max(0.10, round(round(lot/0.10)*0.10, 2)), 2.0)

def simulate_trade(signal, lot_size, future_candles, atr):
    entry=signal["entry"]; sl=signal["sl"]; tp=signal["tp"]; d=signal["direction"]
    risk = abs(entry - sl)
    if risk <= 0: return None
    spread=3.0; slip=random.uniform(0.5,2.0)
    ae = entry+spread/2+slip if d=="long" else entry-spread/2-slip
    mx = min(60, len(future_candles))
    for i in range(mx):
        c = future_candles[i]
        if d == "long":
            if c.low <= sl:
                pnl = (sl - ae) * lot_size * 20
                return {"outcome":"LOSS","pnl":round(pnl,2),"r_mult":-1.0,"bars":i+1}
            if c.high >= tp:
                pnl = (tp - ae) * lot_size * 20
                return {"outcome":"WIN","pnl":round(pnl,2),"r_mult":round(pnl/(risk*lot_size*20),2),"bars":i+1}
        else:
            if c.high >= sl:
                pnl = (ae - sl) * lot_size * 20
                return {"outcome":"LOSS","pnl":round(pnl,2),"r_mult":-1.0,"bars":i+1}
            if c.low <= tp:
                pnl = (ae - tp) * lot_size * 20
                return {"outcome":"WIN","pnl":round(pnl,2),"r_mult":round(pnl/(risk*lot_size*20),2),"bars":i+1}
    ep = future_candles[mx-1].close if future_candles else entry
    pnl = ((ep-ae) if d=="long" else (ae-ep)) * lot_size * 20
    return {"outcome":"WIN" if pnl>0 else "LOSS","pnl":round(pnl,2),
            "r_mult":round(pnl/(risk*lot_size*20),2) if risk>0 else 0,"bars":mx}

def replay(candles):
    days = defaultdict(list)
    for c in candles:
        et = utc_to_et(c.dt)
        if et.weekday() < 5: days[et.date()].append((c, et))
    print(f"\n[REPLAY] {len(days)} trading days, {len(candles)} candles")
    print("=" * 90)

    all_trades = []; pdh=0; pdl=0; pdc=0

    for dd in sorted(days.keys()):
        dc = days[dd]; t = ReplayTracker(); ctx = ReplayContext()
        dt_list = []; cd = {}; dtc = 0; dpnl = 0.0

        print(f"\n{'='*90}")
        print(f"  {dd} ({dd.strftime('%A')}) | PDH:{pdh:.0f} PDL:{pdl:.0f}")
        print(f"{'='*90}")

        for idx, (candle, et_dt) in enumerate(dc):
            et_time = et_dt.time(); sess = get_session(et_time)
            if sess == "BREAK": continue
            t.update(candle, et_time, sess); price = candle.close

            if t.ib_locked:
                ib_r = t.ib_high - t.ib_low if t.ib_low < float("inf") else 0
                atr = max(100, t.session_high - t.session_low) if t.session_low < float("inf") else 150
                if ib_r >= atr * 0.6: ctx.regime = "TREND"
                elif ib_r <= atr * 0.3 and ib_r > 0: ctx.regime = "CHOP"
                else: ctx.regime = "NORMAL"
            else: atr = 150
            ctx.regime_bias = t.ib_direction or "neutral"
            if t.session_low < float("inf") and atr > 0:
                ctx.atr_consumed_pct = (t.session_high - t.session_low) / atr

            if sess != "RTH": continue
            if et_time >= dtime(15,30): continue
            if dtc >= 25: continue

            sigs = generate_signals(price,t,ctx,atr,et_time,pdh,pdl,pdc)
            if not sigs: continue

            elig = [s for s in sigs if s["setup_id"] not in cd or idx - cd[s["setup_id"]] >= 30]
            if not elig: continue

            best = None; bp = 0; bl = "REJECT"; bdc = 0; bdt = 0
            for s in elig:
                p, lv, dc2, dt2 = compute_conviction(s, t, ctx, atr)
                if lv == "REJECT": continue
                if p > bp: best=s; bp=p; bl=lv; bdc=dc2; bdt=dt2
            if best is None: continue

            lot = compute_lot_size(bp, bl)
            future = [c for c,_ in dc[idx+1:idx+61]]
            if not future: continue
            result = simulate_trade(best, lot, future, atr)
            if not result: continue

            cd[best["setup_id"]] = idx; dtc += 1; dpnl += result["pnl"]
            icon = "+" if result["outcome"]=="WIN" else "X"
            print(f"  {icon} {et_time.strftime('%H:%M')} {best['setup_id']:<10s} "
                  f"{best['direction'].upper():<5s} @{best['entry']:.0f} "
                  f"{bl:<8s} {bp:.0%} ({bdc}/{bdt}) "
                  f"{lot:.2f}L [{best['order_type']}] "
                  f"{result['outcome']:<4s} ${result['pnl']:+.0f} ({result['r_mult']:+.1f}R) "
                  f"{best['reason']}")
            rec = {"date":str(dd),"time":et_time.strftime("%H:%M"),
                   "setup_id":best["setup_id"],"direction":best["direction"],
                   "entry":best["entry"],"conviction":bp,"level":bl,"lot_size":lot,
                   "order_type":best["order_type"],"regime":ctx.regime,
                   "outcome":result["outcome"],"pnl":result["pnl"],"r_mult":result["r_mult"]}
            dt_list.append(rec); all_trades.append(rec)

        w = sum(1 for x in dt_list if x["outcome"]=="WIN")
        l = sum(1 for x in dt_list if x["outcome"]=="LOSS")
        wr = w/len(dt_list)*100 if dt_list else 0
        print(f"\n  DAY: {len(dt_list)} trades | {w}W {l}L | WR: {wr:.0f}% | P&L: ${dpnl:+,.0f}")

        if t.session_high > 0: pdh = t.session_high
        if t.session_low < float("inf"): pdl = t.session_low
        if t.price_history: pdc = t.price_history[-1]

    # SUMMARY
    print(f"\n{'='*90}")
    print(f"  FORGE V21 REPLAY -- FINAL SUMMARY")
    print(f"{'='*90}")
    tt = len(all_trades)
    if tt == 0: print("  No trades."); return
    tw = sum(1 for x in all_trades if x["outcome"]=="WIN")
    tl = sum(1 for x in all_trades if x["outcome"]=="LOSS")
    tp2 = sum(x["pnl"] for x in all_trades)
    td = len(days); da = tp2/td if td>0 else 0
    print(f"  Days: {td}")
    print(f"  Total trades: {tt}")
    print(f"  Wins: {tw} | Losses: {tl} | WR: {tw/tt*100:.1f}%")
    print(f"  Total P&L: ${tp2:+,.0f}")
    print(f"  Daily avg: ${da:+,.0f}")
    print(f"  Monthly est: ${da*21:+,.0f}")
    wps = [x["pnl"] for x in all_trades if x["outcome"]=="WIN"]
    lps = [x["pnl"] for x in all_trades if x["outcome"]=="LOSS"]
    if wps: print(f"  Avg WIN: ${sum(wps)/len(wps):+,.0f}")
    if lps: print(f"  Avg LOSS: ${sum(lps)/len(lps):+,.0f}")

    print(f"\n  {'Setup':<12s} {'N':>4s} {'WR':>6s} {'AvgPnL':>8s} {'TotalPnL':>10s} {'Order':>7s}")
    print(f"  {'-'*50}")
    ss = defaultdict(lambda:{"n":0,"w":0,"pnl":0,"t":""})
    for x in all_trades:
        s=ss[x["setup_id"]]; s["n"]+=1
        if x["outcome"]=="WIN": s["w"]+=1
        s["pnl"]+=x["pnl"]; s["t"]=x["order_type"]
    for sid,s in sorted(ss.items(),key=lambda x:x[1]["pnl"],reverse=True):
        wr=s["w"]/s["n"]*100 if s["n"]>0 else 0
        av=s["pnl"]/s["n"] if s["n"]>0 else 0
        print(f"  {sid:<12s} {s['n']:>4d} {wr:>5.1f}% ${av:>+7.0f} ${s['pnl']:>+9,.0f} {s['t']:>7s}")

    print(f"\n  Regime breakdown:")
    for r in ["TREND","NORMAL","CHOP"]:
        rt=[x for x in all_trades if x["regime"]==r]
        if rt:
            rw=sum(1 for x in rt if x["outcome"]=="WIN")
            rp=sum(x["pnl"] for x in rt)
            print(f"    {r:<10s} {len(rt):>4d}t | WR {rw/len(rt)*100:.1f}% | ${rp:+,.0f}")

    print(f"\n  Conviction accuracy:")
    for lv in ["ELITE","HIGH","STANDARD","REDUCED","SCALP"]:
        lt=[x for x in all_trades if x["level"]==lv]
        if lt:
            lw=sum(1 for x in lt if x["outcome"]=="WIN")
            print(f"    {lv:<10s} {len(lt):>4d}t | WR {lw/len(lt)*100:.1f}%")
    print(f"\n{'='*90}")

def main():
    print("NEXUS CAPITAL -- FORGE V21 HISTORICAL REPLAY (FIXED)")
    print("Real Polygon candles -> V21 signals -> Bayesian conviction")
    print("ONE best trade per candle | 25/day cap | 30min cooldowns")
    print("")
    candles = fetch_polygon_history("QQQ", days=10)
    if not candles:
        print("No candle data. Set POLYGON_API_KEY and try again.")
        return
    replay(candles)

if __name__ == "__main__":
    main()
