"""
TITAN FORGE V22.5 — PRECISION SCALP EXECUTOR
==============================================
Divine's core strategy: enter, grab fixed dollar target, exit via limit order,
wait for pullback, re-enter, repeat. 10 × $100 = $1,000/day.

This module runs ALONGSIDE existing strategies, not replacing them.
Scalps are DIRECTED by the direction engine (compass, not wall).
Targets are CALIBRATED per instrument by the research engine's MFE data.

FLOW:
  1. Direction engine says "EURUSD is trending UP"
  2. Scalp executor looks for LONG entry on EURUSD
  3. Entry triggers → places LIMIT SELL at entry + $120 target
  4. Limit fills → profit banked → enters cooldown
  5. Watches for pullback (price retraces 40-60% of the move)
  6. Pullback confirmed → re-enters LONG → new limit at +$120
  7. Repeat until direction changes or daily gate locks

DOES NOT:
  - Replace or interfere with runner/traditional strategies
  - Block any trades
  - Use ATR-based exits (uses FIXED DOLLAR targets)
"""

import numpy as np
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

logger = logging.getLogger("FORGE.scalp")


class ScalpState(Enum):
    IDLE = "IDLE"                    # Looking for entry
    IN_TRADE = "IN_TRADE"           # Position open, limit TP placed
    COOLDOWN = "COOLDOWN"           # Just exited, waiting for pullback
    PULLBACK_WATCH = "PULLBACK_WATCH"  # Detected pullback start, watching for floor


@dataclass
class ScalpConfig:
    """Per-instrument scalp configuration. Set by research engine."""
    target_dollars: float = 100.0    # Fixed dollar TP target
    stop_dollars: float = 150.0      # Fixed dollar SL (1.5x target default)
    cooldown_bars: int = 3           # Bars to wait after exit before re-entry
    pullback_pct: float = 0.40       # Pullback must retrace 40% of move
    max_scalps_per_day: int = 15     # Max scalps per instrument per day
    min_atr_filter: float = 0.3      # Min ATR multiplier to avoid dead markets
    enabled: bool = True


# Default targets per instrument (ESTIMATES — research engine overrides these)
DEFAULT_CONFIGS = {
    "EURUSD":  ScalpConfig(target_dollars=120, stop_dollars=180),
    "GBPUSD":  ScalpConfig(target_dollars=150, stop_dollars=225),
    "USDJPY":  ScalpConfig(target_dollars=130, stop_dollars=195),
    "USDCHF":  ScalpConfig(target_dollars=120, stop_dollars=180),
    "EURGBP":  ScalpConfig(target_dollars=80,  stop_dollars=120),
    "GBPJPY":  ScalpConfig(target_dollars=100, stop_dollars=150),
    "NZDUSD":  ScalpConfig(target_dollars=100, stop_dollars=150),
    "AUDUSD":  ScalpConfig(target_dollars=100, stop_dollars=150),
    "AUDNZD":  ScalpConfig(target_dollars=80,  stop_dollars=120),
    "EURJPY":  ScalpConfig(target_dollars=120, stop_dollars=180),
    "XAUUSD":  ScalpConfig(target_dollars=250, stop_dollars=375),
    "US100":   ScalpConfig(target_dollars=200, stop_dollars=300),
    "USOIL":   ScalpConfig(target_dollars=150, stop_dollars=225),
    "BTCUSD":  ScalpConfig(target_dollars=300, stop_dollars=450),
}


@dataclass
class ScalpTracker:
    """Tracks state for one instrument's scalp activity."""
    state: ScalpState = ScalpState.IDLE
    direction: int = 0              # 1=LONG, -1=SHORT (from direction engine)
    entry_price: float = 0.0
    tp_price: float = 0.0
    sl_price: float = 0.0
    position_id: str = ""
    entry_time: float = 0.0
    cooldown_until: float = 0.0
    scalps_today: int = 0
    wins_today: int = 0
    losses_today: int = 0
    pnl_today: float = 0.0
    last_exit_price: float = 0.0
    last_move_high: float = 0.0     # High of the move that triggered scalp
    last_move_low: float = 0.0      # Low of the move
    pullback_floor: float = 0.0     # Detected pullback floor for re-entry


class PrecisionScalpExecutor:
    """
    Manages precision scalps across all instruments.
    
    Usage:
        executor = PrecisionScalpExecutor()
        
        # On each cycle:
        for sym in instruments:
            action = executor.evaluate(sym, current_price, direction, atr_value)
            if action:
                if action["type"] == "ENTER":
                    # Place market order + limit TP + SL
                elif action["type"] == "EXIT":
                    # Close position
    """
    
    def __init__(self, configs: Dict[str, ScalpConfig] = None):
        self.configs = configs or DEFAULT_CONFIGS.copy()
        self.trackers: Dict[str, ScalpTracker] = {}
        self._day_start: float = 0
    
    def reset_daily(self):
        """Call at start of each trading day."""
        self._day_start = time.time()
        for tracker in self.trackers.values():
            tracker.scalps_today = 0
            tracker.wins_today = 0
            tracker.losses_today = 0
            tracker.pnl_today = 0.0
            if tracker.state != ScalpState.IN_TRADE:
                tracker.state = ScalpState.IDLE
    
    def update_config(self, sym: str, target_dollars: float, stop_dollars: float = None):
        """Update target for an instrument (called after research engine runs)."""
        if sym not in self.configs:
            self.configs[sym] = ScalpConfig()
        self.configs[sym].target_dollars = target_dollars
        if stop_dollars:
            self.configs[sym].stop_dollars = stop_dollars
        else:
            self.configs[sym].stop_dollars = target_dollars * 1.5
        logger.info(f"[SCALP] {sym} target updated: ${target_dollars:.0f} / SL ${self.configs[sym].stop_dollars:.0f}")
    
    def load_research_targets(self, mfe_data: dict):
        """Load targets from research engine's MFE sweet spot output."""
        for key, data in mfe_data.items():
            sym = key.split("_")[0]
            sweet_spot = data.get("sweet_spot", 0)
            if sweet_spot > 0 and sym in self.configs:
                self.update_config(sym, sweet_spot)
    
    def _get_tracker(self, sym: str) -> ScalpTracker:
        if sym not in self.trackers:
            self.trackers[sym] = ScalpTracker()
        return self.trackers[sym]
    
    def _get_config(self, sym: str) -> ScalpConfig:
        return self.configs.get(sym, ScalpConfig())
    
    def evaluate(self, sym: str, current_price: float, direction: int,
                 current_atr: float, lot_size: float = 2.0,
                 dollar_per_unit: float = None) -> Optional[dict]:
        """
        Evaluate whether to enter, hold, or exit a scalp for this instrument.
        
        Args:
            sym: Instrument name
            current_price: Current bid/ask midpoint
            direction: From direction engine (1=LONG, -1=SHORT, 0=BOTH/NEUTRAL)
            current_atr: Current ATR value for the instrument
            lot_size: Position size in lots
            dollar_per_unit: Dollar value per price unit at this lot size
        
        Returns:
            None (no action) or dict with action details
        """
        config = self._get_config(sym)
        tracker = self._get_tracker(sym)
        
        if not config.enabled:
            return None
        
        # Auto-compute dollar_per_unit if not provided
        if dollar_per_unit is None:
            from forge_strategies_v22_5 import DOLLAR_PER_UNIT
            dollar_per_unit = DOLLAR_PER_UNIT.get(sym, 100000) * (lot_size / 2.0)
        
        now = time.time()
        
        # ─── STATE MACHINE ───
        
        if tracker.state == ScalpState.IN_TRADE:
            return self._manage_open_scalp(sym, current_price, tracker, config, dollar_per_unit)
        
        elif tracker.state == ScalpState.COOLDOWN:
            if now >= tracker.cooldown_until:
                tracker.state = ScalpState.PULLBACK_WATCH
                tracker.pullback_floor = current_price if tracker.direction == 1 else current_price
                logger.info(f"[SCALP] {sym} cooldown ended, watching for pullback")
            return None
        
        elif tracker.state == ScalpState.PULLBACK_WATCH:
            return self._watch_pullback(sym, current_price, direction, tracker, config, dollar_per_unit)
        
        elif tracker.state == ScalpState.IDLE:
            return self._find_entry(sym, current_price, direction, current_atr, tracker, config, dollar_per_unit)
        
        return None
    
    def _find_entry(self, sym: str, price: float, direction: int, atr_val: float,
                    tracker: ScalpTracker, config: ScalpConfig, dpu: float) -> Optional[dict]:
        """Look for scalp entry."""
        # Max scalps check
        if tracker.scalps_today >= config.max_scalps_per_day:
            return None
        
        # Need a direction from the engine
        if direction == 0:
            return None  # Wait for directional bias
        
        # ATR filter — skip dead markets
        # (We'd compare against avg ATR but for simplicity just check non-zero)
        if atr_val <= 0:
            return None
        
        # Calculate prices
        target_in_price = config.target_dollars / dpu if dpu > 0 else 0
        stop_in_price = config.stop_dollars / dpu if dpu > 0 else 0
        
        if target_in_price <= 0 or stop_in_price <= 0:
            return None
        
        if direction == 1:  # LONG scalp
            entry_price = price
            tp_price = entry_price + target_in_price
            sl_price = entry_price - stop_in_price
        else:  # SHORT scalp
            entry_price = price
            tp_price = entry_price - target_in_price
            sl_price = entry_price + stop_in_price
        
        # Update tracker
        tracker.state = ScalpState.IN_TRADE
        tracker.direction = direction
        tracker.entry_price = entry_price
        tracker.tp_price = tp_price
        tracker.sl_price = sl_price
        tracker.entry_time = time.time()
        tracker.last_move_high = price
        tracker.last_move_low = price
        
        dir_str = "LONG" if direction == 1 else "SHORT"
        logger.info(f"[SCALP] {sym} ENTER {dir_str} @ {entry_price:.5f} | "
                    f"TP={tp_price:.5f} (${config.target_dollars}) | "
                    f"SL={sl_price:.5f} (${config.stop_dollars})")
        
        return {
            "type": "ENTER",
            "symbol": sym,
            "direction": direction,
            "entry_price": entry_price,
            "tp_price": tp_price,
            "sl_price": sl_price,
            "target_dollars": config.target_dollars,
            "stop_dollars": config.stop_dollars,
            "strategy": "PRECISION_SCALP",
            "use_limit_tp": True,  # Place limit order for TP
        }
    
    def _manage_open_scalp(self, sym: str, price: float, tracker: ScalpTracker,
                           config: ScalpConfig, dpu: float) -> Optional[dict]:
        """Manage an open scalp position. The limit TP handles the exit,
        but we monitor for SL hit and track MFE."""
        
        # Track move extremes
        if tracker.direction == 1:
            if price > tracker.last_move_high:
                tracker.last_move_high = price
        else:
            if price < tracker.last_move_low:
                tracker.last_move_low = price
        
        # SL check (broker handles this too, but we track it)
        if tracker.direction == 1 and price <= tracker.sl_price:
            return self._close_scalp(sym, tracker, config, price, "SL")
        elif tracker.direction == -1 and price >= tracker.sl_price:
            return self._close_scalp(sym, tracker, config, price, "SL")
        
        # TP is handled by the limit order on the broker side
        # But check here too for tracking
        if tracker.direction == 1 and price >= tracker.tp_price:
            return self._close_scalp(sym, tracker, config, price, "TP")
        elif tracker.direction == -1 and price <= tracker.tp_price:
            return self._close_scalp(sym, tracker, config, price, "TP")
        
        return None
    
    def _close_scalp(self, sym: str, tracker: ScalpTracker, config: ScalpConfig,
                     exit_price: float, exit_type: str) -> dict:
        """Record scalp closure and transition to cooldown."""
        if tracker.direction == 1:
            pnl = (exit_price - tracker.entry_price) * (config.target_dollars / 
                   ((tracker.tp_price - tracker.entry_price) if tracker.tp_price != tracker.entry_price else 1))
        else:
            pnl = (tracker.entry_price - exit_price) * (config.target_dollars /
                   ((tracker.entry_price - tracker.tp_price) if tracker.tp_price != tracker.entry_price else 1))
        
        # Simpler P&L calc
        if exit_type == "TP":
            pnl = config.target_dollars
        elif exit_type == "SL":
            pnl = -config.stop_dollars
        
        tracker.scalps_today += 1
        tracker.pnl_today += pnl
        if pnl > 0:
            tracker.wins_today += 1
        else:
            tracker.losses_today += 1
        
        tracker.last_exit_price = exit_price
        tracker.state = ScalpState.COOLDOWN
        tracker.cooldown_until = time.time() + (config.cooldown_bars * 15 * 60)  # M15 bars
        tracker.position_id = ""
        
        dir_str = "LONG" if tracker.direction == 1 else "SHORT"
        emoji = "✅" if pnl > 0 else "❌"
        logger.info(f"[SCALP] {emoji} {sym} {dir_str} closed ({exit_type}) | "
                    f"P&L: ${pnl:+.0f} | Day: {tracker.scalps_today} scalps, ${tracker.pnl_today:+.0f}")
        
        return {
            "type": "CLOSED",
            "symbol": sym,
            "exit_type": exit_type,
            "pnl": pnl,
            "scalps_today": tracker.scalps_today,
            "pnl_today": tracker.pnl_today,
        }
    
    def _watch_pullback(self, sym: str, price: float, direction: int,
                        tracker: ScalpTracker, config: ScalpConfig, dpu: float) -> Optional[dict]:
        """Watch for pullback after a scalp exit, then re-enter."""
        
        # Direction changed — reset and go with new direction
        if direction != 0 and direction != tracker.direction:
            tracker.direction = direction
            tracker.state = ScalpState.IDLE
            return self._find_entry(sym, price, direction, 1.0, tracker, config, dpu)
        
        target_in_price = config.target_dollars / dpu if dpu > 0 else 0
        if target_in_price <= 0:
            return None
        
        if tracker.direction == 1:
            # LONG: wait for price to pull back from the high
            pullback_depth = tracker.last_move_high - price
            required_pullback = target_in_price * config.pullback_pct
            
            # Track the pullback floor
            if price < tracker.pullback_floor:
                tracker.pullback_floor = price
            
            # Pullback happened and price is recovering (floor bounce)
            if pullback_depth >= required_pullback and price > tracker.pullback_floor:
                # Price bounced off the floor — re-enter
                logger.info(f"[SCALP] {sym} pullback detected: floor={tracker.pullback_floor:.5f}, "
                           f"recovering at {price:.5f}")
                tracker.state = ScalpState.IDLE
                return self._find_entry(sym, price, direction, 1.0, tracker, config, dpu)
        
        else:  # SHORT
            pullback_depth = price - tracker.last_move_low
            required_pullback = target_in_price * config.pullback_pct
            
            if price > tracker.pullback_floor:
                tracker.pullback_floor = price
            
            if pullback_depth >= required_pullback and price < tracker.pullback_floor:
                logger.info(f"[SCALP] {sym} pullback detected: ceiling={tracker.pullback_floor:.5f}, "
                           f"dropping at {price:.5f}")
                tracker.state = ScalpState.IDLE
                return self._find_entry(sym, price, direction, 1.0, tracker, config, dpu)
        
        return None
    
    def record_fill(self, sym: str, position_id: str):
        """Record that a scalp order was filled by the broker."""
        tracker = self._get_tracker(sym)
        tracker.position_id = position_id
    
    def record_tp_fill(self, sym: str):
        """Record that a limit TP was filled by the broker."""
        tracker = self._get_tracker(sym)
        if tracker.state == ScalpState.IN_TRADE:
            config = self._get_config(sym)
            self._close_scalp(sym, tracker, config, tracker.tp_price, "TP")
    
    def get_status(self) -> str:
        """Summary of all scalp activity."""
        active = sum(1 for t in self.trackers.values() if t.state == ScalpState.IN_TRADE)
        total_scalps = sum(t.scalps_today for t in self.trackers.values())
        total_pnl = sum(t.pnl_today for t in self.trackers.values())
        total_wins = sum(t.wins_today for t in self.trackers.values())
        total_losses = sum(t.losses_today for t in self.trackers.values())
        wr = total_wins / (total_wins + total_losses) * 100 if (total_wins + total_losses) > 0 else 0
        
        return (f"[SCALP] Active: {active} | Today: {total_scalps} scalps | "
                f"W/L: {total_wins}/{total_losses} ({wr:.0f}%) | P&L: ${total_pnl:+.0f}")
    
    def get_risk_dollars(self) -> float:
        """Total dollar risk across all open scalp positions."""
        total = 0
        for sym, tracker in self.trackers.items():
            if tracker.state == ScalpState.IN_TRADE:
                config = self._get_config(sym)
                total += config.stop_dollars
        return total
    
    def get_open_symbols(self) -> set:
        """Set of symbols with open scalp positions."""
        return {sym for sym, t in self.trackers.items() if t.state == ScalpState.IN_TRADE}
