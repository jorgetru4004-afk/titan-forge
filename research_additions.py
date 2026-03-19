"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   research_additions.py — Layer 3                           ║
║                                                                              ║
║  30 RESEARCH ADDITIONS — FORGE-122 to FORGE-151                             ║
║  "Intelligence that multiplies across all four phases"                      ║
║                                                                              ║
║  FORGE-122: Spread Monitor                                                  ║
║  FORGE-123: Slippage Predictor                                              ║
║  FORGE-124: Partial Fill Intelligence                                       ║
║  FORGE-125: Order Type Optimizer                                            ║
║  FORGE-126: Return on Drawdown Budget                                       ║
║  FORGE-127: Evaluation Fee ROI Calculator                                   ║
║  FORGE-128: Capital Recycling Engine                                        ║
║  FORGE-129: Flash Crash Detection Protocol       (also in emergency_overrides)║
║  FORGE-130: Weekend Gap Risk Quantifier                                     ║
║  FORGE-131: Correlation Spike Emergency Protocol (also in emergency_overrides)║
║  FORGE-132: Platform Latency Monitor                                        ║
║  FORGE-133: Account Warming Protocol                                        ║
║  FORGE-134: Prop Firm Promotion Scanner                                     ║
║  FORGE-135: Multi-Account Trade Fingerprint Variation                       ║
║  FORGE-136: Instant Funding vs Evaluation ROI Calculator                   ║
║  FORGE-137: Setup Performance Database                                      ║
║  FORGE-138: Hot Hand Protocol                                               ║
║  FORGE-139: Edge Decay Detector                                             ║
║  FORGE-140: Time-of-Day Performance Atlas                                   ║
║  FORGE-141: Firm Discount Code Database                                     ║
║  FORGE-142: New Firm Early Mover Intelligence                               ║
║  FORGE-143: Firm-Specific Instrument Rotation                               ║
║  FORGE-144: Patience Score                                                  ║
║  FORGE-145: End of Month Positioning Intelligence                           ║
║  FORGE-146: Liquidity Session Optimizer                                     ║
║  FORGE-147: Benchmark Day Manufacture Protocol                              ║
║  FORGE-148: Win Streak Preservation Protocol                                ║
║  FORGE-149: Evaluation Insurance Position                                   ║
║  FORGE-150: Seasonal Edge Calendar                                          ║
║  FORGE-151: Live Return Attribution Engine                                  ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 2026                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from firm_rules import FirmID

logger = logging.getLogger("titan_forge.research_additions")


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-122: SPREAD MONITOR
# "Refuses to enter when spreads exceed 150% of normal."
# ─────────────────────────────────────────────────────────────────────────────

# Normal spreads per instrument (in price units)
NORMAL_SPREADS: dict[str, float] = {
    "EURUSD": 0.00012, "GBPUSD": 0.00015, "USDJPY": 0.013,
    "ES": 0.25, "NQ": 0.25, "RTY": 0.10,
    "SPY": 0.02, "QQQ": 0.03,
    "US500": 0.50, "US30": 3.0, "US100": 1.0,
    "GC": 0.30, "CL": 0.03,
}
SPREAD_MAX_MULTIPLIER: float = 1.50  # Document: 150% of normal

@dataclass
class SpreadCheckResult:
    instrument:     str
    current_spread: float
    normal_spread:  float
    spread_ratio:   float
    entry_allowed:  bool
    reason:         str

def check_spread(instrument: str, current_spread: float) -> SpreadCheckResult:
    """FORGE-122: Refuse entry when spread > 150% of normal."""
    normal = NORMAL_SPREADS.get(instrument.upper(), current_spread)
    ratio  = current_spread / normal if normal > 0 else 1.0
    allowed = ratio <= SPREAD_MAX_MULTIPLIER

    return SpreadCheckResult(
        instrument=instrument, current_spread=current_spread,
        normal_spread=normal, spread_ratio=round(ratio, 3),
        entry_allowed=allowed,
        reason=(f"Spread {ratio:.1f}x normal — entry {'allowed' if allowed else 'BLOCKED (>150%)'}."
                f" {current_spread:.5f} vs normal {normal:.5f}.")
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-123: SLIPPAGE PREDICTOR
# "If predicted slippage > 0.3% of position value: wait for better conditions."
# ─────────────────────────────────────────────────────────────────────────────

SLIPPAGE_MAX_PCT: float = 0.003  # 0.3% of position value

@dataclass
class SlippagePrediction:
    instrument:         str
    predicted_slippage: float   # In price units
    pct_of_position:    float   # As fraction of price
    entry_allowed:      bool
    factors:            dict
    reason:             str

def predict_slippage(
    instrument:     str,
    price:          float,
    position_size:  float,
    hour_et:        int,         # Hour 0–23 ET
    current_atr:    float,
    avg_daily_vol:  float,
    current_vol:    float,
) -> SlippagePrediction:
    """
    FORGE-123: Predict slippage before entry.
    Factors: time of day, volatility, position size vs liquidity, recent fills.
    """
    base_slip = current_atr * 0.015   # 1.5% of ATR as base
    factors: dict[str, float] = {}

    # Time of day: low-liquidity hours worse
    if hour_et in (0,1,2,3,4,5,6,7):
        mult = 2.5; factors["off_hours"] = 2.5
    elif hour_et in (12,13):
        mult = 1.3; factors["midday"] = 1.3
    else:
        mult = 1.0; factors["peak_hours"] = 1.0

    # Volatility spike
    vol_ratio = current_vol / avg_daily_vol if avg_daily_vol > 0 else 1.0
    if vol_ratio > 2.0:
        mult *= 1.8; factors["high_vol"] = 1.8

    # Large position relative to liquidity
    if position_size > 5.0:
        mult *= 1.2 + (position_size - 5) * 0.02
        factors["large_size"] = mult

    predicted = base_slip * mult
    pct = predicted / price if price > 0 else 0.0
    allowed = pct <= SLIPPAGE_MAX_PCT

    return SlippagePrediction(
        instrument=instrument,
        predicted_slippage=round(predicted, 6),
        pct_of_position=round(pct, 6),
        entry_allowed=allowed,
        factors=factors,
        reason=(
            f"Predicted slippage {pct*100:.3f}% "
            f"({'OK' if allowed else 'BLOCKED >0.3%'}). "
            f"Factors: {list(factors.keys())}."
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-124: PARTIAL FILL INTELLIGENCE
# "Every partial fill triggers immediate reassessment of intended position."
# ─────────────────────────────────────────────────────────────────────────────

class PartialFillAction:
    HOLD_AND_ADD   = "hold_and_add"
    CLOSE_CLEANLY  = "close_cleanly"
    ACCEPT_REDUCED = "accept_reduced"

@dataclass
class PartialFillDecision:
    intended_size:  float
    filled_size:    float
    fill_pct:       float
    action:         str
    reason:         str

def handle_partial_fill(
    intended_size:      float,
    filled_size:        float,
    conditions_improving: bool,
    setup_still_valid:  bool,
    time_sensitive:     bool,
) -> PartialFillDecision:
    """
    FORGE-124: Partial fill intelligence.
    Never have an accidental position size from execution friction.
    """
    fill_pct = filled_size / intended_size if intended_size > 0 else 0.0

    if fill_pct >= 0.90:
        action = PartialFillAction.ACCEPT_REDUCED
        reason = f"{fill_pct:.0%} filled — accept reduced size as full position."
    elif conditions_improving and setup_still_valid and not time_sensitive:
        action = PartialFillAction.HOLD_AND_ADD
        reason = f"{fill_pct:.0%} filled. Conditions improving — hold partial, add when fills improve."
    elif not setup_still_valid or time_sensitive:
        action = PartialFillAction.CLOSE_CLEANLY
        reason = f"{fill_pct:.0%} filled but setup no longer valid or time-sensitive. Close partial cleanly."
    else:
        action = PartialFillAction.ACCEPT_REDUCED
        reason = f"{fill_pct:.0%} filled — accept reduced size."

    return PartialFillDecision(
        intended_size=intended_size, filled_size=filled_size,
        fill_pct=round(fill_pct, 4), action=action, reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-125: ORDER TYPE OPTIMIZER
# "Market: guarantees fill. Limit: guarantees price. Selects correctly."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OrderTypeDecision:
    order_type:     str   # "market" or "limit"
    limit_price:    Optional[float]
    rationale:      str

def optimize_order_type(
    current_spread:     float,
    normal_spread:      float,
    is_momentum_setup:  bool,   # Momentum = market (can't miss)
    is_mean_rev_setup:  bool,   # Mean rev = limit (price matters)
    urgency:            float,  # 0–1: 1 = must get in now
    current_price:      float,
    atr:                float,
) -> OrderTypeDecision:
    """
    FORGE-125: Select optimal order type per context.
    """
    spread_ratio = current_spread / normal_spread if normal_spread > 0 else 1.0

    # High urgency or momentum: always market
    if urgency >= 0.80 or is_momentum_setup:
        return OrderTypeDecision("market", None,
            "High urgency/momentum — market order. Fill > price.")

    # Wide spread: limit order to avoid paying the spread
    if spread_ratio > 1.3:
        limit = current_price - current_spread * 0.5
        return OrderTypeDecision("limit", round(limit, 5),
            f"Wide spread ({spread_ratio:.1f}x). Limit order at {limit:.5f}.")

    # Mean reversion: limit — price matters
    if is_mean_rev_setup:
        limit = current_price + atr * 0.05
        return OrderTypeDecision("limit", round(limit, 5),
            f"Mean reversion setup. Limit at {limit:.5f} — price matters.")

    return OrderTypeDecision("market", None, "Normal conditions — market order.")


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-126: RETURN ON DRAWDOWN BUDGET
# "Return on drawdown budget consumed — not return on account balance."
# "10% profit using 2% of 10% drawdown = 500% RODD."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RODDResult:
    """Return on Drawdown Budget — the true efficiency metric."""
    profit_achieved:    float
    profit_target:      float
    drawdown_used_pct:  float   # % of total drawdown budget consumed
    drawdown_budget:    float
    rodd:               float   # Return on Drawdown Deployed
    efficiency_grade:   str     # "A+" through "F"
    interpretation:     str

def calculate_rodd(
    profit_achieved:    float,
    profit_target:      float,
    drawdown_used:      float,   # Dollars of drawdown consumed
    total_drawdown:     float,   # Total drawdown budget
) -> RODDResult:
    """
    FORGE-126: RODD = profit achieved / drawdown consumed.
    Document example: 10% profit using 2% of 10% DD = 500% RODD.
    """
    if drawdown_used <= 0:
        rodd = float("inf")
    else:
        rodd = profit_achieved / drawdown_used

    dd_pct = drawdown_used / total_drawdown if total_drawdown > 0 else 0.0

    if rodd >= 5.0:     grade = "A+"
    elif rodd >= 3.0:   grade = "A"
    elif rodd >= 2.0:   grade = "B"
    elif rodd >= 1.0:   grade = "C"
    else:               grade = "F"

    interp = (
        f"Earned ${profit_achieved:,.0f} using {dd_pct:.1%} of drawdown budget. "
        f"RODD: {rodd:.1f}x. Grade: {grade}. "
        f"{'Elite efficiency.' if grade == 'A+' else 'Optimize to use less drawdown per dollar earned.'}"
    )

    return RODDResult(
        profit_achieved=profit_achieved, profit_target=profit_target,
        drawdown_used_pct=round(dd_pct, 4), drawdown_budget=total_drawdown,
        rodd=round(rodd, 2), efficiency_grade=grade, interpretation=interp,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-127: EVALUATION FEE ROI CALCULATOR
# "Only purchases evaluations where expected ROI exceeds minimum threshold."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvalROIResult:
    firm_id:                str
    account_size:           float
    eval_fee:               float
    historical_pass_rate:   float
    expected_monthly_payout: float
    expected_lifecycle_months: int
    total_expected_payout:  float
    expected_roi_pct:       float
    meets_threshold:        bool
    recommendation:         str
    MIN_ROI_THRESHOLD_PCT: float = 500.0  # 500% minimum ROI required

def calculate_eval_roi(
    firm_id:                str,
    account_size:           float,
    eval_fee:               float,
    historical_pass_rate:   float,  # 0–1
    expected_monthly_payout: float,
    lifecycle_months:       int,
) -> EvalROIResult:
    """
    FORGE-127: Expected ROI calculation before buying any evaluation.
    Includes: pass rate, expected payout stream, time to pass.
    """
    # Risk-adjusted expected payout
    expected_total = historical_pass_rate * expected_monthly_payout * lifecycle_months
    roi = ((expected_total - eval_fee) / eval_fee) * 100 if eval_fee > 0 else 0.0
    meets = roi >= 500.0

    rec = (
        f"{'✅ BUY' if meets else '❌ SKIP'}: {firm_id} ${account_size:,.0f} "
        f"eval (${eval_fee:.0f}). "
        f"Expected ROI: {roi:.0f}% "
        f"({'≥500% threshold met' if meets else '<500% threshold — skip'})."
    )

    return EvalROIResult(
        firm_id=firm_id, account_size=account_size, eval_fee=eval_fee,
        historical_pass_rate=historical_pass_rate,
        expected_monthly_payout=expected_monthly_payout,
        expected_lifecycle_months=lifecycle_months,
        total_expected_payout=round(expected_total, 2),
        expected_roi_pct=round(roi, 1),
        meets_threshold=meets, recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-128: CAPITAL RECYCLING ENGINE
# "Capital acquisition pipeline never stops. Zero gaps between funded generations."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RecycleDecision:
    retiring_account_id:    str
    recycled_capital:       float
    next_eval_firm:         str
    next_eval_size:         float
    next_eval_fee:          float
    days_gap:               int   # Document: should be 0
    recommendation:         str

def trigger_capital_recycle(
    retiring_account_id:    str,
    payout_received:        float,
    available_capital:      float,
    preferred_next_firm:    str = FirmID.FTMO,
) -> RecycleDecision:
    """
    FORGE-128: Capital Recycling Engine.
    "Idle capital between funded account generations is waste."
    Immediately recycles capital into next evaluation.
    """
    # FTMO $100K warmup: $540 fee
    fee_map = {FirmID.FTMO: 540.0, FirmID.APEX: 147.0, FirmID.DNA_FUNDED: 99.0}
    size_map = {FirmID.FTMO: 100_000.0, FirmID.APEX: 50_000.0, FirmID.DNA_FUNDED: 25_000.0}

    fee  = fee_map.get(preferred_next_firm, 540.0)
    size = size_map.get(preferred_next_firm, 100_000.0)
    recycled = payout_received

    if available_capital >= fee:
        rec = (
            f"🔄 RECYCLE: {retiring_account_id} retiring. "
            f"${recycled:,.0f} received. "
            f"Immediately starting new {preferred_next_firm} ${size:,.0f} eval (${fee:.0f}). "
            f"Zero days gap in pipeline."
        )
    else:
        rec = (
            f"⚠ Cannot recycle immediately — insufficient capital (${available_capital:,.0f} < ${fee:.0f}). "
            f"Save additional ${fee - available_capital:,.0f} then start next eval."
        )

    return RecycleDecision(
        retiring_account_id=retiring_account_id,
        recycled_capital=recycled,
        next_eval_firm=preferred_next_firm,
        next_eval_size=size,
        next_eval_fee=fee,
        days_gap=0,
        recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-129: FLASH CRASH DETECTION PROTOCOL
# "3%+ in under 60 seconds = flash crash. Close all positions immediately."
# (Full implementation in emergency_overrides.py — this is the FORGE number anchor)
# ─────────────────────────────────────────────────────────────────────────────

FORGE129_FLASH_CRASH_THRESHOLD_PCT: float = 0.03   # 3% in under 60 seconds
FORGE129_FLASH_CRASH_WINDOW_SECONDS: int  = 60

def check_forge129_flash_crash(
    price_now:      float,
    price_60s_ago:  float,
) -> bool:
    """
    FORGE-129: Flash Crash Detection.
    3%+ move in 60 seconds = flash crash. Close all, no exceptions.
    Full implementation: emergency_overrides.py.
    """
    if price_60s_ago <= 0:
        return False
    move = abs(price_now - price_60s_ago) / price_60s_ago
    is_crash = move >= FORGE129_FLASH_CRASH_THRESHOLD_PCT
    if is_crash:
        logger.critical("[FORGE-129] 🚨 FLASH CRASH: %.2f%% in 60s. CLOSE ALL.", move * 100)
    return is_crash


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-130: WEEKEND GAP RISK QUANTIFIER
# "For firms allowing weekend holds: calculates specific weekend gap risk."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WeekendGapRisk:
    instrument:             str
    geopolitical_tension:   str    # "low" / "medium" / "high"
    weekend_events:         list[str]
    historical_avg_gap_pct: float
    current_gap_risk_pct:   float  # Estimated max gap this weekend
    max_allowed_position:   float  # Fraction of normal size
    should_hold:            bool
    reason:                 str

def quantify_weekend_gap_risk(
    instrument:             str,
    geopolitical_tension:   str,       # "low" / "medium" / "high"
    weekend_events:         list[str], # e.g. ["Fed speech Sunday", "G7 meeting"]
    historical_avg_gap_pct: float,     # Historical avg weekend gap
    normal_position_size:   float,
) -> WeekendGapRisk:
    """
    FORGE-130: Weekend Gap Risk Quantifier.
    Output: max acceptable overnight position given current weekend risk.
    """
    tension_mult = {"low": 1.0, "medium": 1.5, "high": 2.5}.get(geopolitical_tension, 1.5)
    event_mult   = 1.0 + len(weekend_events) * 0.3

    estimated_gap = historical_avg_gap_pct * tension_mult * event_mult
    # Max position: inversely proportional to gap risk
    max_pos_fraction = max(0.10, min(1.0, 0.02 / estimated_gap)) if estimated_gap > 0 else 1.0
    max_position = normal_position_size * max_pos_fraction

    should_hold = max_pos_fraction >= 0.30  # Only hold if can take meaningful position

    reason = (
        f"Weekend gap risk: est. {estimated_gap:.2%}. "
        f"Tension: {geopolitical_tension}. Events: {weekend_events or ['none']}. "
        f"Max position: {max_pos_fraction:.0%} of normal ({max_position:.2f} units). "
        f"{'Hold allowed.' if should_hold else 'Gap risk too high — close before weekend.'}"
    )

    return WeekendGapRisk(
        instrument=instrument, geopolitical_tension=geopolitical_tension,
        weekend_events=weekend_events, historical_avg_gap_pct=historical_avg_gap_pct,
        current_gap_risk_pct=round(estimated_gap, 4),
        max_allowed_position=round(max_position, 4),
        should_hold=should_hold, reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-131: CORRELATION SPIKE EMERGENCY PROTOCOL
# "Correlation spike can fail three evaluations in one session."
# (Full implementation in emergency_overrides.py)
# ─────────────────────────────────────────────────────────────────────────────

FORGE131_CORRELATION_SPIKE_THRESHOLD: float = 0.95  # All correlations approaching 1.0

def check_forge131_correlation_spike(correlations: list[float]) -> bool:
    """
    FORGE-131: Correlation Spike Emergency.
    When ALL correlations spike toward 1.0 = crisis. Reduce to minimum size.
    Full implementation: emergency_overrides.py.
    """
    if not correlations:
        return False
    avg_corr = sum(correlations) / len(correlations)
    is_spike  = avg_corr >= FORGE131_CORRELATION_SPIKE_THRESHOLD
    if is_spike:
        logger.critical("[FORGE-131] 🚨 CORRELATION SPIKE: avg %.3f. Reduce all positions.", avg_corr)
    return is_spike


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-132: PLATFORM LATENCY MONITOR
# "When latency > 500ms: new entries pause until latency normalizes."
# ─────────────────────────────────────────────────────────────────────────────

LATENCY_PAUSE_THRESHOLD_MS: int = 500

@dataclass
class LatencyStatus:
    latency_ms:         float
    is_acceptable:      bool
    entry_blocked:      bool
    recommendation:     str

def check_platform_latency(latency_ms: float) -> LatencyStatus:
    """
    FORGE-132: Platform Latency Monitor.
    "Not a bad trade — infrastructure failure producing evaluation risk."
    """
    acceptable = latency_ms < LATENCY_PAUSE_THRESHOLD_MS
    return LatencyStatus(
        latency_ms=latency_ms,
        is_acceptable=acceptable,
        entry_blocked=not acceptable,
        recommendation=(
            f"Latency {latency_ms:.0f}ms — {'normal' if acceptable else '⛔ ENTRIES PAUSED (>500ms)'}. "
            + (f"Wait for latency to normalize before new entries." if not acceptable else "")
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-133: ACCOUNT WARMING PROTOCOL
# "Minimum position sizes for first 5 trading days."
# ─────────────────────────────────────────────────────────────────────────────

WARMING_PERIOD_DAYS:    int   = 5
WARMING_SIZE_FRACTION:  float = 0.25   # 25% of normal during warming

@dataclass
class WarmingStatus:
    days_trading:       int
    is_warming:         bool
    size_fraction:      float
    days_remaining:     int
    reason:             str

def check_account_warming(days_trading: int) -> WarmingStatus:
    """
    FORGE-133: Account Warming Protocol.
    "Demonstrates conservative discipline to the firm's monitoring AI at exactly
    the moment it is paying closest attention — the beginning of the funded relationship."
    """
    warming = days_trading < WARMING_PERIOD_DAYS
    fraction = WARMING_SIZE_FRACTION if warming else 1.0
    remaining = max(0, WARMING_PERIOD_DAYS - days_trading)

    return WarmingStatus(
        days_trading=days_trading, is_warming=warming,
        size_fraction=fraction, days_remaining=remaining,
        reason=(
            f"Day {days_trading + 1} of {WARMING_PERIOD_DAYS}-day warming period. "
            f"Size: {fraction:.0%}. "
            f"{'Building track record before full size.' if warming else 'Warming complete. Full size active.'}"
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-134: PROP FIRM PROMOTION SCANNER
# "Never purchases an evaluation at full price without first checking for discounts."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PromotionRecord:
    firm_id:        str
    code:           str
    discount_pct:   float
    valid_until:    Optional[date]
    applies_to:     str   # "all" or specific account sizes

class PropFirmPromotionScanner:
    """
    FORGE-134: Continuous discount code monitoring.
    "Savings across a year fund additional attempts."
    """
    def __init__(self):
        self._codes: list[PromotionRecord] = []

    def add_code(self, firm_id: str, code: str, discount_pct: float,
                 valid_until: Optional[date] = None, applies_to: str = "all") -> None:
        self._codes.append(PromotionRecord(firm_id, code, discount_pct, valid_until, applies_to))
        logger.info("[FORGE-134] Code added: %s %s (%.0f%% off)", firm_id, code, discount_pct * 100)

    def get_best_discount(self, firm_id: str, today: Optional[date] = None) -> Optional[PromotionRecord]:
        today = today or date.today()
        valid = [
            c for c in self._codes
            if c.firm_id == firm_id and (c.valid_until is None or c.valid_until >= today)
        ]
        return max(valid, key=lambda c: c.discount_pct) if valid else None

    def discounted_price(self, firm_id: str, full_price: float) -> tuple[float, str]:
        best = self.get_best_discount(firm_id)
        if best:
            discounted = full_price * (1 - best.discount_pct)
            return discounted, f"Code '{best.code}' saves ${full_price - discounted:.0f} ({best.discount_pct:.0%} off)."
        return full_price, "No discount available. Pay full price."


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-135: MULTI-ACCOUNT TRADE FINGERPRINT VARIATION
# "Each account has a unique trade fingerprint even when executing same strategy."
# "Prevents triggering anti-copy-trading detection."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeFingerprint:
    account_id:     str
    entry_delay_s:  float   # 2–5 minute window (in seconds)
    size_variation: float   # Slightly different size
    limit_offset:   float   # Different limit price level

def generate_trade_fingerprint(
    account_id:     str,
    base_size:      float,
    base_price:     float,
    seed:           Optional[int] = None,
) -> TradeFingerprint:
    """
    FORGE-135: Generate unique trade fingerprint per account.
    "2–5 minute entry timing window, slightly different position sizes,
    different limit price levels."
    """
    rng = random.Random(seed or hash(f"{account_id}{base_price}"))

    delay_s      = rng.uniform(120, 300)  # 2–5 minutes in seconds
    size_var     = base_size * rng.uniform(0.93, 1.07)   # ±7%
    price_offset = base_price * rng.uniform(-0.0003, 0.0003)  # ±0.03%

    return TradeFingerprint(
        account_id=account_id,
        entry_delay_s=round(delay_s, 1),
        size_variation=round(size_var, 4),
        limit_offset=round(price_offset, 6),
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-136: INSTANT FUNDING VS EVALUATION ROI CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FundingPathComparison:
    instant_roi:    float
    eval_roi:       float
    winner:         str    # "instant" or "evaluation"
    savings:        float  # Dollar difference over lifecycle
    recommendation: str

def compare_funding_paths(
    instant_fee:        float,
    instant_split_pct:  float,
    instant_account:    float,
    eval_fee:           float,
    eval_split_pct:     float,
    eval_account:       float,
    monthly_return_pct: float,
    lifecycle_months:   int,
    pass_rate:          float,
) -> FundingPathComparison:
    """
    FORGE-136: Instant vs evaluation ROI comparison.
    "Sometimes paying eval fee for 90% split on larger account beats
    instant funding at 70% split on smaller account."
    """
    # Instant funding
    instant_monthly = instant_account * monthly_return_pct * instant_split_pct
    instant_total   = instant_monthly * lifecycle_months - instant_fee
    instant_roi     = instant_total / instant_fee * 100 if instant_fee > 0 else 0

    # Evaluation path (risk-adjusted by pass rate)
    eval_monthly = eval_account * monthly_return_pct * eval_split_pct
    eval_total   = (eval_monthly * lifecycle_months - eval_fee) * pass_rate
    eval_roi     = eval_total / eval_fee * 100 if eval_fee > 0 else 0

    winner  = "evaluation" if eval_roi > instant_roi else "instant"
    savings = abs(eval_total - instant_total)

    rec = (
        f"{'Evaluation' if winner == 'evaluation' else 'Instant funding'} path wins. "
        f"Eval ROI: {eval_roi:.0f}% vs Instant ROI: {instant_roi:.0f}%. "
        f"Difference: ${savings:,.0f} over {lifecycle_months} months."
    )

    return FundingPathComparison(
        instant_roi=round(instant_roi, 1),
        eval_roi=round(eval_roi, 1),
        winner=winner,
        savings=round(savings, 2),
        recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-137: SETUP PERFORMANCE DATABASE
# "Gets more accurate every evaluation. After 12 months tells exactly which
# setups to prioritize at each firm."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SetupRecord:
    setup_id:   str
    firm_id:    str
    regime:     str
    instrument: str
    hour:       int
    is_win:     bool
    pnl:        float
    mae:        float   # Max adverse excursion
    mfe:        float   # Max favorable excursion
    date:       date

class SetupPerformanceDatabase:
    """
    FORGE-137: Tracks every setup type's performance per firm per regime.
    "Opening range breakout on EUR/USD at FTMO during London session in
    low-volatility trending regime: exact win rate, average winner, average loser,
    expectancy, MAE, MFE."
    """

    def __init__(self):
        self._records: list[SetupRecord] = []

    def record(self, rec: SetupRecord) -> None:
        self._records.append(rec)

    def get_stats(self, setup_id: str, firm_id: str, regime: str = "") -> dict:
        """Get win rate, avg win, avg loss, expectancy for a setup."""
        filtered = [
            r for r in self._records
            if r.setup_id == setup_id and r.firm_id == firm_id
            and (not regime or r.regime == regime)
        ]
        if not filtered:
            return {"trades": 0, "win_rate": 0.0, "expectancy": 0.0}

        wins   = [r.pnl for r in filtered if r.is_win]
        losses = [r.pnl for r in filtered if not r.is_win]
        total  = len(filtered)
        wr     = len(wins) / total
        avg_w  = sum(wins) / len(wins) if wins else 0.0
        avg_l  = sum(losses) / len(losses) if losses else 0.0
        exp    = wr * avg_w + (1-wr) * avg_l

        return {
            "trades": total, "win_rate": round(wr, 4),
            "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
            "expectancy": round(exp, 2),
            "avg_mae": round(sum(r.mae for r in filtered) / total, 4),
            "avg_mfe": round(sum(r.mfe for r in filtered) / total, 4),
        }

    @property
    def total_records(self) -> int:
        return len(self._records)


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-138: HOT HAND PROTOCOL
# "5+ consecutive profitable sessions verified against probability distribution."
# "Increases sizing 15% within safe limits. Reverts immediately on any loss."
# ─────────────────────────────────────────────────────────────────────────────

HOT_HAND_SESSIONS_REQUIRED: int    = 5
HOT_HAND_SIZE_BOOST:        float  = 1.15  # 15% increase

@dataclass
class HotHandStatus:
    consecutive_profitable: int
    is_active:              bool
    size_multiplier:        float
    is_statistically_valid: bool
    reason:                 str

def check_hot_hand(
    consecutive_profitable_sessions: int,
    is_ftmo: bool = False,  # C-14: Hot hand DISABLED at FTMO permanently
) -> HotHandStatus:
    """
    FORGE-138: Hot Hand Protocol.
    C-14: permanently disabled at FTMO.
    "Reverts immediately on any loss."
    """
    if is_ftmo:
        return HotHandStatus(consecutive_profitable_sessions, False, 1.0, False,
                             "Hot Hand DISABLED at FTMO permanently (C-14).")

    active = consecutive_profitable_sessions >= HOT_HAND_SESSIONS_REQUIRED
    # Statistical significance: 5+ sessions < 3% probability of random occurrence
    statistically_valid = consecutive_profitable_sessions >= 5

    mult   = HOT_HAND_SIZE_BOOST if (active and statistically_valid) else 1.0

    return HotHandStatus(
        consecutive_profitable=consecutive_profitable_sessions,
        is_active=active, size_multiplier=mult,
        is_statistically_valid=statistically_valid,
        reason=(
            f"Hot Hand {'ACTIVE' if active else 'inactive'}: "
            f"{consecutive_profitable_sessions}/{HOT_HAND_SESSIONS_REQUIRED} sessions. "
            f"{'15% size boost active.' if active else 'Normal sizing.'}"
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-139: EDGE DECAY DETECTOR
# "Win rate drops 2 SD below historical average across 20+ trades = decay."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EdgeDecayResult:
    setup_id:           str
    recent_win_rate:    float
    historical_win_rate: float
    sd_distance:        float
    edge_decaying:      bool
    action:             str
    reason:             str

def detect_edge_decay(
    setup_id:           str,
    recent_trades:      list[bool],   # Last 20 trades: True=win, False=loss
    historical_wr:      float,
    historical_std:     float = 0.06,
) -> EdgeDecayResult:
    """
    FORGE-139: Edge Decay Detector.
    "Flags early — before losses accumulate enough to damage the evaluation."
    "Rotates to next ranked setup."
    """
    if len(recent_trades) < 20:
        return EdgeDecayResult(setup_id, 0, historical_wr, 0, False, "INSUFFICIENT_DATA",
                               f"Need 20 trades ({len(recent_trades)} available).")

    recent_wr = sum(recent_trades) / len(recent_trades)
    deviation = (historical_wr - recent_wr) / historical_std if historical_std > 0 else 0
    decaying  = deviation >= 2.0   # 2 SD below = decay

    action = "ROTATE_SETUP" if decaying else "CONTINUE"
    reason = (
        f"{'⚠ EDGE DECAY DETECTED' if decaying else 'Edge healthy'}: "
        f"Recent WR {recent_wr:.1%} vs historical {historical_wr:.1%}. "
        f"{deviation:.1f} SD below average. "
        f"{'Rotate to next ranked setup.' if decaying else ''}"
    )

    return EdgeDecayResult(setup_id, round(recent_wr, 4), historical_wr,
                           round(deviation, 3), decaying, action, reason)


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-140: TIME-OF-DAY PERFORMANCE ATLAS
# "Hour-by-hour heat map. Concentrates trading in proven hours."
# ─────────────────────────────────────────────────────────────────────────────

class TimeOfDayAtlas:
    """
    FORGE-140: Maps every trade result by hour.
    "Firm-specific, instrument-specific, hour-by-hour performance heat map."
    """

    def __init__(self):
        # {(firm_id, instrument, hour): [pnl_list]}
        self._data: dict[tuple, list[float]] = {}

    def record(self, firm_id: str, instrument: str, hour: int, pnl: float) -> None:
        key = (firm_id, instrument, hour)
        self._data.setdefault(key, []).append(pnl)

    def get_best_hours(self, firm_id: str, instrument: str, top_n: int = 3) -> list[tuple[int, float]]:
        """Return top N hours by average P&L for this firm/instrument."""
        scored = []
        for hour in range(24):
            key = (firm_id, instrument, hour)
            pnls = self._data.get(key, [])
            if len(pnls) >= 5:   # Need at least 5 trades to be meaningful
                scored.append((hour, sum(pnls) / len(pnls)))
        return sorted(scored, key=lambda x: -x[1])[:top_n]

    def should_trade_now(self, firm_id: str, instrument: str, hour: int) -> bool:
        """Is this hour in the proven profitable hours?"""
        best = self.get_best_hours(firm_id, instrument)
        best_hours = {h for h, _ in best}
        return hour in best_hours if best_hours else True  # Allow all if no data yet

    @property
    def total_records(self) -> int:
        return sum(len(v) for v in self._data.values())


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-141: FIRM DISCOUNT CODE DATABASE
# "Cross-references before every evaluation purchase."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DiscountCode:
    firm_id:        str
    code:           str
    discount_pct:   float
    expiry:         Optional[date]
    applies_to:     str
    verified:       bool

class FirmDiscountDatabase:
    """
    FORGE-141: Live database of verified discount codes.
    "A system that never pays full price saves enough across a year to
    fund several additional attempts."
    """

    def __init__(self):
        self._codes: list[DiscountCode] = []

    def add(self, code: DiscountCode) -> None:
        self._codes.append(code)

    def get_valid(self, firm_id: str, today: Optional[date] = None) -> list[DiscountCode]:
        today = today or date.today()
        return [
            c for c in self._codes
            if c.firm_id == firm_id and c.verified
            and (c.expiry is None or c.expiry >= today)
        ]

    def best_discount(self, firm_id: str) -> Optional[DiscountCode]:
        valid = self.get_valid(firm_id)
        return max(valid, key=lambda c: c.discount_pct) if valid else None

    def apply(self, firm_id: str, price: float) -> tuple[float, str]:
        best = self.best_discount(firm_id)
        if best:
            final = price * (1 - best.discount_pct)
            return final, f"Code '{best.code}': {best.discount_pct:.0%} off → ${final:.0f}"
        return price, "No discount — full price."


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-142: NEW FIRM EARLY MOVER INTELLIGENCE
# "New legitimate firms offer most aggressive terms during launch."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NewFirmAssessment:
    firm_name:          str
    months_operating:   int
    regulatory_ok:      bool
    payout_verified:    bool
    community_rep:      str   # "positive" / "neutral" / "negative"
    launch_discount_pct: float
    is_legitimate:      bool
    early_mover_score:  float   # 0–10: should we be an early adopter?
    recommendation:     str

def assess_new_firm(
    firm_name:          str,
    months_operating:   int,
    regulatory_ok:      bool,
    payout_verified:    bool,
    community_rep:      str,
    launch_discount_pct: float,
) -> NewFirmAssessment:
    """
    FORGE-142: New firm early mover intelligence.
    "Verifies legitimacy through regulatory standing, payout history,
    community reputation, and operating capital indicators."
    """
    legitimate = regulatory_ok and (payout_verified or months_operating < 3)
    score = 0.0

    if regulatory_ok:     score += 3.0
    if payout_verified:   score += 3.0
    if community_rep == "positive": score += 2.0
    elif community_rep == "neutral": score += 1.0
    if launch_discount_pct >= 0.20: score += 2.0   # Aggressive launch terms

    rec = (
        f"{'✅ EARLY MOVER OPPORTUNITY' if score >= 7 and legitimate else '⚠ MONITOR ONLY'}: "
        f"{firm_name}. Score: {score:.0f}/10. "
        f"{'Legitimate early adopter terms.' if legitimate and score >= 7 else 'Insufficient legitimacy indicators.'}"
    )

    return NewFirmAssessment(
        firm_name=firm_name, months_operating=months_operating,
        regulatory_ok=regulatory_ok, payout_verified=payout_verified,
        community_rep=community_rep, launch_discount_pct=launch_discount_pct,
        is_legitimate=legitimate, early_mover_score=round(score, 1),
        recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-143: FIRM-SPECIFIC INSTRUMENT ROTATION
# "Rotates between asset classes based on which regime each is in."
# ─────────────────────────────────────────────────────────────────────────────

# Instruments per firm (document: FTMO allows forex, indices, commodities, crypto, stocks)
FIRM_INSTRUMENTS: dict[str, list[str]] = {
    FirmID.FTMO:           ["EURUSD", "GBPUSD", "US500", "US30", "GOLD"],
    FirmID.APEX:           ["ES", "NQ", "RTY", "GC", "CL"],
    FirmID.DNA_FUNDED:     ["EURUSD", "GBPUSD", "USDJPY"],
    FirmID.FIVEPERCENTERS: ["EURUSD", "GBPUSD", "US500"],
    FirmID.TOPSTEP:        ["ES", "NQ", "RTY", "GC", "CL"],
}

def rotate_to_best_instrument(
    firm_id:    str,
    regime_per_instrument: dict[str, str],   # instrument → "trending" / "ranging" / "choppy"
) -> tuple[str, str]:
    """
    FORGE-143: Rotate to the best instrument for current regime.
    Trending regime → momentum instruments first.
    Ranging → mean reversion instruments first.
    Returns (best_instrument, reason).
    """
    available = FIRM_INSTRUMENTS.get(firm_id, [])
    # Prefer trending regime for momentum strategies
    trending = [i for i in available if regime_per_instrument.get(i) == "trending"]
    ranging  = [i for i in available if regime_per_instrument.get(i) == "ranging"]

    if trending:
        return trending[0], f"Trending regime on {trending[0]}. Momentum strategies active."
    elif ranging:
        return ranging[0], f"Ranging regime on {ranging[0]}. Mean reversion strategies active."
    return available[0] if available else "ES", f"Default to {available[0] if available else 'ES'}."


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-144: PATIENCE SCORE
# "Adjusts conviction threshold to calibrate frequency toward optimal range."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PatienceScore:
    avg_minutes_between_entries: float
    trades_per_session:          int
    conviction_adjustment:       float   # Positive = raise threshold (fewer trades)
    is_overtrading:              bool
    is_undertrading:             bool
    recommendation:              str

def calculate_patience_score(
    total_minutes_trading:  float,
    trades_taken:           int,
    optimal_min_frequency:  float = 45.0,   # Min minutes between trades
    optimal_max_frequency:  float = 180.0,  # Max minutes between trades
) -> PatienceScore:
    """
    FORGE-144: Patience Score.
    "Too many trades = overtrading = accumulated losses.
    Too few = missing opportunities = evaluation expires."
    """
    if trades_taken <= 0:
        avg = float("inf")
    else:
        avg = total_minutes_trading / trades_taken

    overtrading  = avg < optimal_min_frequency
    undertrading = avg > optimal_max_frequency and trades_taken > 0

    if overtrading:
        adj = +0.10   # Raise conviction threshold — fewer trades
        rec = f"Overtrading: avg {avg:.0f} min between trades (need >{optimal_min_frequency:.0f}). Raise threshold."
    elif undertrading:
        adj = -0.05   # Lower threshold slightly — more trades
        rec = f"Undertrading: avg {avg:.0f} min (optimal <{optimal_max_frequency:.0f}). Lower threshold slightly."
    else:
        adj = 0.0
        rec = f"Optimal frequency: avg {avg:.0f} min between trades. No adjustment."

    return PatienceScore(
        avg_minutes_between_entries=round(avg, 1) if avg != float("inf") else 0.0,
        trades_per_session=trades_taken,
        conviction_adjustment=adj,
        is_overtrading=overtrading,
        is_undertrading=undertrading,
        recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-145: END OF MONTH POSITIONING INTELLIGENCE
# "Last 3 trading days: window dressing, futures rollover, index rebalancing."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EndOfMonthSignal:
    trading_day_of_month:   int
    days_to_month_end:      int
    is_eom_window:          bool   # Last 3 trading days
    expected_flow:          str    # "bullish_bias" / "bearish_bias" / "neutral"
    instruments_favored:    list[str]
    size_boost:             float   # Can take slightly larger position on EOM flows
    reason:                 str

def check_end_of_month_signal(
    today:              date,
    instrument:         str,
    month_trading_days: int,    # Total trading days this month
    day_of_month:       int,    # Current trading day count
) -> EndOfMonthSignal:
    """
    FORGE-145: End of Month Positioning Intelligence.
    Window dressing, futures rollover, index rebalancing = predictable flows.
    """
    days_remaining = month_trading_days - day_of_month
    in_window      = days_remaining <= 3 and days_remaining >= 0

    if in_window:
        # Window dressing: fund managers buy winners to show in portfolio
        flow    = "bullish_bias"
        favored = ["US500", "US30", "QQQ", "SPY"]
        boost   = 1.15
        reason  = f"EOM window: {days_remaining} trading day(s) left. Window dressing flow — bullish index bias."
    else:
        flow    = "neutral"
        favored = []
        boost   = 1.0
        reason  = f"{days_remaining} trading days to month end — no EOM flow active."

    return EndOfMonthSignal(
        trading_day_of_month=day_of_month,
        days_to_month_end=days_remaining,
        is_eom_window=in_window,
        expected_flow=flow,
        instruments_favored=favored,
        size_boost=boost,
        reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-146: LIQUIDITY SESSION OPTIMIZER
# "Outside liquidity windows the execution quality degradation costs more
# than the setup is worth."
# ─────────────────────────────────────────────────────────────────────────────

# Optimal liquidity windows per instrument (ET hours)
LIQUIDITY_WINDOWS: dict[str, tuple[int, int]] = {
    "EURUSD":  (8,  12),   # London-NY overlap 8am-12pm ET
    "GBPUSD":  (8,  12),
    "USDJPY":  (8,  12),
    "ES":      (9,  11),   # 9:45am-11:30am ET (core session)
    "NQ":      (9,  11),
    "RTY":     (9,  11),
    "SPY":     (9,  11),
    "QQQ":     (9,  11),
    "US500":   (9,  11),
    "US30":    (9,  11),
    "GOLD":    (8,  10),   # London open
    "GC":      (8,  10),
    "CL":      (9,  14),   # Oil session
    "DEFAULT": (9,  16),
}

def is_in_liquidity_window(instrument: str, hour_et: int) -> tuple[bool, str]:
    """
    FORGE-146: Check if current hour is within optimal liquidity window.
    "Strict session enforcement for each instrument."
    """
    window = LIQUIDITY_WINDOWS.get(instrument.upper(),
             LIQUIDITY_WINDOWS.get(instrument, LIQUIDITY_WINDOWS["DEFAULT"]))
    start, end = window
    in_window = start <= hour_et < end

    reason = (
        f"{instrument}: optimal liquidity {start}am–{end}am ET. "
        f"Current: {hour_et}:00. {'✅ In window.' if in_window else '❌ Outside window — skip.'}"
    )
    return in_window, reason


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-147: BENCHMARK DAY MANUFACTURE PROTOCOL
# "One well-chosen late-session entry can convert a non-qualifying day."
# Topstep: $150 min | Apex: $250 min
# ─────────────────────────────────────────────────────────────────────────────

QUALIFYING_THRESHOLDS_147: dict[str, float] = {
    FirmID.TOPSTEP: 150.0,
    FirmID.APEX:    250.0,
}

@dataclass
class BenchmarkDayProtocol:
    firm_id:            str
    threshold:          float
    current_pnl:        float
    shortfall:          float
    in_final_window:    bool    # Last 60 minutes
    highest_prob_setup: str     # Best setup to deploy
    activation_status:  str
    recommendation:     str

def check_benchmark_day_protocol(
    firm_id:        str,
    current_pnl:    float,
    minutes_to_close: float,
) -> BenchmarkDayProtocol:
    """
    FORGE-147: Benchmark Day Manufacture Protocol.
    "Identifies the single highest-probability remaining setup in the final 60 minutes."
    """
    threshold = QUALIFYING_THRESHOLDS_147.get(firm_id, 150.0)
    shortfall = max(0.0, threshold - current_pnl)
    in_window = minutes_to_close <= 60

    if shortfall <= 0:
        return BenchmarkDayProtocol(
            firm_id, threshold, current_pnl, 0.0, in_window,
            "none", "THRESHOLD_MET",
            f"✅ {firm_id} threshold met: ${current_pnl:.0f} ≥ ${threshold:.0f}."
        )

    if not in_window:
        return BenchmarkDayProtocol(
            firm_id, threshold, current_pnl, shortfall, False,
            "none", "NOT_ACTIVATED",
            f"${shortfall:.0f} short of ${threshold:.0f}. {minutes_to_close:.0f} min remaining — not yet final window."
        )

    # In final 60 minutes with shortfall: activate protocol
    best_setup = "CHOP-04" if shortfall < 100 else "CHOP-10"   # TICK Extreme or POC Gravity

    return BenchmarkDayProtocol(
        firm_id, threshold, current_pnl, shortfall, True,
        best_setup, "ACTIVATED",
        (f"🎯 BENCHMARK PROTOCOL ACTIVE: ${shortfall:.0f} needed in {minutes_to_close:.0f} min. "
         f"Deploy {best_setup}. One high-probability setup. Execute precisely.")
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-148: WIN STREAK PRESERVATION PROTOCOL
# "5+ consecutive profitable sessions → streak preservation mode."
# "Reduced size, higher conviction threshold, earlier stops."
# ─────────────────────────────────────────────────────────────────────────────

WIN_STREAK_THRESHOLD: int = 5

@dataclass
class WinStreakStatus:
    consecutive_sessions:   int
    is_preservation_mode:   bool
    size_reduction:         float   # Fraction: 0.85 = 15% smaller
    conviction_boost:       float   # Extra conviction required (+0.05 = 5% higher)
    earlier_stop_mult:      float   # Stop closer: 0.85 = 15% tighter
    reason:                 str

def check_win_streak_preservation(consecutive_sessions: int) -> WinStreakStatus:
    """
    FORGE-148: Win Streak Preservation Protocol.
    "A winning streak broken by a careless trade is more costly than a
    slightly lower daily return."
    """
    active = consecutive_sessions >= WIN_STREAK_THRESHOLD

    return WinStreakStatus(
        consecutive_sessions=consecutive_sessions,
        is_preservation_mode=active,
        size_reduction=0.85 if active else 1.0,
        conviction_boost=0.05 if active else 0.0,
        earlier_stop_mult=0.85 if active else 1.0,
        reason=(
            f"Win streak: {consecutive_sessions} sessions. "
            f"{'PRESERVATION MODE: 85% size, +5% conviction, 15% tighter stops.' if active else 'Normal mode.'}"
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-149: EVALUATION INSURANCE POSITION
# "95%+ profit target: tiny 0.1% risk directional position."
# "Not profit-generating. Insurance against minor volatility before passing."
# ─────────────────────────────────────────────────────────────────────────────

INSURANCE_TRIGGER_PCT:  float = 0.95   # 95% of target reached
INSURANCE_MAX_RISK_PCT: float = 0.001  # 0.1% of account maximum

@dataclass
class InsurancePositionDecision:
    should_open:        bool
    max_risk_pct:       float
    direction:          str
    rationale:          str

def check_insurance_position(
    profit_pct_complete:    float,    # 0–1: fraction of target reached
    session_trend:          str,      # "bullish" / "bearish" / "neutral"
    days_remaining:         int,
) -> InsurancePositionDecision:
    """
    FORGE-149: Evaluation Insurance Position.
    "95% of target reached AND days remain: take tiny directional position."
    "Maintains positive unrealized P&L protecting profit total."
    """
    if profit_pct_complete < INSURANCE_TRIGGER_PCT:
        return InsurancePositionDecision(
            False, 0.0, "none",
            f"{profit_pct_complete:.1%} complete — insurance not yet triggered (need ≥95%)."
        )

    if days_remaining <= 0:
        return InsurancePositionDecision(
            False, 0.0, "none",
            "No days remaining — evaluation should pass on its own."
        )

    if session_trend == "neutral":
        return InsurancePositionDecision(
            False, 0.0, "none",
            "No dominant session trend — cannot take directional insurance."
        )

    return InsurancePositionDecision(
        should_open=True,
        max_risk_pct=INSURANCE_MAX_RISK_PCT,
        direction=session_trend,
        rationale=(
            f"INSURANCE ACTIVE: {profit_pct_complete:.1%} of target. "
            f"{days_remaining} days remaining. "
            f"Tiny {session_trend} position at 0.1% max risk. "
            f"Purpose: maintain positive unrealized P&L. NOT profit-seeking."
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-150: SEASONAL EDGE CALENDAR
# "Adds seasonal edge score to every setup aligned with current tendencies."
# ─────────────────────────────────────────────────────────────────────────────

# Seasonal patterns per instrument per month (edge direction and strength)
# Positive = bullish seasonal bias, Negative = bearish
SEASONAL_EDGES: dict[str, dict[int, float]] = {
    "EURUSD": {1: 0.3, 2: 0.4, 3: 0.3, 4: 0.2, 5: -0.1,
               6: -0.2, 7: -0.1, 8: 0.0, 9: -0.3, 10: -0.1,
               11: 0.2, 12: 0.3},
    "US500":  {1: 0.4, 2: 0.2, 3: 0.1, 4: 0.3, 5: 0.1,
               6: 0.0, 7: 0.2, 8: -0.1, 9: -0.4, 10: 0.1,
               11: 0.4, 12: 0.5},  # Year-end rally
    "GOLD":   {1: 0.2, 2: 0.3, 3: 0.1, 4: 0.2, 5: 0.1,
               6: -0.1, 7: 0.1, 8: 0.3, 9: 0.4, 10: 0.2,
               11: 0.1, 12: 0.1},
}

@dataclass
class SeasonalEdge:
    instrument:     str
    month:          int
    edge_score:     float    # -1 to +1: direction and strength
    trade_direction: str     # "long" / "short" / "neutral"
    confidence:     str      # "strong" / "moderate" / "weak"
    reason:         str

def get_seasonal_edge(instrument: str, month: int) -> SeasonalEdge:
    """
    FORGE-150: Seasonal Edge Calendar.
    Returns seasonal edge score and directional bias for current month.
    """
    calendar = SEASONAL_EDGES.get(instrument.upper(), {})
    edge = calendar.get(month, 0.0)

    if abs(edge) >= 0.30:
        confidence = "strong"
    elif abs(edge) >= 0.15:
        confidence = "moderate"
    else:
        confidence = "weak"

    direction = "long" if edge > 0.10 else "short" if edge < -0.10 else "neutral"

    return SeasonalEdge(
        instrument=instrument, month=month, edge_score=edge,
        trade_direction=direction, confidence=confidence,
        reason=(
            f"{instrument} seasonal edge month {month}: {edge:+.2f} "
            f"({confidence} {direction} bias). "
            f"{'Align setups with seasonal tendency.' if abs(edge) > 0.1 else 'No strong seasonal bias.'}"
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-151: LIVE RETURN ATTRIBUTION ENGINE
# "Real-time breakdown of exactly what is generating returns right now."
# "Feeds into Opportunity Scoring Engine to dynamically reweight setups."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AttributionSnapshot:
    timestamp:              datetime
    # Breakdown by setup type
    by_setup:               dict[str, float]   # setup_id → pnl this session
    # Breakdown by instrument
    by_instrument:          dict[str, float]
    # Breakdown by regime state
    by_regime:              dict[str, float]
    # Derived: what's working right now
    best_setup_now:         str
    best_instrument_now:    str
    best_regime_now:        str
    # Scoring engine adjustments
    setup_weight_adjustments: dict[str, float]  # Multipliers for opportunity scoring

class LiveReturnAttributionEngine:
    """
    FORGE-151: Live Return Attribution Engine.
    "Dynamically reweights setups during the current session."
    """

    def __init__(self):
        self._by_setup:      dict[str, list[float]] = {}
        self._by_instrument: dict[str, list[float]] = {}
        self._by_regime:     dict[str, list[float]] = {}

    def record_trade(
        self,
        setup_id:   str,
        instrument: str,
        regime:     str,
        pnl:        float,
    ) -> None:
        """Record a completed trade for attribution."""
        self._by_setup.setdefault(setup_id, []).append(pnl)
        self._by_instrument.setdefault(instrument, []).append(pnl)
        self._by_regime.setdefault(regime, []).append(pnl)

    def get_snapshot(self) -> AttributionSnapshot:
        """Get current session attribution snapshot."""
        def avg(lst: list[float]) -> float:
            return sum(lst) / len(lst) if lst else 0.0

        setup_avgs = {k: avg(v) for k, v in self._by_setup.items()}
        inst_avgs  = {k: avg(v) for k, v in self._by_instrument.items()}
        reg_avgs   = {k: avg(v) for k, v in self._by_regime.items()}

        best_setup  = max(setup_avgs,  key=setup_avgs.get)  if setup_avgs  else "none"
        best_inst   = max(inst_avgs,   key=inst_avgs.get)   if inst_avgs   else "none"
        best_regime = max(reg_avgs,    key=reg_avgs.get)    if reg_avgs    else "none"

        # Build weight adjustments: performing setups get boosted in opportunity scoring
        weights: dict[str, float] = {}
        for sid, avg_pnl in setup_avgs.items():
            if avg_pnl > 0:
                weights[sid] = min(1.5, 1.0 + avg_pnl / 500.0)  # Cap at 1.5x
            else:
                weights[sid] = max(0.5, 1.0 + avg_pnl / 500.0)  # Floor at 0.5x

        return AttributionSnapshot(
            timestamp=datetime.now(timezone.utc),
            by_setup={k: round(avg(v), 2) for k, v in self._by_setup.items()},
            by_instrument={k: round(avg(v), 2) for k, v in self._by_instrument.items()},
            by_regime={k: round(avg(v), 2) for k, v in self._by_regime.items()},
            best_setup_now=best_setup,
            best_instrument_now=best_inst,
            best_regime_now=best_regime,
            setup_weight_adjustments=weights,
        )

    def get_setup_weight(self, setup_id: str) -> float:
        """Get current session weight multiplier for this setup."""
        snap = self.get_snapshot()
        return snap.setup_weight_adjustments.get(setup_id, 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-41: PAYOUT OPTIMIZATION ENGINE
# "Times profit-taking to maximize payout efficiency."
# "Tracks withdrawal count to know when DNA Funded's 5% cap lifts after 3 payouts."
# ─────────────────────────────────────────────────────────────────────────────

DNA_FUNDED_CAP_WITHDRAWALS: int   = 3      # Cap lifts after 3 payouts
DNA_FUNDED_CAP_PCT:         float = 0.05   # 5% of account balance cap

PAYOUT_CYCLES: dict[str, int] = {
    FirmID.FTMO:           14,  # 14 days standard
    FirmID.APEX:           30,  # Monthly
    FirmID.DNA_FUNDED:     14,  # 14 days standard, 7 with add-on
    FirmID.FIVEPERCENTERS: 30,
    FirmID.TOPSTEP:        7,   # Weekly after 5 qualifying days
}

@dataclass
class PayoutOptimizationResult:
    """FORGE-41: Payout timing and amount optimization."""
    firm_id:                str
    withdrawal_count:       int
    dna_cap_active:         bool      # DNA: 5% cap on first 3 withdrawals
    max_withdrawal_pct:     float     # Fraction of account balance allowed
    optimal_timing_days:    int       # Days to optimal next payout window
    cash_flow_note:         str

def optimize_payout_timing(
    firm_id:            str,
    account_balance:    float,
    days_since_last:    int,
    withdrawal_count:   int,   # How many payouts taken so far
) -> PayoutOptimizationResult:
    """
    FORGE-41: Payout Optimization Engine.
    Knows each firm's structure. DNA 5% cap lifts after 3 payouts.
    """
    cycle       = PAYOUT_CYCLES.get(firm_id, 14)
    dna_cap     = (firm_id == FirmID.DNA_FUNDED and withdrawal_count < DNA_FUNDED_CAP_WITHDRAWALS)
    max_pct     = DNA_FUNDED_CAP_PCT if dna_cap else 1.0
    days_to_opt = max(0, cycle - days_since_last)
    max_amount  = account_balance * max_pct

    note = (
        f"{firm_id}: {cycle}-day cycle. "
        f"Withdrawal #{withdrawal_count + 1}. "
    )
    if dna_cap:
        note += (f"DNA 5% cap active (withdrawal {withdrawal_count + 1}/3). "
                 f"Max: ${max_amount:,.0f}. Cap lifts at withdrawal #{DNA_FUNDED_CAP_WITHDRAWALS + 1}.")
    else:
        note += f"No cap. Full profit extraction available."

    if days_to_opt > 0:
        note += f" Next optimal window: {days_to_opt} days."
    else:
        note += " Ready to request now."

    return PayoutOptimizationResult(
        firm_id=firm_id, withdrawal_count=withdrawal_count,
        dna_cap_active=dna_cap, max_withdrawal_pct=max_pct,
        optimal_timing_days=days_to_opt, cash_flow_note=note,
    )
