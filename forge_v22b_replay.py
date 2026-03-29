"""
FORGE V22B — WITH TREND FILTER
One rule: don't fight the trend.
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
        self.prices = []; self.candles = []; self.volumes = []
        self.open_price = 0; self.session_high = 0; self.session_low = float("inf")
        self.orb_high = 0; self.orb_low = float("inf"); self.orb_locked = False
        self.ib_high = 0; self.ib_low = float("inf"); self.ib_locked = False
        self.ib_direction = None; self.vwap = 0; self._vv = 0; self._vpv = 0
        self._ib_break_long_fired = False; self._ib_break_short_fired = False
        self._gap_traded = False
        self.prev_day_close = 0

    def update(self, candle, et_time):
        self.prices.append(candle.close)
        self.candles.append(candle)
        self.volumes.append(candle.volume)
        if len(self.prices) > 300: self.prices = self.prices[-300:]
        if len(self.candles) > 300: self.candles = self.candles[-300:]
        if len(self.volumes) > 300: self.volumes = self.volumes[-300:]
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

    def trend_direction(self):
        """THE KEY ADDITION: What is the trend RIGHT NOW?
        Uses 20-bar SMA on M1 prices + price relative to VWAP + today's direction from open.
        Returns: 'UP', 'DOWN', or 'FLAT'"""
        if len(self.prices) < 20: return "FLAT"
        sma20 = sum(self.prices[-20:]) / 20
        price = self.prices[-1]

        # Three votes
        votes_up = 0; votes_down = 0

        # Vote 1: Price vs SMA20
        if price > sma20 + 5: votes_up += 1
        elif price < sma20 - 5: votes_down += 1

        # Vote 2: Price vs VWAP
        if self.vwap > 0:
            if price > self.vwap + 5: votes_up += 1
            elif price < self.vwap - 5: votes_down += 1

        # Vote 3: Price vs open
        if self.open_price > 0:
            if price > self.open_price + 10: votes_up += 1
            elif price < self.open_price - 10: votes_down += 1

        if votes_up >= 2: return "UP"
        if votes_down >= 2: return "DOWN"
        return "FLAT"

    def vol_trend(self, lookback=10):
        if len(self.volumes) < lookback: return "neutral"
        recent = self.volumes[-lookback:]
        h1 = sum(recent[:lookback//2]) / max(1, lookback//2)
        h2 = sum(recent[lookback//2:]) / max(1, lookback//2)
        if h2 > h1 * 1.3: return "increasing"
        if h2 < h1 * 0.7: return "decreasing"
        return "neutral"

    def consecutive_direction(self):
        if len(self.candles) < 2: return 0, "none"
        count = 1
        last_dir = "up" if self.candles[-1].close > self.candles[-1].open else "down"
        for i in range(len(self.candles)-2, max(0, len(self.candles)-10), -1):
            d = "up" if self.candles[i].close > self.candles[i].open else "down"
            if d == last_dir: count += 1
            else: break
        return count, last_dir


def generate_signal(t, price, et_time, pdh, pdl, pdc, idx):
    atr = t.atr; sd = max(atr * 0.3, 15)
    vwap = t.vwap or t.open_price or price
    mins = (et_time.hour - 9) * 60 + et_time.minute - 30
    trend = t.trend_direction()

    # ── SETUP 1: FRESH IB BREAKOUT (WITH TREND) ────────────────
    if t.ib_locked and mins >= 31:
        ib_r = max(t.ib_high - t.ib_low, 20) if t.ib_low < float("inf") else 40

        # LONG breakout only if trend is UP or FLAT
        if price > t.ib_high + 2 and not t._ib_break_long_fired and trend != "DOWN":
            if len(t.prices) >= 4 and t.prices[-4] <= t.ib_high:
                t._ib_break_long_fired = True
                return {"id": "BREAK-01", "dir": "long", "entry": price,
                        "sl": round(price - ib_r * 0.5, 2), "tp": None,
                        "reason": f"Fresh IB break long (trend:{trend})", "type": "breakout"}

        # SHORT breakout only if trend is DOWN or FLAT
        if t.ib_low < float("inf") and price < t.ib_low - 2 and not t._ib_break_short_fired and trend != "UP":
            if len(t.prices) >= 4 and t.prices[-4] >= t.ib_low:
                t._ib_break_short_fired = True
                return {"id": "BREAK-01", "dir": "short", "entry": price,
                        "sl": round(price + ib_r * 0.5, 2), "tp": None,
                        "reason": f"Fresh IB break short (trend:{trend})", "type": "breakout"}

    # ── SETUP 2: TREND FOLLOW — ride the trend ─────────────────
    # NEW: Instead of mean reverting against the trend, FOLLOW it.
    # Enter on pullback to VWAP in a trending market.
    if vwap > 0 and mins >= 30:
        dist = price - vwap
        dist_atr = abs(dist) / atr if atr > 0 else 0

        # PULLBACK TO VWAP IN UPTREND: price comes back near VWAP from above
        if trend == "UP" and 0 < dist < sd * 0.5 and dist_atr < 0.15:
            return {"id": "TREND-01", "dir": "long", "entry": price,
                    "sl": round(vwap - sd * 0.5, 2), "tp": None,  # trail
                    "reason": f"Trend pullback long (trend:UP, near VWAP)", "type": "trend"}

        # PULLBACK TO VWAP IN DOWNTREND
        if trend == "DOWN" and 0 < -dist < sd * 0.5 and dist_atr < 0.15:
            return {"id": "TREND-01", "dir": "short", "entry": price,
                    "sl": round(vwap + sd * 0.5, 2), "tp": None,
                    "reason": f"Trend pullback short (trend:DOWN, near VWAP)", "type": "trend"}

    # ── SETUP 3: MEAN REVERSION (only in FLAT/CHOP markets) ────
    # ONLY when trend is FLAT. Never fight a trend.
    if vwap > 0 and atr > 0 and mins >= 30 and trend == "FLAT":
        dist = price - vwap
        dist_atr = abs(dist) / atr

        if dist_atr >= 0.5:
            if dist > 0:
                return {"id": "REVERT-01", "dir": "short", "entry": price,
                        "sl": round(price + sd, 2), "tp": round(vwap + sd * 0.3, 2),
                        "reason": f"Mean rev short (FLAT, {dist_atr:.1f} ATR ext)", "type": "mean_reversion"}
            else:
                return {"id": "REVERT-01", "dir": "long", "entry": price,
                        "sl": round(price - sd, 2), "tp": round(vwap - sd * 0.3, 2),
                        "reason": f"Mean rev long (FLAT, {dist_atr:.1f} ATR ext)", "type": "mean_reversion"}

    # ── SETUP 4: LEVEL BOUNCE WITH TREND ────────────────────────
    # Only bounce levels in the direction of the trend
    if len(t.candles) >= 2:
        c = t.candles[-1]
        body = abs(c.close - c.open)
        upper_wick = c.high - max(c.close, c.open)
        lower_wick = min(c.close, c.open) - c.low

        # PDH rejection — only short if trend is DOWN or FLAT
        if pdh > 0 and abs(c.high - pdh) < 10 and upper_wick > body * 1.5:
            if trend != "UP":
                return {"id": "LEVEL-01", "dir": "short", "entry": price,
                        "sl": round(pdh + 15, 2), "tp": round(price - 35, 2),
                        "reason": f"PDH reject (trend:{trend})", "type": "level"}

        # PDL rejection — only long if trend is UP or FLAT
        if pdl > 0 and abs(c.low - pdl) < 10 and lower_wick > body * 1.5:
            if trend != "DOWN":
                return {"id": "LEVEL-01", "dir": "long", "entry": price,
                        "sl": round(pdl - 15, 2), "tp": round(price + 35, 2),
                        "reason": f"PDL reject (trend:{trend})", "type": "level"}

        # Round number — only in trend direction
        nearest = round(price / 100) * 100
        if abs(c.high - nearest) < 5 and upper_wick > body * 1.5 and trend != "UP":
            return {"id": "LEVEL-01", "dir": "short", "entry": price,
                    "sl": round(nearest + 15, 2), "tp": round(price - 35, 2),
                    "reason": f"Round {nearest:.0f} reject (trend:{trend})", "type": "level"}
        if abs(c.low - nearest) < 5 and lower_wick > body * 1.5 and trend != "DOWN":
            return {"id": "LEVEL-01", "dir": "long", "entry": price,
                    "sl": round(nearest - 15, 2), "tp": round(price + 35, 2),
                    "reason": f"Round {nearest:.0f} bounce (trend:{trend})", "type": "level"}

    # ── SETUP 5: EXHAUSTION REVERSAL (with trend confirmation) ──
    # Only fade exhaustion when the exhaustion is AGAINST the trend
    consec, last_dir = t.consecutive_direction()
    vol_trend = t.vol_trend(6)

    if consec >= 3 and vol_trend == "decreasing":
        # Fade up-exhaustion only if trend is DOWN or FLAT (don't fade a rally in uptrend)
        if last_dir == "up" and trend != "UP":
            return {"id": "EXHAUST-01", "dir": "short", "entry": price,
                    "sl": round(price + sd * 0.8, 2), "tp": round(price - sd * 1.2, 2),
                    "reason": f"Exhaust {consec}bar up, trend:{trend}, vol declining", "type": "mean_reversion"}
        # Fade down-exhaustion only if trend is UP or FLAT
        if last_dir == "down" and trend != "DOWN":
            return {"id": "EXHAUST-01", "dir": "long", "entry": price,
                    "sl": round(price - sd * 0.8, 2), "tp": round(price + sd * 1.2, 2),
                    "reason": f"Exhaust {consec}bar down, trend:{trend}, vol declining", "type": "mean_reversion"}

    return None


def simulate_trade(signal, lot, future_candles, atr):
    entry = signal["entry"]; sl = signal["sl"]; d = signal["dir"]
    tp = signal["tp"]; risk = abs(entry - sl)
    if risk <= 0: return None
    spread = 3.0; slip = random.uniform(0.5, 1.5)
    ae = entry + spread/2 + slip if d == "long" else entry - spread/2 - slip
    is_trail = tp is None
    tp_check = tp if tp else (float("inf") if d == "long" else 0)
    max_hold = min(45, len(future_candles))
    best = ae; trail_sl = sl

    for i in range(max_hold):
        c = future_candles[i]
        if d == "long":
            best = max(best, c.high)
            if best - ae >= risk: trail_sl = max(trail_sl, best - risk)
            if c.low <= trail_sl:
                pnl = (trail_sl - ae) * lot * 20
                return {"outcome": "WIN" if pnl > 0 else "LOSS", "pnl": round(pnl, 2),
                        "r": round(pnl/(risk*lot*20), 2) if risk > 0 else 0, "bars": i+1}
            if not is_trail and c.high >= tp_check:
                pnl = (tp_check - ae) * lot * 20
                return {"outcome": "WIN", "pnl": round(pnl, 2),
                        "r": round(pnl/(risk*lot*20), 2), "bars": i+1}
        else:
            best = min(best, c.low)
            if ae - best >= risk: trail_sl = min(trail_sl, best + risk)
            if c.high >= trail_sl:
                pnl = (ae - trail_sl) * lot * 20
                return {"outcome": "WIN" if pnl > 0 else "LOSS", "pnl": round(pnl, 2),
                        "r": round(pnl/(risk*lot*20), 2) if risk > 0 else 0, "bars": i+1}
            if not is_trail and c.low <= tp_check:
                pnl = (ae - tp_check) * lot * 20
                return {"outcome": "WIN", "pnl": round(pnl, 2),
                        "r": round(pnl/(risk*lot*20), 2), "bars": i+1}

    ep = future_candles[max_hold-1].close
    pnl = ((ep-ae) if d=="long" else (ae-ep)) * lot * 20
    return {"outcome": "WIN" if pnl > 0 else "LOSS", "pnl": round(pnl, 2),
            "r": round(pnl/(risk*lot*20), 2) if risk > 0 else 0, "bars": max_hold}


def replay(candles):
    days = defaultdict(list)
    for c in candles:
        et = utc_to_et(c.dt)
        if et.weekday() < 5: days[et.date()].append((c, et))

    print(f"\n[REPLAY] {len(days)} trading days")
    print(f"[SYSTEM] 5 setups | TREND FILTER | 1 trade at a time | Don't fight the trend")
    print("=" * 90)

    all_trades = []; pdh = 0; pdl = 0; pdc = 0

    for dd in sorted(days.keys()):
        dc = days[dd]; t = Tracker(); t.prev_day_close = pdc
        day_trades = []; day_pnl = 0.0; last_idx = -999; last_dir = None

        print(f"\n{'='*90}")
        print(f"  {dd} ({dd.strftime('%A')}) | PDH:{pdh:.0f} PDL:{pdl:.0f}")
        print(f"{'='*90}")

        for idx, (candle, et_dt) in enumerate(dc):
            et_time = et_dt.time()
            if dtime(9,30) <= et_time < dtime(16,0): t.update(candle, et_time)
            else: continue
            price = candle.close
            if et_time >= dtime(15, 15): continue
            if idx - last_idx < 45: continue

            sig = generate_signal(t, price, et_time, pdh, pdl, pdc, idx)
            if sig is None: continue
            if sig["dir"] == last_dir and idx - last_idx < 60: continue

            # Lot sizing: trend-following gets bigger size
            if sig["type"] == "trend": lot = 0.30
            elif sig["type"] == "breakout": lot = 0.25
            else: lot = 0.15

            future = [c for c, _ in dc[idx+1:idx+46]]
            if len(future) < 5: continue
            result = simulate_trade(sig, lot, future, t.atr)
            if not result: continue

            last_idx = idx; last_dir = sig["dir"]; day_pnl += result["pnl"]
            icon = "+" if result["outcome"] == "WIN" else "X"
            trend = t.trend_direction()
            print(f"  {icon} {et_time.strftime('%H:%M')} {sig['id']:<12s} "
                  f"{sig['dir'].upper():<5s} @{sig['entry']:.0f} "
                  f"{lot:.2f}L trend:{trend:<4s} "
                  f"{result['outcome']:<4s} ${result['pnl']:+.0f} ({result['r']:+.1f}R) "
                  f"{result['bars']}bars | {sig['reason']}")

            rec = {"date": str(dd), "time": et_time.strftime("%H:%M"),
                   "setup_id": sig["id"], "direction": sig["dir"],
                   "entry": sig["entry"], "lot": lot, "trend": trend,
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
    print(f"  FORGE V22B REPLAY -- WITH TREND FILTER")
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
    if wps and lps: print(f"  Win/Loss ratio: {abs(sum(wps)/len(wps)/((sum(lps)/len(lps)) or 1)):.2f}")

    print(f"\n  {'Setup':<12s} {'N':>4s} {'WR':>6s} {'AvgPnL':>8s} {'TotalPnL':>10s} {'Type':>15s}")
    print(f"  {'-'*60}")
    ss = defaultdict(lambda: {"n":0,"w":0,"pnl":0,"type":""})
    for x in all_trades:
        s = ss[x["setup_id"]]; s["n"] += 1
        if x["outcome"] == "WIN": s["w"] += 1
        s["pnl"] += x["pnl"]; s["type"] = x["type"]
    for sid, s in sorted(ss.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = s["w"]/s["n"]*100 if s["n"] > 0 else 0
        av = s["pnl"]/s["n"] if s["n"] > 0 else 0
        print(f"  {sid:<12s} {s['n']:>4d} {wr:>5.1f}% ${av:>+7.0f} ${s['pnl']:>+9,.0f} {s['type']:>15s}")

    print(f"\n  Trade type breakdown:")
    for tp in ["trend", "breakout", "mean_reversion", "level"]:
        tt2 = [x for x in all_trades if x["type"] == tp]
        if tt2:
            tw2 = sum(1 for x in tt2 if x["outcome"] == "WIN")
            tp3 = sum(x["pnl"] for x in tt2)
            avg = tp3/len(tt2)
            print(f"    {tp:<16s} {len(tt2):>4d}t | WR {tw2/len(tt2)*100:.1f}% | Avg ${avg:+,.0f} | Total ${tp3:+,.0f}")

    print(f"\n  Trend alignment:")
    for td2 in ["UP", "DOWN", "FLAT"]:
        tt2 = [x for x in all_trades if x["trend"] == td2]
        if tt2:
            tw2 = sum(1 for x in tt2 if x["outcome"] == "WIN")
            tp3 = sum(x["pnl"] for x in tt2)
            print(f"    Trend {td2:<4s}: {len(tt2):>4d}t | WR {tw2/len(tt2)*100:.1f}% | ${tp3:+,.0f}")

    # Trades WITH trend vs AGAINST (should never be against now)
    with_trend = [x for x in all_trades if
                  (x["direction"] == "long" and x["trend"] == "UP") or
                  (x["direction"] == "short" and x["trend"] == "DOWN")]
    against = [x for x in all_trades if
               (x["direction"] == "long" and x["trend"] == "DOWN") or
               (x["direction"] == "short" and x["trend"] == "UP")]
    neutral = [x for x in all_trades if x["trend"] == "FLAT"]

    print(f"\n  Direction vs Trend:")
    if with_trend:
        wt_w = sum(1 for x in with_trend if x["outcome"] == "WIN")
        wt_p = sum(x["pnl"] for x in with_trend)
        print(f"    WITH trend:    {len(with_trend):>3d}t | WR {wt_w/len(with_trend)*100:.1f}% | ${wt_p:+,.0f}")
    if against:
        ag_w = sum(1 for x in against if x["outcome"] == "WIN")
        ag_p = sum(x["pnl"] for x in against)
        print(f"    AGAINST trend: {len(against):>3d}t | WR {ag_w/len(against)*100:.1f}% | ${ag_p:+,.0f}")
    if neutral:
        nt_w = sum(1 for x in neutral if x["outcome"] == "WIN")
        nt_p = sum(x["pnl"] for x in neutral)
        print(f"    FLAT/neutral:  {len(neutral):>3d}t | WR {nt_w/len(neutral)*100:.1f}% | ${nt_p:+,.0f}")

    print(f"\n{'='*90}")

def main():
    print("NEXUS CAPITAL -- FORGE V22B REPLAY (TREND FILTER)")
    print("5 setups | TREND FOLLOWING added | Don't fight the trend")
    print("")
    candles = fetch("QQQ", days=10)
    if not candles: return
    replay(candles)

if __name__ == "__main__":
    main()
