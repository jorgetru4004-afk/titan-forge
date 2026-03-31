"""
FORGE v22 INTEGRATION ENGINE
==============================
Single entry point for main.py to use v22 signal generation + GENESIS regime routing.

Instead of v21's hardcoded setup selection, main.py calls:
    engine = ForgeV22Engine()
    signals = engine.process_cycle(snapshots, current_time)

This handles:
  - GENESIS regime detection per instrument
  - v22 signal generation with the correct strategy per regime
  - Correlation guards (no EURUSD + GBPUSD simultaneously)
  - Runner detection and management
  - Limit order tracking
  - Cooldown management
  - Daily trade limits

Integration into main.py is minimal — see patch_main_v22.py
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field

from forge_instruments_v22 import (
    SETUP_CONFIG, InstrumentSetup, Strategy, Direction, TradeType, OrderType,
    get_all_symbols, INSTRUMENT_GROUPS, TIME_OF_DAY_EDGES,
)
from forge_signals_v22 import SignalEngine, MarketSnapshot, Signal
from forge_correlation import CorrelationGuard
from forge_genesis import GenesisEngine, Regime, extract_regime_indicators, create_genesis

logger = logging.getLogger("titan_forge.v22")


# ═══════════════════════════════════════════════════════════════════════════════
# V22 TRADE SIGNAL — what we pass back to main.py for execution
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class V22TradeSignal:
    """Complete trade signal ready for execution by main.py"""
    symbol: str
    mt5_symbol: str          # The resolved MT5 symbol (e.g., "EURUSD.sim")
    direction: str           # "LONG" or "SHORT"
    strategy: str            # Strategy name
    trade_type: str          # "SCALP" or "RUNNER"
    order_type: str          # "MARKET" or "LIMIT"
    entry_price: float
    sl_price: float
    tp_price: float
    risk_pct: float          # Position risk as percentage
    atr: float
    confidence: float
    regime: str              # BEAR/BULL/NEUTRAL
    lot_size: float = 0.0    # Calculated by main.py based on balance
    reason: str = ""         # Human-readable signal reason


# ═══════════════════════════════════════════════════════════════════════════════
# V22 ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class ForgeV22Engine:
    """
    Complete v22 trading engine.
    
    Usage in main.py:
        # Initialize once at startup
        engine = ForgeV22Engine(symbol_map={"EURUSD": "EURUSD.sim", ...})
        
        # Each cycle
        signals = engine.process_cycle(snapshots, current_time, open_positions)
        for sig in signals:
            # Execute via MetaAPI
    """
    
    # Limits
    MAX_OPEN_POSITIONS = 5
    MAX_DAILY_TRADES = 15
    COOLDOWN_BARS = 2           # Bars to wait after a trade on same symbol
    
    def __init__(
        self,
        symbol_map: Optional[Dict[str, str]] = None,
        default_regime: str = Regime.BEAR,
        mode: str = "EVAL",  # EVAL or FUNDED
    ):
        """
        Initialize the v22 engine.
        
        Args:
            symbol_map: Maps our symbols to MT5 symbols {"EURUSD": "EURUSD.sim"}
            default_regime: Starting regime before detection kicks in
            mode: "EVAL" for conservative, "FUNDED" for aggressive
        """
        self.symbol_map = symbol_map or {}
        self.mode = mode
        
        # Create GENESIS regime router
        self.genesis = create_genesis(default_regime=default_regime)
        
        # Signal engine
        self.signal_engine = SignalEngine()
        
        # Correlation guard
        self.correlation = CorrelationGuard()
        
        # State tracking
        self.last_trade_bar: Dict[str, int] = {}  # symbol -> last trade cycle
        self.daily_trades: Dict[str, int] = {}     # date_key -> count
        self.cycle_count = 0
        self.regime_switches: List[Dict] = []      # Log of regime changes
        
        # All instruments we can trade
        self.instruments = list(self.genesis.all_symbols)
        
        logger.info(f"[V22] Engine initialized | Mode: {mode} | "
                    f"Instruments: {len(self.instruments)} | "
                    f"Default regime: {default_regime}")
        logger.info(f"[V22] GENESIS loaded: SHORT={len(self.genesis.short_config)} "
                    f"LONG={len(self.genesis.long_config)} "
                    f"NEUTRAL={len(self.genesis.neutral_config)}")

    def process_cycle(
        self,
        snapshots: Dict[str, MarketSnapshot],
        current_time: datetime,
        open_positions: Optional[Set[str]] = None,
        balance: float = 100000,
    ) -> List[V22TradeSignal]:
        """
        Main entry point — called every cycle from main.py.
        
        Args:
            snapshots: Market data for each instrument {symbol: MarketSnapshot}
            current_time: Current UTC time
            open_positions: Set of symbols that already have open positions
            balance: Current account balance for lot sizing
            
        Returns:
            List of V22TradeSignal ready for execution
        """
        self.cycle_count += 1
        open_positions = open_positions or set()
        
        # Daily trade counter
        date_key = current_time.strftime("%Y-%m-%d")
        if date_key not in self.daily_trades:
            self.daily_trades = {date_key: 0}  # Reset old days
        
        if self.daily_trades.get(date_key, 0) >= self.MAX_DAILY_TRADES:
            return []
        
        # Step 1: Update GENESIS regime for each instrument
        regime_signals = {}
        for symbol, snap in snapshots.items():
            if symbol not in self.genesis.all_symbols:
                continue
            
            indicators = extract_regime_indicators(snap)
            regime, switched = self.genesis.update_regime(
                symbol,
                current_time=current_time,
                **indicators
            )
            regime_signals[symbol] = regime
            
            if switched:
                self.regime_switches.append({
                    "time": current_time.isoformat(),
                    "symbol": symbol,
                    "new_regime": regime,
                    "adx": indicators["adx"],
                })
        
        # Step 2: For each instrument, get the correct setup from GENESIS
        # and temporarily inject it into SETUP_CONFIG for signal generation
        from forge_instruments_v22 import SETUP_CONFIG
        original_configs = {}
        active_setups = {}
        
        for symbol, snap in snapshots.items():
            indicators = extract_regime_indicators(snap)
            setup = self.genesis.get_active_setup(symbol, **indicators, current_time=current_time)
            if setup:
                original_configs[symbol] = SETUP_CONFIG.get(symbol)
                SETUP_CONFIG[symbol] = setup
                active_setups[symbol] = setup
        
        # Step 3: Generate signals using v22 signal engine
        try:
            raw_signals = self.signal_engine.generate_signals(snapshots, current_time=current_time)
        except Exception as e:
            logger.error(f"[V22] Signal generation error: {e}")
            raw_signals = []
        
        # Step 4: Restore original configs
        for symbol, orig in original_configs.items():
            if orig is not None:
                SETUP_CONFIG[symbol] = orig
            elif symbol in SETUP_CONFIG:
                del SETUP_CONFIG[symbol]
        
        # Step 5: Filter signals
        valid_signals = []
        
        for sig in raw_signals:
            # Skip if already have position on this symbol
            if sig.symbol in open_positions:
                continue
            
            # Skip if too many open positions
            if len(open_positions) + len(valid_signals) >= self.MAX_OPEN_POSITIONS:
                break
            
            # Skip if in cooldown
            if self.cycle_count - self.last_trade_bar.get(sig.symbol, -999) < self.COOLDOWN_BARS:
                continue
            
            # Skip if correlation conflict
            current_open = open_positions | {s.symbol for s in valid_signals}
            ok, reason = self.correlation.can_trade(sig.symbol, current_open)
            if not ok:
                continue
            
            # Get the MT5 symbol
            mt5_sym = self.symbol_map.get(sig.symbol, sig.symbol)
            
            # Get the regime for logging
            regime = regime_signals.get(sig.symbol, "DEFAULT")
            
            # Get the setup for trade type info
            setup = active_setups.get(sig.symbol)
            
            # Build the trade signal
            trade = V22TradeSignal(
                symbol=sig.symbol,
                mt5_symbol=mt5_sym,
                direction=sig.direction,
                strategy=sig.strategy.value,
                trade_type=sig.trade_type.value if hasattr(sig, 'trade_type') else "SCALP",
                order_type=sig.order_type.value if hasattr(sig, 'order_type') else "MARKET",
                entry_price=sig.entry_price,
                sl_price=sig.sl_price,
                tp_price=sig.tp_price,
                risk_pct=sig.risk_pct,
                atr=sig.atr_value,
                confidence=sig.final_confidence,
                regime=regime,
                reason=f"{sig.strategy.value} | {regime} | conf={sig.final_confidence:.2f}",
            )
            
            valid_signals.append(trade)
            self.last_trade_bar[sig.symbol] = self.cycle_count
            self.daily_trades[date_key] = self.daily_trades.get(date_key, 0) + 1
        
        if valid_signals:
            for s in valid_signals:
                logger.info(f"[V22] SIGNAL: {s.symbol} {s.direction} {s.strategy} | "
                          f"Regime={s.regime} | Entry={s.entry_price:.5f} "
                          f"SL={s.sl_price:.5f} TP={s.tp_price:.5f}")
        
        return valid_signals

    def get_v22_instruments(self) -> List[str]:
        """Get all instruments v22 trades — used by main.py to resolve symbols."""
        return sorted(self.instruments)

    def get_status(self) -> str:
        """Get engine status for Telegram."""
        regimes = self.genesis.get_all_regimes()
        counts = {}
        for r in regimes.values():
            counts[r] = counts.get(r, 0) + 1
        
        date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        daily = self.daily_trades.get(date_key, 0)
        
        lines = [
            f"🔱 FORGE V22 ENGINE STATUS",
            f"  Cycle: {self.cycle_count} | Mode: {self.mode}",
            f"  Daily trades: {daily}/{self.MAX_DAILY_TRADES}",
            f"  Regime switches today: {len([s for s in self.regime_switches if date_key in s.get('time', '')])}",
            f"  Regimes: " + " | ".join(f"{k}:{v}" for k,v in sorted(counts.items())),
        ]
        return "\n".join(lines)

    def format_telegram_signal(self, sig: V22TradeSignal) -> str:
        """Format a signal for Telegram notification."""
        emoji = "🟢" if sig.direction == "LONG" else "🔴"
        runner = "🏃" if sig.trade_type == "RUNNER" else "🎯"
        
        return (
            f"{emoji} {sig.symbol} {sig.direction} {runner}\n"
            f"Strategy: {sig.strategy}\n"
            f"Regime: {sig.regime}\n"
            f"Entry: {sig.entry_price:.5f}\n"
            f"SL: {sig.sl_price:.5f}\n"
            f"TP: {sig.tp_price:.5f}\n"
            f"Risk: {sig.risk_pct}%\n"
            f"Confidence: {sig.confidence:.2f}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT BUILDER — builds MarketSnapshot from MetaAPI price data
# ═══════════════════════════════════════════════════════════════════════════════

def build_snapshot_from_candles(
    symbol: str,
    candles: list,
    current_bid: float,
    current_ask: float,
    current_hour_utc: int = 12,
) -> Optional[MarketSnapshot]:
    """
    Build a MarketSnapshot from MetaAPI candle history.
    
    Args:
        symbol: Instrument symbol
        candles: List of candle dicts from MetaAPI [{open, high, low, close, tickVolume}, ...]
        current_bid: Current bid price
        current_ask: Current ask price
        current_hour_utc: Current UTC hour
        
    Returns:
        MarketSnapshot or None if insufficient data
    """
    import numpy as np
    
    if not candles or len(candles) < 30:
        return None
    
    # Extract OHLCV
    o = np.array([c.get("open", 0) for c in candles], dtype=float)
    h = np.array([c.get("high", 0) for c in candles], dtype=float)
    l = np.array([c.get("low", 0) for c in candles], dtype=float)
    c = np.array([c.get("close", 0) for c in candles], dtype=float)
    v = np.array([c.get("tickVolume", c.get("volume", 0)) for c in candles], dtype=float)
    
    n = len(c)
    if n < 30:
        return None
    
    # Compute indicators
    def _atr(h,l,c,p=14):
        if len(c)<2: return abs(c[-1])*0.01
        tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1])))
        if len(tr)<p: return np.mean(tr)
        a=np.mean(tr[:p])
        for i in range(p,len(tr)): a=(a*(p-1)+tr[i])/p
        return a

    def _rsi(c,p=14):
        if len(c)<p+1: return 50.0
        d=np.diff(c); g=np.where(d>0,d,0); lo=np.where(d<0,-d,0)
        ag=np.mean(g[:p]); al=np.mean(lo[:p])
        for i in range(p,len(g)): ag=(ag*(p-1)+g[i])/p; al=(al*(p-1)+lo[i])/p
        if al==0: return 100.0
        return 100.0-100.0/(1.0+ag/al)

    def _ema(d,p):
        if len(d)<p: return np.mean(d) if len(d)>0 else 0.0
        m=2.0/(p+1); e=np.mean(d[:p])
        for i in range(p,len(d)): e=(d[i]-e)*m+e
        return e

    def _bollinger(c,p=20,m=2.0):
        if len(c)<p:
            mid=np.mean(c); s=np.std(c) if len(c)>1 else abs(mid)*0.01
            return mid+m*s,mid-m*s,mid
        sma=np.mean(c[-p:]); s=np.std(c[-p:])
        if s==0: s=abs(sma)*0.001
        return sma+m*s,sma-m*s,sma

    def _stochastic(h,l,c,kp=14,dp=3):
        if len(c)<kp+dp: return 50.,50.,50.,50.
        kvs=[]
        for i in range(kp-1,len(c)):
            hi,lo=np.max(h[i-kp+1:i+1]),np.min(l[i-kp+1:i+1])
            kvs.append(100.*(c[i]-lo)/(hi-lo) if hi!=lo else 50.)
        kvs=np.array(kvs)
        if len(kvs)<dp: return kvs[-1],kvs[-1],kvs[-1],kvs[-1]
        return kvs[-1],np.mean(kvs[-dp:]),kvs[-2] if len(kvs)>1 else kvs[-1],np.mean(kvs[-dp-1:-1]) if len(kvs)>dp else np.mean(kvs[-dp:])

    def _vwap(h,l,c,v):
        tp=(h+l+c)/3.; cv=np.cumsum(v); ctv=np.cumsum(tp*v)
        if cv[-1]==0: return c[-1],abs(c[-1])*0.001
        vw=ctv[-1]/cv[-1]; vs=np.std(tp-vw) if len(tp)>1 else abs(c[-1])*0.001
        return vw,max(vs,abs(c[-1])*0.0001)

    def _adx(h,l,c,p=14):
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

    atr = _atr(h,l,c)
    if atr == 0: atr = abs(c[-1]) * 0.001
    rsi = _rsi(c)
    sk,sd,skp,sdp = _stochastic(h,l,c)
    e50 = _ema(c, min(50,n))
    e200 = _ema(c, min(200,n))
    bbu,bbl,bbm = _bollinger(c)
    vwap,vstd = _vwap(h,l,c,v)
    adx,adxp,pdi,mdi = _adx(h,l,c)
    
    # Keltner
    ke = _ema(c, 20)
    ku = ke + 1.5 * atr
    kl = ke - 1.5 * atr
    
    # Session-level data
    sl = min(8, n)
    pi = min(sl+8, n)
    pdh = np.max(h[-pi:-sl]) if pi > sl else np.max(h[:sl])
    pdl = np.min(l[-pi:-sl]) if pi > sl else np.min(l[:sl])
    pdc = c[-sl-1] if n > sl else c[0]
    
    return MarketSnapshot(
        symbol=symbol, opens=o, highs=h, lows=l, closes=c, volumes=v,
        bid=current_bid, ask=current_ask,
        atr=atr, rsi=rsi, stoch_k=sk, stoch_d=sd, stoch_k_prev=skp, stoch_d_prev=sdp,
        ema_50=e50, ema_200=e200, bb_upper=bbu, bb_lower=bbl, bb_middle=bbm,
        vwap=vwap, vwap_std=vstd, adx=adx, adx_prev=adxp, plus_di=pdi, minus_di=mdi,
        prev_day_high=pdh, prev_day_low=pdl, prev_day_close=pdc,
        session_open=o[-sl], session_high=np.max(h[-sl:]), session_low=np.min(l[-sl:]),
        orb_high=h[-sl], orb_low=l[-sl], orb_complete=True,
        asian_high=np.max(h[:min(7,n)]), asian_low=np.min(l[:min(7,n)]), asian_complete=True,
        keltner_upper=ku, keltner_lower=kl, bars_since_open=sl,
        current_hour_utc=current_hour_utc,
    )
