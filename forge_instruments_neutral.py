"""
FORGE v22.3 — NEUTRAL REGIME INSTRUMENT CONFIG (FIXED)
=======================================================
FIXES:
  OLD: All GAP_FILL → requires gaps that don't exist on hourly/daily forex data
  NEW: MEAN_REVERT, STOCH_REVERSAL, VWAP_REVERT as primaries with alt_strategies
  
  These are the SAME strategies that produced the March 30 winning streak
  ($1,344 in one session) before GENESIS switched to the broken NEUTRAL config.

KEY INSIGHT: Forex pairs spend 96-100% of time in NEUTRAL regime.
This config IS the system for most instruments most of the time.
It MUST produce signals or the system sits idle.

"All gas first then brakes." — Jorge Trujillo
"""

from forge_instruments_v22 import (
    InstrumentSetup, Strategy, Direction, TradeType, OrderType,
)
from typing import Dict


NEUTRAL_SETUP_CONFIG: Dict[str, InstrumentSetup] = {

    # ─── MEAN REVERT — dominant strategy in neutral/ranging markets ───
    # RSI extremes + BB breach. Works on hourly data. No gap required.

    # EURUSD — Mean Revert BOTH | Research: 62% WR, PF 2.83
    "EURUSD": InstrumentSetup(
        symbol="EURUSD", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=0.8, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.385, win_rate=0.46, profit_factor=1.37,
        alt_strategies=[Strategy.VWAP_REVERT, Strategy.STOCH_REVERSAL],
    ),

    # USDCHF — Mean Revert BOTH | Research: 55% WR, PF 2.07
    "USDCHF": InstrumentSetup(
        symbol="USDCHF", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.225, win_rate=0.55, profit_factor=2.07,
        alt_strategies=[Strategy.VWAP_REVERT, Strategy.STOCH_REVERSAL],
    ),

    # GBPUSD — Stoch Reversal BOTH | Research: 49% WR, PF 1.42
    "GBPUSD": InstrumentSetup(
        symbol="GBPUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=1.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.235, win_rate=0.49, profit_factor=1.42,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.VWAP_REVERT],
    ),

    # NZDUSD — Mean Revert BOTH | Research: 80% WR, PF 4.19
    "NZDUSD": InstrumentSetup(
        symbol="NZDUSD", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.555, win_rate=0.70, profit_factor=4.19,
        alt_strategies=[Strategy.STOCH_REVERSAL, Strategy.VWAP_REVERT],
    ),

    # ─── VWAP REVERT — works well in ranging/choppy conditions ───

    # EURGBP — VWAP Revert BOTH | Research: 46% WR, PF 1.14
    "EURGBP": InstrumentSetup(
        symbol="EURGBP", strategy=Strategy.VWAP_REVERT,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.307, win_rate=0.46, profit_factor=1.14,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.STOCH_REVERSAL],
    ),

    # XAUUSD — VWAP Revert BOTH | Research: 52% WR, PF 1.18
    "XAUUSD": InstrumentSetup(
        symbol="XAUUSD", strategy=Strategy.VWAP_REVERT,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=4.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.481, win_rate=0.52, profit_factor=1.18,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.STOCH_REVERSAL],
    ),

    # ─── STOCH REVERSAL — catches oversold/overbought in ranges ───

    # GBPJPY — Stoch Reversal BOTH | Research: 53% WR, PF 1.45
    "GBPJPY": InstrumentSetup(
        symbol="GBPJPY", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=0.8,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.404, win_rate=0.53, profit_factor=1.45,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.VWAP_REVERT],
    ),

    # USDJPY — Mean Revert BOTH | Research: 67% WR, PF 1.25
    "USDJPY": InstrumentSetup(
        symbol="USDJPY", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=0.8, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.333, win_rate=0.67, profit_factor=1.25,
        alt_strategies=[Strategy.STOCH_REVERSAL, Strategy.VWAP_REVERT],
    ),

    # ─── INDICES & COMMODITIES ───

    # US100 — Mean Revert BOTH | Research: 48% WR, PF 1.56
    "US100": InstrumentSetup(
        symbol="US100", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=2.0, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.425, win_rate=0.48, profit_factor=1.56,
        alt_strategies=[Strategy.EMA_BOUNCE, Strategy.STOCH_REVERSAL],
    ),

    # USOIL — VWAP Revert BOTH | Research: 44% WR, PF 1.06
    "USOIL": InstrumentSetup(
        symbol="USOIL", strategy=Strategy.VWAP_REVERT,
        direction=Direction.BOTH, sl_atr=1.0, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.308, win_rate=0.44, profit_factor=1.06,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.EMA_BOUNCE],
    ),

    # ─── CRYPTO ───

    # BTCUSD — Stoch Reversal BOTH | PF 1.35
    "BTCUSD": InstrumentSetup(
        symbol="BTCUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.BOTH, sl_atr=1.5, tp_atr=2.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.20, win_rate=0.50, profit_factor=1.35,
        alt_strategies=[Strategy.VWAP_REVERT],
    ),

    # ─── 3 NEW PAIRS ───

    # AUDNZD — Stoch Reversal BOTH
    "AUDNZD": InstrumentSetup(
        symbol="AUDNZD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.BOTH, sl_atr=1.5, tp_atr=1.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.50, win_rate=0.85, profit_factor=3.25,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.VWAP_REVERT],
    ),

    # AUDUSD — Mean Revert BOTH
    "AUDUSD": InstrumentSetup(
        symbol="AUDUSD", strategy=Strategy.MEAN_REVERT,
        direction=Direction.BOTH, sl_atr=1.5, tp_atr=1.5,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.30, win_rate=0.50, profit_factor=1.50,
        alt_strategies=[Strategy.STOCH_REVERSAL, Strategy.VWAP_REVERT],
    ),

    # EURJPY — Stoch Reversal BOTH
    "EURJPY": InstrumentSetup(
        symbol="EURJPY", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.BOTH, sl_atr=1.5, tp_atr=2.0,
        risk_pct=1.5, trade_type=TradeType.RUNNER, order_type=OrderType.MARKET,
        expectancy=0.30, win_rate=0.55, profit_factor=1.75,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.VWAP_REVERT],
    ),

    # ─── GER40/UK100/ETHUSD — keep for GENESIS if pairs become available ───

    "GER40": InstrumentSetup(
        symbol="GER40", strategy=Strategy.VWAP_REVERT,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=2.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.358, win_rate=0.47, profit_factor=1.27,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.STOCH_REVERSAL],
    ),

    "UK100": InstrumentSetup(
        symbol="UK100", strategy=Strategy.VWAP_REVERT,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=4.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.358, win_rate=0.47, profit_factor=1.19,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.STOCH_REVERSAL],
    ),

    "ETHUSD": InstrumentSetup(
        symbol="ETHUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=1.0,
        risk_pct=1.2, trade_type=TradeType.SCALP, order_type=OrderType.MARKET,
        expectancy=0.20, win_rate=0.257, profit_factor=1.65,
        alt_strategies=[Strategy.MEAN_REVERT, Strategy.VWAP_REVERT],
    ),
}


def get_neutral_symbols():
    return list(NEUTRAL_SETUP_CONFIG.keys())


def print_neutral_config():
    print("\nFORGE NEUTRAL CONFIG (FIXED — no more broken GAP_FILL):")
    print(f"{'Symbol':10s} {'Strategy':20s} {'Dir':5s} {'Risk':>5s} {'SL':>4s} {'TP':>4s} {'Alts':>20s}")
    print("-" * 75)
    for sym, s in NEUTRAL_SETUP_CONFIG.items():
        alts = ",".join(a.value[:6] for a in s.alt_strategies) if s.alt_strategies else "none"
        print(f"{sym:10s} {s.strategy.value:20s} {s.direction.value:5s} "
              f"{s.risk_pct:4.1f}% {s.sl_atr:3.1f} {s.tp_atr:3.1f} {alts:>20s}")


if __name__ == "__main__":
    print_neutral_config()
    print(f"\nTotal: {len(NEUTRAL_SETUP_CONFIG)} instruments")
    mr = sum(1 for s in NEUTRAL_SETUP_CONFIG.values() if s.strategy == Strategy.MEAN_REVERT)
    sr = sum(1 for s in NEUTRAL_SETUP_CONFIG.values() if s.strategy == Strategy.STOCH_REVERSAL)
    vr = sum(1 for s in NEUTRAL_SETUP_CONFIG.values() if s.strategy == Strategy.VWAP_REVERT)
    gf = sum(1 for s in NEUTRAL_SETUP_CONFIG.values() if s.strategy == Strategy.GAP_FILL)
    print(f"MEAN_REVERT: {mr} | STOCH_REVERSAL: {sr} | VWAP_REVERT: {vr} | GAP_FILL: {gf}")
    print(f"GAP_FILL count should be 0: {'✅' if gf == 0 else '❌ STILL BROKEN'}")
