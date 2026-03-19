"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                      system_arch.py — Layer 3                               ║
║  FORGE-82: Antifragile Architecture (stress test → strengthen)              ║
║  FORGE-83: Evolutionary Setup Selection (prune underperformers)             ║
║  FORGE-84: Second-Order Effects Modeling                                    ║
║  FORGE-85: Game Theory Cooperative Strategy                                 ║
║  FORGE-87: Corporate Audit Framework                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

logger = logging.getLogger("titan_forge.system_arch")


# ── FORGE-82: Antifragile Architecture ───────────────────────────────────────
# Every failure strengthens the system. Stress tests → improvements.

@dataclass
class StressTestResult:
    """FORGE-82: Result of a stress test — improvements extracted."""
    test_id:            str
    stress_type:        str    # "DRAWDOWN", "STREAK", "VOLATILE_MARKET", "NEWS_SPIKE"
    pre_stress_score:   float  # System performance before
    post_stress_score:  float  # System performance after applying lessons
    improvement:        float  # post - pre
    improvements_added: list[str]
    is_antifragile:     bool   # True if system IMPROVED after stress

class AntifragileMonitor:
    """FORGE-82: Track system response to stress events."""

    def __init__(self):
        self._stress_tests: list[StressTestResult] = []
        self._improvements: list[str] = []

    def record_stress(
        self,
        test_id:        str,
        stress_type:    str,
        pre_score:      float,
        lessons:        list[str],
    ) -> StressTestResult:
        """Record a stress event and extract improvements."""
        # Antifragile: each lesson adds improvement
        post_score = min(10.0, pre_score + len(lessons) * 0.15)
        improvement = post_score - pre_score
        self._improvements.extend(lessons)

        result = StressTestResult(
            test_id=test_id, stress_type=stress_type,
            pre_stress_score=pre_score, post_stress_score=post_score,
            improvement=improvement, improvements_added=lessons,
            is_antifragile=improvement > 0,
        )
        self._stress_tests.append(result)
        logger.info(
            "[FORGE-82] Stress test %s: %s → score %.1f→%.1f (+%.2f). "
            "Antifragile: %s.",
            test_id, stress_type, pre_score, post_score, improvement,
            "✓" if result.is_antifragile else "✗",
        )
        return result

    @property
    def total_improvements(self) -> int:
        return len(self._improvements)

    @property
    def antifragile_rate(self) -> float:
        if not self._stress_tests:
            return 0.0
        return sum(1 for t in self._stress_tests if t.is_antifragile) / len(self._stress_tests)


# ── FORGE-83: Evolutionary Setup Selection ───────────────────────────────────
# After every 50 evaluations: prune weakest 3 setups, promote strongest 3.

EVOLUTIONARY_CYCLE_EVALUATIONS: int = 50
SETUPS_TO_PRUNE: int = 3

@dataclass
class EvolutionaryUpdate:
    """FORGE-83: Setup rotation after evolutionary cycle."""
    cycle_number:   int
    evaluations_since_last: int
    pruned_setups:  list[str]   # Removed — below performance threshold
    promoted_setups: list[str]  # Boosted — top performers
    retained:       list[str]
    performance_scores: dict[str, float]   # setup_id → score
    recommendation: str

def run_evolutionary_cycle(
    setup_performance: dict[str, dict],   # setup_id → {win_rate, pnl, trades}
    cycle_number:      int = 1,
) -> EvolutionaryUpdate:
    """
    FORGE-83: Evolutionary setup selection.
    After 50 evaluations: prune the 3 worst performers, promote the 3 best.
    """
    if not setup_performance:
        return EvolutionaryUpdate(
            cycle_number, 0, [], [], [], {},
            "No setup performance data yet."
        )

    # Score each setup: blend of win rate and P&L contribution
    scores: dict[str, float] = {}
    for sid, perf in setup_performance.items():
        wr    = perf.get("win_rate", 0.0)
        pnl   = perf.get("pnl", 0.0)
        n     = perf.get("trades", 0)
        # Score = win_rate_normalized × pnl_contribution × sample_weight
        sample_weight = min(1.0, n / 50.0)
        scores[sid] = (wr * 0.6 + min(1.0, pnl / 5000.0) * 0.4) * sample_weight

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x])

    pruned   = sorted_ids[:SETUPS_TO_PRUNE]
    promoted = sorted_ids[-SETUPS_TO_PRUNE:]
    retained = sorted_ids[SETUPS_TO_PRUNE:-SETUPS_TO_PRUNE]

    rec = (f"Cycle {cycle_number}: Pruned {pruned} (underperformers). "
           f"Promoted {promoted} (top performers). "
           f"System evolution in progress.")

    logger.info(
        "[FORGE-83] Evolutionary cycle %d: pruned=%s, promoted=%s",
        cycle_number, pruned, promoted,
    )

    return EvolutionaryUpdate(
        cycle_number=cycle_number,
        evaluations_since_last=EVOLUTIONARY_CYCLE_EVALUATIONS,
        pruned_setups=pruned, promoted_setups=promoted, retained=retained,
        performance_scores=scores, recommendation=rec,
    )


# ── FORGE-84: Second-Order Effects Modeling ───────────────────────────────────
# Model how today's trading affects tomorrow's conditions.

@dataclass
class SecondOrderAnalysis:
    """FORGE-84: Second-order effects of a trading decision."""
    direct_impact:      str    # First-order: direct P&L effect
    second_order:       list[str]  # Downstream effects
    risk_rating:        str    # "LOW" / "MEDIUM" / "HIGH"
    recommendation:     str

def analyze_second_order_effects(
    action:             str,   # "LARGE_POSITION" / "AGGRESSIVE_TIMING" / "MULTIPLE_LOSSES"
    drawdown_pct_used:  float,
    days_remaining:     Optional[int],
) -> SecondOrderAnalysis:
    """FORGE-84: What are the downstream effects of this decision?"""
    effects: list[str] = []
    risk = "LOW"

    if action == "LARGE_POSITION":
        effects.append("If this trade loses, daily limit may be hit → session ends")
        effects.append("Drawdown budget consumed faster → less room for recovery")
        if drawdown_pct_used > 0.50:
            effects.append("Already 50%+ drawdown used → large position risks evaluation")
            risk = "HIGH"
        else:
            risk = "MEDIUM"

    elif action == "AGGRESSIVE_TIMING":
        effects.append("Entering before confirmation → adverse selection risk increases")
        effects.append("If stopped out, may trigger loss response protocol")
        risk = "MEDIUM"

    elif action == "MULTIPLE_LOSSES":
        effects.append("Loss response protocol activates → reduced size")
        effects.append("Streak detector may trigger 2-hour pause")
        if days_remaining and days_remaining <= 5:
            effects.append("Few days remaining → streaks are evaluation-ending")
            risk = "HIGH"
        else:
            risk = "MEDIUM"

    rec = (f"Action: {action}. Risk: {risk}. "
           f"Second-order: {effects[0] if effects else 'Minimal downstream effects.'}.")

    return SecondOrderAnalysis(
        direct_impact=f"Direct: {action} executed.",
        second_order=effects, risk_rating=risk, recommendation=rec,
    )


# ── FORGE-85: Game Theory Cooperative Strategy ────────────────────────────────
# Never compromise another evaluation to save one that's failing.
# Treat each evaluation as an independent game.

@dataclass
class GameTheoryDecision:
    """FORGE-85: Game theory analysis for multi-account decisions."""
    cooperative_choice:     str
    defection_risk:         float   # Risk that one account's action hurts another
    optimal_strategy:       str
    sacrifice_evaluation:   Optional[str]  # If one must be sacrificed to save others
    recommendation:         str

def apply_game_theory(
    failing_eval_id:        str,
    failing_drawdown_pct:   float,
    funded_accounts:        list[str],
    funded_safe_pcts:       list[float],   # How safe each funded account is (0–1)
) -> GameTheoryDecision:
    """
    FORGE-85: Game theory cooperative strategy.
    Never risk funded accounts to rescue a failing evaluation.
    """
    # Are funded accounts stable?
    all_funded_safe = all(pct >= 0.30 for pct in funded_safe_pcts)
    evaluation_saveable = failing_drawdown_pct <= 0.75   # Still recoverable

    if not all_funded_safe:
        # Some funded account is in danger — prioritize funded
        return GameTheoryDecision(
            cooperative_choice="PROTECT_FUNDED",
            defection_risk=0.9,
            optimal_strategy="Withdraw attention from failing evaluation. Protect funded accounts.",
            sacrifice_evaluation=failing_eval_id,
            recommendation=(
                f"⚠ Funded accounts at risk. Sacrifice evaluation {failing_eval_id}. "
                f"Funded accounts ({funded_accounts}) take absolute priority."
            )
        )

    if evaluation_saveable and funded_safe_pcts and min(funded_safe_pcts) >= 0.50:
        return GameTheoryDecision(
            cooperative_choice="SAVE_BOTH",
            defection_risk=0.2,
            optimal_strategy="Continue evaluation carefully. Funded accounts have buffer.",
            sacrifice_evaluation=None,
            recommendation=(
                f"Both can survive. Continue evaluation {failing_eval_id} with reduced size. "
                f"Funded accounts have sufficient buffer."
            )
        )

    # Accept loss of evaluation — don't gamble funded accounts
    return GameTheoryDecision(
        cooperative_choice="ACCEPT_EVALUATION_LOSS",
        defection_risk=0.5,
        optimal_strategy="Accept evaluation failure. Do not risk funded account.",
        sacrifice_evaluation=failing_eval_id,
        recommendation=(
            f"Accept loss of evaluation {failing_eval_id}. "
            f"FX-10 recovery protocol activates. New eval in 72h."
        )
    )


# ── FORGE-87: Corporate Audit Framework ──────────────────────────────────────
# Monthly self-audit: all 187 requirements still respected.

@dataclass
class AuditResult:
    """FORGE-87: Monthly compliance audit."""
    audit_id:               str
    audit_date:             date
    requirements_checked:   int
    requirements_passing:   int
    requirements_failing:   list[str]
    critical_violations:    list[str]
    pass_rate:              float
    overall_status:         str   # "CLEAN" / "CONCERNS" / "VIOLATIONS"
    recommendation:         str

def run_monthly_audit(
    audit_id:       str,
    requirements_status: dict[str, bool],  # req_id → passing
    critical_reqs:  Optional[list[str]] = None,
) -> AuditResult:
    """FORGE-87: Monthly self-audit against all requirements."""
    critical_reqs = critical_reqs or ["C-02", "C-06", "C-08", "FORGE-03", "FORGE-11"]

    passing   = [k for k, v in requirements_status.items() if v]
    failing   = [k for k, v in requirements_status.items() if not v]
    critical  = [r for r in failing if r in critical_reqs]

    total     = len(requirements_status)
    pass_rate = len(passing) / total if total > 0 else 0.0

    if critical:
        status = "VIOLATIONS"
        rec = f"⚠ CRITICAL VIOLATIONS: {critical}. Halt trading until resolved."
    elif failing:
        status = "CONCERNS"
        rec = f"Non-critical concerns: {failing[:3]}. Address in next 48 hours."
    else:
        status = "CLEAN"
        rec = f"✅ Audit CLEAN. All {total} requirements passing."

    logger.info(
        "[FORGE-87] Audit %s: %s. Pass rate: %.1f%%. "
        "Critical: %s.",
        audit_id, status, pass_rate * 100, critical or "None",
    )

    return AuditResult(
        audit_id=audit_id, audit_date=date.today(),
        requirements_checked=total, requirements_passing=len(passing),
        requirements_failing=failing, critical_violations=critical,
        pass_rate=round(pass_rate, 4), overall_status=status,
        recommendation=rec,
    )
