"""
FORGE V22 — CLEAN REBUILD REPLAY
===================================
5 setups. Signal freshness. One trade at a time. 3 independent dimensions.
Built from what the data proved works.

set PYTHONIOENCODING=utf-8
set POLYGON_API_KEY=your_key
python forge_v22_replay.py
"""

import json, os, random, ssl, urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from datetime import time as dtime

random.seed(42)
POLYGON_KEY = os.environ.get("POLYGON_API_KEY", "")
QQQ_TO_NQ = 41.2
_SSL = ssl.create_default_context()
_SSL.check_hostname = False
_SSL.verify_mode = ssl.CERT_NONE

@dataclass
class Candle:
    timestamp: float; open: float; high: float; low: float; close: float
    volume: float = 0.0
    @property
    def dt(self): return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

def fetch(ticker="QQQ", days=10):
    if not POLYGON_KEY: print("No API key"); return []
    cs = []; end = date.today(); cur = end - timedelta(days=days)
    while cur < end:
        ce = min(cur + timedelta(days=5), end)
        url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/minute/"
               f"{cur}/{ce}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_KEY}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NEXUS/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                data = json.loads(r.read().decode())
                for b in data.get("results", []):
                    cs.append(Candle(b["t"]/1000, b["o"]*QQQ_TO_NQ, b["h"]*QQQ_TO_NQ,
                                     b["l"]*QQQ_TO_NQ, b["c"]*QQQ_TO_NQ, b.get("v",0)))
        except Exception as e:
            print(f"[DATA] {cur}-{ce}: {e}")
        cur = ce + timedelta(days=1)
    print(f"[DATA] {len(cs)} M1 candles")
    return cs

def utc_to_et(dt_utc):
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo("America/New_York"))
    except:
        return dt_utc - timedelta(hours=4)


class Tracker:
    def __init__(self):
        self.prices = []
        self.candles = []
        self.volumes = []
        self.open_price = 0
        self.session_high = 0
        self.session_low = float("inf")
        self.orb_high = 0; self.orb_low = float("inf"); self.orb_locked = False
        self.ib_high = 0; self.ib_low = float("inf"); self.ib_locked = False
        self.ib_direction = None
        self.vwap = 0; self._vv = 0; self._vpv = 0
        # Signal freshness tracking
        self._ib_break_long_fired = False
        self._ib_break_short_fired = False
        self._was_above_vwap = False
        self._was_below_vwap = False
        self._gap_traded = False

    def update(self, candle, et_time):
        self.prices.append(candle.close)
        self.candles.append(candle)
        self.volumes.append(candle.volume)
        if len(self.prices) > 200: self.prices = self.prices[-200:]
        if len(self.candles) > 200: self.candles = self.candles[-200:]
        if len(self.volumes) > 200: self.volumes = self.volumes[-200:]

        if self.open_price == 0: self.open_price = candle.open
        self.session_high = max(self.session_high, candle.high)
        self.session_low = min(self.session_low, candle.low)
        self._vv += candle.volume + 1
        self._vpv += candle.close * (candle.volume + 1)
        self.vwap = self._vpv / self._vv if self._vv > 0 else candle.close

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

    @property
    def atr(self):
        if self.session_low < float("inf") and self.session_high > 0:
            return max(80, self.session_high - self.session_low)
        return 150

    @property
    def regime(self):
        if not self.ib_locked: return "NORMAL"
        ib_r = self.ib_high - self.ib_low if self.ib_low < float("inf") else 0
        if ib_r <= 0: return "NORMAL"
        if ib_r >= self.atr * 0.6: return "TREND"
        if ib_r <= self.atr * 0.3: return "CHOP"
        return "NORMAL"

    def vol_trend(self, lookback=10):
        """Is volume increasing or decreasing over last N candles?"""
        if len(self.volumes) < lookback: return "neutral"
        recent = self.volumes[-lookback:]
        first_half = sum(recent[:lookback//2]) / max(1, lookback//2)
        second_half = sum(recent[lookback//2:]) / max(1, lookback//2)
        if second_half > first_half * 1.3: return "increasing"
        if second_half < first_half * 0.7: return "decreasing"
        return "neutral"

    def consecutive_direction(self):
        """Count consecutive same-direction candles at the end."""
        if len(self.candles) < 2: return 0, "none"
        count = 1
        last_dir = "up" if self.candles[-1].close > self.candles[-1].open else "down"
        for i in range(len(self.candles)-2, max(0, len(self.candles)-10), -1):
            c = self.candles[i]
            d = "up" if c.close > c.open else "down"
            if d == last_dir: count += 1
            else: break
        return count, last_dir


def generate_signal(tracker, price, et_time, pdh, pdl, prev_close, idx):
    """
    Generate AT MOST ONE signal. Picks the freshest, highest-priority setup.
    Signal freshness is enforced — each signal fires ONCE per condition.
    """
    atr = tracker.atr
    sd = max(atr * 0.3, 15)
    vwap = tracker.vwap or tracker.open_price or price
    mins_since_open = (et_time.hour - 9) * 60 + et_time.minute - 30

    # ── SETUP 1: FRESH IB BREAKOUT ──────────────────────────────
    # Only fires within 3 candles of the actual break. Never again.
    if tracker.ib_locked and mins_since_open >= 31:
        ib_r = max(tracker.ib_high - tracker.ib_low, 20) if tracker.ib_low < float("inf") else 40

        if price > tracker.ib_high + 2 and not tracker._ib_break_long_fired:
            # Check freshness: was price below IB high 3 candles ago?
            if len(tracker.prices) >= 4 and tracker.prices[-4] <= tracker.ib_high:
                tracker._ib_break_long_fired = True
                return {"id": "BREAK-01", "dir": "long", "entry": price,
                        "sl": round(price - ib_r * 0.5, 2), "tp": None,  # trail only
                        "reason": f"Fresh IB break long", "type": "breakout"}

        if tracker.ib_low < float("inf") and price < tracker.ib_low - 2 and not tracker._ib_break_short_fired:
            if len(tracker.prices) >= 4 and tracker.prices[-4] >= tracker.ib_low:
                tracker._ib_break_short_fired = True
                return {"id": "BREAK-01", "dir": "short", "entry": price,
                        "sl": round(price + ib_r * 0.5, 2), "tp": None,
                        "reason": f"Fresh IB break short", "type": "breakout"}

    # ── SETUP 2: MEAN REVERSION TO VWAP ─────────────────────────
    # Price extended 0.5+ ATR from VWAP. Fade back to VWAP.
    if vwap > 0 and atr > 0 and mins_since_open >= 30:
        dist = price - vwap
        dist_atr = abs(dist) / atr

        if dist_atr >= 0.5:
            if dist > 0:  # price above VWAP, short back to VWAP
                return {"id": "REVERT-01", "dir": "short", "entry": price,
                        "sl": round(price + sd, 2), "tp": round(vwap + sd * 0.3, 2),
                        "reason": f"Mean rev short ({dist_atr:.1f} ATR from VWAP)",
                        "type": "mean_reversion"}
            else:  # price below VWAP, long back to VWAP
                return {"id": "REVERT-01", "dir": "long", "entry": price,
                        "sl": round(price - sd, 2), "tp": round(vwap - sd * 0.3, 2),
                        "reason": f"Mean rev long ({dist_atr:.1f} ATR from VWAP)",
                        "type": "mean_reversion"}

    # ── SETUP 3: LEVEL BOUNCE (PDH/PDL/Round) ───────────────────
    # Price touches level AND shows rejection (wick > body)
    if len(tracker.candles) >= 2:
        c = tracker.candles[-1]
        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.close, c.open)
        lower_wick = min(c.close, c.open) - c.low
        has_rejection = False; level_name = ""; d = ""

        # PDH rejection
        if pdh > 0 and abs(c.high - pdh) < 10 and upper_wick > body * 1.5:
            has_rejection = True; level_name = f"PDH {pdh:.0f}"; d = "short"
        # PDL rejection
        elif pdl > 0 and abs(c.low - pdl) < 10 and lower_wick > body * 1.5:
            has_rejection = True; level_name = f"PDL {pdl:.0f}"; d = "long"
        # Round number rejection
        else:
            nearest = round(price / 100) * 100
            if abs(c.high - nearest) < 5 and upper_wick > body * 1.5:
                has_rejection = True; level_name = f"Round {nearest:.0f}"; d = "short"
            elif abs(c.low - nearest) < 5 and lower_wick > body * 1.5:
                has_rejection = True; level_name = f"Round {nearest:.0f}"; d = "long"

        if has_rejection:
            return {"id": "LEVEL-01", "dir": d, "entry": price,
                    "sl": round(price + 20 if d == "short" else price - 20, 2),
                    "tp": round(price - 35 if d == "short" else price + 35, 2),
                    "reason": f"Level bounce: {level_name}", "type": "level"}

    # ── SETUP 4: GAP FADE ───────────────────────────────────────
    # Gap from previous close. Trade ONCE at open direction.
    if prev_close > 0 and tracker.open_price > 0 and not tracker._gap_traded:
        gap_pct = (tracker.open_price - prev_close) / prev_close
        if abs(gap_pct) >= 0.003 and mins_since_open <= 10:
            tracker._gap_traded = True
            d = "short" if gap_pct > 0 else "long"  # FADE the gap
            return {"id": "GAP-01", "dir": d, "entry": price,
                    "sl": round(price + sd if d == "short" else price - sd, 2),
                    "tp": round(prev_close, 2),  # target: gap fill
                    "reason": f"Gap fade {gap_pct:.1%}", "type": "mean_reversion"}

    # ── SETUP 5: EXHAUSTION REVERSAL ─────────────────────────────
    # 3+ consecutive same-direction candles with declining volume
    consec, last_dir = tracker.consecutive_direction()
    vol_trend = tracker.vol_trend(6)

    if consec >= 3 and vol_trend == "decreasing":
        d = "short" if last_dir == "up" else "long"
        return {"id": "EXHAUST-01", "dir": d, "entry": price,
                "sl": round(price + sd * 0.8 if d == "short" else price - sd * 0.8, 2),
                "tp": round(price - sd * 1.2 if d == "short" else price + sd * 1.2, 2),
                "reason": f"Exhaustion {consec} bars {last_dir}, vol declining",
                "type": "mean_reversion"}

    return None


def evaluate_dims(signal, tracker):
    """3 truly independent dimensions."""
    dims = 0; total = 3

    # DIM 1: SIGNAL FRESHNESS — is this happening NOW?
    # For breakouts: check if price was on other side 3 candles ago
    # For mean reversion: check if extension is still growing (not already reversing)
    if signal["type"] == "breakout":
        if len(tracker.prices) >= 4:
            if signal["dir"] == "long" and tracker.prices[-4] < tracker.prices[-1]:
                dims += 1  # price is moving in breakout direction
            elif signal["dir"] == "short" and tracker.prices[-4] > tracker.prices[-1]:
                dims += 1
    elif signal["type"] == "mean_reversion":
        # For mean reversion, freshness = extension is at/near peak
        if len(tracker.prices) >= 3:
            vwap = tracker.vwap
            prev_dist = abs(tracker.prices[-3] - vwap)
            curr_dist = abs(tracker.prices[-1] - vwap)
            if curr_dist >= prev_dist * 0.95:  # still extended, hasn't started reverting
                dims += 1
    elif signal["type"] == "level":
        dims += 1  # level bounces are inherently fresh (rejection candle just formed)

    # DIM 2: VOLUME CONFIRMATION
    vol_trend = tracker.vol_trend(8)
    if signal["type"] == "breakout" and vol_trend == "increasing":
        dims += 1  # breakout on rising volume = real
    elif signal["type"] in ("mean_reversion", "level") and vol_trend == "decreasing":
        dims += 1  # mean reversion on declining volume = exhaustion confirmed
    elif vol_trend == "neutral":
        pass  # no info, no dim

    # DIM 3: CONTEXT ALIGNMENT
    regime = tracker.regime
    if signal["type"] == "breakout" and regime == "TREND":
        dims += 1  # breakout in trend day = aligned
    elif signal["type"] == "mean_reversion" and regime in ("CHOP", "NORMAL"):
        dims += 1  # mean reversion in range day = aligned
    elif signal["type"] == "level" and regime in ("CHOP", "NORMAL"):
        dims += 1  # level bounce in range day = aligned

    return dims, total


def compute_lot(dims):
    if dims >= 3: return 0.30
    if dims >= 2: return 0.20
    return 0.10


def simulate_trade(signal, lot, future_candles, atr):
    entry = signal["entry"]; sl = signal["sl"]; d = signal["dir"]
    tp = signal["tp"]
    risk = abs(entry - sl)
    if risk <= 0: return None

    # Spread + slippage
    spread = 3.0; slip = random.uniform(0.5, 1.5)
    ae = entry + spread/2 + slip if d == "long" else entry - spread/2 - slip

    # For breakouts with no fixed TP, use trailing stop
    is_trail = tp is None
    if is_trail:
        tp_check = float("inf") if d == "long" else 0  # no fixed TP
    else:
        tp_check = tp

    max_hold = min(45, len(future_candles))  # 45 min max hold
    best_price = ae
    trail_sl = sl

    for i in range(max_hold):
        c = future_candles[i]

        if d == "long":
            best_price = max(best_price, c.high)
            # Trailing: once at 1R profit, trail 1R behind
            if best_price - ae >= risk:
                trail_sl = max(trail_sl, best_price - risk)

            if c.low <= trail_sl:
                pnl = (trail_sl - ae) * lot * 20
                outcome = "WIN" if pnl > 0 else "LOSS"
                return {"outcome": outcome, "pnl": round(pnl, 2),
                        "r": round(pnl / (risk * lot * 20), 2) if risk > 0 else 0, "bars": i+1}
            if not is_trail and c.high >= tp_check:
                pnl = (tp_check - ae) * lot * 20
                return {"outcome": "WIN", "pnl": round(pnl, 2),
                        "r": round(pnl / (risk * lot * 20), 2), "bars": i+1}
        else:
            best_price = min(best_price, c.low)
            if ae - best_price >= risk:
                trail_sl = min(trail_sl, best_price + risk)

            if c.high >= trail_sl:
                pnl = (ae - trail_sl) * lot * 20
                outcome = "WIN" if pnl > 0 else "LOSS"
                return {"outcome": outcome, "pnl": round(pnl, 2),
                        "r": round(pnl / (risk * lot * 20), 2) if risk > 0 else 0, "bars": i+1}
            if not is_trail and c.low <= tp_check:
                pnl = (ae - tp_check) * lot * 20
                return {"outcome": "WIN", "pnl": round(pnl, 2),
                        "r": round(pnl / (risk * lot * 20), 2), "bars": i+1}

    # Time exit
    ep = future_candles[max_hold-1].close
    pnl = ((ep - ae) if d == "long" else (ae - ep)) * lot * 20
    return {"outcome": "WIN" if pnl > 0 else "LOSS", "pnl": round(pnl, 2),
            "r": round(pnl / (risk * lot * 20), 2) if risk > 0 else 0, "bars": max_hold}


def replay(candles):
    days = defaultdict(list)
    for c in candles:
        et = utc_to_et(c.dt)
        if et.weekday() < 5: days[et.date()].append((c, et))

    print(f"\n[REPLAY] {len(days)} trading days")
    print(f"[SYSTEM] 5 setups | Fresh signals only | 1 trade at a time | 3 independent dims")
    print("=" * 90)

    all_trades = []; pdh = 0; pdl = 0; pdc = 0

    for dd in sorted(days.keys()):
        dc = days[dd]; t = Tracker()
        day_trades = []; day_pnl = 0.0
        in_trade = False; trade_cooldown = 0
        last_trade_dir = None; last_trade_idx = -999

        print(f"\n{'='*90}")
        print(f"  {dd} ({dd.strftime('%A')}) | PDH:{pdh:.0f} PDL:{pdl:.0f}")
        print(f"{'='*90}")

        for idx, (candle, et_dt) in enumerate(dc):
            et_time = et_dt.time()
            if dtime(9,30) <= et_time < dtime(16,0):
                t.update(candle, et_time)
            else:
                continue

            price = candle.close

            # No trades after 3:15 PM (need time for exit)
            if et_time >= dtime(15, 15): continue

            # ONE TRADE AT A TIME — wait for cooldown after last trade
            if idx - last_trade_idx < 45:  # 45 min between trades
                continue

            # Generate signal (returns at most ONE)
            sig = generate_signal(t, price, et_time, pdh, pdl, pdc, idx)
            if sig is None: continue

            # Don't take same direction as last trade within 60 min
            if sig["dir"] == last_trade_dir and idx - last_trade_idx < 60:
                continue

            # Evaluate dimensions
            dims, total = evaluate_dims(sig, t)

            # Minimum 1 dim confirming to take the trade
            if dims == 0: continue

            lot = compute_lot(dims)

            # Simulate
            future = [c for c, _ in dc[idx+1:idx+46]]
            if len(future) < 5: continue

            result = simulate_trade(sig, lot, future, t.atr)
            if not result: continue

            last_trade_idx = idx
            last_trade_dir = sig["dir"]
            day_pnl += result["pnl"]

            icon = "+" if result["outcome"] == "WIN" else "X"
            print(f"  {icon} {et_time.strftime('%H:%M')} {sig['id']:<12s} "
                  f"{sig['dir'].upper():<5s} @{sig['entry']:.0f} "
                  f"dims:{dims}/{total} {lot:.2f}L "
                  f"{result['outcome']:<4s} ${result['pnl']:+.0f} ({result['r']:+.1f}R) "
                  f"{result['bars']}bars | {sig['reason']}")

            rec = {"date": str(dd), "time": et_time.strftime("%H:%M"),
                   "setup_id": sig["id"], "direction": sig["dir"],
                   "entry": sig["entry"], "dims": dims, "lot": lot,
                   "regime": t.regime, "outcome": result["outcome"],
                   "pnl": result["pnl"], "r": result["r"], "type": sig["type"]}
            day_trades.append(rec); all_trades.append(rec)

        w = sum(1 for x in day_trades if x["outcome"] == "WIN")
        l = sum(1 for x in day_trades if x["outcome"] == "LOSS")
        wr = w/len(day_trades)*100 if day_trades else 0
        print(f"\n  DAY: {len(day_trades)} trades | {w}W {l}L | WR: {wr:.0f}% | P&L: ${day_pnl:+,.0f}")

        if t.session_high > 0: pdh = t.session_high
        if t.session_low < float("inf"): pdl = t.session_low
        if t.prices: pdc = t.prices[-1]

    # SUMMARY
    print(f"\n{'='*90}")
    print(f"  FORGE V22 REPLAY -- CLEAN REBUILD RESULTS")
    print(f"{'='*90}")
    tt = len(all_trades)
    if tt == 0: print("  No trades."); return
    tw = sum(1 for x in all_trades if x["outcome"] == "WIN")
    tl = sum(1 for x in all_trades if x["outcome"] == "LOSS")
    tp2 = sum(x["pnl"] for x in all_trades)
    td = len(days); da = tp2/td if td > 0 else 0
    print(f"  Days: {td}")
    print(f"  Total trades: {tt}")
    print(f"  Wins: {tw} | Losses: {tl} | WR: {tw/tt*100:.1f}%")
    print(f"  Total P&L: ${tp2:+,.0f}")
    print(f"  Daily avg: ${da:+,.0f}")
    print(f"  Monthly est: ${da*21:+,.0f}")
    wps = [x["pnl"] for x in all_trades if x["outcome"] == "WIN"]
    lps = [x["pnl"] for x in all_trades if x["outcome"] == "LOSS"]
    if wps: print(f"  Avg WIN: ${sum(wps)/len(wps):+,.0f}")
    if lps: print(f"  Avg LOSS: ${sum(lps)/len(lps):+,.0f}")
    if wps and lps:
        print(f"  Win/Loss ratio: {abs(sum(wps)/len(wps)) / abs(sum(lps)/len(lps)):.2f}")

    print(f"\n  {'Setup':<12s} {'N':>4s} {'WR':>6s} {'AvgPnL':>8s} {'TotalPnL':>10s} {'Type':>15s}")
    print(f"  {'-'*55}")
    ss = defaultdict(lambda: {"n":0,"w":0,"pnl":0,"type":""})
    for x in all_trades:
        s = ss[x["setup_id"]]; s["n"] += 1
        if x["outcome"] == "WIN": s["w"] += 1
        s["pnl"] += x["pnl"]; s["type"] = x["type"]
    for sid, s in sorted(ss.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = s["w"]/s["n"]*100 if s["n"] > 0 else 0
        av = s["pnl"]/s["n"] if s["n"] > 0 else 0
        print(f"  {sid:<12s} {s['n']:>4d} {wr:>5.1f}% ${av:>+7.0f} ${s['pnl']:>+9,.0f} {s['type']:>15s}")

    print(f"\n  Regime breakdown:")
    for r in ["TREND", "NORMAL", "CHOP"]:
        rt = [x for x in all_trades if x["regime"] == r]
        if rt:
            rw = sum(1 for x in rt if x["outcome"] == "WIN")
            rp = sum(x["pnl"] for x in rt)
            print(f"    {r:<10s} {len(rt):>4d}t | WR {rw/len(rt)*100:.1f}% | ${rp:+,.0f}")

    print(f"\n  Dims accuracy:")
    for d in [3, 2, 1]:
        dt = [x for x in all_trades if x["dims"] == d]
        if dt:
            dw = sum(1 for x in dt if x["outcome"] == "WIN")
            dp = sum(x["pnl"] for x in dt)
            print(f"    {d} dims:  {len(dt):>4d}t | WR {dw/len(dt)*100:.1f}% | ${dp:+,.0f}")

    print(f"\n  Trade type breakdown:")
    for tp in ["breakout", "mean_reversion", "level"]:
        tt2 = [x for x in all_trades if x["type"] == tp]
        if tt2:
            tw2 = sum(1 for x in tt2 if x["outcome"] == "WIN")
            tp3 = sum(x["pnl"] for x in tt2)
            print(f"    {tp:<16s} {len(tt2):>4d}t | WR {tw2/len(tt2)*100:.1f}% | ${tp3:+,.0f}")

    print(f"\n{'='*90}")

def main():
    print("NEXUS CAPITAL -- FORGE V22 CLEAN REBUILD REPLAY")
    print("5 setups | Fresh signals | 1 trade at a time | 3 independent dims")
    print("")
    candles = fetch("QQQ", days=10)
    if not candles: return
    replay(candles)

if __name__ == "__main__":
    main()
