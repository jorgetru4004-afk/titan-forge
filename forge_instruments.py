"""
FORGE v21 — Multi-Instrument Infrastructure
=============================================
Separate trackers per instrument. Per-instrument ATR, VWAP, ORB, IB.
Symbol resolution for FTMO. Polygon ticker mapping.

NEXUS Capital — Jorge Trujillo | Claude — AI Architect | March 2026
"""

import os
import logging
from datetime import time as dtime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("FORGE.instruments")

# ─────────────────────────────────────────────────────────────────
# INSTRUMENT DEFINITIONS
# ─────────────────────────────────────────────────────────────────

ATR_DEFAULTS: Dict[str, float] = {
    "NAS100": 150.0,      # NQ points per day
    "XAUUSD": 40.0,       # dollars per ounce per day
    "EURUSD": 0.0070,     # 70 pips per day
    "CL":     2.50,       # ~$2.50/barrel per day
    "US500":  50.0,       # ES points per day
}

# $ per point per 1.0 standard lot
POINT_VALUE: Dict[str, float] = {
    "NAS100": 20.0,
    "XAUUSD": 100.0,
    "EURUSD": 100000.0,
    "CL":     1000.0,
    "US500":  50.0,
}

MIN_LOT: Dict[str, float] = {
    "NAS100": 0.10,
    "XAUUSD": 0.01,
    "EURUSD": 0.01,
    "CL":     0.10,
    "US500":  0.10,
}

# Polygon ETF proxies for candle data (trend direction ONLY — never for SL/TP)
POLYGON_TICKERS: Dict[str, str] = {
    "NAS100": "QQQ",
    "XAUUSD": "GLD",
    "EURUSD": "FXE",
    "CL":     "USO",
    "US500":  "SPY",
}

# FTMO symbol candidates — test each via MetaAPI at boot
FTMO_SYMBOL_CANDIDATES: Dict[str, List[str]] = {
    "NAS100": ["US100.sim", "NAS100.sim", "USTEC.sim"],
    "XAUUSD": ["XAUUSD.sim", "GOLD.sim"],
    "EURUSD": ["EURUSD.sim"],
    "CL":     ["USOIL.sim", "WTI.sim", "XTIUSD.sim", "OIL.sim"],
    "US500":  ["US500.sim", "SP500.sim", "SPX500.sim"],
}

# Session-specific ORB/IB windows per instrument
INSTRUMENT_SESSIONS: Dict[str, dict] = {
    "NAS100": {"orb_start": dtime(9, 30),  "orb_end": dtime(9, 45),  "ib_end": dtime(10, 30)},
    "XAUUSD": {"orb_start": dtime(3, 0),   "orb_end": dtime(3, 30),  "ib_end": dtime(4, 30)},
    "EURUSD": {"orb_start": dtime(3, 0),   "orb_end": dtime(3, 15),  "ib_end": dtime(4, 0)},
    "CL":     {"orb_start": dtime(9, 0),   "orb_end": dtime(9, 15),  "ib_end": dtime(10, 0)},
    "US500":  {"orb_start": dtime(9, 30),  "orb_end": dtime(9, 45),  "ib_end": dtime(10, 30)},
}

# Liquidity tiers for spread estimation
LIQUIDITY_TIER: Dict[str, str] = {
    "NAS100": "HIGH",
    "XAUUSD": "HIGH",
    "EURUSD": "HIGH",
    "CL":     "MID",
    "US500":  "HIGH",
}


# ─────────────────────────────────────────────────────────────────
# INSTRUMENT TRACKER — one per instrument
# ─────────────────────────────────────────────────────────────────

@dataclass
class InstrumentTracker:
    """Maintains real-time state for a single instrument."""
    instrument: str = ""

    # Price history (M1-level, rolling 120 candles)
    price_history: List[float] = field(default_factory=list)
    m5_candles: list = field(default_factory=list)
    m15_candles: list = field(default_factory=list)

    # VWAP — three anchors
    rth_vwap: float = 0.0          # resets 09:30 ET
    london_vwap: float = 0.0       # resets 03:00 ET
    overnight_vwap: float = 0.0    # resets 18:00 ET
    _vwap_cum_vol: float = 0.0
    _vwap_cum_pv: float = 0.0
    _london_cum_vol: float = 0.0
    _london_cum_pv: float = 0.0
    _overnight_cum_vol: float = 0.0
    _overnight_cum_pv: float = 0.0

    # ORB / IB
    orb_high: float = 0.0
    orb_low: float = 999999.0
    orb_locked: bool = False
    ib_high: float = 0.0
    ib_low: float = 999999.0
    ib_locked: bool = False

    # Session highs/lows
    session_high: float = 0.0
    session_low: float = 999999.0
    overnight_high: float = 0.0
    overnight_low: float = 999999.0
    asian_high: float = 0.0
    asian_low: float = 999999.0

    # PDH / PDL
    prev_day_high: float = 0.0
    prev_day_low: float = 0.0
    prev_day_close: float = 0.0

    # ATR
    atr: float = 0.0
    atr_consumed_pct: float = 0.0

    # Session range (from actual session open)
    session_open: float = 0.0
    session_range: float = 0.0

    # Round numbers (instrument-specific)
    round_numbers: List[float] = field(default_factory=list)

    # Trend state
    m5_trend: str = "neutral"
    m15_trend: str = "neutral"
    h1_trend: str = "neutral"

    def update_price(self, price: float, volume: float = 1.0):
        """Update all trackers with new price tick."""
        self.price_history.append(price)
        if len(self.price_history) > 120:
            self.price_history = self.price_history[-120:]

        # Session H/L
        if price > self.session_high:
            self.session_high = price
        if price < self.session_low:
            self.session_low = price

        # ORB tracking (before lock)
        if not self.orb_locked:
            if price > self.orb_high:
                self.orb_high = price
            if price < self.orb_low:
                self.orb_low = price

        # IB tracking (before lock)
        if not self.ib_locked:
            if price > self.ib_high:
                self.ib_high = price
            if price < self.ib_low:
                self.ib_low = price

        # VWAP updates (RTH anchor)
        self._vwap_cum_vol += volume
        self._vwap_cum_pv += price * volume
        if self._vwap_cum_vol > 0:
            self.rth_vwap = self._vwap_cum_pv / self._vwap_cum_vol

        # London VWAP
        self._london_cum_vol += volume
        self._london_cum_pv += price * volume
        if self._london_cum_vol > 0:
            self.london_vwap = self._london_cum_pv / self._london_cum_vol

        # Overnight VWAP
        self._overnight_cum_vol += volume
        self._overnight_cum_pv += price * volume
        if self._overnight_cum_vol > 0:
            self.overnight_vwap = self._overnight_cum_pv / self._overnight_cum_vol

        # ATR consumed
        if self.session_open > 0 and self.atr > 0:
            self.session_range = self.session_high - self.session_low
            self.atr_consumed_pct = min(self.session_range / self.atr, 2.0)

    def lock_orb(self):
        self.orb_locked = True
        logger.info("[%s] ORB locked: %.2f / %.2f (range: %.2f)",
                     self.instrument, self.orb_high, self.orb_low,
                     self.orb_high - self.orb_low)

    def lock_ib(self):
        self.ib_locked = True
        logger.info("[%s] IB locked: %.2f / %.2f (range: %.2f)",
                     self.instrument, self.ib_high, self.ib_low,
                     self.ib_high - self.ib_low)

    def reset_rth_vwap(self):
        self._vwap_cum_vol = 0.0
        self._vwap_cum_pv = 0.0
        self.rth_vwap = 0.0

    def reset_london_vwap(self):
        self._london_cum_vol = 0.0
        self._london_cum_pv = 0.0
        self.london_vwap = 0.0

    def reset_overnight_vwap(self):
        self._overnight_cum_vol = 0.0
        self._overnight_cum_pv = 0.0
        self.overnight_vwap = 0.0

    def reset_session(self):
        """Full daily reset."""
        self.session_high = 0.0
        self.session_low = 999999.0
        self.orb_high = 0.0
        self.orb_low = 999999.0
        self.orb_locked = False
        self.ib_high = 0.0
        self.ib_low = 999999.0
        self.ib_locked = False
        self.atr_consumed_pct = 0.0
        self.session_range = 0.0
        self.session_open = 0.0
        self.overnight_high = 0.0
        self.overnight_low = 999999.0
        self.asian_high = 0.0
        self.asian_low = 999999.0

    def compute_round_numbers(self, current_price: float):
        """Generate round number levels near current price."""
        inst = self.instrument
        if inst == "NAS100":
            step = 100
            base = int(current_price / step) * step
            self.round_numbers = [float(base + i * step) for i in range(-3, 4)]
        elif inst == "XAUUSD":
            step = 50
            base = int(current_price / step) * step
            self.round_numbers = [float(base + i * step) for i in range(-3, 4)]
        elif inst == "EURUSD":
            step = 0.0050
            base = round(current_price / step) * step
            self.round_numbers = [round(base + i * step, 4) for i in range(-3, 4)]
        elif inst == "CL":
            step = 1.0
            base = int(current_price)
            self.round_numbers = [float(base + i) for i in range(-3, 4)]
        elif inst == "US500":
            step = 50
            base = int(current_price / step) * step
            self.round_numbers = [float(base + i * step) for i in range(-3, 4)]

    def get_vwap_for_session(self, session: str) -> float:
        """Return the correct VWAP anchor for the current session."""
        if session in ("ASIAN",):
            return self.overnight_vwap if self.overnight_vwap > 0 else self.rth_vwap
        elif session in ("LONDON", "PRE_MARKET"):
            return self.london_vwap if self.london_vwap > 0 else self.rth_vwap
        else:
            return self.rth_vwap


# ─────────────────────────────────────────────────────────────────
# SYMBOL RESOLVER — verifies FTMO symbols at boot
# ─────────────────────────────────────────────────────────────────

class SymbolResolver:
    """Discovers and caches working FTMO symbol names per instrument."""

    def __init__(self):
        self._resolved: Dict[str, str] = {}

    async def resolve_all(self, adapter) -> Dict[str, str]:
        """Test each candidate symbol via MetaAPI. Cache working ones."""
        for instrument, candidates in FTMO_SYMBOL_CANDIDATES.items():
            for symbol in candidates:
                try:
                    price = await adapter.get_price(symbol)
                    if price and price.get("bid", 0) > 0:
                        self._resolved[instrument] = symbol
                        logger.info("[SYMBOL] %s → %s (bid=%.4f)",
                                    instrument, symbol, price["bid"])
                        break
                except Exception:
                    continue
            if instrument not in self._resolved:
                logger.warning("[SYMBOL] %s — no valid FTMO symbol found", instrument)
        return self._resolved

    def get_symbol(self, instrument: str) -> Optional[str]:
        return self._resolved.get(instrument)

    def get_active_instruments(self) -> List[str]:
        return list(self._resolved.keys())


# ─────────────────────────────────────────────────────────────────
# TRACKER MANAGER — creates and manages all instrument trackers
# ─────────────────────────────────────────────────────────────────

class TrackerManager:
    """Central manager for all instrument trackers."""

    def __init__(self):
        self.trackers: Dict[str, InstrumentTracker] = {}

    def initialize(self, instruments: List[str]):
        for inst in instruments:
            tracker = InstrumentTracker(instrument=inst)
            tracker.atr = ATR_DEFAULTS.get(inst, 100.0)
            self.trackers[inst] = tracker
            logger.info("[TRACKER] Initialized %s (ATR default=%.2f)", inst, tracker.atr)

    def get(self, instrument: str) -> Optional[InstrumentTracker]:
        return self.trackers.get(instrument)

    def all_trackers(self) -> Dict[str, InstrumentTracker]:
        return self.trackers

    def reset_all_sessions(self):
        for tracker in self.trackers.values():
            # Save PDH/PDL before reset
            if tracker.session_high > 0 and tracker.session_low < 999999:
                tracker.prev_day_high = tracker.session_high
                tracker.prev_day_low = tracker.session_low
                if tracker.price_history:
                    tracker.prev_day_close = tracker.price_history[-1]
            tracker.reset_session()
