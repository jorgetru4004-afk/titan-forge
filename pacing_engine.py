"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   pacing_engine.py — FORGE-04 — Layer 1                     ║
║                                                                              ║
║  PROFIT TARGET PACING ENGINE                                                 ║
║  Calculates the required daily profit to hit the target on time.             ║
║  Adjusts the conviction threshold when behind pace.                          ║
║                                                                              ║
║  What this engine does:                                                      ║
║    • Calculates required daily P&L rate given remaining profit + days        ║
║    • Determines pace status: AHEAD / ON_PACE / BEHIND / CRITICAL             ║
║    • When behind: raises minimum setup conviction threshold                  ║
║    • When ahead:  lowers conviction threshold (fewer, higher-quality trades) ║
║    • Applies Apex-specific urgency escalation (FX-09) for 30-day deadline    ║
║    • Goes SILENT when Approach Protocol (C-02) takes over (within 20% of    ║
║      profit target — at that point, pacing is irrelevant, preservation wins) ║
║                                                                              ║
║  IRON RULE: Pacing engine ONLY adjusts conviction thresholds.                ║
║  It NEVER modifies: position size, risk %, stop loss, drawdown limits.       ║
║  Only session quality changes — never risk management parameters. (FX-09)    ║
║                                                                              ║
║  Integrates with:                                                            ║
║    • EvaluationSnapshot (evaluation_state.py) — pace inputs                 ║
║    • C-02 Approach Protocol (clash_rules.py) — silences pacing near target  ║
║    • FORGE-08 Session Quality Filter — receives adjusted threshold           ║
║    • FORGE-61 Session Classifier — conviction threshold consumer             ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from firm_rules import FirmID, MultiFirmRuleEngine

logger = logging.getLogger("titan_forge.pacing_engine")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — PACE CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Pace ratio thresholds (current_daily_avg / required_daily_rate)
PACE_AHEAD_THRESHOLD:         float = 1.20   # 20%+ ahead of required rate
PACE_ON_THRESHOLD:            float = 0.90   # Within 10% of required rate
PACE_SLIGHTLY_BEHIND:         float = 0.70   # 10–30% behind
PACE_SIGNIFICANTLY_BEHIND:    float = 0.50   # 30–50% behind
# Below 0.50 = CRITICALLY_BEHIND

# Approach protocol silence threshold (from C-02)
# When remaining profit ≤ 20% of total target → pacing engine silenced
APPROACH_SILENCE_PCT: float = 0.20

# ── Base conviction thresholds ────────────────────────────────────────────────
# These are session quality score minimums (0–10 scale, per FORGE-08)
CONVICTION_BASE:             float = 6.0   # Standard threshold
CONVICTION_AHEAD:            float = 7.0   # Raise bar when ahead — be selective
CONVICTION_ON_PACE:          float = 6.0   # Baseline
CONVICTION_SLIGHTLY_BEHIND:  float = 5.5   # Slightly lower bar
CONVICTION_SIGNIFICANTLY:    float = 5.0   # Lower bar — need more setups
CONVICTION_CRITICAL:         float = 4.5   # Minimum viable bar
CONVICTION_ABSOLUTE_FLOOR:   float = 4.0   # Hard floor — never go below (FORGE-08)
CONVICTION_NO_TRADE_FLOOR:   float = 4.0   # Below 4.0 = no trading at all

# ── Apex urgency escalation thresholds (FX-09) ────────────────────────────────
# Days remaining → minimum session quality score
APEX_URGENCY_THRESHOLDS: list[tuple[int, int, float]] = [
    # (days_remaining_max, days_remaining_min, conviction_threshold)
    (30, 15, 6.0),   # Days 15–30: standard 6.0
    (14, 10, 5.0),   # Days 10–14: lower to 5.0
    (9,   6, 4.5),   # Days  6–9:  lower to 4.5
    (5,   3, 4.0),   # Days  3–5:  lower to 4.0
    (2,   1, 3.0),   # Days  1–2:  final push 3.0
]

# ── No-time-limit firm behavior (FTMO) ────────────────────────────────────────
# When no calendar deadline: pacing is purely aspirational, not urgency-based
# Conviction adjustment is gentler — no panic, no escalation
FTMO_BEHIND_ADJUSTMENT: float = -0.5    # Lower bar by 0.5 points max when behind
FTMO_AHEAD_ADJUSTMENT:  float = +1.0   # Raise bar by 1.0 when significantly ahead


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — PACE STATUS
# ─────────────────────────────────────────────────────────────────────────────

class PaceStatus(Enum):
    """How the evaluation's profit accumulation compares to the required rate."""
    AHEAD                = auto()   # 20%+ above required daily rate — slow down, be selective
    ON_PACE              = auto()   # Within 10% of required rate — maintain course
    SLIGHTLY_BEHIND      = auto()   # 10–30% below required rate — minor threshold adjustment
    SIGNIFICANTLY_BEHIND = auto()   # 30–50% below required rate — meaningful adjustment
    CRITICALLY_BEHIND    = auto()   # 50%+ below required rate — maximum adjustment
    SILENCED             = auto()   # Approach Protocol active — pacing irrelevant
    NO_TIME_PRESSURE     = auto()   # No calendar deadline (FTMO) — aspirational only


class ConvictionAdjustment(Enum):
    """Direction and magnitude of conviction threshold adjustment."""
    RAISE_HIGH    = auto()   # +1.0 from base (AHEAD — be very selective)
    RAISE_MEDIUM  = auto()   # +0.5 from base
    NONE          = auto()   # No change (ON_PACE)
    LOWER_SMALL   = auto()   # -0.5 from base (SLIGHTLY_BEHIND)
    LOWER_MEDIUM  = auto()   # -1.0 from base (SIGNIFICANTLY_BEHIND)
    LOWER_MAX     = auto()   # -1.5 from base (CRITICALLY_BEHIND — but never below floor)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — PACE ASSESSMENT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PaceAssessment:
    """
    Complete pace picture for a live evaluation.
    Produced by PacingEngine.assess_pace() after every session.
    Consumed by FORGE-08 Session Quality Filter and FORGE-31 Dashboard.
    """
    # Core pace metrics
    firm_id:                    str
    pace_status:                PaceStatus
    current_profit:             float          # P&L earned so far
    profit_target:              float          # Total required
    profit_remaining:           float          # Still needed
    profit_pct_complete:        float          # 0.0–1.0

    # Daily rate analysis
    calendar_days_elapsed:      int
    calendar_days_remaining:    Optional[int]  # None if no deadline
    trading_days_completed:     int
    avg_daily_profit_so_far:    float          # Historical daily average
    required_daily_profit:      float          # What we need per remaining day
    pace_ratio:                 float          # avg_daily / required_daily (1.0 = perfect)

    # Conviction output (the only thing pacing adjusts)
    base_conviction_threshold:  float          # Standard base (6.0)
    adjusted_conviction:        float          # After pace adjustment
    conviction_adjustment:      ConvictionAdjustment
    adjustment_reason:          str

    # Approach Protocol integration
    approach_protocol_active:   bool           # C-02 silencing pacing?
    approach_threshold_pct:     float          # How close to target (0.0–1.0 remaining)

    # Apex urgency escalation (FX-09)
    apex_urgency_active:        bool
    apex_urgency_threshold:     Optional[float]  # What FX-09 mandates for this day count

    # Warnings
    at_risk_of_expiry:          bool           # < 5 days remaining with large gap
    approaching_deadline:       bool           # < 10 days remaining

    @property
    def is_silenced(self) -> bool:
        return self.pace_status == PaceStatus.SILENCED

    @property
    def is_behind(self) -> bool:
        return self.pace_status in (
            PaceStatus.SLIGHTLY_BEHIND,
            PaceStatus.SIGNIFICANTLY_BEHIND,
            PaceStatus.CRITICALLY_BEHIND,
        )

    def status_line(self) -> str:
        return (
            f"[FORGE-04][{self.firm_id}] "
            f"Pace: {self.pace_status.name} (ratio: {self.pace_ratio:.2f}) | "
            f"Profit: ${self.current_profit:+,.2f} / ${self.profit_target:,.2f} "
            f"({self.profit_pct_complete:.1%}) | "
            f"Required/day: ${self.required_daily_profit:,.2f} | "
            f"Avg/day: ${self.avg_daily_profit_so_far:,.2f} | "
            f"Conviction: {self.adjusted_conviction:.1f} "
            f"(was {self.base_conviction_threshold:.1f})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — THE PACING ENGINE
# FORGE-04. Reads the evaluation snapshot. Outputs a conviction threshold.
# ─────────────────────────────────────────────────────────────────────────────

class PacingEngine:
    """
    FORGE-04: Profit Target Pacing Engine.

    Reads live evaluation metrics. Determines pace status.
    Adjusts conviction threshold — and ONLY the conviction threshold.

    NEVER modifies: position size, risk %, stops, drawdown limits.
    SILENCES itself when C-02 Approach Protocol is active (within 20% of target).

    Usage:
        engine = PacingEngine(rule_engine)

        assessment = engine.assess_pace(
            firm_id=FirmID.FTMO,
            current_profit=4_200.0,
            profit_target=10_000.0,
            calendar_days_elapsed=12,
            calendar_days_remaining=None,   # None = no deadline (FTMO)
            trading_days_completed=10,
        )

        # Feed result to FORGE-08 Session Quality Filter
        min_session_score = assessment.adjusted_conviction
    """

    def __init__(self, rule_engine: MultiFirmRuleEngine):
        self._rule_engine = rule_engine
        self._history: dict[str, list[PaceAssessment]] = {}

    # ── CORE ASSESSMENT ───────────────────────────────────────────────────────

    def assess_pace(
        self,
        firm_id:                    str,
        current_profit:             float,
        profit_target:              float,
        calendar_days_elapsed:      int,
        calendar_days_remaining:    Optional[int],   # None = no deadline
        trading_days_completed:     int,
        base_conviction:            float = CONVICTION_BASE,
    ) -> PaceAssessment:
        """
        Assess the current pace of profit accumulation and return the adjusted
        conviction threshold.

        Args:
            firm_id:                    Active firm.
            current_profit:             Realized profit so far ($).
            profit_target:              Total profit required to pass ($).
            calendar_days_elapsed:      Calendar days since evaluation started.
            calendar_days_remaining:    Calendar days left. None = no deadline (FTMO).
            trading_days_completed:     Days with at least one trade.
            base_conviction:            Baseline session quality threshold (default 6.0).

        Returns:
            PaceAssessment with adjusted_conviction as the key output.
        """
        profit_remaining = max(0.0, profit_target - current_profit)
        profit_pct = min(1.0, current_profit / profit_target) if profit_target > 0 else 0.0
        approach_threshold_pct = profit_remaining / profit_target if profit_target > 0 else 0.0

        # ── Check Approach Protocol (C-02) — silence pacing if within 20% ───
        if approach_threshold_pct <= APPROACH_SILENCE_PCT:
            assessment = self._silenced_assessment(
                firm_id, current_profit, profit_target, profit_remaining,
                profit_pct, approach_threshold_pct,
                calendar_days_elapsed, calendar_days_remaining,
                trading_days_completed, base_conviction,
            )
            self._record(firm_id, assessment)
            return assessment

        # ── Calculate daily rates ─────────────────────────────────────────────
        avg_daily = self._avg_daily_profit(current_profit, trading_days_completed)
        required_daily = self._required_daily_profit(
            profit_remaining, calendar_days_remaining, calendar_days_elapsed
        )

        # ── No time pressure (FTMO — no deadline) ────────────────────────────
        if calendar_days_remaining is None:
            assessment = self._no_time_pressure_assessment(
                firm_id, current_profit, profit_target, profit_remaining,
                profit_pct, approach_threshold_pct, avg_daily, required_daily,
                calendar_days_elapsed, trading_days_completed, base_conviction,
            )
            self._record(firm_id, assessment)
            return assessment

        # ── Calculate pace ratio ──────────────────────────────────────────────
        pace_ratio = (avg_daily / required_daily) if required_daily > 0 else 2.0

        # ── Determine pace status ─────────────────────────────────────────────
        if pace_ratio >= PACE_AHEAD_THRESHOLD:
            pace_status = PaceStatus.AHEAD
        elif pace_ratio >= PACE_ON_THRESHOLD:
            pace_status = PaceStatus.ON_PACE
        elif pace_ratio >= PACE_SLIGHTLY_BEHIND:
            pace_status = PaceStatus.SLIGHTLY_BEHIND
        elif pace_ratio >= PACE_SIGNIFICANTLY_BEHIND:
            pace_status = PaceStatus.SIGNIFICANTLY_BEHIND
        else:
            pace_status = PaceStatus.CRITICALLY_BEHIND

        # ── Apex urgency escalation (FX-09) ──────────────────────────────────
        apex_urgency_active = False
        apex_urgency_threshold: Optional[float] = None
        if firm_id == FirmID.APEX and calendar_days_remaining is not None:
            apex_urgency_active, apex_urgency_threshold = self._apex_urgency(
                calendar_days_remaining
            )

        # ── Calculate conviction adjustment ───────────────────────────────────
        adjusted_conviction, conviction_adj, adj_reason = self._calculate_conviction(
            pace_status=pace_status,
            base_conviction=base_conviction,
            calendar_days_remaining=calendar_days_remaining,
            apex_urgency_active=apex_urgency_active,
            apex_urgency_threshold=apex_urgency_threshold,
            firm_id=firm_id,
            pace_ratio=pace_ratio,
        )

        # ── Deadline risk flags ───────────────────────────────────────────────
        at_risk = (
            calendar_days_remaining is not None
            and calendar_days_remaining <= 5
            and profit_remaining > (profit_target * 0.50)
        )
        approaching = (
            calendar_days_remaining is not None
            and calendar_days_remaining <= 10
        )

        if at_risk:
            logger.error(
                "[FORGE-04][%s] ⚠ AT RISK OF EXPIRY. %d days left, "
                "%.1f%% of target still needed.",
                firm_id, calendar_days_remaining, approach_threshold_pct * 100
            )

        self._log_assessment(firm_id, pace_status, pace_ratio, adjusted_conviction,
                             avg_daily, required_daily)

        assessment = PaceAssessment(
            firm_id=firm_id,
            pace_status=pace_status,
            current_profit=current_profit,
            profit_target=profit_target,
            profit_remaining=profit_remaining,
            profit_pct_complete=profit_pct,
            calendar_days_elapsed=calendar_days_elapsed,
            calendar_days_remaining=calendar_days_remaining,
            trading_days_completed=trading_days_completed,
            avg_daily_profit_so_far=avg_daily,
            required_daily_profit=required_daily,
            pace_ratio=pace_ratio,
            base_conviction_threshold=base_conviction,
            adjusted_conviction=adjusted_conviction,
            conviction_adjustment=conviction_adj,
            adjustment_reason=adj_reason,
            approach_protocol_active=False,
            approach_threshold_pct=approach_threshold_pct,
            apex_urgency_active=apex_urgency_active,
            apex_urgency_threshold=apex_urgency_threshold,
            at_risk_of_expiry=at_risk,
            approaching_deadline=approaching,
        )
        self._record(firm_id, assessment)
        return assessment

    # ── CONVICTION CALCULATION ────────────────────────────────────────────────

    def _calculate_conviction(
        self,
        pace_status:              PaceStatus,
        base_conviction:          float,
        calendar_days_remaining:  Optional[int],
        apex_urgency_active:      bool,
        apex_urgency_threshold:   Optional[float],
        firm_id:                  str,
        pace_ratio:               float,
    ) -> tuple[float, ConvictionAdjustment, str]:
        """
        Calculate the adjusted conviction threshold.
        Returns: (adjusted_value, ConvictionAdjustment enum, reason string)

        IRON RULE: Only the conviction threshold changes.
        Never position size. Never risk %. Never stops.
        """
        if pace_status == PaceStatus.AHEAD:
            adjusted = base_conviction + 1.0
            adj = ConvictionAdjustment.RAISE_HIGH
            reason = (
                f"Pace ratio {pace_ratio:.2f} — {pace_ratio*100:.0f}% of target met ahead of schedule. "
                f"Raising conviction bar to {adjusted:.1f} — be MORE selective, not more aggressive."
            )

        elif pace_status == PaceStatus.ON_PACE:
            adjusted = base_conviction
            adj = ConvictionAdjustment.NONE
            reason = (
                f"Pace ratio {pace_ratio:.2f} — on track. "
                f"Maintaining base conviction {adjusted:.1f}. No adjustment needed."
            )

        elif pace_status == PaceStatus.SLIGHTLY_BEHIND:
            adjusted = base_conviction - 0.5
            adj = ConvictionAdjustment.LOWER_SMALL
            reason = (
                f"Pace ratio {pace_ratio:.2f} — slightly behind. "
                f"Conviction lowered to {adjusted:.1f}. Minor adjustment only."
            )

        elif pace_status == PaceStatus.SIGNIFICANTLY_BEHIND:
            adjusted = base_conviction - 1.0
            adj = ConvictionAdjustment.LOWER_MEDIUM
            reason = (
                f"Pace ratio {pace_ratio:.2f} — significantly behind. "
                f"Conviction lowered to {adjusted:.1f}. Accept more setups — still above absolute floor."
            )

        else:  # CRITICALLY_BEHIND
            adjusted = base_conviction - 1.5
            adj = ConvictionAdjustment.LOWER_MAX
            reason = (
                f"Pace ratio {pace_ratio:.2f} — critically behind. "
                f"Maximum conviction reduction to {adjusted:.1f}. "
                f"Only position size and conviction change — never risk parameters."
            )

        # ── Apex urgency override (FX-09) ─────────────────────────────────────
        # FX-09 sets a hard floor per days-remaining band.
        # ONLY session quality changes — never risk management parameters.
        if apex_urgency_active and apex_urgency_threshold is not None:
            if adjusted < apex_urgency_threshold:
                adjusted = apex_urgency_threshold
                reason += (
                    f" [FX-09 Apex Urgency: floor raised to {apex_urgency_threshold:.1f} "
                    f"based on days remaining — session quality adjustment only]"
                )

        # ── Absolute floor — never go below 4.0 (FORGE-08 hard stop) ─────────
        adjusted = max(CONVICTION_ABSOLUTE_FLOOR, adjusted)

        return adjusted, adj, reason

    # ── MATH ─────────────────────────────────────────────────────────────────

    def _required_daily_profit(
        self,
        profit_remaining:         float,
        calendar_days_remaining:  Optional[int],
        calendar_days_elapsed:    int,
    ) -> float:
        """
        Calculate the required daily profit to hit the target on time.

        For time-limited evaluations: profit_remaining / days_remaining.
        For unlimited evaluations (FTMO): profit_remaining / 20 (aspirational).
        """
        if calendar_days_remaining is None:
            # No deadline — use a 20-day forward horizon as aspirational target
            return profit_remaining / 20.0 if profit_remaining > 0 else 0.0

        if calendar_days_remaining <= 0:
            # No time left — any remaining profit is impossible to achieve normally
            return float("inf")

        # Assume ~70% of remaining calendar days will be active trading days
        estimated_trading_days = max(1, int(calendar_days_remaining * 0.70))
        return profit_remaining / estimated_trading_days

    def _avg_daily_profit(self, current_profit: float, trading_days: int) -> float:
        """Average realized profit per trading day so far."""
        if trading_days <= 0:
            return 0.0
        return current_profit / trading_days

    def get_required_daily_profit(
        self,
        profit_remaining: float,
        calendar_days_remaining: Optional[int],
        calendar_days_elapsed: int = 0,
    ) -> float:
        """Public accessor for required daily profit calculation."""
        return self._required_daily_profit(
            profit_remaining, calendar_days_remaining, calendar_days_elapsed
        )

    # ── APEX URGENCY ESCALATION (FX-09) ──────────────────────────────────────

    def _apex_urgency(self, days_remaining: int) -> tuple[bool, Optional[float]]:
        """
        FX-09: Apex urgency escalation thresholds.
        Returns (is_active, conviction_floor).

        Days 15–30: 6.0 (standard — no urgency)
        Days 10–14: 5.0
        Days  6–9:  4.5
        Days  3–5:  4.0
        Days  1–2:  3.0 (floored at CONVICTION_ABSOLUTE_FLOOR = 4.0)
        """
        for max_days, min_days, threshold in APEX_URGENCY_THRESHOLDS:
            if min_days <= days_remaining <= max_days:
                is_urgent = threshold < CONVICTION_BASE  # Active when below base
                return is_urgent, threshold
        return False, None

    def get_apex_urgency_threshold(self, days_remaining: int) -> float:
        """Public accessor for Apex urgency threshold at a given days-remaining count."""
        _, threshold = self._apex_urgency(days_remaining)
        # Apply absolute floor
        if threshold is not None:
            return max(CONVICTION_ABSOLUTE_FLOOR, threshold)
        return CONVICTION_BASE

    # ── SPECIAL ASSESSMENTS ───────────────────────────────────────────────────

    def _silenced_assessment(
        self,
        firm_id, current_profit, profit_target, profit_remaining,
        profit_pct, approach_threshold_pct,
        calendar_days_elapsed, calendar_days_remaining,
        trading_days_completed, base_conviction,
    ) -> PaceAssessment:
        """Build a SILENCED assessment when C-02 Approach Protocol is active."""
        logger.info(
            "[FORGE-04][%s] 🔕 SILENCED — Approach Protocol active. "
            "%.1f%% of target remaining. Pacing defers to C-02.",
            firm_id, approach_threshold_pct * 100
        )
        return PaceAssessment(
            firm_id=firm_id,
            pace_status=PaceStatus.SILENCED,
            current_profit=current_profit,
            profit_target=profit_target,
            profit_remaining=profit_remaining,
            profit_pct_complete=profit_pct,
            calendar_days_elapsed=calendar_days_elapsed,
            calendar_days_remaining=calendar_days_remaining,
            trading_days_completed=trading_days_completed,
            avg_daily_profit_so_far=self._avg_daily_profit(
                current_profit, trading_days_completed
            ),
            required_daily_profit=0.0,   # Irrelevant when silenced
            pace_ratio=1.0,              # Neutral
            base_conviction_threshold=base_conviction,
            adjusted_conviction=base_conviction,   # No adjustment when silenced
            conviction_adjustment=ConvictionAdjustment.NONE,
            adjustment_reason=(
                f"Approach Protocol active — {approach_threshold_pct:.1%} of target remaining "
                f"(within {APPROACH_SILENCE_PCT:.0%} threshold). "
                f"Pacing engine SILENCED. C-02 controls position sizing."
            ),
            approach_protocol_active=True,
            approach_threshold_pct=approach_threshold_pct,
            apex_urgency_active=False,
            apex_urgency_threshold=None,
            at_risk_of_expiry=False,
            approaching_deadline=False,
        )

    def _no_time_pressure_assessment(
        self,
        firm_id, current_profit, profit_target, profit_remaining,
        profit_pct, approach_threshold_pct, avg_daily, required_daily,
        calendar_days_elapsed, trading_days_completed, base_conviction,
    ) -> PaceAssessment:
        """Build an assessment for time-unlimited firms (FTMO)."""
        pace_ratio = (avg_daily / required_daily) if required_daily > 0 else 1.5

        # Gentle adjustment for no-deadline firms
        if pace_ratio >= 1.5:
            adjusted = base_conviction + FTMO_AHEAD_ADJUSTMENT
            adj = ConvictionAdjustment.RAISE_HIGH
            reason = f"Ahead of aspirational pace ({pace_ratio:.2f}). Raising bar to {adjusted:.1f}."
        elif pace_ratio >= 0.80:
            adjusted = base_conviction
            adj = ConvictionAdjustment.NONE
            reason = "On aspirational pace. No adjustment (no time pressure)."
        else:
            adjusted = base_conviction + FTMO_BEHIND_ADJUSTMENT
            adj = ConvictionAdjustment.LOWER_SMALL
            reason = (
                f"Behind aspirational pace ({pace_ratio:.2f}). "
                f"Minor adjustment to {adjusted:.1f}. No urgency — no deadline."
            )

        adjusted = max(CONVICTION_ABSOLUTE_FLOOR, min(10.0, adjusted))

        return PaceAssessment(
            firm_id=firm_id,
            pace_status=PaceStatus.NO_TIME_PRESSURE,
            current_profit=current_profit,
            profit_target=profit_target,
            profit_remaining=profit_remaining,
            profit_pct_complete=profit_pct,
            calendar_days_elapsed=calendar_days_elapsed,
            calendar_days_remaining=None,
            trading_days_completed=trading_days_completed,
            avg_daily_profit_so_far=avg_daily,
            required_daily_profit=required_daily,
            pace_ratio=pace_ratio,
            base_conviction_threshold=base_conviction,
            adjusted_conviction=adjusted,
            conviction_adjustment=adj,
            adjustment_reason=reason,
            approach_protocol_active=False,
            approach_threshold_pct=approach_threshold_pct,
            apex_urgency_active=False,
            apex_urgency_threshold=None,
            at_risk_of_expiry=False,
            approaching_deadline=False,
        )

    # ── UTILITIES ────────────────────────────────────────────────────────────

    def _log_assessment(
        self,
        firm_id: str,
        pace_status: PaceStatus,
        pace_ratio: float,
        adjusted_conviction: float,
        avg_daily: float,
        required_daily: float,
    ) -> None:
        level = {
            PaceStatus.AHEAD:                logging.DEBUG,
            PaceStatus.ON_PACE:              logging.DEBUG,
            PaceStatus.SLIGHTLY_BEHIND:      logging.INFO,
            PaceStatus.SIGNIFICANTLY_BEHIND: logging.WARNING,
            PaceStatus.CRITICALLY_BEHIND:    logging.ERROR,
        }.get(pace_status, logging.DEBUG)

        logger.log(
            level,
            "[FORGE-04][%s] %s | Ratio: %.2f | Avg/day: $%.2f | "
            "Required/day: $%.2f | Conviction: %.1f",
            firm_id, pace_status.name, pace_ratio,
            avg_daily, required_daily, adjusted_conviction
        )

    def _record(self, firm_id: str, assessment: PaceAssessment) -> None:
        """Keep the last 30 assessments per firm for trend analysis."""
        if firm_id not in self._history:
            self._history[firm_id] = []
        self._history[firm_id].append(assessment)
        if len(self._history[firm_id]) > 30:
            self._history[firm_id].pop(0)

    def get_history(self, firm_id: str) -> list[PaceAssessment]:
        """Return assessment history for a firm (most recent last)."""
        return list(self._history.get(firm_id, []))

    def get_pace_trend(self, firm_id: str) -> Optional[str]:
        """
        Determine if pace is improving, declining, or stable over last 5 assessments.
        Returns 'IMPROVING', 'DECLINING', 'STABLE', or None if insufficient history.
        """
        history = self.get_history(firm_id)
        if len(history) < 3:
            return None
        recent = history[-5:]
        ratios = [a.pace_ratio for a in recent if not a.is_silenced]
        if len(ratios) < 2:
            return None
        first_half = sum(ratios[:len(ratios) // 2]) / (len(ratios) // 2)
        second_half = sum(ratios[len(ratios) // 2:]) / len(ratios[len(ratios) // 2:])
        delta = second_half - first_half
        if delta > 0.10:
            return "IMPROVING"
        elif delta < -0.10:
            return "DECLINING"
        return "STABLE"

    # ── FORGE-31 DASHBOARD FEED ───────────────────────────────────────────────

    def dashboard_data(
        self,
        firm_id: str,
        current_profit: float,
        profit_target: float,
        calendar_days_elapsed: int,
        calendar_days_remaining: Optional[int],
        trading_days_completed: int,
    ) -> dict:
        """
        Structured data for the FORGE-31 Evaluation Dashboard.
        Includes pace status, conviction threshold, trend, and projections.
        """
        assessment = self.assess_pace(
            firm_id=firm_id,
            current_profit=current_profit,
            profit_target=profit_target,
            calendar_days_elapsed=calendar_days_elapsed,
            calendar_days_remaining=calendar_days_remaining,
            trading_days_completed=trading_days_completed,
        )

        projected_completion_days: Optional[float] = None
        if assessment.avg_daily_profit_so_far > 0 and assessment.profit_remaining > 0:
            projected_completion_days = (
                assessment.profit_remaining / assessment.avg_daily_profit_so_far
            )

        return {
            "firm_id":                   firm_id,
            "pace_status":               assessment.pace_status.name,
            "pace_ratio":                round(assessment.pace_ratio, 3),
            "pace_trend":                self.get_pace_trend(firm_id),
            "current_profit":            assessment.current_profit,
            "profit_target":             assessment.profit_target,
            "profit_remaining":          assessment.profit_remaining,
            "profit_pct_complete":       round(assessment.profit_pct_complete, 4),
            "avg_daily_profit":          round(assessment.avg_daily_profit_so_far, 2),
            "required_daily_profit":     round(assessment.required_daily_profit, 2),
            "days_remaining":            calendar_days_remaining,
            "adjusted_conviction":       assessment.adjusted_conviction,
            "conviction_adjustment":     assessment.conviction_adjustment.name,
            "approach_protocol_active":  assessment.approach_protocol_active,
            "apex_urgency_active":       assessment.apex_urgency_active,
            "apex_urgency_threshold":    assessment.apex_urgency_threshold,
            "at_risk_of_expiry":         assessment.at_risk_of_expiry,
            "approaching_deadline":      assessment.approaching_deadline,
            "projected_completion_days": (
                round(projected_completion_days, 1)
                if projected_completion_days else None
            ),
        }
