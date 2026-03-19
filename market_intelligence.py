"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   market_intelligence.py — Layer 3                          ║
║  FORGE-76: Real-Time VaR Monitor                                            ║
║  FORGE-77: Adverse Selection Detection                                      ║
║  FORGE-78: Regime Transition Guard                                          ║
║  FORGE-80: Market Noise vs Signal Filter                                    ║
║  FORGE-81: VWAP Execution Algorithm                                         ║
║  FORGE-88: Liquidity Vacuum Detection (C-19 Level 2 risk)                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.market_intelligence")


# ── FORGE-76: Real-Time Value at Risk Monitor ─────────────────────────────────

@dataclass
class VaRResult:
    """Value at Risk for the current portfolio."""
    var_95_pct:         float    # 95% VaR in dollars
    var_99_pct:         float    # 99% VaR
    expected_shortfall: float    # CVaR — expected loss beyond VaR
    exceeds_limit:      bool
    limit_dollars:      float
    recommendation:     str

def calculate_var(
    portfolio_pnl_history:  list[float],   # Recent P&L observations
    position_size:          float,
    account_equity:         float,
    var_limit_pct:          float = 0.02,  # 2% of account
) -> VaRResult:
    """FORGE-76: Historical VaR calculation."""
    if len(portfolio_pnl_history) < 20:
        return VaRResult(0.0, 0.0, 0.0, False, account_equity * var_limit_pct,
                         "Need 20+ observations for VaR calculation.")

    sorted_pnl = sorted(portfolio_pnl_history)
    n = len(sorted_pnl)
    var_95_idx  = int(n * 0.05)
    var_99_idx  = int(n * 0.01)

    var_95  = abs(sorted_pnl[max(0, var_95_idx)])
    var_99  = abs(sorted_pnl[max(0, var_99_idx)])
    es      = abs(sum(sorted_pnl[:max(1, var_95_idx)]) / max(1, var_95_idx))

    limit   = account_equity * var_limit_pct
    exceeds = var_95 > limit

    rec = (f"95% VaR: ${var_95:.2f} | 99% VaR: ${var_99:.2f} | "
           f"CVaR: ${es:.2f}. Limit: ${limit:.2f}. "
           + ("✓ Within limits." if not exceeds else "⚠ EXCEEDS LIMIT. Reduce exposure."))

    return VaRResult(
        var_95_pct=round(var_95, 2), var_99_pct=round(var_99, 2),
        expected_shortfall=round(es, 2), exceeds_limit=exceeds,
        limit_dollars=round(limit, 2), recommendation=rec,
    )


# ── FORGE-77: Adverse Selection Detection ────────────────────────────────────
# Detects if we're consistently entering at bad prices (being picked off).

@dataclass
class AdverseSelectionCheck:
    is_adverse:         bool
    adverse_ratio:      float   # Fraction of trades that immediately moved against us
    fill_quality_score: float   # 0–10 (higher = better fills)
    recommendation:     str

def detect_adverse_selection(
    immediate_adverse_moves:    int,   # Trades where price moved against us in first 5 min
    total_trades:               int,
    avg_slippage_pct:           float,  # Average execution slippage
) -> AdverseSelectionCheck:
    """FORGE-77: Detect if market is picking us off at entry."""
    if total_trades < 10:
        return AdverseSelectionCheck(False, 0.0, 7.0, "Insufficient data.")

    adverse_ratio = immediate_adverse_moves / total_trades
    slippage_score = max(0.0, 10.0 - avg_slippage_pct * 1000)   # 0.001 slip → 9.0 score
    adverse_score = max(0.0, 10.0 - adverse_ratio * 10)

    fill_quality = (adverse_score + slippage_score) / 2
    is_adverse = adverse_ratio > 0.50 or avg_slippage_pct > 0.005

    rec = (f"Adverse ratio: {adverse_ratio:.0%}. Slippage: {avg_slippage_pct:.3%}. "
           f"Fill quality: {fill_quality:.1f}/10. "
           + ("✅ Normal execution." if not is_adverse
              else "⚠ Adverse selection detected. Switch execution time or price."))

    return AdverseSelectionCheck(
        is_adverse=is_adverse,
        adverse_ratio=round(adverse_ratio, 4),
        fill_quality_score=round(fill_quality, 2),
        recommendation=rec,
    )


# ── FORGE-78: Regime Transition Guard ────────────────────────────────────────
# Don't enter a new trade when the market is switching regimes.

class RegimeTransitionRisk(Enum):
    LOW      = auto()   # Stable regime
    MODERATE = auto()   # Minor regime signals changing
    HIGH     = auto()   # Active regime transition — avoid entries

@dataclass
class RegimeTransitionCheck:
    risk_level:         RegimeTransitionRisk
    can_enter:          bool
    confidence:         float   # 0–1: confidence in current regime
    vix_changing:       bool
    gex_flipping:       bool
    breadth_diverging:  bool
    recommendation:     str

def check_regime_transition(
    vix_change_pct:     float,  # VIX % change in last 30 min
    gex_direction_changed: bool,
    breadth_divergence: float,  # Abs(advance_decline - 0.5) — deviations from balance
    vix_level:          float,
) -> RegimeTransitionCheck:
    """FORGE-78: Detect active regime transitions — avoid entries during."""
    signals = []

    vix_changing = abs(vix_change_pct) > 0.05   # 5%+ VIX move in 30 min
    if vix_changing:
        signals.append("VIX moving rapidly")

    if gex_direction_changed:
        signals.append("GEX direction flipped")

    breadth_div = breadth_divergence > 0.20
    if breadth_div:
        signals.append("Breadth divergence")

    signal_count = len(signals)
    confidence   = max(0.0, 1.0 - signal_count * 0.30)

    if signal_count >= 2:
        risk = RegimeTransitionRisk.HIGH
        can_enter = False
        rec = f"⚠ Regime transition HIGH: {signals}. Skip entry — wait for stabilization."
    elif signal_count == 1:
        risk = RegimeTransitionRisk.MODERATE
        can_enter = True
        rec = f"🟡 Transition MODERATE: {signals}. Reduce size if entering."
    else:
        risk = RegimeTransitionRisk.LOW
        can_enter = True
        rec = "✅ Regime stable. Entry conditions clear."

    return RegimeTransitionCheck(
        risk_level=risk, can_enter=can_enter, confidence=round(confidence, 2),
        vix_changing=vix_changing, gex_flipping=gex_direction_changed,
        breadth_diverging=breadth_div, recommendation=rec,
    )


# ── FORGE-80: Market Noise vs Signal Filter ───────────────────────────────────
# Distinguish real institutional signals from random noise.

@dataclass
class NoiseSignalResult:
    signal_quality:     float    # 0–1: higher = cleaner signal
    is_signal:          bool     # True if likely institutional signal
    noise_indicators:   list[str]
    recommendation:     str

def filter_noise_vs_signal(
    volume_surge:       float,   # Volume / avg volume (1.0 = normal)
    price_move_pct:     float,   # % move in last 5 min
    bid_ask_spread_pct: float,   # Current spread as % of price
    time_of_day_score:  float,   # 0–1: session quality of current time
) -> NoiseSignalResult:
    """FORGE-80: Filter market noise from institutional signals."""
    noise_indicators = []
    quality = 1.0

    # Low volume = noise
    if volume_surge < 1.2:
        quality -= 0.25
        noise_indicators.append(f"Low volume ({volume_surge:.1f}× avg)")

    # Small move with no volume = noise
    if price_move_pct > 0 and price_move_pct < 0.002 and volume_surge < 1.3:
        quality -= 0.20
        noise_indicators.append("Small price move without volume")

    # Wide spread = noise / manipulation
    if bid_ask_spread_pct > 0.003:
        quality -= 0.20
        noise_indicators.append(f"Wide spread ({bid_ask_spread_pct:.3%})")

    # Poor session time = likely noise
    if time_of_day_score < 0.50:
        quality -= 0.15
        noise_indicators.append("Poor session time for institutional activity")

    quality = max(0.0, min(1.0, quality))
    is_signal = quality >= 0.65

    rec = (f"Signal quality: {quality:.0%}. "
           + ("✅ Institutional signal detected." if is_signal
              else f"🔇 Likely noise: {', '.join(noise_indicators) if noise_indicators else 'insufficient confluence'}."))

    return NoiseSignalResult(
        signal_quality=round(quality, 4),
        is_signal=is_signal,
        noise_indicators=noise_indicators,
        recommendation=rec,
    )


# ── FORGE-81: VWAP Execution Algorithm ───────────────────────────────────────
# Execute entries near VWAP for best R:R.

@dataclass
class VWAPExecutionPlan:
    """Optimal execution relative to VWAP."""
    execute_now:        bool
    optimal_entry:      float
    current_distance_pct: float  # How far from VWAP
    entry_style:        str      # "IMMEDIATE" / "LIMIT_AT_VWAP" / "WAIT_FOR_RETEST"
    limit_price:        float
    recommendation:     str

def plan_vwap_execution(
    current_price:  float,
    vwap:           float,
    direction:      str,    # "long" / "short"
    atr:            float,
) -> VWAPExecutionPlan:
    """FORGE-81: Plan VWAP-relative execution."""
    distance_pct = abs(current_price - vwap) / vwap
    distance_atr = abs(current_price - vwap) / atr if atr > 0 else 0.0

    if distance_atr <= 0.3:
        # Near VWAP — execute immediately
        return VWAPExecutionPlan(
            execute_now=True, optimal_entry=current_price,
            current_distance_pct=distance_pct,
            entry_style="IMMEDIATE", limit_price=current_price,
            recommendation=f"Near VWAP ({distance_atr:.2f} ATR). Execute at market."
        )
    elif distance_atr <= 1.0:
        # Within 1 ATR — place limit at VWAP
        limit = vwap + (atr * 0.1) if direction == "long" else vwap - (atr * 0.1)
        return VWAPExecutionPlan(
            execute_now=False, optimal_entry=limit,
            current_distance_pct=distance_pct,
            entry_style="LIMIT_AT_VWAP", limit_price=round(limit, 4),
            recommendation=f"Set limit at VWAP ({vwap:.2f}) ± 0.1 ATR."
        )
    else:
        # Too far — wait for VWAP retest
        return VWAPExecutionPlan(
            execute_now=False, optimal_entry=vwap,
            current_distance_pct=distance_pct,
            entry_style="WAIT_FOR_RETEST", limit_price=vwap,
            recommendation=f"Price {distance_atr:.1f} ATR from VWAP. Wait for retest."
        )


# ── FORGE-88: Liquidity Vacuum Detection (C-19 Level 2) ──────────────────────
# Gaps in the order book = price can fall/rise rapidly with no real fills.

@dataclass
class LiquidityCheck:
    has_vacuum:             bool
    bid_depth_thin:         bool
    ask_depth_thin:         bool
    estimated_slippage_pct: float
    safe_to_trade:          bool
    recommendation:         str

def detect_liquidity_vacuum(
    bid_depth_lots:     float,   # Total size at bid levels
    ask_depth_lots:     float,   # Total size at ask levels
    typical_depth:      float,   # Normal market depth (lots)
    avg_daily_volume:   float,
    current_volume:     float,
) -> LiquidityCheck:
    """FORGE-88: Detect liquidity vacuum before entering a trade (C-19 Level 2)."""
    thin_threshold = typical_depth * 0.40   # < 40% of normal = thin

    bid_thin  = bid_depth_lots < thin_threshold
    ask_thin  = ask_depth_lots < thin_threshold
    has_vacuum = bid_thin or ask_thin

    # Estimate slippage from depth
    available = min(bid_depth_lots, ask_depth_lots)
    if available > 0 and typical_depth > 0:
        est_slip = max(0.0, (1.0 - available / typical_depth) * 0.002)
    else:
        est_slip = 0.002   # Conservative default

    # Volume check: low volume amplifies vacuum risk
    volume_ok = current_volume >= avg_daily_volume * 0.3 if avg_daily_volume > 0 else True
    safe = not has_vacuum and volume_ok

    if has_vacuum:
        side = "bid" if bid_thin else "ask"
        rec = (f"⚠ Liquidity vacuum on {side} side. "
               f"Depth: bid={bid_depth_lots:.1f} ask={ask_depth_lots:.1f} "
               f"(normal: {typical_depth:.1f}). "
               f"Est. slippage: {est_slip:.3%}. Consider waiting.")
    else:
        rec = f"✅ Normal liquidity. Bid: {bid_depth_lots:.1f}, Ask: {ask_depth_lots:.1f}."

    return LiquidityCheck(
        has_vacuum=has_vacuum,
        bid_depth_thin=bid_thin, ask_depth_thin=ask_thin,
        estimated_slippage_pct=round(est_slip, 4),
        safe_to_trade=safe, recommendation=rec,
    )
