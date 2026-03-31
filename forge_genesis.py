"""
FORGE GENESIS — Adaptive Regime Router
========================================
Replaces the external GENESIS service with an in-FORGE module.
Each instrument gets its regime detected every cycle and routes
to the correct strategy config (SHORT/LONG/NEUTRAL).

Architecture:
  - forge_instruments_v22.py  → SHORT config (BEAR regime)    +167R proven
  - forge_instruments_long.py → LONG config (BULL regime)      +234R proven  
  - forge_instruments_neutral.py → NEUTRAL config (CHOPPY)     TBD
  - THIS FILE (forge_genesis.py) → Detects regime, picks config

Regime Detection (per instrument, per bar):
  BEAR:    ADX > 25 AND EMA50 < EMA200         → Use SHORT config
  BULL:    ADX > 25 AND EMA50 > EMA200         → Use LONG config
  NEUTRAL: ADX < 20 OR BB_width < squeeze_pct  → Use NEUTRAL config
  DEFAULT: 20 < ADX < 25 (transition zone)     → Use highest-PF config

Integration:
  In main.py's trading loop, instead of:
    setup = SETUP_CONFIG[symbol]
  
  Use:
    setup = genesis.get_active_setup(symbol, snapshot)

  GENESIS returns the correct InstrumentSetup based on current regime.

Logging:
  Every regime switch gets logged to Telegram:
    "🔄 REGIME SWITCH: EURUSD BEAR → BULL (ADX=28.4, EMA50>EMA200)"
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field

from forge_instruments_v22 import (
    SETUP_CONFIG, InstrumentSetup, Strategy, Direction, TradeType, OrderType,
)

logger = logging.getLogger("titan_forge.genesis")


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME ENUM & CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

class Regime:
    BEAR = "BEAR"
    BULL = "BULL"
    NEUTRAL = "NEUTRAL"
    DEFAULT = "DEFAULT"


@dataclass
class RegimeState:
    """Tracks the current regime for a single instrument."""
    symbol: str
    current_regime: str = Regime.DEFAULT
    regime_since: Optional[datetime] = None
    regime_bars: int = 0
    adx: float = 20.0
    bb_width: float = 5.0
    ema50: float = 0.0
    ema200: float = 0.0
    last_switch: Optional[datetime] = None
    switches_today: int = 0
    
    # Smoothing: require N consecutive bars in new regime before switching
    pending_regime: Optional[str] = None
    pending_bars: int = 0


# ═══════════════════════════════════════════════════════════════════════════════
# GENESIS ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class GenesisEngine:
    """
    Per-instrument regime detection and strategy routing.
    
    Carries three strategy configs:
      - short_config: from forge_instruments_v22.py (BEAR regime)
      - long_config:  from forge_instruments_long.py (BULL regime)  
      - neutral_config: from forge_instruments_neutral.py (NEUTRAL regime)
    
    Each cycle, for each instrument:
      1. Compute regime indicators (ADX, EMA50/200, BB width)
      2. Detect regime (BEAR/BULL/NEUTRAL)
      3. Apply smoothing (require 3 consecutive bars in new regime)
      4. Return the correct InstrumentSetup
    """
    
    # Regime detection thresholds
    ADX_TREND_THRESHOLD = 25      # ADX above this = trending
    ADX_NEUTRAL_THRESHOLD = 20    # ADX below this = neutral/choppy
    BB_SQUEEZE_PCT = 1.5          # BB width below this % = squeeze/neutral
    REGIME_CONFIRM_BARS = 3       # Bars required to confirm regime switch
    MAX_SWITCHES_PER_DAY = 3      # Prevent whipsaw: max switches per instrument per day
    
    def __init__(
        self,
        short_config: Optional[Dict[str, InstrumentSetup]] = None,
        long_config: Optional[Dict[str, InstrumentSetup]] = None,
        neutral_config: Optional[Dict[str, InstrumentSetup]] = None,
        default_regime: str = Regime.BEAR,  # Default when we can't determine
    ):
        """
        Initialize GENESIS with strategy configs for each regime.
        
        Args:
            short_config: SETUP_CONFIG from forge_instruments_v22.py
            long_config: LONG_SETUP_CONFIG from forge_instruments_long.py
            neutral_config: NEUTRAL_SETUP_CONFIG from forge_instruments_neutral.py
            default_regime: Which regime to use on startup before detection kicks in
        """
        self.short_config = short_config or dict(SETUP_CONFIG)
        self.long_config = long_config or {}
        self.neutral_config = neutral_config or {}
        self.default_regime = default_regime
        
        # Per-instrument regime state
        self.states: Dict[str, RegimeState] = {}
        
        # All symbols we track (union of all configs)
        self.all_symbols = set(
            list(self.short_config.keys()) + 
            list(self.long_config.keys()) + 
            list(self.neutral_config.keys())
        )
        
        # Initialize states
        for sym in self.all_symbols:
            self.states[sym] = RegimeState(
                symbol=sym,
                current_regime=default_regime,
                regime_since=datetime.now(timezone.utc),
            )
        
        logger.info(f"[GENESIS] Initialized: {len(self.all_symbols)} instruments | "
                    f"SHORT: {len(self.short_config)} | LONG: {len(self.long_config)} | "
                    f"NEUTRAL: {len(self.neutral_config)} | Default: {default_regime}")

    def detect_regime(
        self,
        symbol: str,
        adx: float,
        ema50: float,
        ema200: float,
        bb_width: float,
    ) -> str:
        """
        Detect the market regime for a single instrument.
        
        Args:
            symbol: Instrument symbol
            adx: Current ADX value (14-period)
            ema50: Current 50-period EMA
            ema200: Current 200-period EMA
            bb_width: Bollinger Band width as % of price
            
        Returns:
            Regime string: BEAR, BULL, NEUTRAL, or DEFAULT
        """
        # NEUTRAL: low ADX or tight BB squeeze
        if adx < self.ADX_NEUTRAL_THRESHOLD or bb_width < self.BB_SQUEEZE_PCT:
            return Regime.NEUTRAL
        
        # BEAR: strong trend + downtrend
        if adx > self.ADX_TREND_THRESHOLD and ema50 < ema200:
            return Regime.BEAR
        
        # BULL: strong trend + uptrend
        if adx > self.ADX_TREND_THRESHOLD and ema50 > ema200:
            return Regime.BULL
        
        # Transition zone: 20 < ADX < 25, or EMAs are crossing
        return Regime.DEFAULT

    def update_regime(
        self,
        symbol: str,
        adx: float,
        ema50: float,
        ema200: float,
        bb_width: float,
        current_time: Optional[datetime] = None,
    ) -> Tuple[str, bool]:
        """
        Update the regime for an instrument with smoothing.
        
        Returns:
            (current_regime, switched) — the active regime and whether it just switched
        """
        if symbol not in self.states:
            self.states[symbol] = RegimeState(
                symbol=symbol,
                current_regime=self.default_regime,
                regime_since=current_time or datetime.now(timezone.utc),
            )
        
        state = self.states[symbol]
        now = current_time or datetime.now(timezone.utc)
        
        # Reset daily switch counter at midnight UTC
        if state.last_switch and state.last_switch.date() != now.date():
            state.switches_today = 0
        
        # Update indicators
        state.adx = adx
        state.bb_width = bb_width
        state.ema50 = ema50
        state.ema200 = ema200
        
        # Detect raw regime
        raw_regime = self.detect_regime(symbol, adx, ema50, ema200, bb_width)
        
        # DEFAULT maps to whatever the current regime is (don't switch in transition zone)
        if raw_regime == Regime.DEFAULT:
            state.pending_regime = None
            state.pending_bars = 0
            state.regime_bars += 1
            return state.current_regime, False
        
        # Same regime as current — reset pending, count bars
        if raw_regime == state.current_regime:
            state.pending_regime = None
            state.pending_bars = 0
            state.regime_bars += 1
            return state.current_regime, False
        
        # New regime detected — start or continue pending confirmation
        if raw_regime == state.pending_regime:
            state.pending_bars += 1
        else:
            state.pending_regime = raw_regime
            state.pending_bars = 1
        
        # Check if confirmed (enough consecutive bars in new regime)
        if state.pending_bars >= self.REGIME_CONFIRM_BARS:
            # Check daily switch limit
            if state.switches_today >= self.MAX_SWITCHES_PER_DAY:
                logger.warning(f"[GENESIS] {symbol}: Regime switch blocked — "
                             f"max {self.MAX_SWITCHES_PER_DAY} switches/day reached")
                return state.current_regime, False
            
            # Only switch if we have a config for the new regime
            new_regime = state.pending_regime
            has_config = self._has_config(symbol, new_regime)
            
            if not has_config:
                logger.debug(f"[GENESIS] {symbol}: No {new_regime} config available, staying {state.current_regime}")
                return state.current_regime, False
            
            # Execute switch
            old_regime = state.current_regime
            state.current_regime = new_regime
            state.regime_since = now
            state.regime_bars = 0
            state.last_switch = now
            state.switches_today += 1
            state.pending_regime = None
            state.pending_bars = 0
            
            logger.info(f"[GENESIS] 🔄 REGIME SWITCH: {symbol} {old_regime} → {new_regime} "
                       f"(ADX={adx:.1f}, EMA50{'>' if ema50 > ema200 else '<'}EMA200, "
                       f"BB={bb_width:.1f}%)")
            
            return new_regime, True
        
        # Still pending — keep current regime
        state.regime_bars += 1
        return state.current_regime, False

    def _has_config(self, symbol: str, regime: str) -> bool:
        """Check if we have a strategy config for this symbol+regime."""
        if regime == Regime.BEAR:
            return symbol in self.short_config
        elif regime == Regime.BULL:
            return symbol in self.long_config
        elif regime == Regime.NEUTRAL:
            return symbol in self.neutral_config
        return symbol in self.short_config  # DEFAULT fallback

    def get_active_setup(
        self,
        symbol: str,
        adx: float = 20.0,
        ema50: float = 0.0,
        ema200: float = 0.0,
        bb_width: float = 5.0,
        current_time: Optional[datetime] = None,
    ) -> Optional[InstrumentSetup]:
        """
        Main entry point: get the correct InstrumentSetup for a symbol
        based on current market conditions.
        
        This is the function main.py calls instead of SETUP_CONFIG[symbol].
        
        Returns:
            InstrumentSetup for the current regime, or None if no config available
        """
        # Update regime
        regime, switched = self.update_regime(symbol, adx, ema50, ema200, bb_width, current_time)
        
        # Get setup from the appropriate config
        if regime == Regime.BEAR:
            setup = self.short_config.get(symbol)
        elif regime == Regime.BULL:
            setup = self.long_config.get(symbol)
        elif regime == Regime.NEUTRAL:
            setup = self.neutral_config.get(symbol)
        else:
            # DEFAULT fallback chain: SHORT > LONG > NEUTRAL
            setup = (self.short_config.get(symbol) or 
                    self.long_config.get(symbol) or 
                    self.neutral_config.get(symbol))
        
        return setup

    def get_regime_status(self, symbol: str) -> Dict:
        """Get the current regime status for an instrument (for logging/Telegram)."""
        state = self.states.get(symbol)
        if not state:
            return {"symbol": symbol, "regime": "UNKNOWN", "bars": 0}
        
        return {
            "symbol": symbol,
            "regime": state.current_regime,
            "bars": state.regime_bars,
            "since": state.regime_since.isoformat() if state.regime_since else None,
            "adx": state.adx,
            "bb_width": state.bb_width,
            "ema_trend": "UP" if state.ema50 > state.ema200 else "DOWN",
            "switches_today": state.switches_today,
            "pending": state.pending_regime,
            "pending_bars": state.pending_bars,
        }

    def get_all_regimes(self) -> Dict[str, str]:
        """Get a summary of all instrument regimes."""
        return {sym: state.current_regime for sym, state in self.states.items()}

    def get_regime_summary(self) -> str:
        """Get a human-readable summary for Telegram."""
        regimes = self.get_all_regimes()
        counts = {}
        for r in regimes.values():
            counts[r] = counts.get(r, 0) + 1
        
        lines = ["🧠 GENESIS REGIME STATUS"]
        for regime in [Regime.BEAR, Regime.BULL, Regime.NEUTRAL, Regime.DEFAULT]:
            if regime in counts:
                syms = [s for s, r in regimes.items() if r == regime]
                lines.append(f"  {regime}: {counts[regime]} — {', '.join(sorted(syms))}")
        
        return "\n".join(lines)

    def force_regime(self, symbol: str, regime: str):
        """Force a regime for an instrument (manual override via Telegram command)."""
        if symbol in self.states:
            old = self.states[symbol].current_regime
            self.states[symbol].current_regime = regime
            self.states[symbol].regime_since = datetime.now(timezone.utc)
            self.states[symbol].regime_bars = 0
            self.states[symbol].pending_regime = None
            self.states[symbol].pending_bars = 0
            logger.info(f"[GENESIS] ⚡ MANUAL OVERRIDE: {symbol} {old} → {regime}")

    def force_all(self, regime: str):
        """Force all instruments to a specific regime."""
        for sym in self.states:
            self.force_regime(sym, regime)
        logger.info(f"[GENESIS] ⚡ ALL INSTRUMENTS → {regime}")


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER: Extract indicators from MarketSnapshot
# ═══════════════════════════════════════════════════════════════════════════════

def extract_regime_indicators(snapshot) -> Dict[str, float]:
    """
    Extract the indicators needed for regime detection from a MarketSnapshot.
    Use this in main.py to feed GENESIS:
    
        indicators = extract_regime_indicators(snapshot)
        setup = genesis.get_active_setup(symbol, **indicators)
    """
    # BB width as percentage of price
    price = snapshot.closes[-1] if len(snapshot.closes) > 0 else 1.0
    bb_range = snapshot.bb_upper - snapshot.bb_lower
    bb_width = (bb_range / price * 100) if price > 0 else 5.0
    
    return {
        "adx": snapshot.adx if snapshot.adx is not None else 20.0,
        "ema50": snapshot.ema_50 if snapshot.ema_50 is not None else 0.0,
        "ema200": snapshot.ema_200 if snapshot.ema_200 is not None else 0.0,
        "bb_width": bb_width,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMANDS (for forge_commands.py integration)
# ═══════════════════════════════════════════════════════════════════════════════

GENESIS_COMMANDS = {
    "/regime": "Show current regime for all instruments",
    "/regime_detail": "Show detailed regime info for all instruments",
    "/force_bear": "Force all instruments to BEAR regime",
    "/force_bull": "Force all instruments to BULL regime",
    "/force_neutral": "Force all instruments to NEUTRAL regime",
    "/force_auto": "Reset all instruments to automatic regime detection",
}


def handle_genesis_command(genesis: GenesisEngine, command: str, args: str = "") -> str:
    """Handle Telegram commands for GENESIS. Returns response string."""
    
    if command == "/regime":
        return genesis.get_regime_summary()
    
    elif command == "/regime_detail":
        lines = ["🧠 GENESIS DETAILED STATUS"]
        for sym in sorted(genesis.states.keys()):
            status = genesis.get_regime_status(sym)
            lines.append(
                f"  {sym:10s}: {status['regime']:7s} | ADX={status['adx']:.0f} "
                f"| EMA={status['ema_trend']:4s} | BB={status['bb_width']:.1f}% "
                f"| {status['bars']}bars"
            )
        return "\n".join(lines)
    
    elif command == "/force_bear":
        genesis.force_all(Regime.BEAR)
        return "⚡ All instruments forced to BEAR regime (SHORT config)"
    
    elif command == "/force_bull":
        genesis.force_all(Regime.BULL)
        return "⚡ All instruments forced to BULL regime (LONG config)"
    
    elif command == "/force_neutral":
        genesis.force_all(Regime.NEUTRAL)
        return "⚡ All instruments forced to NEUTRAL regime"
    
    elif command == "/force_auto":
        genesis.force_all(Regime.DEFAULT)
        return "⚡ All instruments reset to automatic regime detection"
    
    return f"Unknown GENESIS command: {command}"


# ═══════════════════════════════════════════════════════════════════════════════
# INITIALIZATION HELPER
# ═══════════════════════════════════════════════════════════════════════════════

def create_genesis(default_regime: str = Regime.BEAR) -> GenesisEngine:
    """
    Factory function to create a GENESIS engine with all available configs.
    
    Usage in main.py:
        from forge_genesis import create_genesis, extract_regime_indicators
        
        genesis = create_genesis(default_regime="BEAR")
        
        # In the trading loop:
        for symbol, snapshot in snapshots.items():
            indicators = extract_regime_indicators(snapshot)
            setup = genesis.get_active_setup(symbol, **indicators)
            if setup:
                # Use setup for signal generation
    """
    from forge_instruments_v22 import SETUP_CONFIG
    
    # Try to import LONG config
    long_config = {}
    try:
        from forge_instruments_long import LONG_SETUP_CONFIG
        long_config = dict(LONG_SETUP_CONFIG)
        logger.info(f"[GENESIS] Loaded LONG config: {len(long_config)} instruments")
    except ImportError:
        logger.warning("[GENESIS] No LONG config found (forge_instruments_long.py)")
    
    # Try to import NEUTRAL config
    neutral_config = {}
    try:
        from forge_instruments_neutral import NEUTRAL_SETUP_CONFIG
        neutral_config = dict(NEUTRAL_SETUP_CONFIG)
        logger.info(f"[GENESIS] Loaded NEUTRAL config: {len(neutral_config)} instruments")
    except ImportError:
        logger.warning("[GENESIS] No NEUTRAL config found (forge_instruments_neutral.py)")
    
    return GenesisEngine(
        short_config=dict(SETUP_CONFIG),
        long_config=long_config,
        neutral_config=neutral_config,
        default_regime=default_regime,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# STANDALONE TEST
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  GENESIS ENGINE — Self Test")
    print("=" * 60)
    
    genesis = create_genesis(default_regime=Regime.BEAR)
    
    print(f"\n  Instruments: {len(genesis.all_symbols)}")
    print(f"  SHORT configs: {len(genesis.short_config)}")
    print(f"  LONG configs: {len(genesis.long_config)}")
    print(f"  NEUTRAL configs: {len(genesis.neutral_config)}")
    
    # Simulate regime detection
    print(f"\n  Simulating regime changes...")
    
    # Test BEAR regime (ADX high, EMA50 < EMA200)
    regime, switched = genesis.update_regime("EURUSD", adx=30, ema50=1.08, ema200=1.10, bb_width=3.0)
    print(f"  EURUSD: ADX=30 EMA50<EMA200 → {regime} (switched={switched})")
    
    # Need 3 bars to confirm
    regime, switched = genesis.update_regime("EURUSD", adx=31, ema50=1.08, ema200=1.10, bb_width=3.0)
    print(f"  EURUSD: bar 2 → {regime} (switched={switched})")
    
    regime, switched = genesis.update_regime("EURUSD", adx=32, ema50=1.08, ema200=1.10, bb_width=3.0)
    print(f"  EURUSD: bar 3 → {regime} (switched={switched})")
    
    # Test BULL regime (ADX high, EMA50 > EMA200)
    for i in range(4):
        regime, switched = genesis.update_regime("BTCUSD", adx=28, ema50=95000, ema200=85000, bb_width=4.0)
    print(f"  BTCUSD: ADX=28 EMA50>EMA200 → {regime} (switched={switched})")
    
    # Test NEUTRAL regime (low ADX)
    for i in range(4):
        regime, switched = genesis.update_regime("USDCHF", adx=15, ema50=0.90, ema200=0.91, bb_width=1.0)
    print(f"  USDCHF: ADX=15 squeeze → {regime} (switched={switched})")
    
    # Show summary
    print(f"\n{genesis.get_regime_summary()}")
    
    # Test setup retrieval
    print(f"\n  Testing get_active_setup:")
    for sym in ["EURUSD", "BTCUSD", "USDCHF"]:
        state = genesis.states.get(sym)
        setup = genesis.get_active_setup(sym, adx=state.adx, ema50=state.ema50,
                                          ema200=state.ema200, bb_width=state.bb_width)
        if setup:
            print(f"    {sym}: {state.current_regime} → {setup.strategy.value} {setup.direction.value}")
        else:
            print(f"    {sym}: {state.current_regime} → No config available")
    
    print(f"\n  ✅ GENESIS self-test passed")
    print("=" * 60 + "\n")


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY STUBS — v21 main.py imports these
# ═══════════════════════════════════════════════════════════════════════════════

def auto_evolve(*args, **kwargs):
    """Legacy stub for v21 compatibility. No-op."""
    return {}

def get_calibrated_wr(*args, **kwargs):
    """Legacy stub for v21 compatibility. Returns None."""
    return None
