"""
FORGE v22 — LONG REGIME INSTRUMENT CONFIG
==========================================
Built from LONG & NEUTRAL regime research.
Each instrument mapped to its best LONG strategy based on
combos profitable across 2+ market periods (bull, mixed, bear).

This is a SEPARATE config — FORGE will load SHORT or LONG config
based on GENESIS regime detection.
"""

from forge_instruments_v22 import (
    InstrumentSetup, Strategy, Direction, TradeType, OrderType,
)
from typing import Dict


# ─── LONG SETUP CONFIG ──────────────────────────────────────────────────────
# Selected from research: only combos profitable in 2+ periods

LONG_SETUP_CONFIG: Dict[str, InstrumentSetup] = {

    # EURGBP — MEAN_REVERT LONG | 3/3 periods | +49R | 52.5% WR
    # Best LONG combo in the entire research. Works bull, mixed, AND bear.
    "EURGBP": InstrumentSetup(
        symbol="EURGBP", strategy=Strategy.MEAN_REVERT,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.50, win_rate=0.525, profit_factor=5.0,
    ),

    # USOIL — STOCH_REVERSAL LONG | 2/3 periods | +21R combined in research
    #    v22.2L FIX2: MEAN_REVERT was -8R at 9.1% WR.
    #    Stoch oversold catches oil bounces — +17R bull, +4R bear.
    "USOIL": InstrumentSetup(
        symbol="USOIL", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=1.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.30, win_rate=0.375, profit_factor=3.43,
    ),

    # BTCUSD — STOCH_REVERSAL LONG | 3/3 periods | +44R | 30.5% WR
    # Catches oversold bounces on BTC across all regimes.
    "BTCUSD": InstrumentSetup(
        symbol="BTCUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.LONG, sl_atr=1.5, tp_atr=2.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.30, win_rate=0.305, profit_factor=1.57,
    ),

    # NZDUSD — MEAN_REVERT LONG | 3/3 periods | +38R | 41.7% WR
    # Oversold NZD snaps back. Works in all regimes.
    "NZDUSD": InstrumentSetup(
        symbol="NZDUSD", strategy=Strategy.MEAN_REVERT,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.35, win_rate=0.417, profit_factor=9.00,
    ),

    # ETHUSD — STOCH_REVERSAL LONG | 3/3 periods | +32R | 28.6% WR
    # ETH oversold bounces across all regimes.
    "ETHUSD": InstrumentSetup(
        symbol="ETHUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.LONG, sl_atr=1.0, tp_atr=1.5,
        risk_pct=1.2, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.28, win_rate=0.286, profit_factor=2.29,
    ),

    # XAUUSD — STOCH_REVERSAL LONG | 3/3 periods | +28R | 28.9% WR
    # Gold oversold bounces — this is the LONG answer to XAUUSD.
    "XAUUSD": InstrumentSetup(
        symbol="XAUUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=2.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.28, win_rate=0.289, profit_factor=1.85,
    ),

    # GBPUSD — MEAN_REVERT LONG | 3/3 periods | +13R | 18.4% WR
    # Low WR but consistent across all 3 periods. Big winners.
    "GBPUSD": InstrumentSetup(
        symbol="GBPUSD", strategy=Strategy.MEAN_REVERT,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.20, win_rate=0.184, profit_factor=2.17,
    ),

    # USOIL (alt) — MEAN_REVERT LONG | 3/3 periods | +17R | 30.3% WR
    # Second USOIL strategy — but can't run two on same instrument.
    # Keep GAP_FILL as primary (higher R and WR).

    # GBPJPY — STOCH_REVERSAL LONG | 2/3 periods | +10R in research
    #    v22.2L FIX: Was GAP_FILL (-2.55R, 17 trades).
    #    Stoch oversold catches GJ bounces more reliably.
    "GBPJPY": InstrumentSetup(
        symbol="GBPJPY", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=1.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.30, win_rate=0.205, profit_factor=1.43,
    ),

    # EURUSD — MEAN_REVERT LONG | 2/3 periods | +15R | 50% WR
    "EURUSD": InstrumentSetup(
        symbol="EURUSD", strategy=Strategy.MEAN_REVERT,
        direction=Direction.LONG, sl_atr=0.8, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.30, win_rate=0.50, profit_factor=4.00,
    ),

    # NZDUSD — GAP_FILL LONG | 3/3 periods | +15R | 69% WR
    # Already have MEAN_REVERT for NZDUSD. GAP_FILL is backup.
    # Keep MEAN_REVERT as primary (+38R vs +15R).

    # EURUSD — GAP_FILL LONG | 2/3 periods | +13.8R | 82.4% WR
    # Already have MEAN_REVERT. GAP_FILL backup.

    # USDJPY — EMA_BOUNCE LONG | regime-neutral +19R combined
    "USDJPY": InstrumentSetup(
        symbol="USDJPY", strategy=Strategy.EMA_BOUNCE,
        direction=Direction.LONG, sl_atr=1.0, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.30, win_rate=0.333, profit_factor=7.50,
    ),

    # USDCHF — GAP_FILL LONG | 3/3 periods | +11.8R | 66.7% WR
    "USDCHF": InstrumentSetup(
        symbol="USDCHF", strategy=Strategy.GAP_FILL,
        direction=Direction.LONG, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.30, win_rate=0.667, profit_factor=3.40,
    ),

    # GER40 — VWAP_REVERT LONG | 2/3 periods | +22R in research
    #    v22.2L FIX: Was GAP_FILL (-19.91R, 79 trades, 25.3% WR).
    #    VWAP_REVERT catches oversold bounces off VWAP, much more selective.
    "GER40": InstrumentSetup(
        symbol="GER40", strategy=Strategy.VWAP_REVERT,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=4.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.30, win_rate=0.50, profit_factor=4.00,
    ),

    # UK100 — MEAN_REVERT LONG | 2/3 periods | +18R
    "UK100": InstrumentSetup(
        symbol="UK100", strategy=Strategy.MEAN_REVERT,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.35, win_rate=0.500, profit_factor=6.00,
    ),

    # US100 — STOCH_REVERSAL LONG | 2/3 periods | +6R in research
    #    v22.2L FIX: Was GAP_FILL (-4.30R, 39 trades).
    #    Stoch oversold bounces on NQ — catches dip buys.
    "US100": InstrumentSetup(
        symbol="US100", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=1.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.30, win_rate=0.333, profit_factor=1.60,
    ),
}


def get_long_symbols():
    return list(LONG_SETUP_CONFIG.keys())


def print_long_config():
    print("\nFORGE LONG CONFIG:")
    print(f"{'Symbol':10s} {'Strategy':20s} {'Dir':5s} {'Risk':>5s} {'SL':>4s} {'TP':>4s}")
    print("-" * 55)
    for sym, s in LONG_SETUP_CONFIG.items():
        print(f"{sym:10s} {s.strategy.value:20s} {s.direction.value:5s} "
              f"{s.risk_pct:4.1f}% {s.sl_atr:3.1f} {s.tp_atr:3.1f}")


if __name__ == "__main__":
    print_long_config()
