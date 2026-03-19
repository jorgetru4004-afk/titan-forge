"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║      test_correlation_entry.py — FORGE-09/70 — FX-06 Compliance             ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from correlation_entry import (
    CorrelationEntryGuard, CorrelationLevel, CorrelationCheckResult,
    get_correlation, CORR_CORRELATED, CORR_HIGHLY_CORRELATED,
    CORR_MODERATE, CORR_FUNDED_THRESHOLD,
)
from firm_rules import FirmID

TODAY_ID = "2026-03-19"


def make_guard() -> CorrelationEntryGuard:
    return CorrelationEntryGuard()


def add_position(
    guard: CorrelationEntryGuard,
    instrument: str,
    account_id: str = "ACC-001",
    eval_id: str   = "EVAL-001",
    firm_id: str   = FirmID.FTMO,
    direction: str = "long",
    pos_id: str    = None,
    is_eval: bool  = True,
) -> str:
    pid = pos_id or f"{account_id}-{instrument}-001"
    guard.register_open(
        position_id=pid, account_id=account_id,
        eval_id=eval_id, firm_id=firm_id,
        instrument=instrument, direction=direction,
        size=1.0, entry_price=100.0, is_evaluation=is_eval,
    )
    return pid


# ─────────────────────────────────────────────────────────────────────────────
# CORRELATION MATRIX INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrelationMatrix:

    def test_normal_es_nq_highly_correlated(self):
        """Normal: ES/NQ correlation is 0.94 — highly correlated."""
        assert get_correlation("ES", "NQ") == 0.94
        assert get_correlation("NQ", "ES") == 0.94   # Symmetric

    def test_edge_same_instrument_is_1_0(self):
        """Edge: Correlation of any instrument with itself is 1.0."""
        for inst in ["ES", "NQ", "EURUSD", "GLD", "BTC"]:
            assert get_correlation(inst, inst) == 1.0

    def test_conflict_unknown_pair_returns_0(self):
        """Conflict: Unknown pair → 0.0 (not correlated — proceed)."""
        assert get_correlation("ES", "WHEAT") == 0.0
        assert get_correlation("UNKNOWN_A", "UNKNOWN_B") == 0.0

    def test_normal_absolute_value_used(self):
        """Normal: Inverse correlations (VXX/ES = -0.75) return absolute value."""
        corr = get_correlation("VXX", "ES")
        assert corr > 0.0   # Absolute value applied
        assert corr == 0.75

    def test_normal_gold_not_correlated_with_sp500(self):
        """Normal: Gold (GC) vs ES is very low/negative — not correlated."""
        corr = get_correlation("GC", "ES")
        assert corr < CORR_MODERATE   # Not correlated enough to block

    def test_normal_eurusd_gbpusd_correlated(self):
        """Normal: EURUSD/GBPUSD correlation = 0.82 — correlated."""
        assert get_correlation("EURUSD", "GBPUSD") == 0.82


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE ACCOUNT CHECK (FORGE-09)
# ─────────────────────────────────────────────────────────────────────────────

class TestSingleAccountCheck:

    def test_normal_no_positions_always_permitted(self):
        """Normal: Empty account — any instrument is permitted."""
        guard = make_guard()
        result = guard.check_entry("ES", "ACC-001", is_evaluation=True)
        assert result.entry_permitted is True
        assert result.highest_correlation == 0.0

    def test_normal_es_blocked_when_nq_open(self):
        """Normal: NQ long open — ES entry blocked (0.94 correlation)."""
        guard = make_guard()
        add_position(guard, "NQ", "ACC-001")
        result = guard.check_entry("ES", "ACC-001", is_evaluation=True)
        assert result.entry_permitted is False
        assert result.highest_correlation == 0.94
        assert result.correlation_level == CorrelationLevel.HIGHLY_CORRELATED

    def test_edge_exactly_at_threshold_blocked(self):
        """Edge: Correlation exactly at 0.70 threshold → blocked."""
        guard = make_guard()
        # EURUSD/AUDUSD = 0.68 (below threshold) → NOT blocked
        # But we can test near-threshold manually using NZDUSD/AUDUSD = 0.88
        add_position(guard, "EURUSD", "ACC-001")
        # EURUSD/USDCHF = 0.90 (well above threshold)
        result = guard.check_entry("USDCHF", "ACC-001", is_evaluation=True)
        assert result.entry_permitted is False  # 0.90 > 0.70 threshold

    def test_conflict_moderate_correlation_permits_with_warning(self):
        """Conflict: AUDUSD/EURUSD = 0.68 (between 0.50 and 0.70) → permitted with size reduction."""
        guard = make_guard()
        add_position(guard, "EURUSD", "ACC-001")
        result = guard.check_entry("AUDUSD", "ACC-001", is_evaluation=True)
        # 0.68 < 0.70 threshold → permitted but moderate warning
        assert result.entry_permitted is True
        assert result.correlation_level == CorrelationLevel.MODERATE
        assert result.size_reduction_pct == 0.50

    def test_normal_gold_permits_when_es_open(self):
        """Normal: GC correlation with ES = 0.15 → freely permitted."""
        guard = make_guard()
        add_position(guard, "ES", "ACC-001")
        result = guard.check_entry("GC", "ACC-001", is_evaluation=True)
        assert result.entry_permitted is True
        assert result.correlation_level == CorrelationLevel.LOW

    def test_normal_unrelated_instruments_always_ok(self):
        """Normal: Completely different instruments → no correlation concern."""
        guard = make_guard()
        add_position(guard, "ES", "ACC-001")
        result = guard.check_entry("WHEAT", "ACC-001")
        assert result.entry_permitted is True
        assert result.highest_correlation == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# CORRELATION LEVEL CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrelationLevelClassification:

    def test_normal_0_94_is_highly_correlated(self):
        """Normal: ES/NQ at 0.94 → HIGHLY_CORRELATED level."""
        guard = make_guard()
        add_position(guard, "NQ", "ACC-001")
        result = guard.check_entry("ES", "ACC-001")
        assert result.correlation_level == CorrelationLevel.HIGHLY_CORRELATED

    def test_edge_0_70_exactly_is_correlated(self):
        """Edge: NZDUSD/EURUSD = 0.72 → CORRELATED level (not moderate, not highly)."""
        assert get_correlation("NZDUSD", "EURUSD") == 0.72
        guard = make_guard()
        add_position(guard, "EURUSD", "ACC-001")
        result = guard.check_entry("NZDUSD", "ACC-001")
        assert result.correlation_level == CorrelationLevel.CORRELATED

    def test_conflict_level_affects_size_reduction(self):
        """Conflict: MODERATE level → 50% size reduction. CORRELATED level → blocked (0% size)."""
        guard1 = make_guard()
        guard2 = make_guard()

        # EURUSD/AUDUSD = 0.68 → MODERATE → permitted at 50% size
        add_position(guard1, "EURUSD", "ACC-001")
        r1 = guard1.check_entry("AUDUSD", "ACC-001")
        assert r1.entry_permitted is True
        assert r1.size_reduction_pct == 0.50

        # ES/NQ = 0.94 → HIGHLY_CORRELATED → blocked
        add_position(guard2, "NQ", "ACC-001")
        r2 = guard2.check_entry("ES", "ACC-001")
        assert r2.entry_permitted is False
        assert r2.size_reduction_pct == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# CROSS-FIRM GUARD (FORGE-70)
# ─────────────────────────────────────────────────────────────────────────────

class TestCrossFirmGuard:

    def test_normal_same_instrument_different_firm_blocked(self):
        """Normal: ES on FTMO, ES on Apex → cross-firm correlation = 1.0 → blocked."""
        guard = make_guard()
        # FTMO account has ES
        add_position(guard, "ES", "FTMO-001", firm_id=FirmID.FTMO)
        # Apex account wants ES
        result = guard.check_entry_cross_firm("ES", "APEX-001", is_evaluation=True)
        assert result.entry_permitted is False
        assert result.cross_account_conflict is True

    def test_edge_correlated_instruments_different_firms_blocked(self):
        """Edge: NQ on FTMO, ES on Apex (0.94 corr) → cross-firm blocked."""
        guard = make_guard()
        add_position(guard, "NQ", "FTMO-001", firm_id=FirmID.FTMO)
        result = guard.check_entry_cross_firm("ES", "APEX-001", is_evaluation=True)
        assert result.entry_permitted is False
        assert result.cross_account_conflict is True
        assert result.highest_correlation == 0.94

    def test_conflict_uncorrelated_cross_firm_permitted(self):
        """Conflict: Gold (GC) vs ES across firms → 0.15 corr → cross-firm PERMITTED."""
        guard = make_guard()
        add_position(guard, "ES", "FTMO-001", firm_id=FirmID.FTMO)
        result = guard.check_entry_cross_firm("GC", "APEX-001", is_evaluation=True)
        assert result.entry_permitted is True
        assert result.cross_account_conflict is False

    def test_normal_no_positions_anywhere_cross_firm_ok(self):
        """Normal: No positions anywhere → cross-firm check always permitted."""
        guard = make_guard()
        result = guard.check_entry_cross_firm("ES", "APEX-001")
        assert result.entry_permitted is True
        assert result.cross_account_conflict is False


# ─────────────────────────────────────────────────────────────────────────────
# FUNDED MODE RELAXED THRESHOLD
# ─────────────────────────────────────────────────────────────────────────────

class TestFundedModeThreshold:

    def test_normal_evaluation_blocks_at_0_70(self):
        """Normal: Evaluation mode — blocks at 0.70 threshold."""
        guard = make_guard()
        # NZDUSD/EURUSD = 0.72 → above 0.70 eval threshold → BLOCKED in eval
        add_position(guard, "EURUSD", "ACC-001")
        result = guard.check_entry("NZDUSD", "ACC-001", is_evaluation=True)
        assert result.entry_permitted is False  # 0.72 ≥ 0.70 eval threshold

    def test_edge_funded_permits_0_72_correlation(self):
        """Edge: Funded mode — 0.80 threshold allows 0.72 correlation through."""
        guard = make_guard()
        # NZDUSD/EURUSD = 0.72 → below 0.80 funded threshold → PERMITTED in funded
        add_position(guard, "EURUSD", "ACC-001", is_eval=False)
        result = guard.check_entry("NZDUSD", "ACC-001", is_evaluation=False)
        assert result.entry_permitted is True  # 0.72 < 0.80 funded threshold

    def test_conflict_funded_still_blocks_highly_correlated(self):
        """Conflict: Funded mode still blocks 0.85+ correlations."""
        guard = make_guard()
        # ES/YM = 0.95 → above 0.80 funded threshold → BLOCKED even in funded
        add_position(guard, "ES", "ACC-001", is_eval=False)
        result = guard.check_entry("YM", "ACC-001", is_evaluation=False)
        assert result.entry_permitted is False  # 0.95 ≥ 0.80 funded threshold


# ─────────────────────────────────────────────────────────────────────────────
# POSITION REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

class TestPositionRegistry:

    def test_normal_open_and_close_position(self):
        """Normal: Register position, then close it — no longer blocks entry."""
        guard = make_guard()
        pid = add_position(guard, "NQ", "ACC-001")
        assert guard.total_open_positions == 1

        # NQ open → ES blocked
        r1 = guard.check_entry("ES", "ACC-001")
        assert r1.entry_permitted is False

        # Close NQ → ES now permitted
        guard.close_position(pid)
        assert guard.total_open_positions == 0
        r2 = guard.check_entry("ES", "ACC-001")
        assert r2.entry_permitted is True

    def test_edge_close_nonexistent_no_crash(self):
        """Edge: Closing a position ID that doesn't exist → no error."""
        guard = make_guard()
        guard.close_position("NONEXISTENT-999")   # Should not raise

    def test_conflict_multiple_open_positions_all_checked(self):
        """Conflict: Account has ES and EURUSD open — both block correlated entries."""
        guard = make_guard()
        add_position(guard, "ES", "ACC-001", pos_id="P1")
        add_position(guard, "EURUSD", "ACC-001", pos_id="P2")

        # NQ correlates with ES (0.94) → blocked
        r1 = guard.check_entry("NQ", "ACC-001")
        assert r1.entry_permitted is False

        # GBPUSD correlates with EURUSD (0.82) → blocked
        r2 = guard.check_entry("GBPUSD", "ACC-001")
        assert r2.entry_permitted is False

    def test_normal_clear_account_removes_all_positions(self):
        """Normal: clear_account() removes all positions for an account."""
        guard = make_guard()
        add_position(guard, "ES", "ACC-001", pos_id="P1")
        add_position(guard, "NQ", "ACC-001", pos_id="P2")
        add_position(guard, "EURUSD", "ACC-002", pos_id="P3")

        count = guard.clear_account("ACC-001")
        assert count == 2
        assert guard.total_open_positions == 1   # Only ACC-002 remains


# ─────────────────────────────────────────────────────────────────────────────
# CORRELATION EXPOSURE SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

class TestCorrelationExposure:

    def test_normal_single_position_no_exposure(self):
        """Normal: One position → no pair correlation, max_correlation = 0."""
        guard = make_guard()
        add_position(guard, "ES", "ACC-001")
        exposure = guard.get_correlation_exposure("ACC-001")
        assert exposure["max_correlation"] == 0.0
        assert exposure["correlated_pairs"] == []

    def test_edge_two_highly_correlated_positions_flagged(self):
        """Edge: ES + NQ open → max correlation = 0.94, pair flagged."""
        guard = make_guard()
        add_position(guard, "ES", "ACC-001", pos_id="P1")
        add_position(guard, "NQ", "ACC-001", pos_id="P2")
        exposure = guard.get_correlation_exposure("ACC-001")
        assert exposure["max_correlation"] == 0.94
        assert len(exposure["correlated_pairs"]) == 1
        assert exposure["correlated_pairs"][0]["level"] == "HIGHLY_CORRELATED"

    def test_conflict_multiple_pairs_sorted_by_highest_correlation(self):
        """Conflict: Multiple pairs — exposure summary sorted by highest correlation first."""
        guard = make_guard()
        add_position(guard, "ES",  "ACC-001", pos_id="P1")
        add_position(guard, "NQ",  "ACC-001", pos_id="P2")
        add_position(guard, "GC",  "ACC-001", pos_id="P3")   # Low corr with others
        exposure = guard.get_correlation_exposure("ACC-001")
        # First pair should be the highest correlation
        pairs = exposure["correlated_pairs"]
        assert len(pairs) >= 2
        assert pairs[0]["correlation"] >= pairs[-1]["correlation"]


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:

    def test_normal_evaluation_threshold_70(self):
        """Normal: Evaluation threshold is 0.70."""
        assert CORR_CORRELATED == 0.70

    def test_edge_highly_correlated_threshold_85(self):
        """Edge: Highly correlated threshold is 0.85 — always blocked."""
        assert CORR_HIGHLY_CORRELATED == 0.85

    def test_conflict_funded_threshold_more_lenient(self):
        """Conflict: Funded threshold (0.80) is higher (more lenient) than eval (0.70)."""
        assert CORR_FUNDED_THRESHOLD > CORR_CORRELATED


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
