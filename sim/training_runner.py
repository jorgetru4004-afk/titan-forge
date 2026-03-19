"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              sim/training_runner.py — Section 12 Simulation Engine          ║
║                                                                              ║
║  TRAINING RUNNER — Multi-Regime Training Management                         ║
║  Section 12: "training_runner.py — multi-regime training management"        ║
║                                                                              ║
║  Manages the full 1-week simulation training protocol:                      ║
║    Phase 1: 4 historical regime tests (P-12)                                ║
║    Phase 2: Full training run (2021–2024)                                   ║
║    Phase 3: Overfitting validation (2024–2025 out-of-sample)                ║
║    Phase 4: Capability maturity gate (all 6 must mature)                    ║
║    Phase 5: Pre-launch clearance                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sim.sim_engine import SimEngine, SimResult, SimEvaluation, CapabilityMaturity, MATURITY_THRESHOLDS
from sim.data_loader import DataLoader, REGIME_WINDOWS

logger = logging.getLogger("titan_forge.sim.runner")


@dataclass
class RegimeTestResult:
    """P-12: Result of one historical regime test."""
    regime_name:        str
    evaluation:         SimEvaluation
    passed:             bool
    win_rate:           float
    total_pnl:          float
    failure_reason:     Optional[str] = None

    PASS_WIN_RATE:      float = 0.55   # Minimum to pass a regime test
    PASS_TRADE_COUNT:   int   = 10     # Minimum trades required


@dataclass
class TrainingReport:
    """Complete training run results — presented to Jorge before first paid eval."""
    completed_at:           datetime
    # Phase 1: Regime tests
    regime_tests:           dict[str, RegimeTestResult]
    all_regimes_passed:     bool
    # Phase 2 & 3: Training vs validation
    training_result:        Optional[SimResult]
    validation_result:      Optional[SimResult]
    overfitting_ok:         bool
    overfitting_gap:        float   # training WR - validation WR (< 0.10 = OK)
    # Phase 4: Capability maturity
    capability_maturity:    CapabilityMaturity
    all_capabilities_mature: bool
    # Phase 5: Overall
    cleared_for_live:       bool
    blocking_reasons:       list[str]
    summary:                str

    def print_report(self) -> None:
        """Print readable report to logs."""
        logger.info("=" * 70)
        logger.info("TITAN FORGE SIMULATION TRAINING REPORT")
        logger.info("Completed: %s", self.completed_at.strftime("%Y-%m-%d %H:%M UTC"))
        logger.info("=" * 70)

        logger.info("\n📋 PHASE 1: REGIME TESTS (P-12)")
        for name, result in self.regime_tests.items():
            status = "✅ PASS" if result.passed else "❌ FAIL"
            logger.info(
                "  %s %-20s WR: %.1f%% | PnL: $%.0f | Trades: %d",
                status, name, result.win_rate * 100,
                result.total_pnl, result.evaluation.total_trades,
            )

        if self.training_result and self.validation_result:
            logger.info("\n📊 PHASE 2/3: OVERFITTING CHECK")
            logger.info(
                "  Training WR:    %.1f%%",
                self.training_result.overall_win_rate * 100,
            )
            logger.info(
                "  Validation WR:  %.1f%%",
                self.validation_result.overall_win_rate * 100,
            )
            logger.info(
                "  Gap:            %.1f%% (%s)",
                self.overfitting_gap * 100,
                "✅ OK" if self.overfitting_ok else "❌ OVERFIT",
            )

        logger.info("\n🧠 PHASE 4: CAPABILITY MATURITY (FX-03)")
        report = self.capability_maturity.maturity_report()
        for cap, data in report.items():
            status = "✅" if data["mature"] else "⚠️ "
            logger.info(
                "  %s %-35s %d / %d",
                status, cap, data["count"], data["threshold"],
            )

        logger.info("\n%s", "=" * 70)
        if self.cleared_for_live:
            logger.info("✅ CLEARED FOR LIVE TRADING. Start FTMO $10K warmup.")
        else:
            logger.info("❌ NOT CLEARED. Blocking reasons:")
            for reason in self.blocking_reasons:
                logger.info("  • %s", reason)
        logger.info("=" * 70)


class TrainingRunner:
    """
    Section 12: Full training management system.

    Runs the complete simulation training protocol before any real money
    is spent. Takes approximately 1 week at 100× speed.

    Usage:
        runner = TrainingRunner()
        report = runner.run_full_protocol()
        if report.cleared_for_live:
            # Green light — proceed to FTMO $10K warmup
    """

    # Overfitting tolerance: training vs validation WR gap
    MAX_OVERFITTING_GAP = 0.10   # 10% — if bigger, examine for overfitting

    def __init__(
        self,
        engine:     Optional[SimEngine] = None,
        instrument: str = "ES",
        firm_id:    str = "FTMO",
    ):
        self._engine     = engine or SimEngine()
        self._instrument = instrument
        self._firm_id    = firm_id

    def run_full_protocol(self) -> TrainingReport:
        """
        Run the complete simulation training protocol.
        Section 12: all 5 phases, must complete before first paid eval.
        """
        logger.info("[SIM][RUNNER] Starting full training protocol.")
        logger.info("[SIM][RUNNER] Instrument: %s | Firm: %s", self._instrument, self._firm_id)

        blocking_reasons: list[str] = []

        # ── Phase 1: P-12 Regime Tests ────────────────────────────────────────
        logger.info("[SIM][RUNNER] Phase 1: Running 4 regime tests (P-12)...")
        regime_tests    = self._run_regime_tests()
        all_regimes_ok  = all(r.passed for r in regime_tests.values())

        if not all_regimes_ok:
            failed = [n for n, r in regime_tests.items() if not r.passed]
            blocking_reasons.append(
                f"P-12: {len(failed)} regime test(s) failed: {failed}"
            )

        # ── Phase 2: Training Run ─────────────────────────────────────────────
        logger.info("[SIM][RUNNER] Phase 2: Training run (2021–2024)...")
        training = self._engine.run_training(
            self._instrument, self._firm_id, n_evaluations=10
        )

        # ── Phase 3: Validation Run (Overfitting Check) ───────────────────────
        logger.info("[SIM][RUNNER] Phase 3: Validation run (2024–2025 out-of-sample)...")
        validation = self._engine.run_validation(
            self._instrument, self._firm_id, n_evaluations=5
        )

        overfit_gap = abs(training.overall_win_rate - validation.overall_win_rate)
        overfit_ok  = overfit_gap <= self.MAX_OVERFITTING_GAP

        if not overfit_ok:
            blocking_reasons.append(
                f"Overfitting detected: training WR "
                f"{training.overall_win_rate:.1%} vs validation "
                f"{validation.overall_win_rate:.1%} (gap: {overfit_gap:.1%} > 10%)"
            )

        # ── Phase 4: Capability Maturity Gate (FX-03) ─────────────────────────
        logger.info("[SIM][RUNNER] Phase 4: Checking capability maturity (FX-03)...")
        maturity      = self._engine.maturity
        all_mature    = maturity.all_mature

        if not all_mature:
            mature_report = maturity.maturity_report()
            immature = [
                f"{cap} ({d['count']}/{d['threshold']})"
                for cap, d in mature_report.items()
                if not d["mature"]
            ]
            blocking_reasons.append(
                f"FX-03: Capabilities not yet mature: {immature}"
            )

        # ── Phase 5: Minimum Win Rate Check ───────────────────────────────────
        if training.overall_win_rate < 0.60:
            blocking_reasons.append(
                f"Win rate too low: {training.overall_win_rate:.1%} < 60% minimum"
            )

        cleared = len(blocking_reasons) == 0

        # ── Build Report ──────────────────────────────────────────────────────
        if cleared:
            summary = (
                f"✅ TRAINING COMPLETE. System cleared for live trading. "
                f"Training WR: {training.overall_win_rate:.1%}. "
                f"Validation WR: {validation.overall_win_rate:.1%}. "
                f"All 4 regimes passed. All 6 capabilities mature. "
                f"Next step: FTMO $10K warmup evaluation."
            )
        else:
            summary = (
                f"❌ NOT CLEARED. {len(blocking_reasons)} blocking issue(s). "
                f"Run more simulations to mature capabilities."
            )

        report = TrainingReport(
            completed_at=datetime.now(timezone.utc),
            regime_tests=regime_tests,
            all_regimes_passed=all_regimes_ok,
            training_result=training,
            validation_result=validation,
            overfitting_ok=overfit_ok,
            overfitting_gap=round(overfit_gap, 4),
            capability_maturity=maturity,
            all_capabilities_mature=all_mature,
            cleared_for_live=cleared,
            blocking_reasons=blocking_reasons,
            summary=summary,
        )

        report.print_report()
        return report

    def _run_regime_tests(self) -> dict[str, RegimeTestResult]:
        """P-12: Run all 4 required historical regime tests."""
        regime_evals = self._engine.run_all_regime_tests(
            self._instrument, self._firm_id
        )
        results = {}
        for regime_name, evaluation in regime_evals.items():
            passed = (
                evaluation.win_rate >= RegimeTestResult.PASS_WIN_RATE and
                evaluation.total_trades >= RegimeTestResult.PASS_TRADE_COUNT
            )
            failure_reason = None
            if not passed:
                if evaluation.total_trades < RegimeTestResult.PASS_TRADE_COUNT:
                    failure_reason = (
                        f"Too few trades: {evaluation.total_trades} < "
                        f"{RegimeTestResult.PASS_TRADE_COUNT}"
                    )
                else:
                    failure_reason = (
                        f"Win rate too low: {evaluation.win_rate:.1%} < "
                        f"{RegimeTestResult.PASS_WIN_RATE:.1%}"
                    )

            results[regime_name] = RegimeTestResult(
                regime_name=regime_name,
                evaluation=evaluation,
                passed=passed,
                win_rate=evaluation.win_rate,
                total_pnl=evaluation.total_pnl,
                failure_reason=failure_reason,
            )
        return results

    def run_regime_test_only(self, regime_name: str) -> RegimeTestResult:
        """Run a single regime test. Useful for re-testing after fixes."""
        evaluation = self._engine.run_regime_test(
            regime_name, self._instrument, self._firm_id
        )
        passed = (
            evaluation.win_rate >= RegimeTestResult.PASS_WIN_RATE and
            evaluation.total_trades >= RegimeTestResult.PASS_TRADE_COUNT
        )
        return RegimeTestResult(
            regime_name=regime_name, evaluation=evaluation,
            passed=passed, win_rate=evaluation.win_rate,
            total_pnl=evaluation.total_pnl,
        )

    def get_maturity_status(self) -> dict:
        """FX-03: Current capability maturity status."""
        return self._engine.maturity.maturity_report()
