"""
FORGE v21 — SMART ORDER ROUTER
=================================
Limit orders on mean reversion / level setups (zero slippage).
Market orders on breakout / momentum setups (speed matters).
Auto-fallback: if limit doesn't fill in 10-15s and conviction
still HIGH+, cancel limit and send market order.

Measured friction feedback: after 100 trades, real spread/slippage
replaces estimates. EV threshold adjusts to budget for real friction.

NEXUS Capital — Jorge Trujillo | Claude — AI Architect | March 2026
"""
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("FORGE.router")


# ─────────────────────────────────────────────────────────────────
# SETUP CLASSIFICATION — which setups get limits vs market
# ─────────────────────────────────────────────────────────────────

# LIMIT: price is approaching a known level. Place order ahead, let price come.
# Zero slippage. Negative spread cost. Pure upside vs backtest.
LIMIT_SETUPS = frozenset({
    "VWAP-01",          # VWAP bounce long — price dropping to VWAP
    "VWAP-02",          # VWAP reject short — price rising to VWAP
    "LVL-01",           # PDH/PDL test — price approaching prior day level
    "LVL-02",           # Round number scalp — price approaching round #
    "IB-02",            # IB range scalp — price at IB boundary
    "VOL-05",           # Mean reversion — price extended from VWAP
    "VOL-06",           # Noon curve reversal — price at extreme
    "ASIA-REVERT-01",   # Asian mean reversion
    "EXT-REVERT-01",    # Extended hours reversion
    "CL-GAP-01",        # Oil gap fade — price reverts to fill gap
})

# MARKET: breakout/momentum — speed matters more than fill quality.
# Price breaks a level and may never look back. Must fill NOW.
MARKET_SETUPS = frozenset({
    "ORD-02",           # Opening range breakout
    "IB-01",            # IB breakout
    "OD-01",            # Opening drive momentum
    "GAP-02",           # Gap and go
    "VOL-03",           # Trend day momentum
    "ICT-01",           # VWAP reclaim
    "ICT-02",           # Fair value gap
    "ICT-03",           # Liquidity sweep
    "VWAP-03",          # VWAP reclaim momentum
    "NEWS-MOM-01",      # Economic data momentum
    "CL-TREND-01",      # Oil trend follow
    "CL-MOM-01",        # Oil momentum
    "LONDON-GOLD-01",   # London gold breakout
    "LONDON-NQ-01",     # London NQ overnight range break
    "PRE-RANGE-01",     # Pre-market range break
    "LONDON-FX-01",     # London FX momentum
    "ES-LEAD-01",       # ES→NQ speed exploit
    "ASIA-GOLD-01",     # Asian gold trend
    "ES-ORD-02",        # ES opening range breakout
    "SES-01",           # London forex breakout
    "GOLD-CORR-01",     # Gold correlation divergence
})

# Fallback timeout: how long to wait for limit fill before switching to market
LIMIT_TIMEOUT_SECONDS = 12  # 10-15 range, 12 is sweet spot

# Limit order offset: place limit this many points ahead of current price
# so price comes TO the order
LIMIT_OFFSET = {
    "NAS100": 2.0,      # 2 NQ points ahead
    "XAUUSD": 1.0,      # $1 ahead on gold
    "EURUSD": 0.0003,   # 3 pips ahead on FX
    "CL":     0.05,     # 5 cents ahead on oil
    "US500":  1.0,      # 1 ES point ahead
}


# ─────────────────────────────────────────────────────────────────
# ORDER TYPE DECISION
# ─────────────────────────────────────────────────────────────────

def get_order_type(setup_id: str) -> str:
    """Determine if setup should use LIMIT or MARKET order."""
    if setup_id in LIMIT_SETUPS:
        return "LIMIT"
    return "MARKET"


def compute_limit_price(
    setup_id: str,
    direction: str,
    current_price: float,
    instrument: str,
    signal_entry: float,
) -> float:
    """
    Calculate limit price for level setups.
    Place the order slightly ahead of where price is heading.
    
    For LONG mean reversion: price is dropping → place limit BELOW current
    For SHORT mean reversion: price is rising → place limit ABOVE current
    """
    offset = LIMIT_OFFSET.get(instrument, 2.0)

    if direction == "long":
        # Price should come down to us — place limit below current price
        limit = current_price - offset
        # But don't place it worse than the signal entry
        limit = min(limit, signal_entry)
    else:
        # Price should come up to us — place limit above current price
        limit = current_price + offset
        # But don't place it worse than the signal entry
        limit = max(limit, signal_entry)

    return round(limit, 5)


# ─────────────────────────────────────────────────────────────────
# SMART ORDER EXECUTOR
# ─────────────────────────────────────────────────────────────────

class SmartOrderRouter:
    """
    Routes orders intelligently:
    1. Level setups → limit order (zero slippage when filled)
    2. Breakout setups → market order (speed)
    3. If limit doesn't fill in 12s AND conviction still HIGH+ → market fallback
    4. If limit doesn't fill and conviction dropped → cancel, skip trade
    """

    def __init__(self):
        self._pending_limits: Dict[str, dict] = {}
        self._fill_stats: List[dict] = []  # for measured friction feedback

    async def execute(
        self,
        adapter,
        order_request,  # OrderRequest from execution_base
        setup_id: str,
        conviction_level: str,
        conviction_posterior: float,
        instrument_key: str,
        signal_entry: float,
        get_current_price_fn=None,  # async fn to get latest price
    ) -> object:
        """
        Execute order with smart routing.
        Returns OrderResult.
        """
        from execution_base import OrderRequest, OrderType

        order_type = get_order_type(setup_id)

        if order_type == "MARKET":
            # Direct market order — speed is priority
            logger.info("[ROUTER] %s → MARKET order (breakout/momentum)", setup_id)
            result = await adapter.place_order(order_request)
            self._record_fill(setup_id, "MARKET", order_request, result)
            return result

        # ── LIMIT ORDER PATH ─────────────────────────────────────
        limit_price = compute_limit_price(
            setup_id, order_request.direction.value,
            signal_entry, instrument_key, signal_entry,
        )

        # Create limit order
        limit_order = OrderRequest(
            instrument=order_request.instrument,
            direction=order_request.direction,
            size=order_request.size,
            order_type=OrderType.LIMIT,
            limit_price=limit_price,
            stop_loss=order_request.stop_loss,
            take_profit=order_request.take_profit,
            comment=order_request.comment + "|LMT",
        )

        logger.info("[ROUTER] %s → LIMIT order @ %.2f (current: %.2f, offset: %.2f)",
                     setup_id, limit_price, signal_entry,
                     abs(signal_entry - limit_price))

        # Place limit order
        try:
            result = await adapter.place_order(limit_order)
        except Exception as e:
            logger.warning("[ROUTER] Limit order failed: %s — falling back to market", e)
            result = await adapter.place_order(order_request)
            self._record_fill(setup_id, "MARKET_FALLBACK_ERR", order_request, result)
            return result

        # If immediately filled (price was already at level)
        if result.status.value == "filled":
            logger.info("[ROUTER] %s limit FILLED immediately @ %.2f",
                         setup_id, result.fill_price)
            self._record_fill(setup_id, "LIMIT", limit_order, result)
            return result

        # ── WAIT FOR FILL ─────────────────────────────────────────
        order_id = result.order_id
        if not order_id:
            logger.warning("[ROUTER] No order ID returned — treating as failed")
            # Fall back to market
            result = await adapter.place_order(order_request)
            self._record_fill(setup_id, "MARKET_FALLBACK_NOID", order_request, result)
            return result

        logger.info("[ROUTER] %s limit pending (ID: %s) — waiting %ds for fill",
                     setup_id, order_id, LIMIT_TIMEOUT_SECONDS)

        # Poll for fill
        start = time.time()
        filled = False
        while time.time() - start < LIMIT_TIMEOUT_SECONDS:
            await asyncio.sleep(2)  # check every 2 seconds

            try:
                positions = await adapter.get_open_positions()
                # Check if our order became a position
                for pos in positions:
                    comment = getattr(pos, 'comment', '') or ''
                    if order_id in comment or setup_id in comment:
                        filled = True
                        logger.info("[ROUTER] %s limit FILLED after %.1fs",
                                     setup_id, time.time() - start)
                        self._record_fill(setup_id, "LIMIT", limit_order, result)
                        return result
            except Exception:
                pass

        # ── LIMIT DIDN'T FILL — DECIDE: FALLBACK OR CANCEL ───────
        if not filled:
            elapsed = time.time() - start
            logger.info("[ROUTER] %s limit NOT filled after %.1fs — evaluating fallback",
                         setup_id, elapsed)

            # Check if conviction still warrants entry
            should_fallback = conviction_level in ("ELITE", "HIGH", "STANDARD")

            if should_fallback:
                # Cancel the limit order
                try:
                    await adapter.close_position(order_id, None)
                except Exception:
                    pass  # might already be cancelled

                # Send market order as fallback
                logger.info("[ROUTER] %s conviction %s — MARKET FALLBACK",
                             setup_id, conviction_level)
                market_result = await adapter.place_order(order_request)
                self._record_fill(setup_id, "MARKET_FALLBACK", order_request, market_result)
                return market_result
            else:
                # Market rejected the level — price moved away. Don't chase.
                try:
                    await adapter.close_position(order_id, None)
                except Exception:
                    pass

                logger.info("[ROUTER] %s limit unfilled + conviction %s — level rejected, skipping",
                             setup_id, conviction_level)
                self._record_fill(setup_id, "LEVEL_REJECTED", limit_order, result)
                return result

    def _record_fill(self, setup_id: str, order_type: str, order, result):
        """Record fill data for measured friction feedback."""
        intended = order.limit_price if hasattr(order, 'limit_price') and order.limit_price else 0
        actual = result.fill_price if result and result.fill_price else 0
        slippage = abs(actual - intended) if intended > 0 and actual > 0 else 0

        record = {
            "setup_id": setup_id,
            "order_type": order_type,
            "intended_price": intended,
            "fill_price": actual,
            "slippage": slippage,
            "filled": result.status.value == "filled" if result else False,
            "time": time.time(),
        }
        self._fill_stats.append(record)

        if len(self._fill_stats) > 500:
            self._fill_stats = self._fill_stats[-500:]

    # ─────────────────────────────────────────────────────────────
    # MEASURED FRICTION FEEDBACK
    # ─────────────────────────────────────────────────────────────

    def get_measured_friction(self) -> dict:
        """
        After 100+ trades, returns real friction numbers.
        GENESIS uses these to replace backtest estimates.
        """
        if len(self._fill_stats) < 20:
            return {"status": "insufficient_data", "trades": len(self._fill_stats)}

        filled = [s for s in self._fill_stats if s["filled"]]
        if not filled:
            return {"status": "no_fills", "trades": len(self._fill_stats)}

        market_fills = [s for s in filled if s["order_type"] == "MARKET"]
        limit_fills = [s for s in filled if s["order_type"] == "LIMIT"]
        fallback_fills = [s for s in filled if "FALLBACK" in s["order_type"]]
        cancelled = [s for s in self._fill_stats if s["order_type"] == "CANCELLED"]

        market_slippage = (sum(s["slippage"] for s in market_fills) / len(market_fills)
                           if market_fills else 0)
        limit_slippage = (sum(s["slippage"] for s in limit_fills) / len(limit_fills)
                          if limit_fills else 0)

        # Fill rate for limit orders
        total_limits = len(limit_fills) + len(cancelled)
        limit_fill_rate = len(limit_fills) / total_limits if total_limits > 0 else 0

        # Average friction per trade (weighted by order type distribution)
        total_filled = len(filled)
        avg_friction = sum(s["slippage"] for s in filled) / total_filled if total_filled > 0 else 0

        return {
            "status": "measured",
            "total_trades": len(self._fill_stats),
            "total_filled": total_filled,
            "market_orders": len(market_fills),
            "market_avg_slippage": round(market_slippage, 4),
            "limit_orders": len(limit_fills),
            "limit_avg_slippage": round(limit_slippage, 4),
            "limit_fill_rate": round(limit_fill_rate, 4),
            "fallback_orders": len(fallback_fills),
            "cancelled_orders": len(cancelled),
            "avg_friction_per_trade": round(avg_friction, 4),
        }

    def get_ev_threshold(self) -> float:
        """
        Dynamic EV threshold based on measured friction.
        Backtest assumes ~$7-11 friction. If real friction is $12,
        raise threshold to $12 so every trade budgets for real cost.
        
        Default: $0 (no measured data yet)
        After data: measured avg friction in dollars
        """
        friction = self.get_measured_friction()
        if friction["status"] != "measured":
            return 0.0  # no data yet, use default

        # Convert point slippage to dollars (approximate)
        avg_slip_pts = friction["avg_friction_per_trade"]
        # NQ: $20/point, Gold: $100/point, but use conservative estimate
        avg_friction_dollars = avg_slip_pts * 20  # NQ default

        # Add estimated spread cost (~$3 per side for NQ)
        total_friction = avg_friction_dollars + 6.0  # $3 each way

        return round(total_friction, 2)

    def reset(self):
        self._fill_stats.clear()
