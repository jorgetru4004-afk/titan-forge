"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║          test_setup_filter.py — FORGE-06 — FX-06 Compliance                 ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from setup_filter import (
    HighProbabilitySetupFilter, SetupRecord, SetupCategory,
    MarketRegime, FilterVerdict,
    WIN_RATE_MINIMUM_EVALUATION, WIN_RATE_WARNING_ZONE,
    MIN_TRADES_MATURE, CATALYST_STACK_MINIMUM,
    EDGE_DECAY_THRESHOLD, _SETUP_DATABASE,
)


def make_filter() -> HighProbabilitySetupFilter:
    return HighProbabilitySetupFilter()


def make_custom_record(
    sid="CUSTOM-01", win_rate=0.70, trades=100,
    recent_rate=0.70, recent_count=20,
    category=SetupCategory.CUSTOM,
    regimes=(MarketRegime.ANY,),
    catalyst=False,
) -> SetupRecord:
    return SetupRecord(
        setup_id=sid, name=f"Custom Setup {sid}",
        category=category, lifetime_win_rate=win_rate,
        total_trades=trades, avg_rr=2.0,
        recent_win_rate=recent_rate, recent_trade_count=recent_count,
        best_regimes=regimes, requires_catalyst_stack=catalyst,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabaseIntegrity:

    def test_normal_all_30_strategies_loaded(self):
        """Normal: All 30 documented strategies in Section 10 are loaded."""
        f = make_filter()
        assert f.total_setups == 30

    def test_edge_all_strategies_above_60_pct(self):
        """Edge: Every strategy in the database has a win rate ≥ 60% as documented."""
        for sid, rec in _SETUP_DATABASE.items():
            assert rec.lifetime_win_rate >= WIN_RATE_MINIMUM_EVALUATION, \
                f"{sid} has win rate {rec.lifetime_win_rate:.1%} — below 60% minimum"

    def test_conflict_highest_win_rate_is_ict01(self):
        """Conflict: ICT-01 documented as 76% — must be among the highest."""
        assert _SETUP_DATABASE["ICT-01"].lifetime_win_rate == 0.76
        # GEX-01 also 75%
        assert _SETUP_DATABASE["GEX-01"].lifetime_win_rate == 0.75

    def test_normal_lowest_documented_rate_is_68_pct(self):
        """Normal: GEX-05 at 68% is the lowest documented — still well above 60%."""
        assert _SETUP_DATABASE["GEX-05"].lifetime_win_rate == 0.68

    def test_normal_institutional_setups_require_catalyst(self):
        """Normal: INS-01, INS-02, INS-03 require catalyst stack (FORGE-22)."""
        for sid in ["INS-01", "INS-02", "INS-03"]:
            assert _SETUP_DATABASE[sid].requires_catalyst_stack is True

    def test_edge_ict05_works_in_any_regime(self):
        """Edge: ICT-05 Asian Range Raid works in ANY regime."""
        assert MarketRegime.ANY in _SETUP_DATABASE["ICT-05"].best_regimes


# ─────────────────────────────────────────────────────────────────────────────
# WIN RATE GATE
# ─────────────────────────────────────────────────────────────────────────────

class TestWinRateGate:

    def test_normal_above_60_pct_approved(self):
        """Normal: Setup with 70% win rate → APPROVED on win rate gate."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="C1", win_rate=0.70, trades=100))
        result = f.check("C1")
        assert result.win_rate_gate is True
        assert result.verdict == FilterVerdict.APPROVED

    def test_edge_exactly_60_pct_approved(self):
        """Edge: Exactly 60% win rate — meets minimum, approved."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="C2", win_rate=0.60, trades=100))
        result = f.check("C2")
        assert result.win_rate_gate is True
        assert result.verdict == FilterVerdict.APPROVED

    def test_edge_below_60_pct_rejected(self):
        """Edge: 59.9% win rate — below minimum, REJECTED."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="C3", win_rate=0.599, trades=100))
        result = f.check("C3")
        assert result.win_rate_gate is False
        assert result.verdict == FilterVerdict.REJECTED
        assert "WIN RATE FAILED" in result.reason

    def test_conflict_59_pct_rejected_even_with_5r_rr(self):
        """Conflict: High R:R does NOT override win rate gate. Consistency wins."""
        f = make_filter()
        rec = make_custom_record(sid="C4", win_rate=0.59, trades=100)
        rec.avg_rr = 5.0  # Amazing R:R — irrelevant, win rate fails
        f.register_setup(rec)
        result = f.check("C4")
        assert result.verdict == FilterVerdict.REJECTED

    def test_normal_warning_zone_60_to_65_passes_with_flag(self):
        """Normal: 63% win rate — passes but triggers warning zone flag."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="C5", win_rate=0.63, trades=100))
        result = f.check("C5")
        assert result.verdict == FilterVerdict.APPROVED
        assert result.in_warning_zone is True


# ─────────────────────────────────────────────────────────────────────────────
# MATURITY GATE (FX-03)
# ─────────────────────────────────────────────────────────────────────────────

class TestMaturityGate:

    def test_normal_mature_setup_gets_approved(self):
        """Normal: 50+ trades = mature → fully approved."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="M1", trades=50))
        result = f.check("M1")
        assert result.maturity_gate is True
        assert result.verdict == FilterVerdict.APPROVED
        assert result.immature_note is None

    def test_edge_exactly_49_trades_is_immature(self):
        """Edge: 49 trades (one short) → IMMATURE verdict."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="M2", trades=49))
        result = f.check("M2")
        assert result.maturity_gate is False
        assert result.verdict == FilterVerdict.IMMATURE
        assert result.immature_note is not None
        assert "IMMATURE" in result.immature_note

    def test_edge_exactly_50_trades_is_mature(self):
        """Edge: Exactly 50 trades = mature threshold met."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="M3", trades=50))
        result = f.check("M3")
        assert result.maturity_gate is True
        assert result.verdict != FilterVerdict.IMMATURE

    def test_conflict_immature_uses_conservative_default_not_zero(self):
        """Conflict: IMMATURE setup uses 60% default — not rejected on win rate alone."""
        f = make_filter()
        # Setup with 0 trades — immature, but uses 60% default
        f.register_setup(make_custom_record(sid="M4", win_rate=0.80, trades=0))
        result = f.check("M4")
        assert result.win_rate_gate is True   # Default 60% passes the gate
        assert result.verdict == FilterVerdict.IMMATURE  # But flagged as immature
        assert result.effective_win_rate == 0.60  # Conservative default used


# ─────────────────────────────────────────────────────────────────────────────
# EDGE DECAY DETECTION
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeDecayDetection:

    def test_normal_no_decay_approved(self):
        """Normal: Stable win rate — no decay, passes edge decay gate."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="E1", win_rate=0.70, trades=100, recent_rate=0.70, recent_count=20
        ))
        result = f.check("E1")
        assert result.edge_decay_gate is True
        assert result.verdict == FilterVerdict.APPROVED

    def test_edge_exactly_10_pct_drop_triggers_decay(self):
        """Edge: Exactly 10% drop (0.70 → 0.60) triggers edge decay flag."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="E2", win_rate=0.70, trades=100, recent_rate=0.60, recent_count=20
        ))
        result = f.check("E2")
        assert result.edge_decay_gate is False
        assert result.verdict == FilterVerdict.EDGE_DECAY

    def test_edge_9_pct_drop_no_decay(self):
        """Edge: 9% drop (0.70 → 0.61) — below threshold, no decay flag."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="E3", win_rate=0.70, trades=100, recent_rate=0.61, recent_count=20
        ))
        result = f.check("E3")
        assert result.edge_decay_gate is True

    def test_conflict_edge_decay_overrides_good_win_rate(self):
        """Conflict: 75% lifetime rate but decaying → EDGE_DECAY blocks it."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="E4", win_rate=0.75, trades=100, recent_rate=0.60, recent_count=20
        ))
        result = f.check("E4")
        # Lifetime rate is 75% (well above 60%) but decay blocks it
        assert result.verdict == FilterVerdict.EDGE_DECAY
        assert result.effective_win_rate == 0.75  # Still shows the rate — decay is the blocker


# ─────────────────────────────────────────────────────────────────────────────
# REGIME COMPATIBILITY
# ─────────────────────────────────────────────────────────────────────────────

class TestRegimeCompatibility:

    def test_normal_matching_regime_approved(self):
        """Normal: Setup's optimal regime matches current regime → approved."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="R1", win_rate=0.70, trades=100,
            regimes=(MarketRegime.HIGH_VOL_TRENDING,)
        ))
        result = f.check("R1", current_regime=MarketRegime.HIGH_VOL_TRENDING)
        assert result.regime_gate is True

    def test_edge_any_regime_always_passes(self):
        """Edge: Setup that works in ANY regime always passes regime gate."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="R2", regimes=(MarketRegime.ANY,), trades=100
        ))
        for regime in MarketRegime:
            if regime == MarketRegime.ANY:
                continue
            result = f.check("R2", current_regime=regime)
            assert result.regime_gate is True, f"ANY regime must pass for {regime}"

    def test_edge_no_regime_provided_passes(self):
        """Edge: No current regime provided → regime gate passes (no info = no block)."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="R3", regimes=(MarketRegime.HIGH_VOL_TRENDING,), trades=100
        ))
        result = f.check("R3", current_regime=None)
        assert result.regime_gate is True

    def test_conflict_mismatched_regime_rejected(self):
        """Conflict: Trend-only setup in ranging market → REJECTED on regime gate."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="R4", win_rate=0.75, trades=100,
            regimes=(MarketRegime.HIGH_VOL_TRENDING,)
        ))
        result = f.check("R4", current_regime=MarketRegime.LOW_VOL_RANGING)
        assert result.regime_gate is False
        assert result.verdict == FilterVerdict.REJECTED
        assert "REGIME MISMATCH" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# CATALYST STACK GATE (FORGE-22)
# ─────────────────────────────────────────────────────────────────────────────

class TestCatalystStackGate:

    def test_normal_catalyst_setup_with_4_stack_approved(self):
        """Normal: INS-01 with 4-stack score → catalyst gate passes."""
        f = make_filter()
        result = f.check("INS-01", is_evaluation=True, catalyst_stack_score=4)
        assert result.catalyst_gate is True

    def test_edge_catalyst_setup_with_3_stack_rejected(self):
        """Edge: Mature institutional setup with only 3-stack score → REJECTED (need 4+)."""
        f = make_filter()
        # Register a mature institutional setup — catalyst gate only tests with mature setups
        f.register_setup(make_custom_record(
            sid="INST-MATURE", win_rate=0.73, trades=80,
            recent_rate=0.73, recent_count=20, catalyst=True,
        ))
        result = f.check("INST-MATURE", is_evaluation=True, catalyst_stack_score=3)
        assert result.catalyst_gate is False
        assert result.verdict == FilterVerdict.REJECTED
        assert "CATALYST STACK INSUFFICIENT" in result.reason

    def test_conflict_catalyst_requirement_only_in_evaluation(self):
        """Conflict: Catalyst stack NOT required in funded mode — same setup passes."""
        f = make_filter()
        # During evaluation: 3 stack = blocked
        eval_result = f.check("INS-01", is_evaluation=True, catalyst_stack_score=3)
        # During funded: 3 stack = OK (no catalyst requirement in funded)
        fund_result = f.check("INS-01", is_evaluation=False, catalyst_stack_score=3)

        assert eval_result.catalyst_gate is False
        assert fund_result.catalyst_gate is True

    def test_normal_non_catalyst_setup_no_requirement(self):
        """Normal: GEX-01 doesn't require catalyst stack — passes regardless."""
        f = make_filter()
        result = f.check("GEX-01", is_evaluation=True, catalyst_stack_score=0)
        assert result.catalyst_gate is True
        assert result.catalyst_required is False


# ─────────────────────────────────────────────────────────────────────────────
# UNKNOWN SETUP HANDLING
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknownSetupHandling:

    def test_normal_unknown_setup_rejected(self):
        """Normal: Unknown setup ID → REJECTED with explanation."""
        f = make_filter()
        result = f.check("UNKNOWN-99")
        assert result.verdict == FilterVerdict.REJECTED
        assert "not found" in result.reason

    def test_edge_register_then_check_works(self):
        """Edge: Register new setup, then check it — must work."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="NEW-01", win_rate=0.65, trades=60))
        result = f.check("NEW-01")
        assert result.verdict == FilterVerdict.APPROVED

    def test_conflict_duplicate_registration_raises(self):
        """Conflict: Cannot register same setup_id twice."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="DUP-01", win_rate=0.70, trades=100))
        raised = False
        try:
            f.register_setup(make_custom_record(sid="DUP-01"))
        except ValueError:
            raised = True
        assert raised


# ─────────────────────────────────────────────────────────────────────────────
# WIN RATE UPDATE
# ─────────────────────────────────────────────────────────────────────────────

class TestWinRateUpdate:

    def test_normal_update_improves_accuracy(self):
        """Normal: After 50 trades, updating win rate adjusts filter output."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="U1", win_rate=0.65, trades=30))
        before = f.check("U1")
        assert before.verdict == FilterVerdict.IMMATURE  # <50 trades

        f.update_win_rate("U1", new_lifetime_rate=0.68, total_trades=55,
                          recent_rate=0.68, recent_count=20)
        after = f.check("U1")
        assert after.verdict == FilterVerdict.APPROVED  # Now mature

    def test_edge_update_below_60_rejects(self):
        """Edge: Updated win rate drops below 60% → now REJECTED."""
        f = make_filter()
        f.register_setup(make_custom_record(sid="U2", win_rate=0.65, trades=100))
        assert f.check("U2").verdict == FilterVerdict.APPROVED

        f.update_win_rate("U2", 0.58, 120, 0.55, 20)
        result = f.check("U2")
        assert result.verdict == FilterVerdict.REJECTED

    def test_conflict_update_unknown_id_logs_error_no_crash(self):
        """Conflict: Updating unknown setup ID logs error but does not crash."""
        f = make_filter()
        # Should not raise
        f.update_win_rate("NONEXISTENT", 0.70, 100, 0.70, 20)


# ─────────────────────────────────────────────────────────────────────────────
# BATCH CHECK
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchCheck:

    def test_normal_batch_returns_all_results(self):
        """Normal: Batch check returns result for every setup."""
        f = make_filter()
        setups = ["GEX-01", "ICT-01", "VOL-01", "INS-01"]
        results = f.check_batch(setups, is_evaluation=True, catalyst_stack_score=5)
        assert len(results) == 4
        assert all(r.setup_id in setups for r in results)

    def test_edge_get_approved_filters_only_passing(self):
        """Edge: get_approved_setups returns only approved/immature results."""
        f = make_filter()
        setups = ["GEX-01", "UNKNOWN-X", "ICT-01"]
        approved = f.get_approved_setups(setups, is_evaluation=False)
        ids = [r.setup_id for r in approved]
        assert "UNKNOWN-X" not in ids
        assert "GEX-01" in ids

    def test_conflict_mixed_batch_correctly_separates_verdicts(self):
        """Conflict: Batch with approved + rejected — correct verdicts for each."""
        f = make_filter()
        # Register a mature non-catalyst setup that will pass all gates
        f.register_setup(make_custom_record(
            sid="MATURE-PASS", win_rate=0.72, trades=80,
            recent_rate=0.72, recent_count=20,
        ))
        # MATURE-PASS: 72% win rate, mature, no catalyst required → APPROVED
        # INS-01: catalyst required, score=1, 0 trades → catalyst gate fails → REJECTED
        results = f.check_batch(
            ["MATURE-PASS", "INS-01"],
            is_evaluation=True,
            catalyst_stack_score=1
        )
        by_id = {r.setup_id: r for r in results}
        assert by_id["MATURE-PASS"].verdict == FilterVerdict.APPROVED
        assert by_id["INS-01"].verdict == FilterVerdict.REJECTED


# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY STATS
# ─────────────────────────────────────────────────────────────────────────────

class TestSummaryStats:

    def test_normal_stats_contain_required_keys(self):
        """Normal: summary_stats() has all required fields."""
        f = make_filter()
        stats = f.summary_stats()
        for key in ["total_setups", "average_win_rate", "min_win_rate",
                    "max_win_rate", "edge_decaying", "filter_count"]:
            assert key in stats

    def test_edge_average_win_rate_matches_documentation(self):
        """Edge: Average win rate of 30 strategies should be ~71.8% per the document."""
        f = make_filter()
        stats = f.summary_stats()
        # Document states average win rate is 71.8%
        assert abs(stats["average_win_rate"] - 0.718) < 0.005, \
            f"Average WR {stats['average_win_rate']:.3f} ≠ documented 71.8%"

    def test_conflict_rejection_rate_increases_on_rejects(self):
        """Conflict: Rejection rate reflects blocked setups accurately."""
        f = make_filter()
        f.register_setup(make_custom_record(
            sid="PASS-RR", win_rate=0.70, trades=60,
            recent_rate=0.70, recent_count=20,
        ))
        f.check("PASS-RR")   # APPROVED → not rejected
        f.check("UNKNOWN-99")  # REJECTED → rejected
        # 1 rejected out of 2 total = 50%
        assert abs(f.filter_rejection_rate - 0.50) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:

    def test_normal_win_rate_minimum_is_60(self):
        assert WIN_RATE_MINIMUM_EVALUATION == 0.60

    def test_edge_maturity_threshold_is_50(self):
        assert MIN_TRADES_MATURE == 50

    def test_conflict_catalyst_minimum_is_4(self):
        assert CATALYST_STACK_MINIMUM == 4

    def test_normal_edge_decay_threshold_is_10_pct(self):
        assert EDGE_DECAY_THRESHOLD == 0.10

    def test_normal_warning_zone_is_65_pct(self):
        assert WIN_RATE_WARNING_ZONE == 0.65


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    passed = failed = 0
    failures = []
    for cls_name in sorted(dir()):
        cls = eval(cls_name)
        if not (isinstance(cls, type) and cls_name.startswith("Test")):
            continue
        inst = cls()
        for meth_name in sorted(dir(inst)):
            if not meth_name.startswith("test_"):
                continue
            try:
                if hasattr(inst, "setup_method"):
                    inst.setup_method()
                getattr(inst, meth_name)()
                print(f"  ✅ {cls_name}::{meth_name}")
                passed += 1
            except Exception as e:
                print(f"  ❌ {cls_name}::{meth_name}")
                failures.append((cls_name, meth_name, traceback.format_exc()))
                failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    if failures:
        for cn, mn, tb in failures:
            print(f"\nFAIL: {cn}::{mn}\n{tb}")
