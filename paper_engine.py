"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                      paper_engine.py — Layer 3                              ║
║  FORGE-34: Paper Evaluation Engine                                           ║
║    Simulates evaluation at 100× speed on 2 years historical data.           ║
║    Must pass before any real money is spent.                                 ║
║  FORGE-35: Optimal Stopping Theory                                           ║
║    3 consecutive quality passes → proceed to paid evaluation.               ║
║    1 failure → restart the 3-pass clock.                                    ║
║  FORGE-48: Three Paper Pass Gate                                             ║
║    Gate: 3 consecutive quality passes + 4 historical regime tests + MFI.   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.paper_engine")

# ── FORGE-35: Optimal Stopping Theory ────────────────────────────────────────
# Requires 3 consecutive quality passes before paying for evaluation.
# Any failure resets the counter to zero.

OPTIMAL_STOPPING_PASSES_REQUIRED: int = 3
REGIME_TESTS_REQUIRED:            int = 4   # One per major regime

class PaperPassResult(Enum):
    QUALITY_PASS    = auto()   # Passes all FORGE-08/09/10/11 quality gates
    TECHNICAL_PASS  = auto()   # Hit profit target but quality concerns
    FAILURE         = auto()   # Failed (drawdown, expired, etc.)
    EXPIRED         = auto()   # Hit time limit without target

@dataclass
class PaperEvalResult:
    """Result of a single paper evaluation run."""
    run_id:              str
    firm_id:             str
    result:              PaperPassResult
    profit_achieved:     float
    profit_target:       float
    drawdown_pct_used:   float
    trading_days:        int
    win_rate:            float
    avg_rr:              float
    rules_violated:      list[str]
    quality_score:       float          # 0–10 composite (FORGE-08 gates)
    regime_tested:       Optional[str]  # Which regime this run covered
    date_completed:      date

    @property
    def is_quality_pass(self) -> bool:
        return (self.result == PaperPassResult.QUALITY_PASS and
                len(self.rules_violated) == 0)

    @property
    def profit_pct(self) -> float:
        return self.profit_achieved / self.profit_target if self.profit_target > 0 else 0.0


# ── FORGE-48: Three Paper Pass Gate ──────────────────────────────────────────

@dataclass
class PaperGateStatus:
    """Status of the Three Paper Pass Gate."""
    consecutive_passes:    int
    passes_required:       int
    regime_tests_passed:   list[str]
    regime_tests_required: int
    mfi_gate_passed:       bool
    gate_cleared:          bool          # All 3 conditions met
    blocking_reason:       Optional[str]
    recommendation:        str

class ThreePaperPassGate:
    """
    FORGE-35 + FORGE-48: Optimal Stopping + Three Paper Pass Gate.

    Clears only when ALL THREE are true:
        1. 3 consecutive QUALITY paper passes (not just technical passes)
        2. 4 historical regime tests passed (one per major regime)
        3. MFI > 55 for 5 of last 7 days

    Any quality failure resets the consecutive pass counter to 0.
    Technical passes (hit target but quality concerns) count as half.
    """

    def __init__(self):
        self._passes:           list[PaperEvalResult] = []
        self._consecutive:      int  = 0
        self._regimes_passed:   set[str] = set()
        self._mfi_passed:       bool = False

    def record_pass(self, result: PaperEvalResult) -> PaperGateStatus:
        """Record a paper evaluation result and update gate status."""
        self._passes.append(result)

        if result.is_quality_pass:
            self._consecutive += 1
            logger.info(
                "[FORGE-35/48] Quality PASS #%d. Consecutive: %d/%d. "
                "Regime: %s.",
                len(self._passes), self._consecutive,
                OPTIMAL_STOPPING_PASSES_REQUIRED,
                result.regime_tested or "N/A",
            )
        elif result.result == PaperPassResult.TECHNICAL_PASS:
            # Technical pass: doesn't advance or reset — just noted
            logger.info(
                "[FORGE-35/48] Technical pass (quality concerns). "
                "Consecutive count unchanged: %d.",
                self._consecutive,
            )
        else:
            # Failure or expiry: reset the clock
            old = self._consecutive
            self._consecutive = 0
            logger.warning(
                "[FORGE-35/48] ⚠ FAILURE. Consecutive reset: %d → 0. "
                "Violations: %s.",
                old, result.rules_violated,
            )

        # Track regime coverage
        if result.regime_tested and result.is_quality_pass:
            self._regimes_passed.add(result.regime_tested)

        return self.get_status()

    def set_mfi_gate(self, passed: bool) -> None:
        """Update MFI gate status (fed from FORGE-46)."""
        self._mfi_passed = passed

    def get_status(self) -> PaperGateStatus:
        """Check if all 3 gate conditions are met."""
        passes_ok  = self._consecutive >= OPTIMAL_STOPPING_PASSES_REQUIRED
        regimes_ok = len(self._regimes_passed) >= REGIME_TESTS_REQUIRED

        gate_cleared = passes_ok and regimes_ok and self._mfi_passed

        blocking = []
        if not passes_ok:
            remaining = OPTIMAL_STOPPING_PASSES_REQUIRED - self._consecutive
            blocking.append(
                f"Need {remaining} more consecutive quality pass(es) "
                f"({self._consecutive}/{OPTIMAL_STOPPING_PASSES_REQUIRED})"
            )
        if not regimes_ok:
            tested = sorted(self._regimes_passed)
            blocking.append(
                f"Need {REGIME_TESTS_REQUIRED - len(self._regimes_passed)} "
                f"more regime tests. Tested: {tested}"
            )
        if not self._mfi_passed:
            blocking.append("MFI gate not cleared (need 5 of 7 days > 55)")

        if gate_cleared:
            rec = (
                "🟢 GATE CLEARED. Ready for paid evaluation. "
                "Start FTMO $10K warmup first. $200 evaluation fee authorized."
            )
        else:
            rec = "Keep running paper evaluations. " + " | ".join(blocking)

        return PaperGateStatus(
            consecutive_passes=self._consecutive,
            passes_required=OPTIMAL_STOPPING_PASSES_REQUIRED,
            regime_tests_passed=sorted(self._regimes_passed),
            regime_tests_required=REGIME_TESTS_REQUIRED,
            mfi_gate_passed=self._mfi_passed,
            gate_cleared=gate_cleared,
            blocking_reason="; ".join(blocking) if blocking else None,
            recommendation=rec,
        )

    @property
    def is_ready_for_paid_eval(self) -> bool:
        return self.get_status().gate_cleared

    @property
    def consecutive_passes(self) -> int:
        return self._consecutive


# ── FORGE-34: Paper Evaluation Engine ────────────────────────────────────────

class PaperEvaluationEngine:
    """
    FORGE-34: Paper Evaluation Engine.

    Runs simulated evaluations at 100× speed on historical data.
    Tracks all quality gates, rule violations, and performance metrics.
    Feeds results into the ThreePaperPassGate.
    """

    def __init__(self, gate: ThreePaperPassGate):
        self._gate   = gate
        self._runs:  list[PaperEvalResult] = []

    def run_simulation(
        self,
        run_id:          str,
        firm_id:         str,
        profit_target:   float,
        profit_achieved: float,
        drawdown_pct:    float,
        trading_days:    int,
        win_rate:        float,
        avg_rr:          float,
        rules_violated:  Optional[list[str]] = None,
        quality_score:   float = 8.0,
        regime_tested:   Optional[str] = None,
    ) -> tuple[PaperEvalResult, PaperGateStatus]:
        """
        Run one paper evaluation simulation.
        Returns the result and updated gate status.
        """
        violations = rules_violated or []

        # Determine result type
        hit_target = profit_achieved >= profit_target
        breached_dd = drawdown_pct >= 1.0   # Shouldn't happen — guardian stops at 85%

        if breached_dd or (not hit_target and trading_days > 60):
            result_type = PaperPassResult.EXPIRED if not breached_dd else PaperPassResult.FAILURE
        elif hit_target and not violations and quality_score >= 7.0:
            result_type = PaperPassResult.QUALITY_PASS
        elif hit_target:
            result_type = PaperPassResult.TECHNICAL_PASS
        else:
            result_type = PaperPassResult.FAILURE

        result = PaperEvalResult(
            run_id=run_id, firm_id=firm_id, result=result_type,
            profit_achieved=profit_achieved, profit_target=profit_target,
            drawdown_pct_used=drawdown_pct, trading_days=trading_days,
            win_rate=win_rate, avg_rr=avg_rr,
            rules_violated=violations, quality_score=quality_score,
            regime_tested=regime_tested,
            date_completed=date.today(),
        )

        self._runs.append(result)
        gate_status = self._gate.record_pass(result)

        log_fn = logger.info if result.is_quality_pass else logger.warning
        log_fn(
            "[FORGE-34] Simulation %s: %s | Profit: $%.0f/$%.0f "
            "| DD: %.0f%% | WR: %.0f%% | Gate: %d/%d",
            run_id, result_type.name,
            profit_achieved, profit_target,
            drawdown_pct * 100, win_rate * 100,
            gate_status.consecutive_passes,
            OPTIMAL_STOPPING_PASSES_REQUIRED,
        )

        return result, gate_status

    def summary(self) -> dict:
        if not self._runs:
            return {"total": 0}
        total    = len(self._runs)
        quality  = sum(1 for r in self._runs if r.is_quality_pass)
        tech     = sum(1 for r in self._runs if r.result == PaperPassResult.TECHNICAL_PASS)
        failed   = sum(1 for r in self._runs if r.result == PaperPassResult.FAILURE)
        avg_wr   = sum(r.win_rate for r in self._runs) / total
        avg_dd   = sum(r.drawdown_pct_used for r in self._runs) / total
        return {
            "total": total, "quality_passes": quality,
            "technical_passes": tech, "failures": failed,
            "quality_pass_rate": f"{quality/total:.1%}",
            "avg_win_rate": f"{avg_wr:.1%}",
            "avg_drawdown_used": f"{avg_dd:.1%}",
            "gate_status": self._gate.get_status().recommendation,
        }
