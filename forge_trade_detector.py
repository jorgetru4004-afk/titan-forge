"""
TITAN FORGE V22.5 — CLOSED TRADE DETECTOR
===========================================
Fixes the V22.4 bug where the _bal key in prev_positions dict
interfered with position ID comparison, causing closures to go undetected.

The bleeder killer, daily gate, and trade logging all depend on this.

USAGE IN main.py:
    from forge_trade_detector import TradeDetector
    
    detector = TradeDetector()
    
    # In main loop:
    current_positions = get_positions()  # From MetaAPI
    closed, opened = detector.update(current_positions, account_balance, account_equity)
    
    for trade in closed:
        logger.info(f"[CLOSED] {trade}")
        daily_gate.register_close(trade["symbol"], trade["pnl"])
    
    for trade in opened:
        logger.info(f"[OPENED] {trade}")
        daily_gate.register_open(trade["symbol"], trade["risk_dollars"])
"""

import time
import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

logger = logging.getLogger("FORGE.detector")


@dataclass
class ClosedTrade:
    """Record of a detected trade closure."""
    position_id: str
    symbol: str
    direction: str        # "LONG" or "SHORT"
    pnl: float            # Estimated P&L in dollars
    entry_price: float
    close_price: float    # Estimated from balance change
    duration_seconds: float
    detected_at: float    # Timestamp
    
    def __str__(self):
        emoji = "✅" if self.pnl > 0 else "❌" if self.pnl < 0 else "⚪"
        return (f"{emoji} {self.symbol} {self.direction} | P&L: ${self.pnl:+.0f} | "
                f"ID: {self.position_id}")


@dataclass 
class OpenedTrade:
    """Record of a newly detected position."""
    position_id: str
    symbol: str
    direction: str
    entry_price: float
    lots: float
    sl_price: float
    detected_at: float
    risk_dollars: float  # Estimated dollar risk to SL
    
    def __str__(self):
        return (f"🔫 {self.symbol} {self.direction} | Entry: {self.entry_price} | "
                f"Lots: {self.lots} | Risk: ${self.risk_dollars:.0f} | ID: {self.position_id}")


class TradeDetector:
    """
    Detects trade openings and closures by comparing position snapshots.
    
    V22.4 BUG: prev_positions dict contained a '_bal' key for balance tracking.
    When iterating position IDs, '_bal' was compared against real position IDs,
    causing the detector to think positions appeared/disappeared every cycle.
    
    FIX: Store balance separately, never mix metadata with position data.
    """
    
    def __init__(self):
        self._prev_positions: Dict[str, dict] = {}  # id → position info
        self._prev_balance: float = 0.0              # Stored SEPARATELY (the fix)
        self._prev_equity: float = 0.0
        self._position_open_times: Dict[str, float] = {}  # id → open timestamp
        self._initialized: bool = False
    
    def update(self, current_positions: list, balance: float, equity: float
               ) -> Tuple[List[ClosedTrade], List[OpenedTrade]]:
        """
        Compare current positions against previous snapshot.
        
        Args:
            current_positions: List of position dicts from MetaAPI, each with:
                - id: position ID string
                - symbol: e.g., "EURUSD.sim"
                - type: "POSITION_TYPE_BUY" or "POSITION_TYPE_SELL"
                - openPrice: entry price
                - volume: lot size
                - stopLoss: SL price (optional)
                - profit: unrealized P&L (optional)
            balance: Current account balance
            equity: Current account equity
        
        Returns:
            (closed_trades: list, opened_trades: list)
        """
        # Build current position map (CLEAN — no metadata keys)
        current_map = {}
        for pos in current_positions:
            pid = str(pos.get("id", ""))
            if not pid:
                continue
            current_map[pid] = {
                "id": pid,
                "symbol": pos.get("symbol", "").replace(".sim", ""),
                "direction": "LONG" if "BUY" in str(pos.get("type", "")) else "SHORT",
                "entry_price": float(pos.get("openPrice", 0)),
                "lots": float(pos.get("volume", 0)),
                "sl_price": float(pos.get("stopLoss", 0)),
                "profit": float(pos.get("profit", 0)),
            }
        
        closed_trades = []
        opened_trades = []
        
        if not self._initialized:
            # First call — just record state, don't detect
            self._prev_positions = current_map
            self._prev_balance = balance
            self._prev_equity = equity
            self._initialized = True
            for pid in current_map:
                self._position_open_times[pid] = time.time()
            logger.info(f"[DETECTOR] Initialized with {len(current_map)} positions, "
                       f"bal=${balance:.0f}")
            return [], []
        
        # Detect CLOSURES (positions that were in prev but not in current)
        prev_ids = set(self._prev_positions.keys())
        curr_ids = set(current_map.keys())
        
        closed_ids = prev_ids - curr_ids
        opened_ids = curr_ids - prev_ids
        
        # Balance change tells us the realized P&L of closed trades
        balance_change = balance - self._prev_balance
        
        for pid in closed_ids:
            prev_pos = self._prev_positions[pid]
            
            # Estimate P&L from balance change if only one trade closed
            if len(closed_ids) == 1:
                pnl = balance_change
            else:
                # Multiple closures — split balance change evenly (rough estimate)
                # Better: use the last known unrealized P&L
                pnl = prev_pos.get("profit", balance_change / len(closed_ids))
            
            open_time = self._position_open_times.pop(pid, time.time())
            duration = time.time() - open_time
            
            closed = ClosedTrade(
                position_id=pid,
                symbol=prev_pos["symbol"],
                direction=prev_pos["direction"],
                pnl=round(pnl, 2),
                entry_price=prev_pos["entry_price"],
                close_price=0.0,  # Can't determine exact close price
                duration_seconds=duration,
                detected_at=time.time(),
            )
            closed_trades.append(closed)
            logger.info(f"[DETECTOR] {closed}")
        
        # Detect OPENINGS (positions in current but not in prev)
        for pid in opened_ids:
            pos = current_map[pid]
            self._position_open_times[pid] = time.time()
            
            # Estimate risk in dollars
            risk_dollars = 0
            if pos["sl_price"] > 0 and pos["entry_price"] > 0:
                sl_distance = abs(pos["entry_price"] - pos["sl_price"])
                # Rough dollar value — will be refined per instrument
                pip_value = pos["lots"] * 100000  # Approximate for forex
                risk_dollars = sl_distance * pip_value
            
            opened = OpenedTrade(
                position_id=pid,
                symbol=pos["symbol"],
                direction=pos["direction"],
                entry_price=pos["entry_price"],
                lots=pos["lots"],
                sl_price=pos["sl_price"],
                detected_at=time.time(),
                risk_dollars=round(risk_dollars, 0),
            )
            opened_trades.append(opened)
            logger.info(f"[DETECTOR] {opened}")
        
        # Update state
        self._prev_positions = current_map
        self._prev_balance = balance
        self._prev_equity = equity
        
        return closed_trades, opened_trades
    
    def get_open_symbols(self) -> set:
        """Return set of currently open symbols (without .sim suffix)."""
        return {p["symbol"] for p in self._prev_positions.values()}
    
    def get_open_count(self) -> int:
        """Return number of currently open positions."""
        return len(self._prev_positions)
    
    def get_position_risk(self, symbol: str) -> float:
        """Get estimated risk for a specific open position."""
        for pos in self._prev_positions.values():
            if pos["symbol"] == symbol:
                if pos["sl_price"] > 0 and pos["entry_price"] > 0:
                    return abs(pos["entry_price"] - pos["sl_price"]) * pos["lots"] * 100000
        return 0
    
    def get_total_risk(self) -> float:
        """Get total estimated risk across all open positions."""
        total = 0
        for pos in self._prev_positions.values():
            if pos["sl_price"] > 0 and pos["entry_price"] > 0:
                total += abs(pos["entry_price"] - pos["sl_price"]) * pos["lots"] * 100000
        return total
