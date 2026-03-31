"""
FORGE v22 — Instrument Configuration
=====================================
14 instruments, each mapped to exactly ONE proven strategy.
Research: 5 runs, 20 instruments, 299 tests, 105 profitable combos.
Every instrument is mean-reverting (Hurst ~0.00).
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ─── Trade Type Classification ───────────────────────────────────────────────

class TradeType(str, Enum):
    SCALP = "SCALP"    # Hard TP, no trailing, exit at TP or SL
    RUNNER = "RUNNER"  # Partial at 1R, trail rest with runner detection


class OrderType(str, Enum):
    LIMIT = "LIMIT"    # Place limit with 5-bar timeout -> market fallback
    MARKET = "MARKET"  # Execute immediately on signal


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"


class Strategy(str, Enum):
    MEAN_REVERT = "MEAN_REVERT"
    VWAP_REVERT = "VWAP_REVERT"
    STOCH_REVERSAL = "STOCH_REVERSAL"
    EMA_BOUNCE = "EMA_BOUNCE"
    PREV_DAY_HL = "PREV_DAY_HL"
    ORB = "ORB"
    ASIAN_BREAKOUT = "ASIAN_BREAKOUT"
    GAP_FILL = "GAP_FILL"
    VOL_COMPRESS = "VOL_COMPRESS"
    CONFLUENCE = "CONFLUENCE"


# ─── Strategy → Default Trade Type + Order Type ──────────────────────────────

STRATEGY_DEFAULTS = {
    Strategy.MEAN_REVERT:     {"trade_type": TradeType.SCALP,  "order_type": OrderType.LIMIT},
    Strategy.VWAP_REVERT:     {"trade_type": TradeType.SCALP,  "order_type": OrderType.LIMIT},
    Strategy.STOCH_REVERSAL:  {"trade_type": TradeType.SCALP,  "order_type": OrderType.LIMIT},
    Strategy.EMA_BOUNCE:      {"trade_type": TradeType.SCALP,  "order_type": OrderType.LIMIT},   # Can upgrade to RUNNER
    Strategy.PREV_DAY_HL:     {"trade_type": TradeType.RUNNER, "order_type": OrderType.MARKET},
    Strategy.ORB:             {"trade_type": TradeType.RUNNER, "order_type": OrderType.MARKET},
    Strategy.ASIAN_BREAKOUT:  {"trade_type": TradeType.RUNNER, "order_type": OrderType.MARKET},
    Strategy.GAP_FILL:        {"trade_type": TradeType.SCALP,  "order_type": OrderType.LIMIT},
    Strategy.VOL_COMPRESS:    {"trade_type": TradeType.RUNNER, "order_type": OrderType.MARKET},
    Strategy.CONFLUENCE:      {"trade_type": TradeType.RUNNER, "order_type": OrderType.MARKET},
}


# ─── Instrument Setup ────────────────────────────────────────────────────────

@dataclass
class InstrumentSetup:
    symbol: str
    strategy: Strategy
    direction: Direction
    sl_atr: float           # Stop loss in ATR multiples
    tp_atr: float           # Take profit in ATR multiples
    risk_pct: float         # Risk per trade (% of balance)
    trade_type: TradeType
    order_type: OrderType
    expectancy: float       # Research-proven expectancy
    win_rate: float         # Research-proven win rate
    profit_factor: float    # Research-proven profit factor
    # Direction-specific overrides (for BOTH direction instruments)
    long_sl: Optional[float] = None
    long_tp: Optional[float] = None
    short_sl: Optional[float] = None
    short_tp: Optional[float] = None
    # FTMO symbol suffix
    ftmo_suffix: str = ""
    # Pip multiplier for position sizing
    pip_value: Optional[float] = None
    # Min lot size (FTMO minimum)
    min_lot: float = 0.01
    # Point value for SL/TP calculation
    point_value: Optional[float] = None


# ─── SETUP_CONFIG: The 14 proven instruments ─────────────────────────────────

SETUP_CONFIG: Dict[str, InstrumentSetup] = {

    # 1. USDCHF — Mean Revert | LONG only | SCALP
    #    v22.2 FIX: Was Stoch Reversal (23% WR, -3R over 100 trades).
    #    Ranging pair — RSI extremes snap back hard. Mean Revert is
    #    the highest WR strategy at 43.8%.
    "USDCHF": InstrumentSetup(
        symbol="USDCHF", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.225, win_rate=0.55, profit_factor=2.07,
    ),

    # NZDUSD — Gap Fill | SHORT only | SCALP
    "NZDUSD": InstrumentSetup(
        symbol="NZDUSD", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.555, win_rate=0.80, profit_factor=4.19,
    ),

    # US100 — EMA Bounce | SHORT only | SCALP (can upgrade to RUNNER)
    "US100": InstrumentSetup(
        symbol="US100", strategy=Strategy.EMA_BOUNCE,
        direction=Direction.BOTH, sl_atr=2.0, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.425, win_rate=0.48, profit_factor=1.56,
    ),

    # EURGBP — VWAP Revert | SHORT only | SCALP
    "EURGBP": InstrumentSetup(
        symbol="EURGBP", strategy=Strategy.VWAP_REVERT,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.307, win_rate=0.46, profit_factor=1.14,
    ),

    # 8. EURUSD — Mean Revert | SHORT only | SCALP
    #    v22.1 FIX: Was CONFLUENCE, fired 25 of 55 trades (45% of activity)
    #    at 20% win rate = -2.68R. Mean Revert only fires on RSI extremes.
    "EURUSD": InstrumentSetup(
        symbol="EURUSD", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=0.8, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.385, win_rate=0.46, profit_factor=1.37,
    ),

    # 9. XAUUSD — VWAP Revert | SHORT only | SCALP
    #    v22.2 FIX: Was Mean Revert (4.8% WR, -22R over 6mo).
    #    Gold trends through RSI extremes. VWAP Revert requires 2-std
    #    deviation PLUS rejection candle — filters out trend continuation.
    "XAUUSD": InstrumentSetup(
        symbol="XAUUSD", strategy=Strategy.VWAP_REVERT,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=4.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.481, win_rate=0.52, profit_factor=1.18,
    ),

    # 10. GBPJPY — Prev Day HL | SHORT only | RUNNER
    "GBPJPY": InstrumentSetup(
        symbol="GBPJPY", strategy=Strategy.PREV_DAY_HL,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=0.8,
        risk_pct=1.5, trade_type=TradeType.RUNNER, order_type=OrderType.MARKET,
        expectancy=0.404, win_rate=0.53, profit_factor=1.45,
    ),

    # 11. USDJPY — Gap Fill / ORB | SHORT only | RUNNER
    "USDJPY": InstrumentSetup(
        symbol="USDJPY", strategy=Strategy.ORB,
        direction=Direction.BOTH, sl_atr=0.8, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.RUNNER, order_type=OrderType.MARKET,
        expectancy=0.333, win_rate=0.67, profit_factor=1.25,
    ),

    # 12. BTCUSD — Mean Revert | SHORT only | SCALP
    "BTCUSD": InstrumentSetup(
        symbol="BTCUSD", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=1.5, tp_atr=2.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.220, win_rate=0.42, profit_factor=0.97,
    ),

    # 13. USOIL — EMA Bounce | LONG only | SCALP
    "USOIL": InstrumentSetup(
        symbol="USOIL", strategy=Strategy.EMA_BOUNCE,
        direction=Direction.BOTH, sl_atr=1.0, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.308, win_rate=0.44, profit_factor=1.06,
    ),

    # 14. GBPUSD — Stoch Reversal | SHORT only | SCALP
    #    v22.2 FIX: Was ORB (22.2% WR, -35.54R over 6mo, 167 trades).
    #    GBPUSD fades every breakout — mean-reverting pair.
    #    Stoch Reversal catches overbought/oversold extremes.
    "GBPUSD": InstrumentSetup(
        symbol="GBPUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=1.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.235, win_rate=0.49, profit_factor=1.42,
    ),
}


# ─── Time-of-Day Edges (statistically significant) ───────────────────────────
# Format: {hour_utc: [(symbol, direction, p_value), ...]}

TIME_OF_DAY_EDGES: Dict[int, List[Tuple[str, str, float]]] = {
    2:  [("US500", "SHORT", 0.030)],  # US500 dropped but keep for reference
    15: [("EURJPY", "LONG", 0.037)],
    17: [("US30", "SHORT", 0.045)],
    19: [("USDJPY", "SHORT", 0.017), ("XAGUSD", "LONG", 0.042)],
    20: [
        ("NZDUSD", "SHORT", 0.001),
        ("EURGBP", "SHORT", 0.0004),
        ("EURUSD", "SHORT", 0.025),
        ("BTCUSD", "SHORT", 0.043),
        ("GBPJPY", "SHORT", 0.042),
    ],
    21: [("XAUUSD", "SHORT", 0.0001), ("EURGBP", "LONG", 0.002)],
}

# Confidence adjustment for time-of-day edges
TOD_EDGE_BOOST = 0.15       # +15% confidence during edge hours
TOD_SUPPRESS = -0.15        # -15% confidence during non-edge hours (for symbols WITH edges)


# ─── Correlation Groups (never trade simultaneously) ─────────────────────────

CORRELATION_GROUPS = {
    "redundant": [  # >0.80 — NEVER trade simultaneously
        {"pair": ("EURUSD", "GBPUSD"), "corr": 0.862},
    ],
    "high": [  # 0.50-0.80 — max 1 position in group
        {"pair": ("EURUSD", "US500"), "corr": 0.531},
        {"pair": ("GBPUSD", "US500"), "corr": 0.525},
    ],
    "diversified": [  # Near zero — trade freely
        {"pair": ("USDJPY", "US100"), "corr": -0.040},
        {"pair": ("GBPUSD", "USOIL"), "corr": -0.048},
        {"pair": ("USDJPY", "BTCUSD"), "corr": -0.071},
    ],
}


# ─── Monthly Seasonality ─────────────────────────────────────────────────────
# Format: {month: [(symbol, direction, size_boost), ...]}

MONTHLY_SEASONALITY: Dict[int, List[Tuple[str, str, float]]] = {
    5:  [
        ("GBPJPY", "LONG", 0.20),
        ("GBPUSD", "LONG", 0.20),
        ("XAUUSD", "LONG", 0.20),
    ],
    6:  [
        ("USDCHF", "LONG", 0.20),
        ("US100", "LONG", 0.20),
    ],
}


# ─── Instrument Metadata (for position sizing + MetaAPI) ─────────────────────

INSTRUMENT_META = {
    # Forex pairs
    "USDCHF":  {"type": "forex", "pip_size": 0.0001, "lot_size": 100000, "ftmo_suffix": ".sim"},
    "NZDUSD":  {"type": "forex", "pip_size": 0.0001, "lot_size": 100000, "ftmo_suffix": ".sim"},
    "EURGBP":  {"type": "forex", "pip_size": 0.0001, "lot_size": 100000, "ftmo_suffix": ".sim"},
    "EURUSD":  {"type": "forex", "pip_size": 0.0001, "lot_size": 100000, "ftmo_suffix": ".sim"},
    "GBPJPY":  {"type": "forex", "pip_size": 0.01,   "lot_size": 100000, "ftmo_suffix": ".sim"},
    "USDJPY":  {"type": "forex", "pip_size": 0.01,   "lot_size": 100000, "ftmo_suffix": ".sim"},
    "GBPUSD":  {"type": "forex", "pip_size": 0.0001, "lot_size": 100000, "ftmo_suffix": ".sim"},
    # Indices
    "US100":   {"type": "index", "pip_size": 0.1,    "lot_size": 1,      "ftmo_suffix": ".sim"},
    # Commodities
    "XAUUSD":  {"type": "commodity", "pip_size": 0.01, "lot_size": 100,  "ftmo_suffix": ".sim"},
    "USOIL":   {"type": "commodity", "pip_size": 0.01, "lot_size": 1000, "ftmo_suffix": ".sim"},
    # Crypto
    "BTCUSD":  {"type": "crypto", "pip_size": 0.01,   "lot_size": 1,     "ftmo_suffix": ""},
}


def get_ftmo_symbol(symbol: str) -> str:
    """Get the FTMO-specific symbol name (adds .sim suffix for OANDA)."""
    meta = INSTRUMENT_META.get(symbol, {})
    suffix = meta.get("ftmo_suffix", "")
    return f"{symbol}{suffix}"


def get_setup(symbol: str) -> Optional[InstrumentSetup]:
    """Get the setup configuration for a symbol."""
    return SETUP_CONFIG.get(symbol)


def get_sl_tp_for_direction(setup: InstrumentSetup, direction: str) -> Tuple[float, float]:
    """Get SL/TP in ATR multiples for a specific direction.
    
    Handles instruments with direction-specific overrides.
    """
    if direction == "LONG" and setup.long_sl is not None:
        return setup.long_sl, setup.long_tp
    elif direction == "SHORT" and setup.short_sl is not None:
        return setup.short_sl, setup.short_tp
    return setup.sl_atr, setup.tp_atr


def get_all_symbols() -> List[str]:
    """Return all active symbols."""
    return list(SETUP_CONFIG.keys())
