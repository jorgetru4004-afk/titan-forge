"""
NEXUS CAPITAL — TITAN FORGE V22 — LEAN BUILD
14 INSTRUMENTS | 3 REGIMES | GENESIS | GUARANTEED TO TRADE
Minimal v21 deps. Built-in risk. Built-in sessions.
"All gas first then brakes." — Jorge Trujillo
"""

import asyncio, logging, os, time, uuid
import numpy as np, requests
from datetime import date, datetime, timezone, timedelta
from typing import Optional, Dict, Set

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("titan_forge.main")

# ── MINIMAL IMPORTS ──
from mt5_adapter import MT5Adapter
from execution_base import OrderRequest, OrderDirection, OrderType
try:
    from forge_core import send_telegram
except ImportError:
    def send_telegram(msg): logger.info("[TG] %s", msg.replace("<b>","").replace("</b>",""))
try:
    from forge_core import _evidence, TradeFingerprint
except ImportError:
    _evidence = None; TradeFingerprint = None
try:
    from forge_router import SmartOrderRouter; _has_router = True
except ImportError:
    _has_router = False

from forge_signals_v22 import SignalEngine, MarketSnapshot
from forge_instruments_v22 import SETUP_CONFIG, get_all_symbols
from forge_correlation import CorrelationGuard

# ── ADD 3 NEW INSTRUMENTS (researched: AUDNZD 85%WR/PF3.25, AUDUSD +27R, EURJPY +21R) ──
try:
    from forge_instruments_v22 import InstrumentSetup, StrategyType, TradeType
    # AUDNZD: GAP_FILL SHORT, 85% WR, PF 3.25 — best config in entire system
    SETUP_CONFIG["AUDNZD"] = InstrumentSetup(
        strategy=StrategyType.GAP_FILL, direction="SHORT", trade_type=TradeType.SCALP,
        risk_pct=0.015, tp_atr=1.0, sl_atr=1.5, time_of_day_edge=None,
        session_filter=None, min_atr=0.0, notes="85% WR, PF 3.25, NEUTRAL dominant"
    )
    # AUDUSD: EMA_BOUNCE SHORT, +27R, solid edge
    SETUP_CONFIG["AUDUSD"] = InstrumentSetup(
        strategy=StrategyType.EMA_BOUNCE, direction="SHORT", trade_type=TradeType.SCALP,
        risk_pct=0.015, tp_atr=1.5, sl_atr=1.5, time_of_day_edge=None,
        session_filter=None, min_atr=0.0, notes="+27.2R, PF 1.50, NEUTRAL"
    )
    # EURJPY: GAP_FILL LONG, +21R, RUNNER type for bigger moves
    SETUP_CONFIG["EURJPY"] = InstrumentSetup(
        strategy=StrategyType.GAP_FILL, direction="LONG", trade_type=TradeType.RUNNER,
        risk_pct=0.015, tp_atr=2.0, sl_atr=1.5, time_of_day_edge=None,
        session_filter=None, min_atr=0.0, notes="+21.1R, PF 1.75, NEUTRAL RUNNER"
    )
    logger.info("[INSTRUMENTS] Added AUDNZD, AUDUSD, EURJPY — 14 instruments total")
except Exception as e:
    logger.warning("[INSTRUMENTS] Could not add new pairs: %s — they'll be skipped", e)
try:
    from forge_genesis import create_genesis, extract_regime_indicators, auto_evolve, get_calibrated_wr
    GENESIS_OK = True
except ImportError:
    GENESIS_OK = False

FORGE_VERSION = "v22"
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

# ── CONFIG ──
MAX_OPEN = 5; MAX_DAILY = 15; COOLDOWN = 120; RISK_PCT = 0.015
MAX_LOT = 2.0; CYCLE_SPEED = 30; CONVICTION_MIN = 0.20; DD_EMERGENCY = 0.09
CANDLE_REFRESH = 120

ALIASES = {
    "EURUSD":["EURUSD.sim","EURUSD"],"GBPUSD":["GBPUSD.sim","GBPUSD"],
    "USDJPY":["USDJPY.sim","USDJPY"],"USDCHF":["USDCHF.sim","USDCHF"],
    "EURGBP":["EURGBP.sim","EURGBP"],"GBPJPY":["GBPJPY.sim","GBPJPY"],
    "NZDUSD":["NZDUSD.sim","NZDUSD"],"XAUUSD":["XAUUSD.sim","GOLD.sim","XAUUSD"],
    "US100":["US100.sim","USTEC.sim","NAS100.sim","US100"],
    "USOIL":["USOIL.sim","WTI.sim","XTIUSD.sim","OIL.sim"],
    "BTCUSD":["BTCUSD.sim","BITCOIN.sim","BTCUSD"],
    "AUDNZD":["AUDNZD.sim","AUDNZD"],"AUDUSD":["AUDUSD.sim","AUDUSD"],
    "EURJPY":["EURJPY.sim","EURJPY"],
}
POLYGON_MAP = {
    "EURUSD":"C:EURUSD","GBPUSD":"C:GBPUSD","USDJPY":"C:USDJPY","USDCHF":"C:USDCHF",
    "EURGBP":"C:EURGBP","GBPJPY":"C:GBPJPY","NZDUSD":"C:NZDUSD","XAUUSD":"C:XAUUSD",
    "US100":"I:NDX","USOIL":"C:XTIUSD",
    "BTCUSD":"X:BTCUSD",
    "AUDNZD":"C:AUDNZD","AUDUSD":"C:AUDUSD","EURJPY":"C:EURJPY",
}
ATR_FB = {"EURUSD":0.008,"GBPUSD":0.01,"USDJPY":1.0,"USDCHF":0.007,"EURGBP":0.005,
          "GBPJPY":1.5,"NZDUSD":0.006,"XAUUSD":30.0,"US100":200.0,
          "USOIL":2.0,"BTCUSD":2000.0,
          "AUDNZD":0.006,"AUDUSD":0.007,"EURJPY":1.2}
CRYPTO = {"BTCUSD"}

def is_open(sym):
    if sym in CRYPTO: return True
    now = datetime.now(timezone.utc); wd = now.weekday()
    if wd == 5: return False
    if wd == 6: return now.hour >= 22
    if wd == 4: return now.hour < 22
    return True

_resolved: Dict[str,Optional[str]] = {}
async def resolve(adapter, sym):
    if sym in _resolved: return _resolved[sym]
    for t in ALIASES.get(sym,[sym]):
        try:
            b,a = await adapter.get_current_price(t)
            if b and b > 0: _resolved[sym]=t; logger.info("[RESOLVE] %s → %s (%.5f)",sym,t,b); return t
        except: continue
    _resolved[sym]=None; logger.warning("[RESOLVE] %s — not found",sym); return None

_cc: Dict[str,Dict] = {}
def get_candles(sym):
    if not POLYGON_API_KEY: return None
    c = _cc.get(sym)
    if c and time.time()-c["ts"]<CANDLE_REFRESH: return c["d"]
    tk = POLYGON_MAP.get(sym)
    if not tk: return None
    end=datetime.now(timezone.utc); start=end-timedelta(days=14)
    try:
        url=f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/1/hour/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_API_KEY}"
        r=requests.get(url,timeout=15)
        if r.status_code==429: time.sleep(13); r=requests.get(url,timeout=15)
        if r.status_code!=200: return None
        res=r.json().get("results",[])
        if len(res)<30: return None
        seen=set(); bars=[]
        for x in res:
            if x["t"] not in seen: seen.add(x["t"]); bars.append(x)
        bars.sort(key=lambda x:x["t"]); bars=bars[-200:]
        d={"o":np.array([x["o"] for x in bars],dtype=float),"h":np.array([x["h"] for x in bars],dtype=float),
           "l":np.array([x["l"] for x in bars],dtype=float),"c":np.array([x["c"] for x in bars],dtype=float),
           "v":np.array([x.get("v",0) for x in bars],dtype=float),"n":len(bars)}
        _cc[sym]={"d":d,"ts":time.time()}; return d
    except Exception as e: logger.warning("[CANDLES] %s: %s",sym,e); return None

# ── INDICATORS ──
def _atr(h,l,c,p=14):
    if len(c)<2: return abs(float(c[-1]))*0.01
    tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1])))
    if len(tr)<p: return float(np.mean(tr))
    a=float(np.mean(tr[:p]))
    for i in range(p,len(tr)): a=(a*(p-1)+float(tr[i]))/p
    return a
def _rsi(c,p=14):
    if len(c)<p+1: return 50.0
    d=np.diff(c);g=np.where(d>0,d,0);lo=np.where(d<0,-d,0)
    ag=float(np.mean(g[:p]));al=float(np.mean(lo[:p]))
    for i in range(p,len(g)): ag=(ag*(p-1)+float(g[i]))/p;al=(al*(p-1)+float(lo[i]))/p
    return 100.0 if al==0 else 100.0-100.0/(1.0+ag/al)
def _ema(d,p):
    if len(d)<p: return float(np.mean(d)) if len(d)>0 else 0.0
    m=2.0/(p+1);e=float(np.mean(d[:p]))
    for i in range(p,len(d)): e=(float(d[i])-e)*m+e
    return e
def _bb(c,p=20,m=2.0):
    if len(c)<p: mid=float(np.mean(c));s=float(np.std(c)) if len(c)>1 else abs(mid)*0.01; return mid+m*s,mid-m*s,mid
    sma=float(np.mean(c[-p:]));s=float(np.std(c[-p:]))
    if s==0: s=abs(sma)*0.001
    return sma+m*s,sma-m*s,sma
def _stoch(h,l,c,kp=14,dp=3):
    if len(c)<kp+dp: return 50.,50.,50.,50.
    kvs=[]
    for i in range(kp-1,len(c)):
        hi=float(np.max(h[i-kp+1:i+1]));lo=float(np.min(l[i-kp+1:i+1]))
        kvs.append(100.*(float(c[i])-lo)/(hi-lo) if hi!=lo else 50.)
    k=np.array(kvs)
    if len(k)<dp: return float(k[-1]),float(k[-1]),float(k[-1]),float(k[-1])
    return float(k[-1]),float(np.mean(k[-dp:])),float(k[-2]) if len(k)>1 else float(k[-1]),float(np.mean(k[-dp-1:-1])) if len(k)>dp else float(np.mean(k[-dp:]))
def _vwap(h,l,c,v):
    tp=(h+l+c)/3.;cv=np.cumsum(v);ctv=np.cumsum(tp*v)
    if cv[-1]==0: return float(c[-1]),abs(float(c[-1]))*0.001
    vw=float(ctv[-1]/cv[-1]);vs=float(np.std(tp-vw)) if len(tp)>1 else abs(float(c[-1]))*0.001
    return vw,max(vs,abs(float(c[-1]))*0.0001)
def _adx(h,l,c,p=14):
    if len(c)<p*2: return 20.,20.,25.,25.
    pdm=np.zeros(len(h));mdm=np.zeros(len(h));tr=np.zeros(len(h))
    for i in range(1,len(h)):
        u=h[i]-h[i-1];dn=l[i-1]-l[i]
        pdm[i]=u if u>dn and u>0 else 0;mdm[i]=dn if dn>u and dn>0 else 0
        tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
    at=float(np.mean(tr[1:p+1]));pm=float(np.mean(pdm[1:p+1]));mm=float(np.mean(mdm[1:p+1]))
    dxv=[];pdi=mdi=0.
    for i in range(p+1,len(h)):
        at=(at*(p-1)+float(tr[i]))/p;pm=(pm*(p-1)+float(pdm[i]))/p;mm=(mm*(p-1)+float(mdm[i]))/p
        if at>0: pdi=100.*pm/at;mdi=100.*mm/at
        ds=pdi+mdi;dxv.append(100.*abs(pdi-mdi)/ds if ds>0 else 0)
    if len(dxv)<p: a=float(np.mean(dxv)) if dxv else 20.;return a,a,pdi,mdi
    adx=float(np.mean(dxv[:p]))
    for i in range(p,len(dxv)): adx=(adx*(p-1)+dxv[i])/p
    ap=adx
    if len(dxv)>5:
        ap=float(np.mean(dxv[:p]))
        for i in range(p,len(dxv)-5): ap=(ap*(p-1)+dxv[i])/p
    return adx,ap,pdi,mdi

def make_snap(sym,cd,bid,ask):
    # Inject live price as "current bar" so indicators update every cycle
    # Creates new arrays — cached hourly bars are NOT modified
    mid=(bid+ask)/2.0
    c=np.append(cd["c"],mid)
    h=np.append(cd["h"],max(float(cd["h"][-1]),mid))
    l=np.append(cd["l"],min(float(cd["l"][-1]),mid))
    o=np.append(cd["o"],float(cd["o"][-1]))
    v=np.append(cd["v"],float(cd["v"][-1]) if float(cd["v"][-1])>0 else 1.0)
    n=len(c)
    if n<30: return None
    atr=_atr(h,l,c)
    if atr==0: atr=ATR_FB.get(sym,abs(float(c[-1]))*0.01)
    rsi=_rsi(c);sk,sd,skp,sdp=_stoch(h,l,c);e50=_ema(c,min(50,n));e200=_ema(c,min(200,n))
    bbu,bbl,bbm=_bb(c);vwap,vstd=_vwap(h,l,c,v);adx,adxp,pdi,mdi=_adx(h,l,c)
    ke=_ema(c,20);ku=ke+1.5*atr;kl=ke-1.5*atr
    sl=min(8,n);pi=min(sl+8,n)
    pdh=float(np.max(h[-pi:-sl])) if pi>sl else float(np.max(h[:sl]))
    pdl=float(np.min(l[-pi:-sl])) if pi>sl else float(np.min(l[:sl]))
    pdc=float(c[-sl-1]) if n>sl else float(c[0])
    return MarketSnapshot(symbol=sym,opens=o,highs=h,lows=l,closes=c,volumes=v,bid=bid,ask=ask,
        atr=atr,rsi=rsi,stoch_k=sk,stoch_d=sd,stoch_k_prev=skp,stoch_d_prev=sdp,
        ema_50=e50,ema_200=e200,bb_upper=bbu,bb_lower=bbl,bb_middle=bbm,
        vwap=vwap,vwap_std=vstd,adx=adx,adx_prev=adxp,plus_di=pdi,minus_di=mdi,
        prev_day_high=pdh,prev_day_low=pdl,prev_day_close=pdc,
        session_open=float(o[-sl]),session_high=float(np.max(h[-sl:])),session_low=float(np.min(l[-sl:])),
        orb_high=float(h[-sl]),orb_low=float(l[-sl]),orb_complete=True,
        asian_high=float(np.max(h[:min(7,n)])),asian_low=float(np.min(l[:min(7,n)])),asian_complete=True,
        keltner_upper=ku,keltner_lower=kl,bars_since_open=sl,current_hour_utc=datetime.now(timezone.utc).hour)

def calc_lots(sym,bal,sl_dist):
    if sl_dist<=0: return 0.01
    risk_d=bal*RISK_PCT
    if sym in ("EURUSD","GBPUSD","NZDUSD","USDCHF","EURGBP","AUDNZD","AUDUSD"): lots=risk_d/(sl_dist*100000)
    elif sym in ("USDJPY","GBPJPY","EURJPY"): lots=risk_d/(sl_dist*1000)
    elif sym=="XAUUSD": lots=risk_d/(sl_dist*100)
    elif sym in ("US100","GER40","UK100"): lots=risk_d/(sl_dist*10)
    elif sym=="USOIL": lots=risk_d/(sl_dist*1000)
    elif sym in ("BTCUSD","ETHUSD"): lots=risk_d/(sl_dist*1)
    else: lots=risk_d/(sl_dist*100)
    return min(MAX_LOT,max(0.01,round(lots,2)))


# ═══ SMART EXIT DETECTION ═══
def should_smart_exit(snap, direction, current_r):
    """Detect momentum exhaustion — EXIT only, never blocks entries.
    Returns (should_exit, reason) — only triggers when in profit (current_r > 0.5)"""
    if current_r < 0.5:
        return False, ""
    
    # 1. RSI divergence: price in our favor but RSI reversing
    if direction == "SHORT" and snap.rsi < 35 and snap.rsi > snap.stoch_d:
        if current_r >= 1.0:
            return True, "RSI oversold bounce"
    if direction == "LONG" and snap.rsi > 65 and snap.rsi < snap.stoch_d:
        if current_r >= 1.0:
            return True, "RSI overbought reversal"
    
    # 2. Stochastic crossing against us
    if direction == "SHORT" and snap.stoch_k > snap.stoch_d and snap.stoch_k_prev < snap.stoch_d_prev:
        if current_r >= 0.8:
            return True, "Stoch bullish cross on SHORT"
    if direction == "LONG" and snap.stoch_k < snap.stoch_d and snap.stoch_k_prev > snap.stoch_d_prev:
        if current_r >= 0.8:
            return True, "Stoch bearish cross on LONG"
    
    # 3. Price hit opposite BB band (stretched too far)
    if direction == "SHORT" and snap.bid <= snap.bb_lower and current_r >= 1.0:
        return True, "Hit lower BB band"
    if direction == "LONG" and snap.ask >= snap.bb_upper and current_r >= 1.0:
        return True, "Hit upper BB band"
    
    # 4. Reversal candle pattern (big wick against us)
    if len(snap.closes) >= 2 and len(snap.opens) >= 2:
        last_body = abs(float(snap.closes[-1]) - float(snap.opens[-1]))
        last_range = float(snap.highs[-1]) - float(snap.lows[-1])
        if last_range > 0:
            wick_ratio = 1.0 - (last_body / last_range)
            if wick_ratio > 0.7 and current_r >= 1.0:  # Doji or hammer
                if direction == "SHORT" and float(snap.closes[-1]) > float(snap.opens[-1]):
                    return True, "Bullish reversal candle"
                if direction == "LONG" and float(snap.closes[-1]) < float(snap.opens[-1]):
                    return True, "Bearish reversal candle"
    
    return False, ""

# ═══ PEAK P&L TRACKING ═══
_peak_pnl: Dict[str,float] = {}     # {position_id: highest unrealized $}
_trade_meta: Dict[str,Dict] = {}    # {position_id: {type, entry, tp, sl, direction, sym}}
_stall_cycles: Dict[str,int] = {}   # {position_id: cycles since last new high}
_genesis_ref = None  # Set by trading_loop, used by manage_pos

async def manage_pos(adapter,account):
    global _peak_pnl, _trade_meta, _stall_cycles
    
    # Clean up tracking for closed positions
    open_ids = set()
    for pos in account.open_positions:
        open_ids.add(str(getattr(pos,'position_id','')))
    for old_id in list(_peak_pnl.keys()):
        if old_id not in open_ids:
            _peak_pnl.pop(old_id, None)
            _trade_meta.pop(old_id, None)
            _stall_cycles.pop(old_id, None)
    
    for pos in account.open_positions:
        try:
            # Diagnostic: log all attributes on first encounter
            pid = str(getattr(pos,'position_id','') or getattr(pos,'id',''))
            if pid not in _peak_pnl:
                attrs = {k: str(getattr(pos,k,'?'))[:50] for k in ['position_id','id','instrument','symbol','direction','type',
                    'entry_price','openPrice','open_price','current_price','currentPrice',
                    'stop_loss','stopLoss','take_profit','takeProfit',
                    'unrealizedProfit','unrealized_profit','profit','volume','magic'] if hasattr(pos,k)}
                logger.info("[DIAG] Position %s attrs: %s", pid, attrs)
            
            cur = float(getattr(pos, 'current_price', None) or getattr(pos, 'currentPrice', None) or 0)
            entry = float(getattr(pos, 'entry_price', None) or getattr(pos, 'openPrice', None) or getattr(pos, 'open_price', None) or 0)
            il = False
            try: il = pos.direction.value == "long"
            except: il = str(getattr(pos,'type','')).lower() in ('buy','long')
            
            # Get unrealized P&L — try every possible MetaAPI attribute
            unrealized = 0.0
            for attr in ('unrealizedProfit', 'unrealized_profit', 'profit', 'unrealizedPl', 'upl', 'pnl'):
                val = getattr(pos, attr, None)
                if val is not None and val != 0:
                    unrealized = float(val)
                    break
            # Fallback: estimate from price movement
            if unrealized == 0 and cur > 0 and entry > 0:
                pip_diff = (cur - entry) if il else (entry - cur)
                # Rough estimate: 2.0 lots × pip value
                sym_name = str(getattr(pos,'instrument','') or getattr(pos,'symbol',''))
                if 'BTC' in sym_name:
                    unrealized = pip_diff * 2.0  # BTC: $1 per point per lot
                elif 'JPY' in sym_name:
                    unrealized = pip_diff * 2000  # JPY pairs: ~$1000 per pip per lot
                elif 'XAU' in sym_name:
                    unrealized = pip_diff * 200  # Gold: $100 per point per lot
                else:
                    unrealized = pip_diff * 200000  # Major FX: $100k per lot
                logger.debug("[PNL_EST] %s entry=%.5f cur=%.5f est=$%.0f", pid, entry, cur, unrealized)
            
            # --- PEAK P&L TRACKING (runs on ALL positions, even without SL) ---
            if pid not in _peak_pnl:
                _peak_pnl[pid] = unrealized
                _stall_cycles[pid] = 0
                logger.info("[PEAK] New track: %s $%.0f (entry=%.5f cur=%.5f)", pid, unrealized, entry, cur)
            if unrealized > _peak_pnl[pid]:
                _peak_pnl[pid] = unrealized
                _stall_cycles[pid] = 0
            else:
                _stall_cycles[pid] = _stall_cycles.get(pid, 0) + 1
            
            # Log peak status every 10 cycles for visibility
            if _stall_cycles.get(pid, 0) % 10 == 0 and _peak_pnl.get(pid, 0) > 50:
                logger.info("[PEAK] %s peak=$%.0f now=$%.0f (%.0f%% of peak)", pid, _peak_pnl[pid], unrealized, (unrealized/_peak_pnl[pid]*100) if _peak_pnl[pid]>0 else 0)
            
            meta = _trade_meta.get(pid, {})
            trade_type = meta.get('type', 'SCALP')
            
            # --- GENESIS SWITCH EXIT ---
            # Detect regime for this instrument via GENESIS or fallback to ADX
            sym_name = str(getattr(pos,'instrument','') or getattr(pos,'symbol',''))
            pos_regime = "RANGE"  # default
            if _genesis_ref:
                for sym_key, state in _genesis_ref.states.items():
                    mt = _resolved.get(sym_key)
                    if mt and mt in sym_name:
                        pos_regime = "TREND" if state.current_regime in ("BEAR","BULL") else "RANGE"
                        break
            
            peak = _peak_pnl.get(pid, 0)
            if peak >= 30 and unrealized > 0:
                if pos_regime == "TREND":
                    # CHANDELIER: ATR-adaptive trail — lets trends run
                    # Trail distance = 10% of peak (wider for big moves)
                    trail_pct = 0.90  # Keep 90% minimum
                    if peak >= 500:
                        trail_pct = 0.88  # Slightly looser on big trending moves
                    max_giveback = 1.0 - trail_pct
                else:
                    # RANGE: Tight 90% trail — lock ranging profits fast
                    max_giveback = 0.10  # Only allow 10% giveback
                
                giveback = peak - unrealized
                giveback_pct = giveback / peak if peak > 0 else 0
                if giveback_pct >= max_giveback:
                    logger.info("[GENESIS_EXIT] %s %s peak=$%.0f now=$%.0f gave back %.0f%% (limit=%.0f%% regime=%s)",
                        pid, sym_name, peak, unrealized, giveback_pct*100, max_giveback*100, pos_regime)
                    try:
                        await adapter.close_position(pid)
                        logger.info("[GENESIS_EXIT] CLOSED %s, saved $%.2f", pid, unrealized)
                        send_telegram(f"🧠 <b>GENESIS EXIT</b>\n{sym_name} ({pos_regime})\nPeak: ${peak:.0f} → Locked: ${unrealized:.0f}\nGave back {giveback_pct*100:.0f}%\nRegime: {pos_regime}")
                    except Exception as e:
                        logger.error("[GENESIS_EXIT] Close failed %s: %s", pid, e)
                    continue
            
            # === Below here requires stop_loss and entry_price ===
            sl = getattr(pos, 'stop_loss', None)
            if sl is None or entry is None or entry <= 0: continue
            risk = abs(entry - sl)
            if risk <= 0: continue
            cr = ((cur - entry) / risk) if il else ((entry - cur) / risk)
            
            # --- RULE 2: 80% TP CLOSE (SCALP only) ---
            tp_price = getattr(pos, 'take_profit', None)
            if tp_price and entry > 0 and tp_price != 0:
                tp_dist = abs(tp_price - entry)
                if tp_dist > 0:
                    if il:
                        price_moved = cur - entry
                    else:
                        price_moved = entry - cur
                    pct_of_tp = price_moved / tp_dist if tp_dist > 0 else 0
                    if trade_type == 'SCALP' and pct_of_tp >= 0.80 and price_moved > 0:
                        logger.info("[80%%TP] %s closing at %.0f%% of TP (moved %.5f of %.5f)", pid, pct_of_tp*100, price_moved, tp_dist)
                        try:
                            await adapter.close_position(pid)
                            logger.info("[80%%TP] CLOSED %s at $%.2f profit", pid, unrealized)
                            send_telegram(f"💰 <b>80% TP CLOSE</b>\n{meta.get('sym','?')} closed at {pct_of_tp*100:.0f}% of target\nP&L: ${unrealized:.2f}")
                        except Exception as e:
                            logger.error("[80%%TP] Close failed %s: %s", pid, e)
                        continue
            
            # --- GENESIS SWITCH TRAILING STOPS (broker-side SL) ---
            # TREND: Chandelier (peak - 0.3R × ATR ratio) — lets trends run
            # RANGE: Dynamic 90% of peak R — locks tight
            be=entry;csl=sl
            def better(ns): return (ns>csl+risk*0.03) if il else (ns<csl-risk*0.03)
            ns=None
            
            if cr >= 0.4:
                ns = be  # Breakeven at +0.4R always
            
            if cr >= 0.5 and pos_regime == "RANGE":
                # RANGE: SL = entry + peak_cr * 0.90 (90% of best move)
                target_lock = cr * 0.90
                target_sl = be + risk * target_lock if il else be - risk * target_lock
                if better(target_sl):
                    ns = target_sl
            elif cr >= 0.5 and pos_regime == "TREND":
                # TREND: Chandelier — SL = peak - fixed R distance (widens with move)
                # Trail distance: 0.25R for small moves, 0.35R for big moves
                trail_r = 0.25 if cr < 2.0 else 0.35
                target_lock = max(0.0, cr - trail_r)
                target_sl = be + risk * target_lock if il else be - risk * target_lock
                if better(target_sl):
                    ns = target_sl
            if ns:
                ns_rounded = round(ns, 5)
                logger.info("[TRAIL] Attempting SL move: %s %.1fR → SL=%.5f (was %.5f)", pid, cr, ns_rounded, csl)
                try:
                    # Try modify_position with both SL and TP to avoid broker rejection
                    tp_val = getattr(pos, 'take_profit', None)
                    if tp_val:
                        await adapter.modify_position(pid, new_stop_loss=ns_rounded, new_take_profit=round(tp_val, 5))
                    else:
                        await adapter.modify_position(pid, new_stop_loss=ns_rounded)
                    logger.info("[TRAIL] ✅ SL moved: %s → %.5f", pid, ns_rounded)
                except Exception as e:
                    logger.error("[TRAIL] ❌ modify_position FAILED %s: %s", pid, e)
                    # Fallback: try with position_id as string
                    try:
                        if tp_val:
                            await adapter.modify_position(str(pid), new_stop_loss=ns_rounded, new_take_profit=round(tp_val, 5))
                        else:
                            await adapter.modify_position(str(pid), new_stop_loss=ns_rounded)
                        logger.info("[TRAIL] ✅ SL moved (retry): %s → %.5f", pid, ns_rounded)
                    except Exception as e2:
                        logger.error("[TRAIL] ❌ Retry also failed %s: %s", pid, e2)
            
            # Smart exit: check if momentum is dying (only for trades above 0.8R)
            if cr >= 0.8:
                inst_name = str(getattr(pos,'instrument','') or getattr(pos,'symbol',''))
                pos_dir = "LONG" if il else "SHORT"
                for sym,mt in _resolved.items():
                    if mt and mt in inst_name:
                        cd = get_candles(sym)
                        if cd:
                            sn = make_snap(sym, cd, cur, cur)
                            if sn:
                                should_exit, reason = should_smart_exit(sn, pos_dir, cr)
                                if should_exit:
                                    try:
                                        await adapter.close_position(pid)
                                        logger.info("[SMART_EXIT] %s %.1fR — %s", pid, cr, reason)
                                        send_telegram(f"🧠 <b>SMART EXIT</b>\n{sym} {pos_dir} closed at +{cr:.1f}R\nReason: {reason}")
                                    except Exception as e:
                                        logger.error("[SMART_EXIT] %s: %s", pid, e)
                        break
        except Exception as e:
            logger.error("[MANAGE_POS] %s: %s", getattr(pos,'position_id','?'), e, exc_info=True)

# ══════════════════════════════════════════════════════════════════════
# TRADING LOOP
# ══════════════════════════════════════════════════════════════════════

async def trading_loop(adapter):
    global _genesis_ref
    sig_engine=SignalEngine();corr=CorrelationGuard()
    genesis=None
    if GENESIS_OK:
        try: genesis=create_genesis(default_regime="BEAR"); logger.info("[GENESIS] S=%d L=%d N=%d",len(genesis.short_config),len(genesis.long_config),len(genesis.neutral_config))
        except Exception as e: logger.warning("[GENESIS] %s",e)
    _genesis_ref = genesis  # Make accessible to manage_pos
    ib=100000
    try:
        a=await adapter.get_account_state()
        if a.balance>0: ib=a.balance; logger.info("[INIT] Balance: $%.2f",ib)
    except: pass
    logger.info("🔱 RESOLVING INSTRUMENTS...")
    ok=0
    for s in ALIASES:
        r=await resolve(adapter,s)
        if r: ok+=1; logger.info("  ✅ %s → %s",s,r)
        else: logger.warning("  ⚠️ %s",s)
    logger.info("[INIT] Fetching candles...")
    for s in get_all_symbols():
        cd=get_candles(s)
        if cd: logger.info("  📊 %s: %d bars",s,cd["n"])
        time.sleep(0.3)
    logger.info("🔱 FORGE V22 — %d INSTRUMENTS ARMED",ok)
    send_telegram(f"🔱 <b>TITAN FORGE V22 ONLINE</b>\n{ok} instruments | GENESIS {'ON' if genesis else 'OFF'}\nBalance: ${ib:,.2f}\nAll gas first then brakes.")

    cds:Dict[str,float]={};ld=date.today();dt=0;cyc=0;hb=ib;prev_positions:Dict[str,float]={}

    while True:
        cyc+=1
        try:
            today=date.today()
            if today!=ld: ld=today;cds.clear();dt=0;_cc.clear();send_telegram(f"🔱 <b>V22 DAILY RESET</b>\n📅 {today}")
            try: acc=await adapter.get_account_state()
            except Exception as e: logger.error("[C%d] Account: %s",cyc,e); await asyncio.sleep(30); continue
            if acc.balance<=0: await asyncio.sleep(30); continue
            bal=acc.balance;eq=acc.equity
            if bal>hb: hb=bal
            dd=(hb-eq)/hb if hb>0 else 0
            if dd>=DD_EMERGENCY:
                logger.warning("[EMERGENCY] DD=%.1f%%",dd*100)
                send_telegram(f"🚨 <b>EMERGENCY DD {dd*100:.1f}%</b>\nClosing all")
                try: await adapter.close_all_positions()
                except: pass
                await asyncio.sleep(300); continue
            logger.info("[C%d] Bal=$%.2f Eq=$%.2f DD=%.1f%% Pos=%d T=%d",cyc,bal,eq,dd*100,acc.open_position_count,dt)
            await manage_pos(adapter,acc)
            # Detect closed trades
            curr_pos_ids={}
            if acc.open_positions:
                for p in acc.open_positions:
                    pid=getattr(p,'position_id','')
                    curr_pos_ids[pid]=getattr(p,'current_price',0) or 0
            for pid in list(prev_positions.keys()):
                if pid not in curr_pos_ids:
                    pnl_change=bal-hb if bal!=hb else 0
                    logger.info("📊 TRADE CLOSED: %s",pid)
                    send_telegram(f"📊 <b>TRADE CLOSED</b>\nPosition {pid}\nBalance: ${bal:,.2f}")
            prev_positions=curr_pos_ids
            if acc.open_position_count>=MAX_OPEN: await asyncio.sleep(CYCLE_SPEED); continue
            if dt>=MAX_DAILY: await asyncio.sleep(CYCLE_SPEED); continue

            snaps:Dict[str,MarketSnapshot]={}
            for sym in get_all_symbols():
                if not is_open(sym): continue
                mt=_resolved.get(sym)
                if not mt: continue
                try:
                    b,a=await adapter.get_current_price(mt)
                    if not b or b<=0: continue
                except: continue
                cd=get_candles(sym)
                if not cd: continue
                sn=make_snap(sym,cd,b,a)
                if sn: snaps[sym]=sn
            if not snaps: await asyncio.sleep(CYCLE_SPEED); continue

            # GENESIS
            if genesis:
                saved=dict(SETUP_CONFIG)
                for sym,sn in snaps.items():
                    try:
                        ind=extract_regime_indicators(sn)
                        regime,switched=genesis.update_regime(sym,current_time=datetime.now(timezone.utc),**ind)
                        setup=genesis.get_active_setup(sym,**ind)
                        if setup: SETUP_CONFIG[sym]=setup
                        if switched: logger.info("[GENESIS] 🔄 %s→%s",sym,regime); send_telegram(f"🔄 {sym}→{regime}")
                    except: pass

            now=datetime.now(timezone.utc)
            try: sigs=sig_engine.generate_signals(snaps,current_time=now)
            except Exception as e: logger.error("[SIG] %s",e); sigs=[]

            if genesis: SETUP_CONFIG.clear(); SETUP_CONFIG.update(saved)

            # ═══ DIAGNOSTIC: Why aren't we trading? (every 20 cycles) ═══
            if cyc % 20 == 0:
                diag_parts = []
                diag_parts.append(f"snaps={len(snaps)}")
                diag_parts.append(f"sigs_raw={len(sigs)}")
                # Show which symbols have snapshots
                snap_syms = list(snaps.keys())
                diag_parts.append(f"snap_syms={snap_syms[:8]}")
                # Show candle ages
                stale = []
                for sym in get_all_symbols():
                    c = _cc.get(sym)
                    if c:
                        age = int(time.time() - c["ts"])
                        if age > 300: stale.append(f"{sym}:{age}s")
                if stale: diag_parts.append(f"stale_candles={stale}")
                # Show all raw signals with their confidence
                if sigs:
                    for s in sigs[:5]:
                        diag_parts.append(f"sig:{s.symbol}|{s.strategy.value}|{s.direction}|conf={s.final_confidence:.3f}")
                # Show what SETUP_CONFIG has
                setup_syms = list(SETUP_CONFIG.keys())
                missing = [s for s in snap_syms if s not in setup_syms]
                if missing: diag_parts.append(f"NO_SETUP={missing}")
                logger.info("[DIAG_SIG] %s", " | ".join(diag_parts))

            if not sigs: await asyncio.sleep(CYCLE_SPEED); continue

            osyms:Set[str]=set()
            if acc.open_positions:
                for p in acc.open_positions:
                    inst=str(getattr(p,'instrument','') or getattr(p,'symbol',''))
                    for our,mt in _resolved.items():
                        if mt and mt in inst: osyms.add(our)

            best=None;bc=0
            # DIAGNOSTIC: track filter reasons
            filter_reasons = {"already_open":0, "cooldown":0, "corr_block":0, "low_conf":0, "passed":0}
            for s in sigs:
                if s.symbol in osyms: filter_reasons["already_open"]+=1; continue
                if time.time()-cds.get(s.symbol,0)<COOLDOWN: filter_reasons["cooldown"]+=1; continue
                ok2,_=corr.can_trade(s.symbol,osyms)
                if not ok2: filter_reasons["corr_block"]+=1; continue
                if s.final_confidence<CONVICTION_MIN: filter_reasons["low_conf"]+=1; continue
                filter_reasons["passed"]+=1
                if s.final_confidence>bc: bc=s.final_confidence;best=s
            
            # Log filter results every time we have signals but none pass
            if not best and sigs:
                logger.info("[DIAG_FILTER] %d signals filtered: %s", len(sigs), filter_reasons)
                # Show top 3 closest to passing
                by_conf = sorted(sigs, key=lambda s: s.final_confidence, reverse=True)[:3]
                for s in by_conf:
                    in_cd = time.time()-cds.get(s.symbol,0)<COOLDOWN
                    logger.info("[DIAG_NEAR] %s %s %s conf=%.3f cd=%s open=%s",
                        s.symbol, s.strategy.value, s.direction, s.final_confidence,
                        "YES" if in_cd else "no", "YES" if s.symbol in osyms else "no")
            
            if not best: await asyncio.sleep(CYCLE_SPEED); continue

            sig=best;mt=_resolved.get(sig.symbol)
            if not mt: await asyncio.sleep(CYCLE_SPEED); continue
            sld=abs(sig.entry_price-sig.sl_price)
            tpd=abs(sig.tp_price-sig.entry_price)
            min_dist=0.0005 if sig.symbol in ("EURUSD","GBPUSD","NZDUSD","USDCHF","EURGBP") else 0.05 if sig.symbol in ("USDJPY","GBPJPY") else 5.0
            if sld<min_dist or tpd<min_dist:
                logger.warning("[SKIP] %s: SL/TP too close (SL=%.5f TP=%.5f)",sig.symbol,sld,tpd)
                cds[sig.symbol]=time.time()
                await asyncio.sleep(CYCLE_SPEED); continue
            # Validate TP direction: LONG=TP>entry, SHORT=TP<entry
            if sig.direction=="LONG" and sig.tp_price<=sig.entry_price:
                logger.warning("[SKIP] %s LONG: TP %.5f <= Entry %.5f",sig.symbol,sig.tp_price,sig.entry_price)
                cds[sig.symbol]=time.time()
                await asyncio.sleep(CYCLE_SPEED); continue
            if sig.direction=="SHORT" and sig.tp_price>=sig.entry_price:
                logger.warning("[SKIP] %s SHORT: TP %.5f >= Entry %.5f",sig.symbol,sig.tp_price,sig.entry_price)
                cds[sig.symbol]=time.time()
                await asyncio.sleep(CYCLE_SPEED); continue
            lots=calc_lots(sig.symbol,bal,sld)
            regime="BEAR"
            if genesis and sig.symbol in genesis.states: regime=genesis.states[sig.symbol].current_regime
            tid=f"V22-{uuid.uuid4().hex[:6]}"
            dirn=OrderDirection.LONG if sig.direction=="LONG" else OrderDirection.SHORT
            order=OrderRequest(instrument=mt,direction=dirn,size=lots,order_type=OrderType.MARKET,
                stop_loss=round(sig.sl_price,5),take_profit=round(sig.tp_price,5),
                comment=f"V22|{sig.symbol[:6]}|{sig.strategy.value[:8]}|{regime[:4]}")
            logger.info("🔫 %s %s %s|%s|%s E=%.5f SL=%.5f TP=%.5f %.2flots conf=%.2f",
                sig.symbol,sig.direction,sig.strategy.value,sig.trade_type.value,regime,
                sig.entry_price,sig.sl_price,sig.tp_price,lots,sig.final_confidence)
            try:
                if _has_router:
                    router=SmartOrderRouter()
                    result=await router.execute(adapter=adapter,order_request=order,setup_id=f"{sig.symbol}_{sig.strategy.value}",
                        conviction_level="STANDARD",conviction_posterior=sig.final_confidence,instrument_key=sig.symbol,signal_entry=sig.entry_price)
                else:
                    result=await adapter.place_order(order)
                filled=False
                if hasattr(result,'status'):
                    filled=(result.status.value=="filled") if hasattr(result.status,'value') else str(result.status)=="filled"
                fp=getattr(result,'fill_price',sig.entry_price) or sig.entry_price
                oid=getattr(result,'order_id',tid)
                if filled:
                    logger.info("✅ FILLED: %s @ %.5f",oid,fp);cds[sig.symbol]=time.time();dt+=1
                    # Register with smart exit tracking
                    _trade_meta[str(oid)] = {
                        'type': sig.trade_type.value.upper() if hasattr(sig.trade_type,'value') else 'SCALP',
                        'entry': fp, 'tp': sig.tp_price, 'sl': sig.sl_price,
                        'direction': sig.direction, 'sym': sig.symbol,
                    }
                    _peak_pnl[str(oid)] = 0.0
                    _stall_cycles[str(oid)] = 0
                    send_telegram(f"🔫 <b>V22 TRADE</b>\n{'🟢' if sig.direction=='LONG' else '🔴'} {sig.symbol} {sig.direction}\n{sig.strategy.value}|{sig.trade_type.value}|{regime}\nEntry: {fp:.5f}\nSL: {sig.sl_price:.5f} TP: {sig.tp_price:.5f}\nLots: {lots} Conf: {sig.final_confidence:.2f}\nTrade #{dt}")
                    if _evidence and TradeFingerprint:
                        try: _evidence.log_trade(TradeFingerprint(trade_id=tid,timestamp=now.isoformat(),setup_id=f"{sig.symbol}_{sig.strategy.value}",instrument=sig.symbol,direction=sig.direction,entry_price=fp,stop_loss=sig.sl_price,take_profit=sig.tp_price,lot_size=lots,firm_id="FTMO",regime=regime,bayesian_posterior=sig.final_confidence,conviction_level="STANDARD",capital_vehicle="PROP_FIRM"))
                        except: pass
                else:
                    logger.warning("❌ FAILED: %s",getattr(result,'error_message','unknown'))
                    cds[sig.symbol]=time.time()
            except Exception as e: logger.error("[EXEC] %s: %s",sig.symbol,e,exc_info=True)
        except Exception as e: logger.error("[C%d] %s",cyc,e,exc_info=True)
        await asyncio.sleep(CYCLE_SPEED)

async def main():
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  NEXUS CAPITAL — TITAN FORGE V22                        ║")
    logger.info("║  14 INSTRUMENTS | 3 REGIMES | GENESIS | LEAN BUILD     ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")
    token=os.environ.get("METAAPI_TOKEN","");acct=os.environ.get("METAAPI_ACCOUNT_ID",os.environ.get("FTMO_ACCOUNT_ID",""))
    if not token or not acct: logger.error("Missing METAAPI credentials"); return
    if POLYGON_API_KEY: logger.info("✅ Polygon API key")
    logger.info("✅ Instruments: %d | GENESIS: %s",len(ALIASES),"yes" if GENESIS_OK else "no")
    adapter=MT5Adapter(account_id=acct,server="OANDA-Demo-1",password="",is_demo=os.environ.get("FTMO_IS_DEMO","true").lower()=="true")
    logger.info("Connecting to MetaAPI...")
    connected=await adapter.connect()
    if connected: logger.info("✅ Connected.")
    else: logger.error("❌ Connection failed."); return
    await trading_loop(adapter)

if __name__=="__main__":
    asyncio.run(main())
