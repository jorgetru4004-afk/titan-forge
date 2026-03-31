"""
FORGE v22 — Runner Detection & Trailing Stop Management
=========================================================
The intelligence is in TRADE MANAGEMENT, not trade filtering.

SCALP: Hard TP, no trailing, exit at TP or SL.
RUNNER: Partial at 1R, trail rest with runner detection logic.

Runner Detection Logic:
  KEEP holding if ALL true:
    1. ADX > 25 and ADX > ADX[5 bars ago]
    2. Price on correct side of VWAP for trade direction
    3. ATR consumed < 85% for the day
    4. No reversal candle pattern against us
  
  CUT immediately if ANY true:
    1. ADX < 20 (trend dead)
    2. Price crosses VWAP against us
    3. Reversal candle with volume spike
    4. Hit trailing stop
    5. Max hold exceeded (50 bars)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from enum import Enum

from forge_instruments_v22 import TradeType

logger = logging.getLogger("FORGE.runner")


# ─── Trade State ─────────────────────────────────────────────────────────────

class TradeState(str, Enum):
    PENDING = "PENDING"           # Limit order placed, not yet filled
    ACTIVE = "ACTIVE"             # Filled, full position
    PARTIAL = "PARTIAL"           # Partial taken at 1R, runner active
    BREAKEVEN = "BREAKEVEN"       # Stop moved to breakeven
    TRAILING = "TRAILING"         # Trailing stop active
    CLOSED = "CLOSED"             # Fully closed


class ExitReason(str, Enum):
    TP_HIT = "TP_HIT"
    SL_HIT = "SL_HIT"
    TRAILING_STOP = "TRAILING_STOP"
    BREAKEVEN_STOP = "BREAKEVEN_STOP"
    ADX_DEAD = "ADX_DEAD"
    VWAP_CROSS = "VWAP_CROSS"
    REVERSAL_CANDLE = "REVERSAL_CANDLE"
    MAX_HOLD = "MAX_HOLD"
    MANUAL = "MANUAL"


@dataclass
class ManagedTrade:
    """A trade being actively managed by the runner system."""
    trade_id: str
    symbol: str
    direction: str              # "LONG" or "SHORT"
    trade_type: TradeType       # SCALP or RUNNER
    entry_price: float
    sl_price: float
    tp_price: float
    current_sl: float           # Active stop loss (may move to BE or trail)
    risk_amount: float          # Dollar risk (for R calculations)
    position_size: float        # Lots
    remaining_size: float       # Lots remaining after partials
    state: TradeState = TradeState.ACTIVE
    bars_held: int = 0
    max_favorable: float = 0.0  # Max favorable excursion in R
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    partial_taken: bool = False
    breakeven_set: bool = False
    exit_reason: Optional[ExitReason] = None

    @property
    def r_per_unit(self) -> float:
        """Dollar risk per 1R move."""
        return abs(self.entry_price - self.sl_price) if self.risk_amount == 0 else self.risk_amount

    def current_r(self, current_price: float) -> float:
        """Current P&L in R multiples."""
        if self.r_per_unit == 0:
            return 0.0
        if self.direction == "LONG":
            return (current_price - self.entry_price) / abs(self.entry_price - self.sl_price)
        else:
            return (self.entry_price - current_price) / abs(self.entry_price - self.sl_price)


# ─── Runner Detection ────────────────────────────────────────────────────────

@dataclass
class RunnerContext:
    """Market context for runner decision-making."""
    current_price: float
    adx: float
    adx_5bars_ago: float
    vwap: float
    atr_consumed_pct: float     # How much of daily ATR has been consumed (0-1)
    has_reversal_candle: bool   # Engulfing or pin bar against our direction
    has_volume_spike: bool      # Volume spike on reversal candle
    bars_held: int


class RunnerDetector:
    """
    Decides whether to keep holding or cut a runner trade.
    
    The brain of trade management. Enter aggressively (all gas),
    manage intelligently (then brakes when needed).
    """

    # Config
    BREAKEVEN_R = 0.5           # Move SL to breakeven at +0.5R
    PARTIAL_R = 1.0             # Take 50% partial at +1R
    PARTIAL_PCT = 0.50          # Percentage to close at partial
    TRAILING_R = 1.5            # Trail stop 1.5R behind price
    MAX_HOLD_BARS = 50          # Force exit after 50 bars
    ADX_ALIVE = 25              # ADX must be above this for "trend alive"
    ADX_DEAD = 20               # ADX below this = trend dead, cut
    ATR_BUDGET_MAX = 0.85       # Max daily ATR consumption

    def should_keep_holding(self, trade: ManagedTrade, ctx: RunnerContext) -> Tuple[bool, Optional[ExitReason]]:
        """
        Core runner detection logic.
        
        Returns:
            (keep_holding, exit_reason)
            - (True, None) = keep holding
            - (False, ExitReason) = cut with reason
        """
        # ─── Immediate cuts (ANY true = exit) ─────────────────────────

        # 1. Max hold exceeded
        if ctx.bars_held >= self.MAX_HOLD_BARS:
            logger.info(f"RUNNER CUT {trade.symbol}: max hold {self.MAX_HOLD_BARS} bars")
            return False, ExitReason.MAX_HOLD

        # 2. ADX dead (trend is over)
        if ctx.adx < self.ADX_DEAD:
            logger.info(f"RUNNER CUT {trade.symbol}: ADX {ctx.adx:.1f} < {self.ADX_DEAD}")
            return False, ExitReason.ADX_DEAD

        # 3. Price crossed VWAP against us
        if trade.direction == "LONG" and ctx.current_price < ctx.vwap:
            logger.info(f"RUNNER CUT {trade.symbol}: LONG but price {ctx.current_price} < VWAP {ctx.vwap}")
            return False, ExitReason.VWAP_CROSS
        elif trade.direction == "SHORT" and ctx.current_price > ctx.vwap:
            logger.info(f"RUNNER CUT {trade.symbol}: SHORT but price {ctx.current_price} > VWAP {ctx.vwap}")
            return False, ExitReason.VWAP_CROSS

        # 4. Reversal candle with volume spike
        if ctx.has_reversal_candle and ctx.has_volume_spike:
            logger.info(f"RUNNER CUT {trade.symbol}: reversal candle + volume spike")
            return False, ExitReason.REVERSAL_CANDLE

        # ─── Keep holding checks (ALL must be true) ──────────────────

        keep_checks = [
            ctx.adx > self.ADX_ALIVE,
            ctx.adx > ctx.adx_5bars_ago,               # ADX still rising
            ctx.atr_consumed_pct < self.ATR_BUDGET_MAX, # ATR budget remaining
            not ctx.has_reversal_candle,                 # No reversal pattern
        ]

        if all(keep_checks):
            logger.debug(f"RUNNER HOLD {trade.symbol}: all checks pass, ADX={ctx.adx:.1f}")
            return True, None

        # Some checks failed but no immediate cut trigger
        # If ADX is rising but consumed ATR > 85%, still cut
        if ctx.atr_consumed_pct >= self.ATR_BUDGET_MAX:
            logger.info(f"RUNNER CUT {trade.symbol}: ATR consumed {ctx.atr_consumed_pct:.0%}")
            return False, ExitReason.MAX_HOLD  # Close enough to max hold reason

        # Reversal candle without volume spike — warning but hold
        if ctx.has_reversal_candle and not ctx.has_volume_spike:
            logger.warning(f"RUNNER WARNING {trade.symbol}: reversal candle but no volume spike — holding")
            return True, None

        # Default: trend weakening but not dead — hold for now
        return True, None


# ─── Trade Manager ───────────────────────────────────────────────────────────

class TradeManager:
    """
    Manages the lifecycle of all active trades.
    
    SCALP: Simple — TP or SL, no management needed beyond placing orders.
    RUNNER: Full lifecycle with breakeven, partial, trailing, and runner detection.
    """

    def __init__(self):
        self.active_trades: Dict[str, ManagedTrade] = {}
        self.runner_detector = RunnerDetector()
        self._closed_trades: List[ManagedTrade] = []

    def add_trade(self, trade: ManagedTrade):
        """Register a new trade for management."""
        self.active_trades[trade.trade_id] = trade
        logger.info(
            f"MANAGING: {trade.trade_id} {trade.symbol} {trade.direction} "
            f"{trade.trade_type.value} | entry={trade.entry_price:.5f} "
            f"SL={trade.sl_price:.5f} TP={trade.tp_price:.5f}"
        )

    def update_trade(
        self,
        trade_id: str,
        current_price: float,
        ctx: Optional[RunnerContext] = None,
    ) -> List[Dict]:
        """
        Update a managed trade with current price. Returns list of actions to take.
        
        Actions:
            {"action": "MOVE_SL", "new_sl": float}
            {"action": "PARTIAL_CLOSE", "pct": float, "reason": str}
            {"action": "CLOSE_ALL", "reason": str}
            {"action": "HOLD"}
        """
        trade = self.active_trades.get(trade_id)
        if trade is None or trade.state == TradeState.CLOSED:
            return [{"action": "HOLD"}]

        trade.bars_held += 1
        actions = []

        # Calculate current R
        current_r = trade.current_r(current_price)

        # Track max favorable excursion
        if current_r > trade.max_favorable:
            trade.max_favorable = current_r

        # ─── SCALP Management ─────────────────────────────────────────
        if trade.trade_type == TradeType.SCALP:
            return self._manage_scalp(trade, current_price, current_r)

        # ─── RUNNER Management ────────────────────────────────────────
        return self._manage_runner(trade, current_price, current_r, ctx)

    def _manage_scalp(self, trade: ManagedTrade, price: float, current_r: float) -> List[Dict]:
        """
        SCALP: Move to breakeven at +0.5R, then just wait for TP or SL.
        No partials, no trailing — exit at TP or SL.
        """
        actions = []

        # Move to breakeven at +0.5R (protect capital fast)
        if not trade.breakeven_set and current_r >= RunnerDetector.BREAKEVEN_R:
            trade.current_sl = trade.entry_price
            trade.breakeven_set = True
            trade.state = TradeState.BREAKEVEN
            actions.append({
                "action": "MOVE_SL",
                "new_sl": trade.entry_price,
                "reason": f"Breakeven at +{current_r:.2f}R",
            })
            logger.info(f"SCALP BE: {trade.symbol} moved SL to breakeven at +{current_r:.2f}R")

        # Check if SL hit
        if trade.direction == "LONG" and price <= trade.current_sl:
            return self._close_trade(trade, ExitReason.SL_HIT if not trade.breakeven_set else ExitReason.BREAKEVEN_STOP)
        elif trade.direction == "SHORT" and price >= trade.current_sl:
            return self._close_trade(trade, ExitReason.SL_HIT if not trade.breakeven_set else ExitReason.BREAKEVEN_STOP)

        # Check if TP hit
        if trade.direction == "LONG" and price >= trade.tp_price:
            return self._close_trade(trade, ExitReason.TP_HIT)
        elif trade.direction == "SHORT" and price <= trade.tp_price:
            return self._close_trade(trade, ExitReason.TP_HIT)

        return actions if actions else [{"action": "HOLD"}]

    def _manage_runner(
        self, trade: ManagedTrade, price: float, current_r: float,
        ctx: Optional[RunnerContext] = None,
    ) -> List[Dict]:
        """
        RUNNER: Full lifecycle management.
        
        1. Move to breakeven at +0.5R
        2. Take 50% partial at +1R
        3. Trail remaining with runner detection
        4. Cut if trend dies
        """
        actions = []

        # Step 1: Move to breakeven at +0.5R
        if not trade.breakeven_set and current_r >= RunnerDetector.BREAKEVEN_R:
            trade.current_sl = trade.entry_price
            trade.breakeven_set = True
            trade.state = TradeState.BREAKEVEN
            actions.append({
                "action": "MOVE_SL",
                "new_sl": trade.entry_price,
                "reason": f"Breakeven at +{current_r:.2f}R",
            })
            logger.info(f"RUNNER BE: {trade.symbol} moved SL to breakeven at +{current_r:.2f}R")

        # Step 2: Take 50% partial at +1R
        if not trade.partial_taken and current_r >= RunnerDetector.PARTIAL_R:
            trade.partial_taken = True
            trade.remaining_size = trade.position_size * (1 - RunnerDetector.PARTIAL_PCT)
            trade.state = TradeState.PARTIAL
            actions.append({
                "action": "PARTIAL_CLOSE",
                "pct": RunnerDetector.PARTIAL_PCT,
                "reason": f"Partial at +{current_r:.2f}R",
            })
            logger.info(f"RUNNER PARTIAL: {trade.symbol} closing {RunnerDetector.PARTIAL_PCT:.0%} at +{current_r:.2f}R")

        # Step 3: Trailing stop (after partial taken)
        if trade.partial_taken:
            r_unit = abs(trade.entry_price - trade.sl_price)
            if r_unit > 0:
                if trade.direction == "LONG":
                    new_trail = price - RunnerDetector.TRAILING_R * r_unit
                    if new_trail > trade.current_sl:
                        trade.current_sl = new_trail
                        trade.state = TradeState.TRAILING
                        actions.append({
                            "action": "MOVE_SL",
                            "new_sl": new_trail,
                            "reason": f"Trail to {new_trail:.5f} (+{current_r:.2f}R)",
                        })
                else:
                    new_trail = price + RunnerDetector.TRAILING_R * r_unit
                    if new_trail < trade.current_sl:
                        trade.current_sl = new_trail
                        trade.state = TradeState.TRAILING
                        actions.append({
                            "action": "MOVE_SL",
                            "new_sl": new_trail,
                            "reason": f"Trail to {new_trail:.5f} (+{current_r:.2f}R)",
                        })

        # Step 4: Runner detection (only after partial is taken)
        if trade.partial_taken and ctx is not None:
            keep, exit_reason = self.runner_detector.should_keep_holding(trade, ctx)
            if not keep and exit_reason is not None:
                return self._close_trade(trade, exit_reason)

        # Check trailing stop hit
        if trade.direction == "LONG" and price <= trade.current_sl:
            reason = ExitReason.TRAILING_STOP if trade.state == TradeState.TRAILING else ExitReason.BREAKEVEN_STOP
            return self._close_trade(trade, reason)
        elif trade.direction == "SHORT" and price >= trade.current_sl:
            reason = ExitReason.TRAILING_STOP if trade.state == TradeState.TRAILING else ExitReason.BREAKEVEN_STOP
            return self._close_trade(trade, reason)

        return actions if actions else [{"action": "HOLD"}]

    def _close_trade(self, trade: ManagedTrade, reason: ExitReason) -> List[Dict]:
        """Close a trade and record the reason."""
        trade.state = TradeState.CLOSED
        trade.exit_reason = reason
        self._closed_trades.append(trade)
        del self.active_trades[trade.trade_id]
        logger.info(f"CLOSED: {trade.trade_id} {trade.symbol} reason={reason.value} "
                    f"bars={trade.bars_held} max_R={trade.max_favorable:.2f}")
        return [{"action": "CLOSE_ALL", "reason": reason.value}]

    def get_active_count(self) -> int:
        return len(self.active_trades)

    def get_active_symbols(self) -> List[str]:
        return [t.symbol for t in self.active_trades.values()]

    def get_trade_summary(self) -> Dict:
        """Summary stats for Telegram reporting."""
        active = len(self.active_trades)
        closed = len(self._closed_trades)
        if closed == 0:
            return {"active": active, "closed": 0, "win_rate": 0, "avg_r": 0}

        winners = sum(1 for t in self._closed_trades if t.max_favorable > 0)
        avg_r = sum(t.max_favorable for t in self._closed_trades) / closed

        return {
            "active": active,
            "closed": closed,
            "win_rate": winners / closed if closed > 0 else 0,
            "avg_r": avg_r,
            "exit_reasons": {r.value: sum(1 for t in self._closed_trades if t.exit_reason == r)
                           for r in ExitReason},
        }
