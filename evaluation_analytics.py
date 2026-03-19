"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  evaluation_analytics.py — Layer 2                          ║
║  FORGE-24: Evaluation Post-Mortem (feeds firm performance database)         ║
║  FORGE-25: Firm Performance Database (win rates, pass days, drawdown)       ║
║  FORGE-27: Calibration Ratchet (cannot repeat same mistake twice)           ║
║  FORGE-32: Pass Probability Score (hourly math — below 30% = survival)     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger("titan_forge.evaluation_analytics")


# ── FORGE-25: Firm Performance Database ──────────────────────────────────────

@dataclass
class FirmPerformanceRecord:
    """Historical performance at a specific firm. Grows with every evaluation."""
    firm_id:             str
    evaluations_total:   int   = 0
    evaluations_passed:  int   = 0
    evaluations_failed:  int   = 0
    best_setups:         list[str] = field(default_factory=list)
    avg_pass_days:       float = 0.0
    avg_drawdown_used:   float = 0.0   # Average fraction of drawdown budget used
    avg_daily_profit:    float = 0.0
    consecutive_passes:  int   = 0
    last_updated:        Optional[date] = None

    @property
    def pass_rate(self) -> float:
        if self.evaluations_total == 0:
            return 0.0
        return self.evaluations_passed / self.evaluations_total

    def record_pass(self, pass_days: int, drawdown_used: float, daily_profit: float) -> None:
        self.evaluations_total  += 1
        self.evaluations_passed += 1
        self.consecutive_passes += 1
        n = self.evaluations_passed
        self.avg_pass_days   = (self.avg_pass_days   * (n-1) + pass_days)    / n
        self.avg_drawdown_used = (self.avg_drawdown_used * (n-1) + drawdown_used) / n
        self.avg_daily_profit = (self.avg_daily_profit * (n-1) + daily_profit) / n
        self.last_updated = date.today()

    def record_fail(self) -> None:
        self.evaluations_total  += 1
        self.evaluations_failed += 1
        self.consecutive_passes  = 0
        self.last_updated = date.today()


class FirmPerformanceDatabase:
    """FORGE-25: Firm Performance Database — grows with every evaluation."""

    def __init__(self):
        self._records: dict[str, FirmPerformanceRecord] = {}

    def get_or_create(self, firm_id: str) -> FirmPerformanceRecord:
        if firm_id not in self._records:
            self._records[firm_id] = FirmPerformanceRecord(firm_id=firm_id)
        return self._records[firm_id]

    def record_evaluation_result(
        self, firm_id: str, passed: bool,
        pass_days: int = 0, drawdown_used: float = 0.0, daily_profit: float = 0.0,
        top_setups: Optional[list[str]] = None,
    ) -> None:
        rec = self.get_or_create(firm_id)
        if passed:
            rec.record_pass(pass_days, drawdown_used, daily_profit)
            if top_setups:
                # Update best setups (keep top 5 most common)
                for s in (top_setups or []):
                    if s not in rec.best_setups:
                        rec.best_setups.append(s)
                rec.best_setups = rec.best_setups[:5]
        else:
            rec.record_fail()
        logger.info(
            "[FORGE-25] Recorded %s at %s. Pass rate: %.1f%%.",
            "PASS" if passed else "FAIL", firm_id,
            rec.pass_rate * 100,
        )

    def get_pass_rate(self, firm_id: str) -> float:
        return self._records.get(firm_id, FirmPerformanceRecord(firm_id)).pass_rate

    def summary(self) -> dict[str, dict]:
        return {
            fid: {
                "total": r.evaluations_total,
                "passed": r.evaluations_passed,
                "pass_rate": f"{r.pass_rate:.1%}",
                "consecutive_passes": r.consecutive_passes,
                "best_setups": r.best_setups,
            }
            for fid, r in self._records.items()
        }


# ── FORGE-24: Evaluation Post-Mortem ─────────────────────────────────────────

@dataclass
class PostMortem:
    """Full root cause analysis for one evaluation."""
    eval_id:         str
    firm_id:         str
    outcome:         str   # "PASSED" / "FAILED" / "EXPIRED"
    date_completed:  date
    # Metrics
    profit_achieved: float
    profit_target:   float
    drawdown_used:   float   # Fraction
    trading_days:    int
    # Root cause (for failures)
    primary_cause:   Optional[str]
    contributing_factors: list[str]
    # Lessons
    lessons:         list[str]
    rule_violations: list[str]
    regression_test_id: Optional[str]   # FX-10: every failure generates a regression test

def build_post_mortem(
    eval_id: str, firm_id: str, outcome: str,
    profit: float, target: float, drawdown_pct: float,
    trading_days: int, failure_reason: Optional[str] = None,
) -> PostMortem:
    """FORGE-24: Build post-mortem. Full root cause. Feeds firm performance DB."""
    lessons = []
    causes  = []
    violations = []

    if outcome == "FAILED":
        if failure_reason and "drawdown" in failure_reason.lower():
            causes.append("Drawdown limit breached — position sizing too aggressive")
            lessons.append("Review safety margin thresholds. Reduce base size.")
        if failure_reason and "daily" in failure_reason.lower():
            causes.append("Daily loss limit hit — session management failure")
            lessons.append("Improve session quality scoring. Skip marginal sessions.")
        if drawdown_pct > 0.70:
            causes.append("Excessive drawdown consumption")
            violations.append("FORGE-11 Layer 3 guardian may not have been respected")

    regression_id = f"REGR-{eval_id}-{date.today().strftime('%Y%m%d')}" if outcome == "FAILED" else None

    pm = PostMortem(
        eval_id=eval_id, firm_id=firm_id, outcome=outcome,
        date_completed=date.today(),
        profit_achieved=profit, profit_target=target,
        drawdown_used=drawdown_pct, trading_days=trading_days,
        primary_cause=causes[0] if causes else None,
        contributing_factors=causes[1:],
        lessons=lessons,
        rule_violations=violations,
        regression_test_id=regression_id,
    )
    logger.info(
        "[FORGE-24] Post-mortem: %s %s. Outcome: %s. "
        "Profit: $%.0f/$%.0f. DD used: %.0f%%.",
        firm_id, eval_id, outcome, profit, target, drawdown_pct * 100,
    )
    return pm


# ── FORGE-27: Calibration Ratchet ─────────────────────────────────────────────
# After each failure: adds guard. After each pass: improves calibration.
# Cannot repeat same mistake twice.

class CalibrationRatchet:
    """FORGE-27: Cannot repeat same mistake twice."""

    def __init__(self):
        self._guards:    list[dict] = []   # Active guardrails from failures
        self._successes: list[dict] = []   # Successful calibrations
        self._version:   int = 0

    def add_guard_from_failure(self, post_mortem: PostMortem) -> None:
        """After each failure: add a guard to prevent the same mistake."""
        if not post_mortem.primary_cause:
            return

        guard = {
            "id":         f"GUARD-{len(self._guards)+1:03d}",
            "source_eval": post_mortem.eval_id,
            "cause":       post_mortem.primary_cause,
            "rule":        self._cause_to_rule(post_mortem.primary_cause),
            "added_at":    date.today().isoformat(),
        }
        self._guards.append(guard)
        self._version += 1
        logger.warning(
            "[FORGE-27] Guard added: %s → %s (v%d)",
            guard["id"], guard["rule"], self._version,
        )

    def record_pass(self, firm_id: str) -> None:
        """After each pass: refine calibration."""
        self._successes.append({"firm": firm_id, "date": date.today().isoformat()})
        self._version += 1
        logger.info("[FORGE-27] Calibration improved after pass at %s (v%d).", firm_id, self._version)

    def get_active_guards(self) -> list[dict]:
        return list(self._guards)

    @property
    def version(self) -> int:
        return self._version

    @staticmethod
    def _cause_to_rule(cause: str) -> str:
        if "drawdown" in cause.lower():
            return "Reduce base position size by 20%. Review FORGE-03 safety margin."
        elif "daily" in cause.lower():
            return "Raise session quality threshold by 0.5 for this firm."
        elif "setup" in cause.lower():
            return "Remove lowest-performing setup type from this firm's rotation."
        return "Review and tighten the triggering parameter."


# ── FORGE-32: Pass Probability Score ──────────────────────────────────────────
# Hourly mathematical probability of passing. Below 30%: automatic survival mode.

def calculate_pass_probability(
    current_profit:         float,
    profit_target:          float,
    days_elapsed:           int,
    days_remaining:         Optional[int],   # None = no deadline
    drawdown_pct_used:      float,
    trading_days_completed: int,
    pass_rate_at_this_firm: float = 0.65,   # Historical pass rate
) -> float:
    """
    FORGE-32: Hourly mathematical pass probability.
    Below 30% → automatic survival mode.

    Weights:
        - Profit completion (40%)
        - Time adequacy (25%)
        - Drawdown buffer health (20%)
        - Pace vs historical (15%)
    """
    profit_pct   = min(1.0, current_profit / profit_target) if profit_target > 0 else 0.0
    profit_score = profit_pct

    if days_remaining is not None:
        total_days = days_elapsed + days_remaining
        time_score = days_remaining / total_days if total_days > 0 else 0.0
    else:
        time_score = 0.80   # No deadline = generous time score

    dd_health = max(0.0, 1.0 - drawdown_pct_used)

    if days_elapsed > 0 and days_remaining is not None:
        daily_rate  = current_profit / days_elapsed if days_elapsed > 0 else 0.0
        needed_rate = (profit_target - current_profit) / max(1, int(days_remaining * 0.7))
        pace_score  = min(1.0, daily_rate / needed_rate) if needed_rate > 0 else 1.0
    else:
        pace_score = 0.50

    probability = (
        profit_score * 0.40 +
        time_score   * 0.25 +
        dd_health    * 0.20 +
        pace_score   * 0.15
    )
    # Adjust by historical firm pass rate
    probability = probability * 0.7 + (probability * pass_rate_at_this_firm * 0.3)
    probability = round(max(0.0, min(1.0, probability)), 4)

    if probability < 0.30:
        logger.warning(
            "[FORGE-32] ⚠ Pass probability %.1f%% — SURVIVAL MODE. "
            "Preserve remaining drawdown. No aggressive entries.",
            probability * 100,
        )

    return probability
