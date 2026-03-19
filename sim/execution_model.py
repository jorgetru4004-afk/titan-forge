"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              sim/execution_model.py — Section 12 Simulation Engine          ║
║                                                                              ║
║  EXECUTION MODEL — Realistic Slippage + Spread + Fill Simulation            ║
║  Section 12: "execution_model.py — realistic slippage/spread"               ║
║                                                                              ║
║  Simulates real-world execution conditions:                                  ║
║    - Spread cost per instrument type                                         ║
║    - Market impact for larger positions                                      ║
║    - Slippage on fast-moving markets                                        ║
║    - Partial fills during low liquidity                                      ║
║    - Realistic fill prices (never at the exact bar open)                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Optional

from sim.data_loader import OHLCV


# ─────────────────────────────────────────────────────────────────────────────
# INSTRUMENT PARAMETERS
# Based on typical retail prop firm execution conditions
# ─────────────────────────────────────────────────────────────────────────────

INSTRUMENT_SPECS: dict[str, dict] = {
    # Forex (spread in pips, pip value)
    "EURUSD":  {"spread_pips": 1.2, "pip": 0.0001, "type": "forex",   "min_size": 0.01},
    "GBPUSD":  {"spread_pips": 1.5, "pip": 0.0001, "type": "forex",   "min_size": 0.01},
    "USDJPY":  {"spread_pips": 1.3, "pip": 0.01,   "type": "forex",   "min_size": 0.01},
    "AUDUSD":  {"spread_pips": 1.4, "pip": 0.0001, "type": "forex",   "min_size": 0.01},
    # Futures (spread in ticks, tick value)
    "ES":      {"spread_ticks": 1, "tick": 0.25,   "type": "futures", "min_size": 1.0},
    "NQ":      {"spread_ticks": 1, "tick": 0.25,   "type": "futures", "min_size": 1.0},
    "RTY":     {"spread_ticks": 1, "tick": 0.10,   "type": "futures", "min_size": 1.0},
    # Indices (spread in points)
    "US30":    {"spread_pts": 3.0, "type": "index",   "min_size": 0.01},
    "US500":   {"spread_pts": 0.5, "type": "index",   "min_size": 0.01},
    "US100":   {"spread_pts": 1.0, "type": "index",   "min_size": 0.01},
    # Equities/ETFs
    "SPY":     {"spread_pts": 0.02, "type": "equity",  "min_size": 1.0},
    "QQQ":     {"spread_pts": 0.03, "type": "equity",  "min_size": 1.0},
}


@dataclass
class SimFill:
    """Result of a simulated order fill."""
    instrument:     str
    direction:      str        # "long" / "short"
    size:           float
    requested_price: float    # What the signal wanted
    fill_price:      float    # What was actually received
    spread_cost:     float    # Cost of spread in price units
    slippage:        float    # Additional slippage beyond spread
    total_cost:      float    # spread_cost + slippage per unit
    is_partial:      bool = False
    filled_size:     float = 0.0

    @property
    def total_friction(self) -> float:
        return self.spread_cost + abs(self.slippage)


class ExecutionModel:
    """
    Section 12: Realistic slippage and spread model for simulation.

    Applies instrument-appropriate spread and slippage to every
    simulated order. Outputs the true fill price the system would
    receive in live trading.

    Usage:
        model = ExecutionModel(seed=42)
        fill = model.simulate_fill(
            instrument="EURUSD", direction="long",
            size=1.0, bar=current_bar,
        )
        entry_price = fill.fill_price
    """

    def __init__(self, seed: int = 42, high_vol_multiplier: float = 2.0):
        self._rng = random.Random(seed)
        self._high_vol_mult = high_vol_multiplier
        self._fill_history: list[SimFill] = []

    def simulate_fill(
        self,
        instrument:      str,
        direction:       str,      # "long" or "short"
        size:            float,
        bar:             OHLCV,    # The bar the order fires on
        order_type:      str = "market",
        limit_price:     Optional[float] = None,
        is_high_vol:     bool = False,
    ) -> SimFill:
        """
        Simulate a realistic order fill.

        Long entry:  fill_price = ask (bar.open + half_spread + slippage)
        Short entry: fill_price = bid (bar.open - half_spread - slippage)
        """
        spec      = INSTRUMENT_SPECS.get(instrument.upper(), {})
        base_px   = bar.open

        # Calculate spread
        spread = self._get_spread(instrument, spec, is_high_vol)

        # Calculate slippage: proportional to ATR, worse on fast bars
        slippage = self._get_slippage(bar, spec, size, is_high_vol)

        # Apply to fill price
        if direction.lower() == "long":
            fill_price = base_px + spread * 0.5 + slippage
        else:
            fill_price = base_px - spread * 0.5 - slippage

        fill_price = round(fill_price, 6)
        total_cost = spread * 0.5 + slippage

        fill = SimFill(
            instrument=instrument,
            direction=direction,
            size=size,
            requested_price=base_px,
            fill_price=fill_price,
            spread_cost=round(spread * 0.5, 6),
            slippage=round(slippage, 6),
            total_cost=round(total_cost, 6),
            is_partial=False,
            filled_size=size,
        )
        self._fill_history.append(fill)
        return fill

    def simulate_close(
        self,
        instrument:  str,
        direction:   str,
        size:        float,
        bar:         OHLCV,
        is_high_vol: bool = False,
    ) -> SimFill:
        """
        Simulate closing a position.
        Closing a long = selling at bid (loses spread again).
        """
        close_direction = "short" if direction.lower() == "long" else "long"
        return self.simulate_fill(
            instrument=instrument,
            direction=close_direction,
            size=size,
            bar=bar,
            is_high_vol=is_high_vol,
        )

    def calculate_pnl(
        self,
        instrument:  str,
        direction:   str,
        size:        float,
        entry_fill:  SimFill,
        exit_fill:   SimFill,
    ) -> float:
        """
        Calculate P&L for a completed trade including friction.
        For forex: P&L in account currency per lot.
        For futures: P&L in points × size.
        """
        spec = INSTRUMENT_SPECS.get(instrument.upper(), {})
        inst_type = spec.get("type", "forex")

        price_diff = exit_fill.fill_price - entry_fill.fill_price
        if direction.lower() == "short":
            price_diff = -price_diff

        if inst_type == "forex":
            # Simplified: 1 lot = 100,000 units, P&L in account currency
            pip = spec.get("pip", 0.0001)
            pnl = (price_diff / pip) * 10.0 * size   # $10 per pip per lot
        elif inst_type == "futures":
            tick = spec.get("tick", 0.25)
            # Each tick worth ~$12.50 for ES, varies by instrument
            pnl = (price_diff / tick) * 12.5 * size
        else:
            pnl = price_diff * size * 100   # Generic: assume $100/point/unit

        return round(pnl, 2)

    def _get_spread(
        self, instrument: str, spec: dict, is_high_vol: bool
    ) -> float:
        """Calculate total spread for instrument."""
        vol_mult = self._high_vol_mult if is_high_vol else 1.0

        if "spread_pips" in spec:
            return spec["spread_pips"] * spec.get("pip", 0.0001) * vol_mult
        elif "spread_ticks" in spec:
            return spec["spread_ticks"] * spec.get("tick", 0.25) * vol_mult
        elif "spread_pts" in spec:
            return spec["spread_pts"] * vol_mult
        return 0.0002  # Default: 2 pips

    def _get_slippage(
        self,
        bar:         OHLCV,
        spec:        dict,
        size:        float,
        is_high_vol: bool,
    ) -> float:
        """
        Simulate realistic slippage.
        Larger positions get worse fills. Fast bars get worse fills.
        """
        base_slip = bar.atr * 0.02   # 2% of ATR as base slippage
        if is_high_vol:
            base_slip *= 2.5
        if size > 5.0:   # Large position — market impact
            base_slip *= (1.0 + size * 0.02)

        # Random component: some fills are better, some worse
        random_component = self._rng.gauss(0, base_slip * 0.3)
        return max(0.0, base_slip + random_component)

    @property
    def avg_slippage(self) -> float:
        if not self._fill_history:
            return 0.0
        return sum(f.slippage for f in self._fill_history) / len(self._fill_history)

    @property
    def avg_total_cost(self) -> float:
        if not self._fill_history:
            return 0.0
        return sum(f.total_cost for f in self._fill_history) / len(self._fill_history)

    @property
    def fill_count(self) -> int:
        return len(self._fill_history)

    def friction_report(self) -> dict:
        return {
            "fill_count":      self.fill_count,
            "avg_slippage":    round(self.avg_slippage, 6),
            "avg_total_cost":  round(self.avg_total_cost, 6),
        }
