"""
FORGE v22 — NEUTRAL REGIME INSTRUMENT CONFIG
==============================================
Built from NEUTRAL regime research (9 months, ADX < 20 / BB squeeze only).
Activated by GENESIS when an instrument is in choppy/ranging conditions.

KEY INSIGHT: Forex pairs spend 96-100% of time in NEUTRAL regime.
This config is effectively the PRIMARY config for most forex pairs.

GAP_FILL dominates — gaps fill in both directions when markets range.
STOCH_REVERSAL catches oversold/overbought bounces in ranges.

Both-direction setups are used where both sides are profitable.
"""

from forge_instruments_v22 import (
    InstrumentSetup, Strategy, Direction, TradeType, OrderType,
)
from typing import Dict


NEUTRAL_SETUP_CONFIG: Dict[str, InstrumentSetup] = {

    # ─── GAP_FILL BOTH — these instruments gap-fill in both directions ───

    # GER40 — GAP_FILL BOTH | +101R combined | LONG +50R SHORT +51R
    # Best neutral combo in entire research. 60%+ WR both sides.
    "GER40": InstrumentSetup(
        symbol="GER40", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.50, win_rate=0.608, profit_factor=3.30,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # USOIL — GAP_FILL BOTH | +98R combined | LONG +54R SHORT +44R
    # Oil gaps fill hard in range-bound markets. 69-77% WR.
    "USOIL": InstrumentSetup(
        symbol="USOIL", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.50, win_rate=0.735, profit_factor=5.13,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # US100 — GAP_FILL BOTH | +71R combined | LONG +32R SHORT +39R
    # NQ gaps fill beautifully in chop. 72-75% WR.
    "US100": InstrumentSetup(
        symbol="US100", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.45, win_rate=0.740, profit_factor=6.27,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # USDCHF — GAP_FILL BOTH | +52R combined | LONG +27R SHORT +25R
    # 77-83% WR on gap fills. PF 14+ on LONG side.
    "USDCHF": InstrumentSetup(
        symbol="USDCHF", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.50, win_rate=0.800, profit_factor=9.56,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # UK100 — GAP_FILL BOTH | +51R combined | LONG +35R SHORT +16R
    # Strong LONG bias on gap fills in chop. 71% WR LONG.
    "UK100": InstrumentSetup(
        symbol="UK100", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.40, win_rate=0.626, profit_factor=2.70,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # EURGBP — GAP_FILL BOTH | +43R combined | LONG +17R SHORT +25R
    # 84-88% WR. EURGBP is 100% NEUTRAL — this is its ONLY config.
    "EURGBP": InstrumentSetup(
        symbol="EURGBP", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.50, win_rate=0.862, profit_factor=12.62,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # GBPUSD — GAP_FILL BOTH | +30R combined | LONG +22R SHORT +8R
    # 65-77% WR. 98.4% of time in NEUTRAL.
    "GBPUSD": InstrumentSetup(
        symbol="GBPUSD", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.40, win_rate=0.714, profit_factor=5.43,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # GBPJPY — GAP_FILL BOTH | +29R combined | LONG +15R SHORT +14R
    # Balanced both sides. 59-75% WR.
    "GBPJPY": InstrumentSetup(
        symbol="GBPJPY", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.40, win_rate=0.661, profit_factor=3.73,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # USDJPY — GAP_FILL BOTH | +20R combined | LONG +12R SHORT +8R
    # 66-76% WR. PF 4-5.
    "USDJPY": InstrumentSetup(
        symbol="USDJPY", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.40, win_rate=0.719, profit_factor=4.79,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # NZDUSD — GAP_FILL BOTH | +18R combined | LONG +13R SHORT +5R
    # 69-71% WR. 96.5% of time in NEUTRAL.
    "NZDUSD": InstrumentSetup(
        symbol="NZDUSD", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.40, win_rate=0.700, profit_factor=4.70,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # XAUUSD — GAP_FILL BOTH | +12R combined | LONG +2R SHORT +9R
    # 66-90% WR but low volume. SHORT side stronger.
    "XAUUSD": InstrumentSetup(
        symbol="XAUUSD", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.35, win_rate=0.812, profit_factor=6.60,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # EURUSD — GAP_FILL BOTH | +11R combined | LONG +0.6R SHORT +11R
    # 37-84% WR. SHORT much stronger. 98% NEUTRAL.
    "EURUSD": InstrumentSetup(
        symbol="EURUSD", strategy=Strategy.GAP_FILL,
        direction=Direction.BOTH, sl_atr=2.5, tp_atr=3.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.35, win_rate=0.619, profit_factor=3.90,
        long_sl=2.5, long_tp=3.0, short_sl=2.5, short_tp=3.0,
    ),

    # ─── STOCH_REVERSAL — crypto works better with stoch in neutral ───

    # BTCUSD — STOCH_REVERSAL BOTH | +17R combined | LONG +9R SHORT +8R
    # Catches overbought/oversold in BTC ranges. 50% NEUTRAL.
    "BTCUSD": InstrumentSetup(
        symbol="BTCUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.BOTH, sl_atr=0.5, tp_atr=1.0,
        risk_pct=1.5, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.20, win_rate=0.199, profit_factor=1.35,
        long_sl=0.5, long_tp=1.0, short_sl=0.5, short_tp=1.0,
    ),

    # ETHUSD — STOCH_REVERSAL LONG | +15R | 25.7% WR
    # LONG only in neutral — oversold bounces in ETH ranges.
    "ETHUSD": InstrumentSetup(
        symbol="ETHUSD", strategy=Strategy.STOCH_REVERSAL,
        direction=Direction.LONG, sl_atr=0.5, tp_atr=1.0,
        risk_pct=1.2, trade_type=TradeType.SCALP, order_type=OrderType.LIMIT,
        expectancy=0.20, win_rate=0.257, profit_factor=1.65,
    ),
}


def get_neutral_symbols():
    return list(NEUTRAL_SETUP_CONFIG.keys())


def print_neutral_config():
    print("\nFORGE NEUTRAL CONFIG:")
    print(f"{'Symbol':10s} {'Strategy':20s} {'Dir':5s} {'Risk':>5s} {'SL':>4s} {'TP':>4s}")
    print("-" * 55)
    for sym, s in NEUTRAL_SETUP_CONFIG.items():
        print(f"{sym:10s} {s.strategy.value:20s} {s.direction.value:5s} "
              f"{s.risk_pct:4.1f}% {s.sl_atr:3.1f} {s.tp_atr:3.1f}")


if __name__ == "__main__":
    print_neutral_config()
    print(f"\nTotal: {len(NEUTRAL_SETUP_CONFIG)} instruments")
    gap = sum(1 for s in NEUTRAL_SETUP_CONFIG.values() if s.strategy == Strategy.GAP_FILL)
    stoch = sum(1 for s in NEUTRAL_SETUP_CONFIG.values() if s.strategy == Strategy.STOCH_REVERSAL)
    both = sum(1 for s in NEUTRAL_SETUP_CONFIG.values() if s.direction == Direction.BOTH)
    print(f"GAP_FILL: {gap} | STOCH_REVERSAL: {stoch}")
    print(f"BOTH direction: {both} | Single direction: {len(NEUTRAL_SETUP_CONFIG) - both}")
