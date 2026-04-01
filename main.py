"""
TITAN FORGE V22.6-AGG — MAIN DEPLOYMENT SCRIPT
=================================================
AGGRESSIVE REBUILD from V22.5 diagnostic findings.

CHANGES FROM V22.5:
  1. DEFAULT_LOTS 2.0 → 1.0 (budget lasted 2 trades, now lasts 6+)
  2. COOLDOWN 90s → 45s (more re-entry opportunities)
  3. Risk budget $4K → $6K (room for 6+ concurrent trades)
  4. Direction engine: ALL strategies allowed in all regimes (compass not wall)
  5. Scalps: direction=0 uses momentum fallback instead of blocking
  6. Gate sync: _open_symbols synced with broker each cycle (no phantom blocks)
  7. Trailing stops: Chandelier exit — tight at profit, wide at entry (don't leave $$$)
  8. Signal lookback: checks last 3 bars not just last 1 (catch recent signals)
  9. Dynamic lots: scales with budget remaining
 10. Polygon fallback: MetaAPI SDK for symbols Polygon can't serve

PHILOSOPHY: "ALL GAS FIRST, THEN BRAKES WHEN NEEDED"
  - Enter on every qualified setup across all regimes
  - Brakes ONLY when equity threatens prop limits (-3.5%/-4.0%)
  - NEVER block a trade because of regime/strategy type mismatch
  - Exits squeeze every dollar — trailing gets TIGHTER as profit grows

ENV VARS REQUIRED:
  META_API_TOKEN, META_API_ACCOUNT_ID, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""

import os, sys, time, json, logging, traceback, asyncio
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

VERSION = "V22.6-AGG"
META_API_TOKEN = os.environ.get("META_API_TOKEN", os.environ.get("METAAPI_TOKEN", os.environ.get("TOKEN", "")))
META_API_ACCOUNT_ID = os.environ.get("META_API_ACCOUNT_ID", os.environ.get("METAAPI_ACCOUNT_ID", os.environ.get("ACCOUNT_ID", "")))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", os.environ.get("TELEGRAM_BOT_TOKEN", ""))
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", os.environ.get("CHAT_ID", ""))

CYCLE_SPEED = 20
DEFAULT_LOTS = 1.0       # FIX: was 2.0 — budget exhausted in 2 trades
CANDLE_COUNT = 300
SIGNAL_LOOKBACK = 3      # NEW: check last 3 bars for signals, not just bar[-1]

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

CALIBRATED_COMBOS = {
    "VWAP_TREND": {
        "GBPJPY": (2.0, 4.0), "NZDUSD": (1.5, 3.0), "USDJPY": (2.0, 4.0),
        "EURUSD": (1.0, 2.0), "AUDUSD": (2.0, 2.0), "USOIL": (2.0, 2.0),
    },
    "LONDON_BREAKOUT": {
        "US100": (2.0, 4.0), "GBPJPY": (0.8, 2.0), "AUDNZD": (1.0, 1.5),
        "USDJPY": (2.0, 2.0), "USOIL": (2.0, 2.0),
    },
    "PREV_DAY_HL": {"XAUUSD": (0.5, 1.5), "GBPJPY": (1.0, 3.0)},
    "MEAN_REVERT": {"GBPJPY": (1.0, 3.0), "USDJPY": (0.8, 2.0), "USDCHF": (2.0, 4.0)},
    "BREAKOUT": {"XAUUSD": (1.0, 1.5), "GBPJPY": (1.0, 3.0)},
    "PREV_DAY_BOUNCE": {"EURJPY": (2.0, 4.0), "USDJPY": (2.0, 4.0)},
    "LIQUIDITY_SWEEP": {"XAUUSD": (0.5, 0.75), "BTCUSD": (1.0, 3.0), "EURGBP": (2.0, 3.0)},
    "MOMENTUM_CONT": {"US100": (1.0, 3.0)},
    "ASIAN_RANGE": {"US100": (1.5, 2.0)},
    "VOL_SQUEEZE": {"US100": (1.5, 3.0)},
}

ENABLED_STRATEGIES = list(CALIBRATED_COMBOS.keys())

ENABLE_SCALPS = True
ENABLE_DIRECTION_ENGINE = True
COOLDOWN_SECONDS = 45    # FIX: was 90 — too slow for aggressive system

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
                               max_risk_budget=6000, max_open=8)  # FIX: was 4000
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
        self.symbols: Dict[str, str] = {}
        self.connection = None
        self.account = None
        self.meta_api = None

    async def connect(self):
        try:
            from metaapi_cloud_sdk import MetaApi
            self.meta_api = MetaApi(self.token)
            logger.info(f"[MT5] Getting account {self.acct[:12]}...")
            self.account = await self.meta_api.metatrader_account_api.get_account(self.acct)
            logger.info(f"[MT5] State: {self.account.state} | Region: {self.account.region}")
            if self.account.state != 'DEPLOYED':
                logger.info("[MT5] Deploying...")
                await self.account.deploy()
            logger.info("[MT5] Waiting for connection...")
            await self.account.wait_connected()
            self.connection = self.account.get_rpc_connection()
            await self.connection.connect()
            logger.info("[MT5] Waiting for sync...")
            await self.connection.wait_synchronized()
            logger.info("[MT5] ✅ Connected and synchronized!")
            return True
        except Exception as e:
            logger.error(f"[MT5] Connection failed: {e}")
            return False

    async def account_info(self):
        try:
            info = await self.connection.get_account_information()
            logger.info(f"[MT5] bal=${info.get('balance',0)} eq=${info.get('equity',0)}")
            return info
        except Exception as e:
            logger.error(f"[MT5] Account info error: {e}")
        return {"balance": 0, "equity": 0}

    async def positions(self):
        try:
            return await self.connection.get_positions()
        except: return []

    async def candles(self, symbol, tf="15m", count=300):
        """
        FIX: Try Polygon first, then MetaAPI SDK fallback.
        Polygon can't serve US100 (I:NDX) or USOIL on Currencies plan.
        """
        clean = symbol.replace(".sim", "")

        # ── METHOD 1: POLYGON ──
        poly_map = {
            "EURUSD": "C:EURUSD", "GBPUSD": "C:GBPUSD", "USDJPY": "C:USDJPY",
            "USDCHF": "C:USDCHF", "EURGBP": "C:EURGBP", "GBPJPY": "C:GBPJPY",
            "NZDUSD": "C:NZDUSD", "AUDUSD": "C:AUDUSD", "AUDNZD": "C:AUDNZD",
            "EURJPY": "C:EURJPY", "XAUUSD": "C:XAUUSD", "BTCUSD": "X:BTCUSD",
            # FIX: removed US100 and USOIL — they don't work on Currencies plan
        }
        ticker = poly_map.get(clean)
        if ticker:
            try:
                from datetime import timedelta
                end = datetime.now(timezone.utc)
                start = end - timedelta(days=8)
                poly_key = os.environ.get("POLYGON_API_KEY", "SV9EsHr4rokUMivv7dyjTTsuwn_Eg9Za")
                url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/15/minute/"
                       f"{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}"
                       f"?adjusted=true&sort=asc&limit={count}&apiKey={poly_key}")
                r = req.get(url, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    bars = data.get("results", [])
                    if bars and len(bars) >= 50:
                        return self._parse_candles_polygon(bars)
                    else:
                        logger.warning(f"[CANDLE] Polygon {clean}: only {len(bars) if bars else 0} bars")
                else:
                    logger.warning(f"[CANDLE] Polygon {clean}: HTTP {r.status_code}")
            except Exception as e:
                logger.warning(f"[CANDLE] Polygon {clean}: {e}")

        # ── METHOD 2: METAAPI SDK (fallback for all, primary for US100/USOIL) ──
        try:
            mt5_sym = symbol if ".sim" in symbol else f"{clean}.sim"
            candle_data = await self.connection.get_historical_candles(
                mt5_sym, "15m", None, count
            )
            if candle_data and len(candle_data) >= 50:
                return self._parse_candles(candle_data)
        except Exception as e:
            logger.debug(f"[CANDLE] SDK historical {clean}: {e}")

        # ── METHOD 3: MetaAPI REST market-data endpoint ──
        try:
            region = getattr(self.account, 'region', 'london')
            headers = {"auth-token": self.token}
            url = (f"https://mt-market-data-client-api-v1.{region}.agiliumtrade.agiliumtrade.ai/"
                   f"users/current/accounts/{self.acct}/historical-market-data/symbols/"
                   f"{symbol}/timeframes/15m/candles?limit={count}")
            r = req.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                bars = r.json()
                if bars and len(bars) >= 50:
                    return self._parse_candles(bars)
        except Exception as e:
            logger.debug(f"[CANDLE] REST market-data {clean}: {e}")

        # ── METHOD 4: MetaAPI REST client endpoint ──
        try:
            region = getattr(self.account, 'region', 'london')
            headers = {"auth-token": self.token}
            url = (f"https://mt-client-api-v1.{region}.agiliumtrade.agiliumtrade.ai/"
                   f"users/current/accounts/{self.acct}/historical-market-data/symbols/"
                   f"{symbol}/timeframes/15m/candles?limit={count}")
            r = req.get(url, headers=headers, timeout=15)
            if r.status_code == 200:
                bars = r.json()
                if bars and len(bars) >= 50:
                    return self._parse_candles(bars)
        except Exception as e:
            logger.debug(f"[CANDLE] REST client {clean}: {e}")

        logger.warning(f"[CANDLE] ❌ ALL methods failed for {clean}")
        return None

    def _parse_candles(self, cc):
        return {
            "o": np.array([c["open"] for c in cc]),
            "h": np.array([c["high"] for c in cc]),
            "l": np.array([c["low"] for c in cc]),
            "c": np.array([c["close"] for c in cc]),
            "v": np.array([c.get("tickVolume", c.get("volume", 1)) for c in cc]),
            "n": len(cc),
        }

    def _parse_candles_polygon(self, bars):
        return {
            "o": np.array([b["o"] for b in bars]),
            "h": np.array([b["h"] for b in bars]),
            "l": np.array([b["l"] for b in bars]),
            "c": np.array([b["c"] for b in bars]),
            "v": np.array([b.get("v", 1) for b in bars]),
            "n": len(bars),
        }

    async def order(self, symbol, direction, lots, sl=0, tp=0, comment=""):
        try:
            opts = {"comment": comment[:63]}
            if sl > 0: opts["stopLoss"] = sl
            if tp > 0: opts["takeProfit"] = tp
            if direction == "LONG":
                result = await self.connection.create_market_buy_order(symbol, lots, **opts)
            else:
                result = await self.connection.create_market_sell_order(symbol, lots, **opts)
            oid = result.get("orderId", result.get("positionId", ""))
            logger.info(f"[MT5] ✅ {direction} {symbol} {lots} SL={sl} TP={tp} ID={oid}")
            return str(oid)
        except Exception as e:
            logger.error(f"[MT5] Order error: {e}")
        return None

    async def close(self, pid):
        try:
            await self.connection.close_position(pid)
            return True
        except: return False

    async def modify_sl(self, pid, sl):
        try:
            await self.connection.modify_position(pid, stop_loss=sl)
            return True
        except: return False

    async def close_all(self):
        closed = 0
        for p in await self.positions():
            pid = str(p.get("id", ""))
            if pid and await self.close(pid): closed += 1
        return closed

    async def resolve_symbols(self):
        for name, mt5s in INSTRUMENTS.items():
            for sym_try in [mt5s, name]:
                try:
                    spec = await self.connection.get_symbol_specification(sym_try)
                    if spec:
                        self.symbols[name] = sym_try
                        logger.info(f"  ✅ {name} → {sym_try}")
                        break
                except:
                    pass
            else:
                logger.warning(f"  ⚠️ {name} not found")


# ═══════════════════════════════════════════════════════════════
# TRAILING STOP MANAGER — DON'T LEAVE MONEY ON THE TABLE
# ═══════════════════════════════════════════════════════════════
#
# PHILOSOPHY: Let winners run, but lock in profit progressively.
#   - DON'T move to BE too early (0.3R was killing good trades)
#   - Chandelier trail: trail from the HIGH of the move, not from entry
#   - Trail gets TIGHTER as profit grows (squeeze the last dollar out)
#   - At 1R+: trail 2.0 ATR from peak (room to breathe)
#   - At 2R+: trail 1.2 ATR from peak (tightening)
#   - At 3R+: trail 0.8 ATR from peak (squeezing)
#   - At 5R+: trail 0.5 ATR from peak (don't give back a monster)

_position_peaks: Dict[str, float] = {}  # pid → highest profit seen

async def manage_trails(api, positions, cdata):
    global _position_peaks
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

            # Track peak profit for chandelier exit
            if pid not in _position_peaks:
                _position_peaks[pid] = cp
            if d == 1:
                _position_peaks[pid] = max(_position_peaks[pid], cp)
            else:
                _position_peaks[pid] = min(_position_peaks[pid], cp)
            peak = _position_peaks[pid]

            # ── CHANDELIER TRAILING (trail from peak, not entry) ──
            nsl = csl
            if rm >= 5.0:
                # Monster winner — squeeze tight: 0.5 ATR from peak
                t = peak - 0.5 * av if d == 1 else peak + 0.5 * av
            elif rm >= 3.0:
                # Big winner — tight trail: 0.8 ATR from peak
                t = peak - 0.8 * av if d == 1 else peak + 0.8 * av
            elif rm >= 2.0:
                # Good winner — moderate trail: 1.2 ATR from peak
                t = peak - 1.2 * av if d == 1 else peak + 1.2 * av
            elif rm >= 1.0:
                # In profit — wide trail: 2.0 ATR from peak
                t = peak - 2.0 * av if d == 1 else peak + 2.0 * av
            elif rm >= 0.5:
                # FIX: BE at 0.5R not 0.3R — give trade room to work
                t = ep + av * 0.05 if d == 1 else ep - av * 0.05  # Tiny profit lock
            else:
                continue

            if d == 1:
                nsl = max(csl, t)
                if nsl > csl and nsl > ep * 0.9:  # Sanity: don't trail below 90% of entry
                    await api.modify_sl(pid, round(nsl, 5))
                    logger.info(f"[TRAIL] {sym} {pid[:8]} {rm:.1f}R SL→{nsl:.5f} (peak={peak:.5f})")
            else:
                nsl = min(csl, t) if csl > 0 else t
                if csl == 0 or nsl < csl:
                    await api.modify_sl(pid, round(nsl, 5))
                    logger.info(f"[TRAIL] {sym} {pid[:8]} {rm:.1f}R SL→{nsl:.5f} (peak={peak:.5f})")
        except Exception as e:
            logger.error(f"[TRAIL] {pos.get('id','?')}: {e}")

    # Clean up peaks for closed positions
    open_pids = {str(p.get("id", "")) for p in positions}
    stale = [pid for pid in _position_peaks if pid not in open_pids]
    for pid in stale:
        del _position_peaks[pid]


# ═══════════════════════════════════════════════════════════════
# SIGNAL GENERATOR — AGGRESSIVE: CHECK LAST 3 BARS
# ═══════════════════════════════════════════════════════════════

def gen_signals(sym, data):
    """
    FIX: Check last SIGNAL_LOOKBACK bars (3) not just bar[-1].
    A signal on bar[-2] or bar[-3] is still actionable on M15 (45 min window).
    This dramatically increases signal throughput.
    """
    signals = []
    for sname in ENABLED_STRATEGIES:
        if sname not in ALL_STRATEGIES: continue
        combo = CALIBRATED_COMBOS.get(sname, {})
        if sym not in combo: continue
        sl_mult, tp_mult = combo[sym]
        fn, meta = ALL_STRATEGIES[sname]
        try:
            sigs = fn(o=data["o"], h=data["h"], l=data["l"], c=data["c"], v=data["v"])
            if len(sigs) < 1:
                continue

            # Check last SIGNAL_LOOKBACK bars for signals (most recent first)
            for lookback_i in range(min(SIGNAL_LOOKBACK, len(sigs))):
                idx = -(lookback_i + 1)
                if sigs[idx] != 0:
                    atr_arr = calc_atr(data["h"], data["l"], data["c"])
                    signals.append({
                        "sym": sym, "strat": sname, "dir": int(sigs[idx]),
                        "dir_str": "LONG" if sigs[idx] > 0 else "SHORT",
                        "dir_type": meta.direction_type,
                        "atr": atr_arr[-1] if len(atr_arr) > 0 else 0,
                        "price": data["c"][-1],  # Always use current price for entry
                        "sl_mult": sl_mult, "tp_mult": tp_mult,
                        "signal_age": lookback_i,  # 0=current bar, 1=prev, 2=2 ago
                    })
                    break  # One signal per strategy (most recent)
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

async def handle_cmd(cmd, api):
    global paused, stopped
    if cmd == "/stop":
        stopped = True; paused = True
        c = await api.close_all()
        return f"🛑 STOPPED. Closed {c} positions."
    elif cmd == "/pause":
        paused = True
        return "⏸️ PAUSED. Managing open only."
    elif cmd == "/resume":
        paused = False
        return "▶️ RESUMED."
    elif cmd == "/status":
        i = await api.account_info(); p = await api.positions()
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
    asyncio.run(_main())

async def _main():
    global paused, stopped, cycle, trades_today

    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info(f"║  NEXUS CAPITAL — TITAN FORGE {VERSION}                ║")
    logger.info("║  AGGRESSIVE | Direction | Scalps | Gate | Chandelier   ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    if not META_API_TOKEN or not META_API_ACCOUNT_ID:
        logger.error("Missing META_API_TOKEN or META_API_ACCOUNT_ID"); return

    api = API(META_API_TOKEN, META_API_ACCOUNT_ID)
    logger.info("[BOOT] Connecting...")
    if not await api.connect():
        await asyncio.sleep(30)
        if not await api.connect():
            tg_send(f"❌ FORGE {VERSION} — connection FAILED"); return
    health.ok("MetaAPI")

    logger.info("[BOOT] Resolving symbols...")
    await api.resolve_symbols()

    info = await api.account_info()
    bal = info.get("balance", 100000)
    if daily_gate: daily_gate.set_sod_balance(bal)

    n_combos = sum(len(v) for v in CALIBRATED_COMBOS.values())
    boot = (f"🚀 FORGE {VERSION} ONLINE\nBal: ${bal:,.0f}\n"
            f"Instruments: {len(api.symbols)}\n"
            f"Strats: {len(ENABLED_STRATEGIES)} | Combos: {n_combos}\n"
            f"Scalps: {'ON' if ENABLE_SCALPS else 'OFF'}\n"
            f"Lots: {DEFAULT_LOTS} | Cooldown: {COOLDOWN_SECONDS}s\n"
            f"Gate: -3.5%/-4.0% | Budget: $6K\n{health.compact()}")
    logger.info(boot); tg_send(boot)

    # ─── LOOP ───
    while not stopped:
        t0 = time.time(); cycle += 1
        try:
            # 1. Telegram
            cmd = tg_check()
            if cmd:
                resp = await handle_cmd(cmd, api)
                tg_send(resp)
                if stopped: break

            # 2. Account
            info = await api.account_info()
            bal = info.get("balance", bal)
            eq = info.get("equity", bal)

            # 3. Positions
            positions = await api.positions()
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

            # 4.5 FIX: Sync daily gate with actual positions (prevents phantom blocks)
            if daily_gate:
                try:
                    daily_gate.sync_open_symbols(positions)
                except: pass

            # 5. Daily gate
            can_trade, must_close, reason = True, False, "OK"
            if daily_gate:
                try:
                    osyms = {p.get("symbol","").replace(".sim","") for p in positions}
                    can_trade, must_close, reason = daily_gate.update(eq, bal, npos, osyms)
                    if must_close:
                        logger.warning(f"[GATE] 🚨 {reason}")
                        tg_send(f"🚨 EMERGENCY CLOSE\n{reason}")
                        await api.close_all(); paused = True; continue
                    health.ok("DailyGate", reason)
                except Exception as e:
                    can_trade = False
                    health.fail("DailyGate", str(e)[:40])
                    tg_send(f"❌ Gate crashed — stopped: {str(e)[:80]}")

            # 6. Candles
            cdata = {}
            for sym, mt5s in api.symbols.items():
                try:
                    d = await api.candles(mt5s, "15m", CANDLE_COUNT)
                    if d and d["n"] >= 50: cdata[sym] = d
                except: pass
            if not cdata:
                logger.warning(f"[C{cycle}] No candle data — skipping")
                await asyncio.sleep(CYCLE_SPEED); continue

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
                await manage_trails(api, positions, cdata)
            except Exception as e:
                logger.error(f"[TRAIL] {e}")

            # 9. Signals + execution — AGGRESSIVE
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
                            # Direction filter (only blocks counter-trend in STRONG trend)
                            if direction_engine and ENABLE_DIRECTION_ENGINE:
                                try:
                                    ok, why = direction_engine.should_allow_trade(sym, s["dir_type"], s["dir"])
                                    if not ok:
                                        logger.info(f"[DIR] BLOCKED {sym} {s['strat']} — {why}")
                                        continue
                                except: pass
                            # Risk budget with dynamic lot sizing
                            sl_m = s.get("sl_mult", 1.0)
                            tp_m = s.get("tp_mult", 1.5)
                            atr_val = s["atr"]
                            dpu = DOLLAR_PER_UNIT.get(sym, 100000)
                            rdol_per_lot = atr_val * dpu * sl_m  # Risk per 1.0 lot

                            # FIX: Dynamic lot sizing based on remaining budget
                            lots = DEFAULT_LOTS
                            if direction_engine:
                                dr = direction_engine.get_last(sym)
                                if dr: lots = DEFAULT_LOTS * dr.aggression

                            # Cap lots so risk fits within remaining budget
                            if daily_gate and rdol_per_lot > 0:
                                remaining = daily_gate.max_risk_budget - sum(daily_gate._open_risk.values())
                                max_lots_for_budget = remaining / rdol_per_lot
                                lots = min(lots, max_lots_for_budget)

                            lots = round(max(min(lots, 5.0), 0.01), 2)

                            rdol = rdol_per_lot * lots
                            if daily_gate:
                                ok, _ = daily_gate.can_afford_risk(sym, rdol)
                                if not ok: continue

                            # SL/TP — calibrated per combo
                            av = atr_val; p = s["price"]
                            if s["dir"] == 1:
                                sl = p - sl_m * av; tp = p + tp_m * av
                            else:
                                sl = p + sl_m * av; tp = p - tp_m * av
                            # Execute
                            mt5s = api.symbols.get(sym, f"{sym}.sim")
                            age = s.get("signal_age", 0)
                            age_str = f" age={age}" if age > 0 else ""
                            cmt = f"{VERSION}|{sym}|{s['strat'][:8]}"
                            logger.info(f"🔫 {sym} {s['dir_str']} {s['strat']} lots={lots}{age_str}")
                            oid = await api.order(mt5s, s["dir_str"], lots, round(sl,5), round(tp,5), cmt)
                            if oid:
                                last_sig[sym] = time.time(); trades_today += 1
                                tg_send(f"🔫 {s['dir_str']} {sym}\n{s['strat']} | {lots}L | "
                                        f"SL={sl:.5f} TP={tp:.5f}{age_str}")
                                break  # One trade per symbol per cycle
                    except Exception as e:
                        logger.error(f"[SIG] {sym}: {e}")

            # 10. Precision scalps — FIX: direction=0 uses momentum fallback
            if ENABLE_SCALPS and scalp_executor and can_trade and not paused and calc_atr:
                try:
                    for sym, d in cdata.items():
                        sd = 0
                        if direction_engine:
                            dr = direction_engine.get_last(sym)
                            if dr: sd = dr.direction

                        # FIX: When direction=0, use 5-bar momentum instead of blocking
                        if sd == 0:
                            if len(d["c"]) >= 6:
                                sd = 1 if d["c"][-1] > d["c"][-6] else -1
                            else:
                                continue  # Not enough data

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
                            oid = await api.order(mt5s, ds, DEFAULT_LOTS,
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
                        f"P={npos} T={trades_today} cd={len(cdata)} {health.compact()}")

        except Exception as e:
            logger.error(f"[C{cycle}] ERROR: {e}")
            traceback.print_exc()
            tg_send(f"❌ C{cycle}: {str(e)[:150]}")

        elapsed = time.time() - t0
        await asyncio.sleep(max(1, CYCLE_SPEED - elapsed))

    logger.info(f"[SHUTDOWN] FORGE {VERSION} stopped.")
    tg_send(f"🛑 FORGE {VERSION} STOPPED.")

if __name__ == "__main__":
    main()
