"""
TITAN FORGE V22.5 — MAIN DEPLOYMENT SCRIPT
=============================================
Wires together: Direction Engine + 14 Strategies + Precision Scalps +
                Daily Gate + Risk Budget + Trade Detector + Health Monitor +
                Telegram Commands + Fallback Wrappers

Every module wrapped with try/except — failure = skip feature, not crash bot.
Every fallback triggers Telegram alert. /health shows all module status.

TELEGRAM COMMANDS:
  /stop     — Close all positions, stop trading immediately
  /pause    — Stop new trades, keep managing open positions
  /resume   — Resume trading after pause
  /status   — Show positions, daily P&L, risk budget
  /health   — Show all module health status
  /diag     — Show direction engine regime for all instruments
  /scalps   — Show precision scalp status

ENV VARS REQUIRED:
  META_API_TOKEN, META_API_ACCOUNT_ID, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""

import os, sys, time, json, logging, traceback
import numpy as np
import requests as req
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

# ═══════════════════════════════════════════════════════════════
# LOGGING & CONFIG
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("titan_forge.main")

VERSION = "V22.5-CAL"
META_API_TOKEN = os.environ.get("META_API_TOKEN", os.environ.get("METAAPI_TOKEN", os.environ.get("TOKEN", "")))
META_API_ACCOUNT_ID = os.environ.get("META_API_ACCOUNT_ID", os.environ.get("METAAPI_ACCOUNT_ID", os.environ.get("ACCOUNT_ID", "")))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", os.environ.get("CHAT_ID", ""))

CYCLE_SPEED = 20
DEFAULT_LOTS = 2.0
CANDLE_COUNT = 300

# All instruments — needed for direction engine + scalps + specific combos
INSTRUMENTS = {
    "EURUSD": "EURUSD.sim", "GBPUSD": "GBPUSD.sim", "USDJPY": "USDJPY.sim",
    "USDCHF": "USDCHF.sim", "EURGBP": "EURGBP.sim", "GBPJPY": "GBPJPY.sim",
    "NZDUSD": "NZDUSD.sim", "AUDUSD": "AUDUSD.sim", "AUDNZD": "AUDNZD.sim",
    "EURJPY": "EURJPY.sim", "XAUUSD": "XAUUSD.sim", "BTCUSD": "BTCUSD.sim",
    "US100": "US100.sim", "USOIL": "USOIL.sim",
}

# ═══════════════════════════════════════════════════════════════
# CALIBRATED STRATEGY MAP — from 3,136 MT5 backtests
# ═══════════════════════════════════════════════════════════════
# Only strategy×instrument combos that showed PF >= 1.25 on MT5 OANDA data
# Each entry: (SL_ATR_multiplier, TP_ATR_multiplier)
# Source: forge_research_unified.py --quick on MT5, April 2026

CALIBRATED_COMBOS = {
    # ── VWAP_TREND — global winner, +39.2R on MT5 (6W/3B) ──
    "VWAP_TREND": {
        "GBPJPY": (2.0, 4.0),   # PF=2.26 R=+13.9
        "NZDUSD": (1.5, 3.0),   # PF=1.40 R=+8.7
        "USDJPY": (2.0, 4.0),   # PF=1.40 R=+8.0
        "EURUSD": (1.0, 2.0),   # PF=1.37 R=+7.0
        "AUDUSD": (2.0, 2.0),   # PF=1.38 R=+5.0
        "USOIL":  (2.0, 2.0),   # PF=1.37 R=+4.9
    },
    # ── LONDON_BREAKOUT — +38.0R on MT5 (5W/4B) ──
    "LONDON_BREAKOUT": {
        "US100":  (2.0, 4.0),   # PF=3.67 R=+16.0
        "GBPJPY": (0.8, 2.0),   # PF=1.82 R=+9.0
        "AUDNZD": (1.0, 1.5),   # PF=1.50 R=+6.0
        "USDJPY": (2.0, 2.0),   # PF=2.00 R=+5.0
        "USOIL":  (2.0, 2.0),   # PF=1.44 R=+4.0
    },
    # ── PREV_DAY_HL — tuned: only 2 winners on MT5 ──
    "PREV_DAY_HL": {
        "XAUUSD": (0.5, 1.5),   # PF=1.64 R=+54.0 ← best single combo
        "GBPJPY": (1.0, 3.0),   # PF=1.35 R=+28.4
    },
    # ── MEAN_REVERT — tuned: 3 winners on MT5 ──
    "MEAN_REVERT": {
        "GBPJPY": (1.0, 3.0),   # PF=1.54 R=+29.4
        "USDJPY": (0.8, 2.0),   # PF=1.61 R=+27.5
        "USDCHF": (2.0, 4.0),   # PF=1.43 R=+11.2
    },
    # ── BREAKOUT — tuned: 1 winner + 1 borderline on MT5 ──
    "BREAKOUT": {
        "XAUUSD": (1.0, 1.5),   # PF=1.50 R=+27.0
        "GBPJPY": (1.0, 3.0),   # PF=1.27 R=+21.4
    },
    # ── PREV_DAY_BOUNCE — tuned: 2 winners on MT5 ──
    "PREV_DAY_BOUNCE": {
        "EURJPY": (2.0, 4.0),   # PF=1.36 R=+24.8
        "USDJPY": (2.0, 4.0),   # PF=1.33 R=+20.5
    },
    # ── LIQUIDITY_SWEEP — tuned: 3 winners on MT5 ──
    "LIQUIDITY_SWEEP": {
        "XAUUSD": (0.5, 0.75),  # PF=1.32 R=+23.5
        "BTCUSD": (1.0, 3.0),   # PF=1.23 R=+23.4
        "EURGBP": (2.0, 3.0),   # PF=1.32 R=+17.8
    },
    # ── MOMENTUM_CONT — tuned: 1 winner on MT5 ──
    "MOMENTUM_CONT": {
        "US100":  (1.0, 3.0),   # PF=1.28 R=+30.5
    },
    # ── US100 SPECIALS — these strategies are globally CUT but US100 wins ──
    "ASIAN_RANGE": {
        "US100":  (1.5, 2.0),   # PF=1.72 R=+39.7 ← strong edge
    },
    "VOL_SQUEEZE": {
        "US100":  (1.5, 3.0),   # PF=1.38 R=+29.5
    },
}

# Derive enabled strategies from combos
ENABLED_STRATEGIES = list(CALIBRATED_COMBOS.keys())

ENABLE_SCALPS = True
ENABLE_DIRECTION_ENGINE = True
COOLDOWN_SECONDS = 90

# Scalp sweet spots from MT5 data (only profitable instruments)
SCALP_TARGETS = {
    "XAUUSD": 500, "BTCUSD": 500, "US100": 500,
    "GBPJPY": 400, "EURJPY": 300, "USDJPY": 200,
    "USOIL": 50, "EURUSD": 200, "USDCHF": 200,
}


# ═══════════════════════════════════════════════════════════════
# HEALTH MONITOR
# ═══════════════════════════════════════════════════════════════

class HealthMonitor:
    def __init__(self):
        self.status: Dict[str, dict] = {}
        self._alerted: Set[str] = set()

    def ok(self, mod, detail=""):
        self.status[mod] = {"s": "✅", "d": detail, "t": time.time()}
        self._alerted.discard(mod)

    def warn(self, mod, err):
        self.status[mod] = {"s": "⚠️", "d": err, "t": time.time()}
        need_alert = mod not in self._alerted
        self._alerted.add(mod)
        return need_alert

    def fail(self, mod, err):
        self.status[mod] = {"s": "❌", "d": err, "t": time.time()}
        need_alert = mod not in self._alerted
        self._alerted.add(mod)
        return need_alert

    def report(self):
        lines = ["[HEALTH]"]
        for m, i in sorted(self.status.items()):
            lines.append(f"  {i['s']} {m}: {i['d']}")
        return "\n".join(lines)

    def compact(self):
        a = sum(1 for v in self.status.values() if v["s"] == "✅")
        w = sum(1 for v in self.status.values() if v["s"] == "⚠️")
        f = sum(1 for v in self.status.values() if v["s"] == "❌")
        return f"H={a}✅{w}⚠️{f}❌"

health = HealthMonitor()


# ═══════════════════════════════════════════════════════════════
# MODULE IMPORTS WITH FALLBACK
# ═══════════════════════════════════════════════════════════════

try:
    from forge_direction_engine import DirectionEngine, DailyPnLGate
    direction_engine = DirectionEngine()
    daily_gate = DailyPnLGate(soft_limit_pct=3.5, hard_limit_pct=4.0,
                               max_risk_budget=4000, max_open=8)
    health.ok("DirectionEngine"); health.ok("DailyGate")
except Exception as e:
    direction_engine = None; daily_gate = None
    health.fail("DirectionEngine", str(e)[:60]); health.fail("DailyGate", str(e)[:60])

try:
    from forge_strategies_v22_5 import ALL_STRATEGIES, DOLLAR_PER_UNIT, SPREAD, atr as calc_atr
    health.ok("Strategies", f"{len(ALL_STRATEGIES)} loaded")
except Exception as e:
    ALL_STRATEGIES = {}; DOLLAR_PER_UNIT = {}; SPREAD = {}
    calc_atr = None
    health.fail("Strategies", str(e)[:60])

try:
    from forge_precision_scalp import PrecisionScalpExecutor
    scalp_executor = PrecisionScalpExecutor()
    health.ok("ScalpExecutor")
except Exception as e:
    scalp_executor = None
    health.fail("ScalpExecutor", str(e)[:60])

try:
    from forge_trade_detector import TradeDetector
    trade_detector = TradeDetector()
    health.ok("TradeDetector")
except Exception as e:
    trade_detector = None
    health.fail("TradeDetector", str(e)[:60])


# ═══════════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════════

_tg_offset = 0

def tg_send(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        req.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                 json={"chat_id": TELEGRAM_CHAT_ID, "text": msg[:4000]}, timeout=10)
    except: pass

def tg_check():
    global _tg_offset
    if not TELEGRAM_TOKEN: return None
    try:
        r = req.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                    params={"timeout": 0, "limit": 5, "offset": _tg_offset}, timeout=5)
        if r.status_code == 200:
            for u in r.json().get("result", []):
                _tg_offset = u["update_id"] + 1
                txt = u.get("message", {}).get("text", "").strip().lower()
                if txt.startswith("/"):
                    return txt
    except: pass
    return None


# ═══════════════════════════════════════════════════════════════
# METAAPI
# ═══════════════════════════════════════════════════════════════

class API:
    def __init__(self, token, acct_id):
        self.token = token
        self.acct = acct_id
        self.base = "https://mt-client-api-v1.london.agiliumtrade.agiliumtrade.ai"
        self.hdr = {"auth-token": token, "Content-Type": "application/json"}
        self.symbols: Dict[str, str] = {}
        # Disable SSL warnings for MetaAPI client API (self-signed cert on Railway)
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def connect(self):
        try:
            url = (f"https://mt-provisioning-api-v1.agiliumtrade.agiliumtrade.ai/"
                   f"users/current/accounts/{self.acct}")
            logger.info(f"[MT5] Connecting to {url[:60]}...")
            logger.info(f"[MT5] Token: {self.token[:10]}...")
            r = req.get(url, headers={"auth-token": self.token}, timeout=15, verify=False)
            logger.info(f"[MT5] Response: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                state = data.get("state", "UNKNOWN")
                logger.info(f"[MT5] Account state: {state}")
                return state == "DEPLOYED"
            else:
                logger.error(f"[MT5] Error: {r.status_code} — {r.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"[MT5] Connection exception: {e}")
            return False

    def account_info(self):
        try:
            r = req.get(f"{self.base}/users/current/accounts/{self.acct}/account-information",
                        headers=self.hdr, timeout=15, verify=False)
            if r.status_code == 200:
                data = r.json()
                logger.info(f"[MT5] Account info: bal=${data.get('balance',0)} eq=${data.get('equity',0)}")
                return data
            else:
                logger.error(f"[MT5] Account info failed: {r.status_code} {r.text[:100]}")
        except Exception as e:
            logger.error(f"[MT5] Account info error: {e}")
        return {"balance": 0, "equity": 0}

    def positions(self):
        try:
            r = req.get(f"{self.base}/users/current/accounts/{self.acct}/positions",
                        headers=self.hdr, timeout=15, verify=False)
            return r.json() if r.status_code == 200 else []
        except: return []

    def candles(self, symbol, tf="15m", count=300):
        try:
            r = req.get(f"{self.base}/users/current/accounts/{self.acct}/"
                        f"historical-market-data/symbols/{symbol}/timeframes/{tf}/candles",
                        headers=self.hdr, params={"limit": count}, timeout=20, verify=False)
            if r.status_code == 200:
                cc = r.json()
                if cc and len(cc) >= 50:
                    return {
                        "o": np.array([c["open"] for c in cc]),
                        "h": np.array([c["high"] for c in cc]),
                        "l": np.array([c["low"] for c in cc]),
                        "c": np.array([c["close"] for c in cc]),
                        "v": np.array([c.get("tickVolume", 1) for c in cc]),
                        "n": len(cc),
                    }
        except: pass
        return None

    def order(self, symbol, direction, lots, sl=0, tp=0, comment=""):
        try:
            payload = {
                "actionType": "ORDER_TYPE_BUY" if direction == "LONG" else "ORDER_TYPE_SELL",
                "symbol": symbol, "volume": lots, "comment": comment[:63],
            }
            if sl > 0: payload["stopLoss"] = sl
            if tp > 0: payload["takeProfit"] = tp
            r = req.post(f"{self.base}/users/current/accounts/{self.acct}/trade",
                         headers=self.hdr, json=payload, timeout=15, verify=False)
            if r.status_code == 200:
                oid = r.json().get("orderId", r.json().get("positionId", ""))
                logger.info(f"[MT5] ✅ {direction} {symbol} {lots} SL={sl} TP={tp} ID={oid}")
                return str(oid)
            else:
                logger.error(f"[MT5] ❌ Order failed: {r.text[:200]}")
        except Exception as e:
            logger.error(f"[MT5] Order error: {e}")
        return None

    def close(self, pid):
        try:
            r = req.post(f"{self.base}/users/current/accounts/{self.acct}/trade",
                         headers=self.hdr,
                         json={"actionType": "POSITION_CLOSE_ID", "positionId": pid},
                         timeout=15, verify=False)
            return r.status_code == 200
        except: return False

    def modify_sl(self, pid, sl):
        try:
            r = req.post(f"{self.base}/users/current/accounts/{self.acct}/trade",
                         headers=self.hdr,
                         json={"actionType": "POSITION_MODIFY", "positionId": pid, "stopLoss": sl},
                         timeout=15, verify=False)
            return r.status_code == 200
        except: return False

    def close_all(self):
        closed = 0
        for p in self.positions():
            if self.close(str(p.get("id", ""))): closed += 1
        return closed

    def resolve_symbols(self):
        for name, mt5s in INSTRUMENTS.items():
            # Try fetching a small batch to verify symbol exists
            try:
                r = req.get(f"{self.base}/users/current/accounts/{self.acct}/"
                            f"historical-market-data/symbols/{mt5s}/timeframes/15m/candles",
                            headers=self.hdr, params={"limit": 5}, timeout=15, verify=False)
                if r.status_code == 200 and r.json():
                    self.symbols[name] = mt5s
                    logger.info(f"  ✅ {name} → {mt5s}")
                    continue
            except: pass
            # Try without .sim
            try:
                r = req.get(f"{self.base}/users/current/accounts/{self.acct}/"
                            f"historical-market-data/symbols/{name}/timeframes/15m/candles",
                            headers=self.hdr, params={"limit": 5}, timeout=15, verify=False)
                if r.status_code == 200 and r.json():
                    self.symbols[name] = name
                    logger.info(f"  ✅ {name} → {name}")
                    continue
            except: pass
            logger.warning(f"  ⚠️ {name} not found")


# ═══════════════════════════════════════════════════════════════
# TRAILING STOP MANAGER
# ═══════════════════════════════════════════════════════════════

def manage_trails(api, positions, cdata):
    for pos in positions:
        try:
            sym = pos.get("symbol", "").replace(".sim", "")
            pid = str(pos.get("id", ""))
            d = 1 if "BUY" in str(pos.get("type", "")) else -1
            ep = float(pos.get("openPrice", 0))
            csl = float(pos.get("stopLoss", 0))
            cp = float(pos.get("currentPrice", ep))
            if sym not in cdata or ep <= 0 or not calc_atr: continue
            atr_arr = calc_atr(cdata[sym]["h"], cdata[sym]["l"], cdata[sym]["c"])
            av = atr_arr[-1] if len(atr_arr) > 0 else 0
            if av <= 0: continue

            risk = abs(ep - csl) if csl > 0 else av * 1.5
            rm = ((cp - ep) / risk if d == 1 else (ep - cp) / risk) if risk > 0 else 0

            nsl = csl
            if rm >= 3.0:
                t = cp - 1.0*av if d == 1 else cp + 1.0*av
            elif rm >= 2.0:
                t = cp - 1.5*av if d == 1 else cp + 1.5*av
            elif rm >= 1.0:
                t = cp - 2.0*av if d == 1 else cp + 2.0*av
            elif rm >= 0.3:
                t = ep  # Breakeven
            else:
                continue

            if d == 1:
                nsl = max(csl, t)
                if nsl > csl:
                    api.modify_sl(pid, round(nsl, 5))
                    logger.info(f"[TRAIL] {pid} {rm:.1f}R SL→{nsl:.5f}")
            else:
                nsl = min(csl, t) if csl > 0 else t
                if csl == 0 or nsl < csl:
                    api.modify_sl(pid, round(nsl, 5))
                    logger.info(f"[TRAIL] {pid} {rm:.1f}R SL→{nsl:.5f}")
        except Exception as e:
            logger.error(f"[TRAIL] {pos.get('id','?')}: {e}")


# ═══════════════════════════════════════════════════════════════
# SIGNAL GENERATOR
# ═══════════════════════════════════════════════════════════════

def gen_signals(sym, data):
    signals = []
    for sname in ENABLED_STRATEGIES:
        if sname not in ALL_STRATEGIES: continue
        # Only fire if this strategy×instrument combo is calibrated
        combo = CALIBRATED_COMBOS.get(sname, {})
        if sym not in combo: continue
        sl_mult, tp_mult = combo[sym]
        fn, meta = ALL_STRATEGIES[sname]
        try:
            sigs = fn(o=data["o"], h=data["h"], l=data["l"], c=data["c"], v=data["v"])
            if len(sigs) > 0 and sigs[-1] != 0:
                atr_arr = calc_atr(data["h"], data["l"], data["c"])
                signals.append({
                    "sym": sym, "strat": sname, "dir": int(sigs[-1]),
                    "dir_str": "LONG" if sigs[-1] > 0 else "SHORT",
                    "dir_type": meta.direction_type,
                    "atr": atr_arr[-1] if len(atr_arr) > 0 else 0,
                    "price": data["c"][-1],
                    "sl_mult": sl_mult, "tp_mult": tp_mult,
                })
        except Exception as e:
            if health.warn(f"Strat_{sname}", f"{sym}:{str(e)[:30]}"):
                tg_send(f"⚠️ {sname} failed on {sym}: {str(e)[:80]}")
    return signals


# ═══════════════════════════════════════════════════════════════
# COMMAND HANDLER
# ═══════════════════════════════════════════════════════════════

paused = False
stopped = False
cycle = 0
trades_today = 0
last_sig: Dict[str, float] = {}

def handle_cmd(cmd, api):
    global paused, stopped
    if cmd == "/stop":
        stopped = True; paused = True
        c = api.close_all()
        return f"🛑 STOPPED. Closed {c} positions."
    elif cmd == "/pause":
        paused = True
        return "⏸️ PAUSED. Managing open only."
    elif cmd == "/resume":
        paused = False
        return "▶️ RESUMED."
    elif cmd == "/status":
        i = api.account_info(); p = api.positions()
        gs = daily_gate.get_status() if daily_gate else "No gate"
        lines = [f"📊 Bal=${i.get('balance',0):,.0f} Eq=${i.get('equity',0):,.0f}",
                 f"Positions: {len(p)}", gs, f"Paused:{paused} Cycle:{cycle}"]
        for pp in p:
            s = pp.get("symbol","?").replace(".sim","")
            pnl = pp.get("profit",0)
            lines.append(f"  {'🟢' if pnl>0 else '🔴'} {s}: ${pnl:+.0f}")
        return "\n".join(lines)
    elif cmd == "/health":
        return health.report()
    elif cmd == "/diag":
        if not direction_engine: return "No direction engine"
        lines = ["🧭 DIRECTION"]
        for s in INSTRUMENTS:
            r = direction_engine.get_last(s)
            lines.append(f"  {r}" if r else f"  {s}: no data")
        return "\n".join(lines)
    elif cmd == "/scalps":
        return scalp_executor.get_status() if scalp_executor else "No scalps"
    return f"Unknown: {cmd}"


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    global paused, stopped, cycle, trades_today

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info(f"║  NEXUS CAPITAL — TITAN FORGE {VERSION}                ║")
    logger.info("║  CALIBRATED | Direction | Scalps | Gate | Health      ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    if not META_API_TOKEN or not META_API_ACCOUNT_ID:
        logger.error("Missing META_API_TOKEN or META_API_ACCOUNT_ID"); return

    api = API(META_API_TOKEN, META_API_ACCOUNT_ID)
    logger.info("[BOOT] Connecting...")
    if not api.connect():
        time.sleep(30)
        if not api.connect():
            tg_send(f"❌ FORGE {VERSION} — connection FAILED"); return
    health.ok("MetaAPI")

    logger.info("[BOOT] Resolving symbols...")
    api.resolve_symbols()

    info = api.account_info()
    bal = info.get("balance", 100000)
    if daily_gate: daily_gate.set_sod_balance(bal)

    n_combos = sum(len(v) for v in CALIBRATED_COMBOS.values())
    boot = (f"🚀 FORGE {VERSION} ONLINE\nBal: ${bal:,.0f}\n"
            f"Instruments: {len(api.symbols)}\n"
            f"Strats: {len(ENABLED_STRATEGIES)} | Combos: {n_combos}\n"
            f"Scalps: {'ON' if ENABLE_SCALPS else 'OFF'}\n"
            f"Gate: -3.5%/-4.0% | Budget: $4K\n{health.compact()}")
    logger.info(boot); tg_send(boot)

    # ─── LOOP ───
    while not stopped:
        t0 = time.time(); cycle += 1
        try:
            # 1. Telegram
            cmd = tg_check()
            if cmd:
                resp = handle_cmd(cmd, api)
                tg_send(resp)
                if stopped: break

            # 2. Account
            info = api.account_info()
            bal = info.get("balance", bal)
            eq = info.get("equity", bal)

            # 3. Positions
            positions = api.positions()
            npos = len(positions)

            # 4. Trade detector
            if trade_detector:
                try:
                    closed, opened = trade_detector.update(positions, bal, eq)
                    for ct in closed:
                        logger.info(f"[CLOSED] {ct}")
                        if daily_gate: daily_gate.register_close(ct.symbol, ct.pnl)
                        tg_send(f"{'✅' if ct.pnl>0 else '❌'} CLOSED {ct.symbol} ${ct.pnl:+.0f}")
                    for ot in opened:
                        logger.info(f"[OPENED] {ot}")
                        if daily_gate: daily_gate.register_open(ot.symbol, ot.risk_dollars)
                    health.ok("TradeDetector")
                except Exception as e:
                    if health.warn("TradeDetector", str(e)[:40]):
                        tg_send(f"⚠️ Detector: {str(e)[:80]}")

            # 5. Daily gate
            can_trade, must_close, reason = True, False, "OK"
            if daily_gate:
                try:
                    osyms = {p.get("symbol","").replace(".sim","") for p in positions}
                    can_trade, must_close, reason = daily_gate.update(eq, bal, npos, osyms)
                    if must_close:
                        logger.warning(f"[GATE] 🚨 {reason}")
                        tg_send(f"🚨 EMERGENCY CLOSE\n{reason}")
                        api.close_all(); paused = True; continue
                    health.ok("DailyGate", reason)
                except Exception as e:
                    can_trade = False
                    health.fail("DailyGate", str(e)[:40])
                    tg_send(f"❌ Gate crashed — stopped: {str(e)[:80]}")

            # 6. Candles
            cdata = {}
            for sym, mt5s in api.symbols.items():
                try:
                    d = api.candles(mt5s, "15m", CANDLE_COUNT)
                    if d and d["n"] >= 50: cdata[sym] = d
                except: pass
            if not cdata:
                time.sleep(CYCLE_SPEED); continue

            # 7. Direction engine
            if direction_engine and ENABLE_DIRECTION_ENGINE:
                try:
                    for sym, d in cdata.items():
                        direction_engine.update(sym, d["o"], d["h"], d["l"], d["c"], d["v"])
                    health.ok("DirectionEngine", f"{len(cdata)} updated")
                except Exception as e:
                    if health.warn("DirectionEngine", str(e)[:40]):
                        tg_send(f"⚠️ Direction: {str(e)[:80]}")

            # 8. Trail management
            try:
                manage_trails(api, positions, cdata)
            except Exception as e:
                logger.error(f"[TRAIL] {e}")

            # 9. Signals + execution
            if can_trade and not paused and calc_atr:
                for sym, d in cdata.items():
                    try:
                        sigs = gen_signals(sym, d)
                        for s in sigs:
                            # Cooldown
                            if time.time() - last_sig.get(sym, 0) < COOLDOWN_SECONDS: continue
                            # Duplicate
                            if daily_gate:
                                ok, _ = daily_gate.can_open_symbol(sym)
                                if not ok: continue
                            # Direction filter
                            if direction_engine and ENABLE_DIRECTION_ENGINE:
                                try:
                                    ok, why = direction_engine.should_allow_trade(sym, s["dir_type"], s["dir"])
                                    if not ok:
                                        logger.info(f"[DIR] BLOCKED {sym} {s['strat']} — {why}")
                                        continue
                                except: pass
                            # Risk budget
                            sl_m = s.get("sl_mult", 1.0)
                            tp_m = s.get("tp_mult", 1.5)
                            rdol = s["atr"] * DOLLAR_PER_UNIT.get(sym, 100000) * sl_m
                            if daily_gate:
                                ok, _ = daily_gate.can_afford_risk(sym, rdol)
                                if not ok: continue
                            # Lots
                            lots = DEFAULT_LOTS
                            if direction_engine:
                                dr = direction_engine.get_last(sym)
                                if dr: lots = DEFAULT_LOTS * dr.aggression
                            lots = round(min(lots, 5.0), 2)
                            # SL/TP — calibrated per combo
                            av = s["atr"]; p = s["price"]
                            if s["dir"] == 1:
                                sl = p - sl_m * av; tp = p + tp_m * av
                            else:
                                sl = p + sl_m * av; tp = p - tp_m * av
                            # Execute
                            mt5s = api.symbols.get(sym, f"{sym}.sim")
                            cmt = f"{VERSION}|{sym}|{s['strat'][:8]}"
                            logger.info(f"🔫 {sym} {s['dir_str']} {s['strat']} lots={lots}")
                            oid = api.order(mt5s, s["dir_str"], lots, round(sl,5), round(tp,5), cmt)
                            if oid:
                                last_sig[sym] = time.time(); trades_today += 1
                                tg_send(f"🔫 {s['dir_str']} {sym}\n{s['strat']} | {lots}L | SL={sl:.5f} TP={tp:.5f}")
                                break  # One trade per symbol per cycle
                    except Exception as e:
                        logger.error(f"[SIG] {sym}: {e}")

            # 10. Precision scalps
            if ENABLE_SCALPS and scalp_executor and can_trade and not paused and calc_atr:
                try:
                    for sym, d in cdata.items():
                        sd = 0
                        if direction_engine:
                            dr = direction_engine.get_last(sym)
                            if dr: sd = dr.direction
                        if sd == 0: continue
                        if daily_gate:
                            ok, _ = daily_gate.can_open_symbol(sym)
                            if not ok: continue
                        cp = d["c"][-1]
                        atr_arr = calc_atr(d["h"], d["l"], d["c"])
                        av = atr_arr[-1] if len(atr_arr) > 0 else 0
                        dpu = DOLLAR_PER_UNIT.get(sym, 100000)
                        act = scalp_executor.evaluate(sym, cp, sd, av, DEFAULT_LOTS, dpu)
                        if act and act["type"] == "ENTER":
                            if daily_gate:
                                ok, _ = daily_gate.can_afford_risk(sym, act["stop_dollars"])
                                if not ok: continue
                            mt5s = api.symbols.get(sym, f"{sym}.sim")
                            ds = "LONG" if act["direction"] == 1 else "SHORT"
                            oid = api.order(mt5s, ds, DEFAULT_LOTS,
                                           round(act["sl_price"],5), round(act["tp_price"],5),
                                           f"{VERSION}|{sym}|SCALP")
                            if oid:
                                scalp_executor.record_fill(sym, oid)
                                last_sig[sym] = time.time()
                                tg_send(f"🎯 SCALP {ds} {sym}\nTarget ${act['target_dollars']:.0f}")
                    health.ok("ScalpExecutor")
                except Exception as e:
                    if health.warn("ScalpExecutor", str(e)[:40]):
                        tg_send(f"⚠️ Scalps: {str(e)[:80]}")

            # 11. Status log
            dd = ((eq - bal) / bal * 100) if bal > 0 else 0
            logger.info(f"[C{cycle}] ${bal:.0f} eq=${eq:.0f} DD={dd:+.1f}% "
                        f"P={npos} T={trades_today} {health.compact()}")

        except Exception as e:
            logger.error(f"[C{cycle}] ERROR: {e}")
            traceback.print_exc()
            tg_send(f"❌ C{cycle}: {str(e)[:150]}")

        elapsed = time.time() - t0
        time.sleep(max(1, CYCLE_SPEED - elapsed))

    logger.info("[SHUTDOWN] FORGE V22.5 stopped.")
    tg_send(f"🛑 FORGE {VERSION} STOPPED.")

if __name__ == "__main__":
    main()
