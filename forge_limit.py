"""
FORGE v22 — Limit Order Management
=====================================
Limit orders for mean reversion strategies with intelligent fallback to market.

Rules:
- Place limit at signal price ± 0.2 ATR
- Valid for 5 bars
- If not filled in 5 bars AND setup still valid -> switch to MARKET
- "Setup still valid" = indicator still in zone, price hasn't moved >1 ATR away
- If price moved >1 ATR past limit -> CANCEL (R:R destroyed)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from enum import Enum

from forge_instruments_v22 import OrderType, Strategy
from forge_signals_v22 import Signal

logger = logging.getLogger("FORGE.limit")


# ─── Limit Order State ──────────────────────────────────────────────────────

class LimitState(str, Enum):
    PENDING = "PENDING"         # Limit placed, waiting for fill
    FILLED = "FILLED"           # Filled, trade active
    EXPIRED = "EXPIRED"         # 5 bars elapsed, deciding fallback
    MARKET_FALLBACK = "MARKET_FALLBACK"  # Converting to market order
    CANCELLED = "CANCELLED"     # Cancelled (R:R destroyed or setup invalid)


@dataclass
class PendingLimit:
    """A limit order being tracked for fill or fallback."""
    order_id: str
    signal: Signal
    limit_price: float
    bars_elapsed: int = 0
    max_bars: int = 5
    state: LimitState = LimitState.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Validation context (snapshot at signal time)
    signal_rsi: Optional[float] = None
    signal_stoch_k: Optional[float] = None
    signal_price: float = 0.0

    @property
    def is_active(self) -> bool:
        return self.state in (LimitState.PENDING, LimitState.EXPIRED)


# ─── Setup Validity Checks ──────────────────────────────────────────────────

def _is_setup_still_valid(
    pending: PendingLimit,
    current_price: float,
    current_rsi: float,
    current_stoch_k: float,
    atr: float,
) -> Tuple[bool, str]:
    """
    Check if the original setup conditions are still valid for market fallback.
    
    Returns: (is_valid, reason)
    """
    strategy = pending.signal.strategy

    # Universal check: has price moved >1 ATR past the limit?
    price_dist = abs(current_price - pending.limit_price)
    if price_dist > atr:
        return False, f"Price moved {price_dist/atr:.1f} ATR past limit (max 1.0)"

    # Strategy-specific validation
    if strategy == Strategy.MEAN_REVERT:
        # RSI must still be in the extreme zone
        if pending.signal.direction == "LONG" and current_rsi > 40:
            return False, f"RSI recovered to {current_rsi:.1f} (need <40 for LONG)"
        elif pending.signal.direction == "SHORT" and current_rsi < 60:
            return False, f"RSI recovered to {current_rsi:.1f} (need >60 for SHORT)"

    elif strategy == Strategy.STOCH_REVERSAL:
        # Stochastic must still be near extreme zone
        if pending.signal.direction == "LONG" and current_stoch_k > 35:
            return False, f"Stoch K recovered to {current_stoch_k:.1f} (need <35)"
        elif pending.signal.direction == "SHORT" and current_stoch_k < 65:
            return False, f"Stoch K recovered to {current_stoch_k:.1f} (need >65)"

    elif strategy == Strategy.VWAP_REVERT:
        # Just check price distance — VWAP mean reversion zone
        pass  # Price distance check above is sufficient

    elif strategy == Strategy.EMA_BOUNCE:
        # Price should still be near EMA50
        pass  # Price distance check above is sufficient

    elif strategy == Strategy.GAP_FILL:
        # Gap should still exist
        pass  # Price distance check is sufficient

    return True, "Setup still valid"


# ─── Limit Order Manager ────────────────────────────────────────────────────

class LimitOrderManager:
    """
    Manages pending limit orders with intelligent fallback.
    
    Workflow:
    1. Signal engine generates a LIMIT signal
    2. Place limit order via MetaAPI
    3. Track bars elapsed
    4. On each bar: check if filled, expired, or should cancel
    5. If expired + setup valid -> market fallback
    6. If expired + setup invalid or price moved -> cancel
    """

    def __init__(self):
        self.pending_orders: Dict[str, PendingLimit] = {}
        self._stats = {"placed": 0, "filled": 0, "market_fallback": 0, "cancelled": 0}

    def add_limit(self, order_id: str, signal: Signal,
                  rsi: float = 0, stoch_k: float = 0) -> PendingLimit:
        """Register a new pending limit order."""
        pending = PendingLimit(
            order_id=order_id,
            signal=signal,
            limit_price=signal.entry_price,
            max_bars=signal.limit_valid_bars,
            signal_rsi=rsi,
            signal_stoch_k=stoch_k,
            signal_price=signal.entry_price,
        )
        self.pending_orders[order_id] = pending
        self._stats["placed"] += 1
        logger.info(
            f"LIMIT PLACED: {order_id} {signal.symbol} {signal.direction} "
            f"at {signal.entry_price:.5f} | valid for {signal.limit_valid_bars} bars"
        )
        return pending

    def on_fill(self, order_id: str) -> Optional[PendingLimit]:
        """Called when MetaAPI confirms a limit order fill."""
        pending = self.pending_orders.get(order_id)
        if pending is None:
            return None

        pending.state = LimitState.FILLED
        self._stats["filled"] += 1
        logger.info(f"LIMIT FILLED: {order_id} {pending.signal.symbol} after {pending.bars_elapsed} bars")
        del self.pending_orders[order_id]
        return pending

    def update_tick(
        self,
        order_id: str,
        current_price: float,
        current_rsi: float = 50.0,
        current_stoch_k: float = 50.0,
        atr: float = 0.0,
    ) -> Dict:
        """
        Update a pending limit order with new bar data.
        
        Returns action dict:
            {"action": "HOLD"}           - Keep waiting
            {"action": "MARKET_ENTRY"}   - Switch to market order (setup still valid)
            {"action": "CANCEL"}         - Cancel order (setup invalid / R:R destroyed)
        """
        pending = self.pending_orders.get(order_id)
        if pending is None or not pending.is_active:
            return {"action": "HOLD"}

        pending.bars_elapsed += 1

        # Check if price moved >1 ATR past limit (R:R destroyed)
        if atr > 0:
            if pending.signal.direction == "LONG":
                # For LONG, if price dropped way below our limit, cancel
                if current_price < pending.limit_price - atr:
                    return self._cancel_order(order_id, "Price dropped >1 ATR below limit")
                # For LONG, if price ripped above us, cancel (would be chasing)
                if current_price > pending.limit_price + atr:
                    return self._cancel_order(order_id, "Price moved >1 ATR above limit (chasing)")
            else:
                if current_price > pending.limit_price + atr:
                    return self._cancel_order(order_id, "Price rose >1 ATR above limit")
                if current_price < pending.limit_price - atr:
                    return self._cancel_order(order_id, "Price moved >1 ATR below limit (chasing)")

        # Check if max bars elapsed
        if pending.bars_elapsed >= pending.max_bars:
            pending.state = LimitState.EXPIRED
            logger.info(f"LIMIT EXPIRED: {order_id} {pending.signal.symbol} after {pending.bars_elapsed} bars")

            # Check if setup is still valid for market fallback
            is_valid, reason = _is_setup_still_valid(
                pending, current_price, current_rsi, current_stoch_k, atr
            )

            if is_valid:
                return self._market_fallback(order_id, current_price)
            else:
                return self._cancel_order(order_id, reason)

        return {"action": "HOLD"}

    def _market_fallback(self, order_id: str, current_price: float) -> Dict:
        """Convert expired limit to market order."""
        pending = self.pending_orders.get(order_id)
        if pending is None:
            return {"action": "HOLD"}

        pending.state = LimitState.MARKET_FALLBACK
        self._stats["market_fallback"] += 1
        
        # Recalculate entry at current price (SL/TP remain same distance)
        signal = pending.signal
        logger.info(
            f"MARKET FALLBACK: {order_id} {signal.symbol} | "
            f"limit was {pending.limit_price:.5f}, market at {current_price:.5f}"
        )

        del self.pending_orders[order_id]
        return {
            "action": "MARKET_ENTRY",
            "signal": signal,
            "entry_price": current_price,
            "reason": f"Limit expired after {pending.bars_elapsed} bars, setup still valid",
        }

    def _cancel_order(self, order_id: str, reason: str) -> Dict:
        """Cancel a pending limit order."""
        pending = self.pending_orders.get(order_id)
        if pending is None:
            return {"action": "HOLD"}

        pending.state = LimitState.CANCELLED
        self._stats["cancelled"] += 1
        logger.info(f"LIMIT CANCELLED: {order_id} {pending.signal.symbol} | {reason}")
        
        del self.pending_orders[order_id]
        return {"action": "CANCEL", "reason": reason}

    def get_pending_count(self) -> int:
        return len(self.pending_orders)

    def get_pending_symbols(self) -> List[str]:
        return [p.signal.symbol for p in self.pending_orders.values()]

    def get_stats(self) -> Dict:
        return dict(self._stats)

    def cancel_all(self) -> int:
        """Cancel all pending limits (e.g., on session close)."""
        count = len(self.pending_orders)
        for order_id in list(self.pending_orders.keys()):
            self._cancel_order(order_id, "Bulk cancel")
        return count


# ─── Slippage Check (for MARKET orders) ─────────────────────────────────────

def check_slippage(
    expected_price: float,
    actual_price: float,
    atr: float,
    max_slippage_atr: float = 0.3,
) -> Tuple[bool, float]:
    """
    Check if market order slippage is acceptable.
    
    Returns: (acceptable, slippage_in_atr)
    """
    if atr <= 0:
        return True, 0.0

    slippage = abs(actual_price - expected_price)
    slippage_atr = slippage / atr

    if slippage_atr > max_slippage_atr:
        logger.warning(
            f"SLIPPAGE REJECT: expected={expected_price:.5f} actual={actual_price:.5f} "
            f"slippage={slippage_atr:.2f} ATR (max {max_slippage_atr})"
        )
        return False, slippage_atr

    return True, slippage_atr
