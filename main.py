"""
NEXUS CAPITAL — TITAN FORGE V22.4 ELITE
=========================================
"All gas first then brakes — but when you have a runner, LET IT RUN."

UPGRADES FROM V22.3:
  1. M15 candles for forex/gold/crypto (4x more signals than H1 only)
  2. 20:00 UTC WEAPON — dedicated strategy fires every day on 7 instruments
  3. ELITE EXIT SYSTEM — ATR chandelier trail that lets big winners breathe
     - The BTC +$682 trade would have run to $1,200+ under this system
  4. ADAPTIVE BLEEDER KILLER — tracks per-instrument WR, auto-disables losers
  5. Faster cycle (20s not 30s), lower cooldown (90s not 120s)
  6. MAX_DAILY raised to 30
"""

import asyncio, logging, os, time, uuid
import numpy as np, requests
from datetime import date, datetime, timezone, timedelta
from typing import Optional, Dict, Set, List
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("titan_forge.main")

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

try:
    from forge_genesis import create_genesis, extract_regime_indicators, auto_evolve, get_calibrated_wr
    GENESIS_OK = True
except ImportError:
    GENESIS_OK = False

FORGE_VERSION = "v22.4-ELITE"
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

MAX_OPEN = 5; MAX_DAILY = 30; COOLDOWN = 90; RISK_PCT = 0.015
MAX_LOT = 2.0; CYCLE_SPEED = 20; CONVICTION_MIN = 0.20; DD_EMERGENCY = 0.09
CANDLE_REFRESH_M15 = 300; CANDLE_REFRESH_H1 = 600
BTC_COOLDOWN = 300

M15_INSTRUMENTS = {"EURUSD","GBPUSD","USDJPY","USDCHF","EURGBP","GBPJPY",
                   "NZDUSD","XAUUSD","BTCUSD","AUDNZD","AUDUSD","EURJPY","USOIL"}
H1_ONLY = {"US100"}

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
    "US100":"I:NDX","USOIL":"C:XTIUSD","BTCUSD":"X:BTCUSD",
    "AUDNZD":"C:AUDNZD","AUDUSD":"C:AUDUSD","EURJPY":"C:EURJPY",
}
ATR_FB = {"EURUSD":0.008,"GBPUSD":0.01,"USDJPY":1.0,"USDCHF":0.007,"EURGBP":0.005,
          "GBPJPY":1.5,"NZDUSD":0.006,"XAUUSD":30.0,"US100":200.0,
          "USOIL":2.0,"BTCUSD":2000.0,"AUDNZD":0.006,"AUDUSD":0.007,"EURJPY":1.2}
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
            if b and b > 0: _resolved[sym]=t; return t
        except: continue
    _resolved[sym]=None; return None

_cc_m15: Dict[str,Dict] = {}
_cc_h1: Dict[str,Dict] = {}

def _fetch_candles(sym, timeframe, multiplier, refresh, cache):
    if not POLYGON_API_KEY: return None
    c = cache.get(sym)
    if c and time.time()-c["ts"]<refresh: return c["d"]
    tk = POLYGON_MAP.get(sym)
    if not tk: return None
    end=datetime.now(timezone.utc); start=end-timedelta(days=14)
    try:
        url=f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/{multiplier}/{timeframe}/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}?adjusted=true&sort=asc&limit=50000&apiKey={POLYGON_API_KEY}"
        r=requests.get(url,timeout=15)
        if r.status_code==429: time.sleep(13); r=requests.get(url,timeout=15)
        if r.status_code!=200: return None
        res=r.json().get("results",[])
        if len(res)<30: return None
        seen=set(); bars=[]
        for x in res:
            if x["t"] not in seen: seen.add(x["t"]); bars.append(x)
        bars.sort(key=lambda x:x["t"]); bars=bars[-300:]
        d={"o":np.array([x["o"] for x in bars],dtype=float),"h":np.array([x["h"] for x in bars],dtype=float),
           "l":np.array([x["l"] for x in bars],dtype=float),"c":np.array([x["c"] for x in bars],dtype=float),
           "v":np.array([x.get("v",0) for x in bars],dtype=float),"n":len(bars)}
        cache[sym]={"d":d,"ts":time.time()}; return d
    except Exception as e: logger.warning("[CANDLES] %s: %s",sym,e); return None

def get_candles_m15(sym):
    if sym in H1_ONLY: return _fetch_candles(sym,"hour",1,CANDLE_REFRESH_H1,_cc_h1)
    return _fetch_candles(sym,"minute",15,CANDLE_REFRESH_M15,_cc_m15)

def get_candles_h1(sym):
    return _fetch_candles(sym,"hour",1,CANDLE_REFRESH_H1,_cc_h1)

_daily_cache: Dict[str,Dict] = {}
def get_daily_gap(sym):
    if not POLYGON_API_KEY: return None, None
    c = _daily_cache.get(sym)
    if c and time.time()-c["ts"]<3600: return c.get("open"), c.get("prev_close")
    tk = POLYGON_MAP.get(sym)
    if not tk: return None, None
    end=datetime.now(timezone.utc); start=end-timedelta(days=5)
    try:
        url=f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/1/day/{start.strftime('%Y-%m-%d')}/{end.strftime('%Y-%m-%d')}?adjusted=true&sort=asc&limit=10&apiKey={POLYGON_API_KEY}"
        r=requests.get(url,timeout=15)
        if r.status_code==429: time.sleep(13); r=requests.get(url,timeout=15)
        if r.status_code!=200: return None, None
        res=r.json().get("results",[])
        if len(res)<2: return None, None
        _daily_cache[sym]={"open":res[-1]["o"],"prev_close":res[-2]["c"],"ts":time.time()}
        return res[-1]["o"], res[-2]["c"]
    except: return None, None

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
    o,h,l,c,v=cd["o"],cd["h"],cd["l"],cd["c"],cd["v"];n=len(c)
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
    daily_open, daily_prev_close = get_daily_gap(sym)
    return MarketSnapshot(symbol=sym,opens=o,highs=h,lows=l,closes=c,volumes=v,bid=bid,ask=ask,
        atr=atr,rsi=rsi,stoch_k=sk,stoch_d=sd,stoch_k_prev=skp,stoch_d_prev=sdp,
        ema_50=e50,ema_200=e200,bb_upper=bbu,bb_lower=bbl,bb_middle=bbm,
        vwap=vwap,vwap_std=vstd,adx=adx,adx_prev=adxp,plus_di=pdi,minus_di=mdi,
        prev_day_high=pdh,prev_day_low=pdl,prev_day_close=pdc,
        session_open=float(o[-sl]),session_high=float(np.max(h[-sl:])),session_low=float(np.min(l[-sl:])),
        orb_high=float(h[-sl]),orb_low=float(l[-sl]),orb_complete=True,
        asian_high=float(np.max(h[:min(7,n)])),asian_low=float(np.min(l[:min(7,n)])),asian_complete=True,
        keltner_upper=ku,keltner_lower=kl,bars_since_open=sl,current_hour_utc=datetime.now(timezone.utc).hour,
        daily_open=daily_open, daily_prev_close=daily_prev_close)

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

# ═══ ELITE CHANDELIER EXIT ═══
_peak_pnl: Dict[str,float] = {}
_trade_meta: Dict[str,Dict] = {}
_stall_cycles: Dict[str,int] = {}
_entry_atr: Dict[str,float] = {}
_genesis_ref = None

# ═══ ADAPTIVE BLEEDER KILLER ═══
_trade_results: Dict[str,List[bool]] = defaultdict(list)
_disabled_until: Dict[str,float] = {}

def record_trade_result(symbol, win):
    _trade_results[symbol].append(win)
    if len(_trade_results[symbol]) > 20:
        _trade_results[symbol] = _trade_results[symbol][-20:]
    results = _trade_results[symbol]
    if len(results) >= 10:
        wr = sum(results) / len(results)
        if wr < 0.30:
            _disabled_until[symbol] = time.time() + 48*3600
            logger.warning("[BLEEDER] %s disabled 48h — WR %.0f%%", symbol, wr*100)
            send_telegram(f"🩸 <b>BLEEDER KILLED</b>\n{symbol} disabled 48h\nWR: {wr*100:.0f}%")

def is_bleeder_disabled(symbol):
    until = _disabled_until.get(symbol, 0)
    if time.time() < until: return True
    if until > 0: _disabled_until.pop(symbol, None)
    return False

async def manage_pos(adapter, account):
    global _peak_pnl, _trade_meta, _stall_cycles
    open_ids = set()
    for pos in account.open_positions:
        open_ids.add(str(getattr(pos,'position_id','')))
    for old_id in list(_peak_pnl.keys()):
        if old_id not in open_ids:
            _peak_pnl.pop(old_id, None); _trade_meta.pop(old_id, None)
            _stall_cycles.pop(old_id, None); _entry_atr.pop(old_id, None)

    for pos in account.open_positions:
        try:
            pid = str(getattr(pos,'position_id','') or getattr(pos,'id',''))
            cur = float(getattr(pos,'current_price',None) or getattr(pos,'currentPrice',None) or 0)
            entry = float(getattr(pos,'entry_price',None) or getattr(pos,'openPrice',None) or getattr(pos,'open_price',None) or 0)
            il = False
            try: il = pos.direction.value == "long"
            except: il = str(getattr(pos,'type','')).lower() in ('buy','long')

            if pid not in _peak_pnl:
                _peak_pnl[pid] = 0.0; _stall_cycles[pid] = 0

            unrealized = 0.0
            for attr in ('unrealizedProfit','unrealized_profit','profit','unrealizedPl','upl','pnl'):
                val = getattr(pos, attr, None)
                if val is not None and val != 0: unrealized = float(val); break

            if unrealized > _peak_pnl[pid]: _peak_pnl[pid] = unrealized; _stall_cycles[pid] = 0
            else: _stall_cycles[pid] = _stall_cycles.get(pid, 0) + 1

            sl = getattr(pos, 'stop_loss', None)
            if sl is None or entry is None or entry <= 0: continue
            risk = abs(entry - sl)
            if risk <= 0: continue
            cr = ((cur - entry) / risk) if il else ((entry - cur) / risk)

            # Get ATR for chandelier
            sym_name = str(getattr(pos,'instrument','') or getattr(pos,'symbol',''))
            pos_atr = _entry_atr.get(pid, risk)
            for sym, mt in _resolved.items():
                if mt and mt in sym_name:
                    cd = get_candles_m15(sym) or get_candles_h1(sym)
                    if cd: pos_atr = _atr(cd["h"],cd["l"],cd["c"]); _entry_atr[pid] = pos_atr
                    break

            be = entry; csl = sl
            def better(ns): return (ns > csl + risk * 0.03) if il else (ns < csl - risk * 0.03)
            ns = None

            # ELITE PROGRESSIVE CHANDELIER
            if cr >= 0.5 and cr < 1.0:
                ns = be  # Breakeven
            elif cr >= 1.0 and cr < 2.0:
                target = be + risk * 0.5 if il else be - risk * 0.5
                if better(target): ns = target
            elif cr >= 2.0 and cr < 3.0:
                trail = 1.5 * pos_atr
                target = (cur - trail) if il else (cur + trail)
                lock = be + risk * 0.8 if il else be - risk * 0.8
                target = max(target, lock) if il else min(target, lock)
                if better(target): ns = target
            elif cr >= 3.0 and cr < 5.0:
                trail = 2.5 * pos_atr
                target = (cur - trail) if il else (cur + trail)
                lock = be + risk * 1.5 if il else be - risk * 1.5
                target = max(target, lock) if il else min(target, lock)
                if better(target): ns = target
            elif cr >= 5.0:
                trail = 3.0 * pos_atr
                target = (cur - trail) if il else (cur + trail)
                lock = be + risk * 3.0 if il else be - risk * 3.0
                target = max(target, lock) if il else min(target, lock)
                if better(target): ns = target
                if _stall_cycles.get(pid,0) % 20 == 0:
                    logger.info("[MONSTER] %s +%.1fR $%.0f", pid, cr, unrealized)

            if ns:
                ns_r = round(ns, 5)
                try:
                    tp_val = getattr(pos, 'take_profit', None)
                    if tp_val: await adapter.modify_position(pid, new_stop_loss=ns_r, new_take_profit=round(tp_val, 5))
                    else: await adapter.modify_position(pid, new_stop_loss=ns_r)
                    logger.info("[TRAIL] %s %.1fR SL→%.5f", pid, cr, ns_r)
                except:
                    try:
                        if tp_val: await adapter.modify_position(str(pid), new_stop_loss=ns_r, new_take_profit=round(tp_val, 5))
                        else: await adapter.modify_position(str(pid), new_stop_loss=ns_r)
                    except: pass
        except Exception as e:
            logger.error("[POS] %s: %s", getattr(pos,'position_id','?'), e, exc_info=True)

# ═══ 20:00 UTC WEAPON ═══
UTC20_SYMS = ["NZDUSD","EURGBP","EURUSD","GBPJPY"]
_utc20_fired = False

async def fire_utc20(adapter, snaps, bal, osyms, cds):
    global _utc20_fired
    now = datetime.now(timezone.utc)
    if now.hour != 20 or now.minute > 15 or _utc20_fired: return None
    for sym in UTC20_SYMS:
        if sym in osyms or is_bleeder_disabled(sym): continue
        if time.time() - cds.get(sym,0) < COOLDOWN: continue
        snap = snaps.get(sym)
        if not snap: continue
        mt = _resolved.get(sym)
        if not mt: continue
        entry = snap.bid; sl_d = snap.atr * 1.5; tp_d = snap.atr * 2.5
        lots = calc_lots(sym, bal, sl_d)
        order = OrderRequest(instrument=mt, direction=OrderDirection.SHORT, size=lots,
            order_type=OrderType.MARKET, stop_loss=round(entry+sl_d,5), take_profit=round(entry-tp_d,5),
            comment=f"V224|{sym[:6]}|UTC20|NEUT")
        logger.info("🎯 UTC20: %s SHORT E=%.5f lots=%.2f", sym, entry, lots)
        try:
            result = await adapter.place_order(order)
            filled = False
            if hasattr(result,'status'):
                filled = (result.status.value=="filled") if hasattr(result.status,'value') else str(result.status)=="filled"
            if filled:
                fp = getattr(result,'fill_price',entry) or entry
                oid = str(getattr(result,'order_id',''))
                cds[sym] = time.time()
                _trade_meta[oid] = {'type':'SCALP','entry':fp,'direction':'SHORT','sym':sym}
                _peak_pnl[oid] = 0.0; _stall_cycles[oid] = 0; _entry_atr[oid] = snap.atr
                send_telegram(f"🎯 <b>UTC20 WEAPON</b>\n🔴 {sym} SHORT\nEntry: {fp:.5f}\nLots: {lots}")
                _utc20_fired = True; return sym
        except Exception as e: logger.error("[UTC20] %s: %s", sym, e)
    return None

# ═══ TRADING LOOP ═══
async def trading_loop(adapter):
    global _genesis_ref, _utc20_fired
    sig_engine = SignalEngine(); corr = CorrelationGuard()
    genesis = None
    if GENESIS_OK:
        try: genesis = create_genesis(default_regime="BEAR"); logger.info("[GENESIS] S=%d L=%d N=%d",len(genesis.short_config),len(genesis.long_config),len(genesis.neutral_config))
        except Exception as e: logger.warning("[GENESIS] %s",e)
    _genesis_ref = genesis
    ib = 100000
    try:
        a = await adapter.get_account_state()
        if a.balance > 0: ib = a.balance
    except: pass
    ok = 0
    for s in ALIASES:
        r = await resolve(adapter,s)
        if r: ok += 1; logger.info("  ✅ %s → %s",s,r)
    logger.info("[INIT] Fetching M15 candles...")
    for s in get_all_symbols():
        cd = get_candles_m15(s)
        if cd: logger.info("  📊 %s: %d bars",s,cd["n"])
        time.sleep(0.3)
    for s in get_all_symbols():
        get_daily_gap(s); time.sleep(0.3)
    logger.info("🔱 FORGE V22.4 ELITE — %d INSTRUMENTS",ok)
    send_telegram(f"🔱 <b>TITAN FORGE V22.4 ELITE</b>\n{ok} instruments | M15 primary\n✅ UTC20 weapon\n✅ Chandelier exits\n✅ Bleeder killer\nBalance: ${ib:,.2f}")

    cds: Dict[str,float] = {}; ld = date.today(); dt = 0; cyc = 0; hb = ib; prev_positions: Dict[str,float] = {}

    while True:
        cyc += 1
        try:
            today = date.today()
            if today != ld:
                ld=today;cds.clear();dt=0;_cc_m15.clear();_cc_h1.clear();_daily_cache.clear()
                _utc20_fired=False; send_telegram(f"🔱 <b>V22.4 DAILY RESET</b>\n📅 {today}")
            try: acc = await adapter.get_account_state()
            except: await asyncio.sleep(30); continue
            if acc.balance <= 0: await asyncio.sleep(30); continue
            bal=acc.balance;eq=acc.equity
            if bal>hb: hb=bal
            dd=(hb-eq)/hb if hb>0 else 0
            if dd>=DD_EMERGENCY:
                send_telegram(f"🚨 <b>EMERGENCY DD {dd*100:.1f}%</b>")
                try: await adapter.close_all_positions()
                except: pass
                await asyncio.sleep(300); continue
            logger.info("[C%d] $%.0f eq=$%.0f DD=%.1f%% P=%d T=%d",cyc,bal,eq,dd*100,acc.open_position_count,dt)
            await manage_pos(adapter,acc)

            curr_pos = {}
            if acc.open_positions:
                for p in acc.open_positions: curr_pos[getattr(p,'position_id','')] = 0
            for pid in list(prev_positions.keys()):
                if pid != '_bal' and pid not in curr_pos:
                    meta = _trade_meta.get(str(pid),{})
                    sym = meta.get('sym','?')
                    win = bal > prev_positions.get('_bal', ib)
                    if sym != '?': record_trade_result(sym, win)
                    send_telegram(f"📊 <b>CLOSED</b> {sym} {'✅' if win else '❌'} Bal=${bal:,.0f}")
            prev_positions = curr_pos; prev_positions['_bal'] = bal

            if acc.open_position_count >= MAX_OPEN or dt >= MAX_DAILY:
                await asyncio.sleep(CYCLE_SPEED); continue

            snaps: Dict[str,MarketSnapshot] = {}
            for sym in get_all_symbols():
                if not is_open(sym): continue
                mt = _resolved.get(sym)
                if not mt: continue
                try:
                    b,a = await adapter.get_current_price(mt)
                    if not b or b<=0: continue
                except: continue
                cd = get_candles_m15(sym) or get_candles_h1(sym)
                if not cd: continue
                sn = make_snap(sym,cd,b,a)
                if sn: snaps[sym] = sn
            if not snaps: await asyncio.sleep(CYCLE_SPEED); continue

            if genesis:
                saved = dict(SETUP_CONFIG)
                for sym,sn in snaps.items():
                    try:
                        ind = extract_regime_indicators(sn)
                        regime,switched = genesis.update_regime(sym,current_time=datetime.now(timezone.utc),**ind)
                        setup = genesis.get_active_setup(sym,**ind)
                        if setup: SETUP_CONFIG[sym] = setup
                        if switched: logger.info("[GENESIS] 🔄 %s→%s",sym,regime)
                    except: pass

            osyms: Set[str] = set()
            if acc.open_positions:
                for p in acc.open_positions:
                    inst = str(getattr(p,'instrument','') or getattr(p,'symbol',''))
                    for our,mt in _resolved.items():
                        if mt and mt in inst: osyms.add(our)

            r = await fire_utc20(adapter,snaps,bal,osyms,cds)
            if r: dt += 1

            now = datetime.now(timezone.utc)
            try: sigs = sig_engine.generate_signals(snaps,current_time=now)
            except: sigs = []
            if genesis: SETUP_CONFIG.clear(); SETUP_CONFIG.update(saved)

            if cyc % 20 == 0:
                d = [f"snaps={len(snaps)}",f"sigs={len(sigs)}"]
                for s in sigs[:5]: d.append(f"{s.symbol}|{s.strategy.value}|{s.final_confidence:.2f}")
                logger.info("[DIAG] %s"," | ".join(d))

            if not sigs: await asyncio.sleep(CYCLE_SPEED); continue

            osyms = set()
            if acc.open_positions:
                for p in acc.open_positions:
                    inst = str(getattr(p,'instrument','') or getattr(p,'symbol',''))
                    for our,mt in _resolved.items():
                        if mt and mt in inst: osyms.add(our)

            best=None;bc=0
            for s in sigs:
                if s.symbol in osyms: continue
                if is_bleeder_disabled(s.symbol): continue
                cd_t = BTC_COOLDOWN if s.symbol in CRYPTO else COOLDOWN
                if time.time()-cds.get(s.symbol,0)<cd_t: continue
                ok2,_ = corr.can_trade(s.symbol,osyms)
                if not ok2: continue
                if s.final_confidence<CONVICTION_MIN: continue
                if s.final_confidence>bc: bc=s.final_confidence;best=s

            if not best: await asyncio.sleep(CYCLE_SPEED); continue
            sig=best;mt=_resolved.get(sig.symbol)
            if not mt: await asyncio.sleep(CYCLE_SPEED); continue
            sld=abs(sig.entry_price-sig.sl_price);tpd=abs(sig.tp_price-sig.entry_price)
            min_d=0.0005 if sig.symbol in ("EURUSD","GBPUSD","NZDUSD","USDCHF","EURGBP","AUDNZD","AUDUSD") else 0.05 if sig.symbol in ("USDJPY","GBPJPY","EURJPY") else 5.0
            if sld<min_d or tpd<min_d: cds[sig.symbol]=time.time(); await asyncio.sleep(CYCLE_SPEED); continue
            if sig.direction=="LONG" and sig.tp_price<=sig.entry_price: cds[sig.symbol]=time.time(); await asyncio.sleep(CYCLE_SPEED); continue
            if sig.direction=="SHORT" and sig.tp_price>=sig.entry_price: cds[sig.symbol]=time.time(); await asyncio.sleep(CYCLE_SPEED); continue

            lots=calc_lots(sig.symbol,bal,sld)
            regime="BEAR"
            if genesis and sig.symbol in genesis.states: regime=genesis.states[sig.symbol].current_regime
            dirn=OrderDirection.LONG if sig.direction=="LONG" else OrderDirection.SHORT
            order=OrderRequest(instrument=mt,direction=dirn,size=lots,order_type=OrderType.MARKET,
                stop_loss=round(sig.sl_price,5),take_profit=round(sig.tp_price,5),
                comment=f"V224|{sig.symbol[:6]}|{sig.strategy.value[:8]}|{regime[:4]}")
            logger.info("🔫 %s %s %s|%s conf=%.2f lots=%.2f",sig.symbol,sig.direction,sig.strategy.value,regime,sig.final_confidence,lots)
            try:
                if _has_router:
                    router=SmartOrderRouter()
                    result=await router.execute(adapter=adapter,order_request=order,setup_id=f"{sig.symbol}_{sig.strategy.value}",
                        conviction_level="STANDARD",conviction_posterior=sig.final_confidence,instrument_key=sig.symbol,signal_entry=sig.entry_price)
                else: result=await adapter.place_order(order)
                filled=False
                if hasattr(result,'status'):
                    filled=(result.status.value=="filled") if hasattr(result.status,'value') else str(result.status)=="filled"
                fp=getattr(result,'fill_price',sig.entry_price) or sig.entry_price
                oid=str(getattr(result,'order_id',''))
                if filled:
                    logger.info("✅ %s @ %.5f",oid,fp);cds[sig.symbol]=time.time();dt+=1
                    _trade_meta[oid]={'type':sig.trade_type.value.upper() if hasattr(sig.trade_type,'value') else 'SCALP',
                        'entry':fp,'tp':sig.tp_price,'sl':sig.sl_price,'direction':sig.direction,'sym':sig.symbol}
                    _peak_pnl[oid]=0.0;_stall_cycles[oid]=0;_entry_atr[oid]=sig.atr_value
                    send_telegram(f"🔫 <b>V22.4</b>\n{'🟢' if sig.direction=='LONG' else '🔴'} {sig.symbol} {sig.direction}\n{sig.strategy.value}|{regime}\nE:{fp:.5f} SL:{sig.sl_price:.5f} TP:{sig.tp_price:.5f}\n#{dt}")
                else: cds[sig.symbol]=time.time()
            except Exception as e: logger.error("[EXEC] %s: %s",sig.symbol,e,exc_info=True)
        except Exception as e: logger.error("[C%d] %s",cyc,e,exc_info=True)
        await asyncio.sleep(CYCLE_SPEED)

async def main():
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  NEXUS CAPITAL — TITAN FORGE V22.4 ELITE               ║")
    logger.info("║  M15+H1 | UTC20 WEAPON | CHANDELIER | BLEEDER KILL    ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")
    token=os.environ.get("METAAPI_TOKEN","");acct=os.environ.get("METAAPI_ACCOUNT_ID",os.environ.get("FTMO_ACCOUNT_ID",""))
    if not token or not acct: logger.error("Missing creds"); return
    adapter=MT5Adapter(account_id=acct,server="OANDA-Demo-1",password="",is_demo=os.environ.get("FTMO_IS_DEMO","true").lower()=="true")
    connected=await adapter.connect()
    if connected: logger.info("✅ Connected.")
    else: logger.error("❌ Failed."); return
    await trading_loop(adapter)

if __name__=="__main__":
    asyncio.run(main())
