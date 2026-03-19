"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║          test_clean_setup.py — FORGE-10 — FX-06 Compliance                  ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clean_setup import (
    CleanSetupFilter, CleanSetupResult, CleanRuleVerdict, SessionBias,
    make_entry_proposal,
    CHASE_THRESHOLD_STANDARD, CHASE_THRESHOLD_SWEEP, CHASE_THRESHOLD_MOMENTUM,
    EXTENSION_THRESHOLD_STANDARD, EXTENSION_THRESHOLD_MOMENTUM,
    TREND_ALIGNMENT_MINIMUM, TREND_ALIGNMENT_STRICT,
    COUNTER_TREND_EXEMPT_SETUPS, SWEEP_SETUPS, MOMENTUM_SETUPS,
)


def make_filter() -> CleanSetupFilter:
    return CleanSetupFilter()


def clean_proposal(**kwargs):
    """Build a clean proposal (should pass all rules by default)."""
    defaults = dict(
        setup_id="ICT-01",
        direction="long",
        current_price=4800.0,
        intended_entry=4800.0,   # No chase
        atr=10.0,
        vwap=4795.0,             # Within 2 ATR
        session_bias=SessionBias.BULLISH,
        trend_score=0.75,
        gex_confirms=True,
        is_evaluation=True,
    )
    defaults.update(kwargs)
    return make_entry_proposal(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# RULE 1: NO CHASING
# ─────────────────────────────────────────────────────────────────────────────

class TestNoChaseRule:

    def test_normal_at_intended_entry_passes(self):
        """Normal: Price exactly at intended entry → zero chase → PASS."""
        f = make_filter()
        p = clean_proposal(current_price=4800.0, intended_entry=4800.0, atr=10.0)
        result = f.validate(p)
        assert result.no_chase_rule.verdict == CleanRuleVerdict.PASS
        assert result.no_chase_rule.value == 0.0

    def test_edge_exactly_at_standard_threshold_passes(self):
        """Edge: Distance = exactly 0.5 ATR (standard threshold) → PASS."""
        f = make_filter()
        p = clean_proposal(
            current_price=4805.0, intended_entry=4800.0, atr=10.0  # 5/10 = 0.5 ATR
        )
        result = f.validate(p)
        assert result.no_chase_rule.verdict == CleanRuleVerdict.PASS
        assert abs(result.no_chase_rule.value - 0.5) < 1e-6

    def test_edge_one_tick_over_threshold_fails(self):
        """Edge: Distance = 0.51 ATR (just over standard 0.5) → FAIL."""
        f = make_filter()
        p = clean_proposal(
            current_price=4805.1, intended_entry=4800.0, atr=10.0  # 5.1/10 = 0.51 ATR
        )
        result = f.validate(p)
        assert result.no_chase_rule.verdict == CleanRuleVerdict.FAIL

    def test_conflict_sweep_setup_has_relaxed_threshold(self):
        """Conflict: ICT-02 (sweep) allows 1.5 ATR chase vs 0.5 for standard."""
        f = make_filter()
        # 1.0 ATR distance — fails standard, passes sweep
        standard = make_entry_proposal(
            setup_id="ICT-01", direction="long",
            current_price=4810.0, intended_entry=4800.0, atr=10.0,
            vwap=4795.0, session_bias=SessionBias.BULLISH, trend_score=0.75,
        )
        sweep = make_entry_proposal(
            setup_id="ICT-02", direction="long",
            current_price=4810.0, intended_entry=4800.0, atr=10.0,
            vwap=4795.0, session_bias=SessionBias.BULLISH, trend_score=0.75,
        )
        std_result  = f.validate(standard)
        sweep_result = f.validate(sweep)
        assert std_result.no_chase_rule.verdict   == CleanRuleVerdict.FAIL   # 1.0 > 0.5
        assert sweep_result.no_chase_rule.verdict == CleanRuleVerdict.PASS   # 1.0 ≤ 1.5

    def test_normal_retrace_entry_always_passes_chase_rule(self):
        """Normal: Retrace entry flag → chase rule PASS regardless of distance."""
        f = make_filter()
        p = clean_proposal(
            current_price=4850.0, intended_entry=4800.0, atr=10.0,  # 5.0 ATR — would normally fail
            is_retrace=True,
        )
        result = f.validate(p)
        assert result.no_chase_rule.verdict == CleanRuleVerdict.PASS

    def test_normal_momentum_setup_1_atr_passes(self):
        """Normal: Momentum setup (GEX-01) at 1.0 ATR chase → PASS (threshold 1.0)."""
        f = make_filter()
        p = make_entry_proposal(
            setup_id="GEX-01", direction="long",
            current_price=4810.0, intended_entry=4800.0, atr=10.0,
            vwap=4795.0, session_bias=SessionBias.BULLISH, trend_score=0.75,
        )
        result = f.validate(p)
        assert result.no_chase_rule.verdict == CleanRuleVerdict.PASS


# ─────────────────────────────────────────────────────────────────────────────
# RULE 2: NO EXTENDED ENTRIES
# ─────────────────────────────────────────────────────────────────────────────

class TestNoExtensionRule:

    def test_normal_close_to_vwap_passes(self):
        """Normal: Price 1 ATR from VWAP → well within 2 ATR limit → PASS."""
        f = make_filter()
        p = clean_proposal(
            current_price=4810.0, intended_entry=4810.0, vwap=4800.0, atr=10.0
            # 10/10 = 1.0 ATR from VWAP → ≤ 2.0 → PASS
        )
        result = f.validate(p)
        assert result.no_extension_rule.verdict == CleanRuleVerdict.PASS

    def test_edge_exactly_2_atr_from_vwap_passes(self):
        """Edge: Exactly 2.0 ATR from VWAP (standard threshold) → PASS."""
        f = make_filter()
        p = clean_proposal(
            current_price=4820.0, intended_entry=4820.0, vwap=4800.0, atr=10.0
            # 20/10 = 2.0 ATR → exactly at threshold → PASS
        )
        result = f.validate(p)
        assert result.no_extension_rule.verdict == CleanRuleVerdict.PASS
        assert abs(result.no_extension_rule.value - 2.0) < 1e-6

    def test_edge_2_01_atr_from_vwap_fails(self):
        """Edge: 2.01 ATR from VWAP (just over standard 2.0 threshold) → FAIL."""
        f = make_filter()
        p = clean_proposal(
            current_price=4820.1, intended_entry=4820.1, vwap=4800.0, atr=10.0
            # 20.1/10 = 2.01 ATR
        )
        result = f.validate(p)
        assert result.no_extension_rule.verdict == CleanRuleVerdict.FAIL

    def test_conflict_momentum_setup_gets_3_atr_tolerance(self):
        """Conflict: Momentum (GEX-01) at 2.5 ATR → PASS (3.0 limit); standard → FAIL."""
        f = make_filter()
        # 2.5 ATR from VWAP
        std = make_entry_proposal(
            setup_id="ICT-01", direction="long",
            current_price=4825.0, intended_entry=4825.0, atr=10.0,
            vwap=4800.0, session_bias=SessionBias.BULLISH, trend_score=0.75,
        )
        mom = make_entry_proposal(
            setup_id="GEX-01", direction="long",
            current_price=4825.0, intended_entry=4825.0, atr=10.0,
            vwap=4800.0, session_bias=SessionBias.BULLISH, trend_score=0.75,
        )
        std_r = f.validate(std)
        mom_r = f.validate(mom)
        assert std_r.no_extension_rule.verdict == CleanRuleVerdict.FAIL   # 2.5 > 2.0
        assert mom_r.no_extension_rule.verdict == CleanRuleVerdict.PASS   # 2.5 ≤ 3.0

    def test_normal_mean_reversion_exempt_from_extension_rule(self):
        """Normal: VOL-01 (mean reversion) is EXEMPT from extension rule."""
        f = make_filter()
        # Extended from VWAP is good for mean reversion — rule does not apply
        p = make_entry_proposal(
            setup_id="VOL-01", direction="short",  # Fade the extension
            current_price=4840.0, intended_entry=4840.0, atr=10.0,
            vwap=4800.0, session_bias=SessionBias.NEUTRAL, trend_score=0.70,
            # 4.0 ATR from VWAP — would fail standard, but VOL-01 is exempt
        )
        result = f.validate(p)
        assert result.no_extension_rule.verdict == CleanRuleVerdict.EXEMPT


# ─────────────────────────────────────────────────────────────────────────────
# RULE 3: NO COUNTER-TREND
# ─────────────────────────────────────────────────────────────────────────────

class TestNoCounterTrend:

    def test_normal_long_in_bullish_session_passes(self):
        """Normal: Long entry in BULLISH session → aligned → PASS."""
        f = make_filter()
        p = clean_proposal(direction="long", session_bias=SessionBias.BULLISH, trend_score=0.75)
        result = f.validate(p)
        assert result.no_counter_trend_rule.verdict == CleanRuleVerdict.PASS

    def test_normal_short_in_bullish_session_fails(self):
        """Normal: Short entry in BULLISH session → counter-trend → FAIL."""
        f = make_filter()
        p = clean_proposal(direction="short", session_bias=SessionBias.BULLISH, trend_score=0.75)
        result = f.validate(p)
        assert result.no_counter_trend_rule.verdict == CleanRuleVerdict.FAIL
        assert "counter-trend" in result.no_counter_trend_rule.reason.lower()

    def test_edge_neutral_bias_permits_either_direction(self):
        """Edge: NEUTRAL session bias → both long and short are permitted."""
        f = make_filter()
        long_p  = clean_proposal(direction="long",  session_bias=SessionBias.NEUTRAL)
        short_p = clean_proposal(direction="short", session_bias=SessionBias.NEUTRAL)
        assert f.validate(long_p).no_counter_trend_rule.verdict  == CleanRuleVerdict.PASS
        assert f.validate(short_p).no_counter_trend_rule.verdict == CleanRuleVerdict.PASS

    def test_conflict_mean_reversion_setups_exempt(self):
        """Conflict: VOL-01 short in bullish session → EXEMPT (not counter-trend violation)."""
        f = make_filter()
        p = make_entry_proposal(
            setup_id="VOL-01", direction="short",  # Fading a high
            current_price=4830.0, intended_entry=4830.0, atr=10.0,
            vwap=4800.0, session_bias=SessionBias.BULLISH, trend_score=0.50,
            # Short in bullish session — normally counter-trend
            # But VOL-01 is mean reversion → EXEMPT
        )
        result = f.validate(p)
        assert result.no_counter_trend_rule.verdict == CleanRuleVerdict.EXEMPT

    def test_normal_trend_score_below_minimum_fails(self):
        """Normal: Aligned direction but trend_score 0.55 < minimum 0.60 → FAIL."""
        f = make_filter()
        p = clean_proposal(
            direction="long", session_bias=SessionBias.BULLISH,
            trend_score=0.55,   # Below TREND_ALIGNMENT_MINIMUM (0.60)
        )
        result = f.validate(p)
        assert result.no_counter_trend_rule.verdict == CleanRuleVerdict.FAIL

    def test_normal_trend_score_exactly_at_minimum_passes(self):
        """Normal: trend_score = 0.60 exactly → PASS (at threshold)."""
        f = make_filter()
        p = clean_proposal(
            direction="long", session_bias=SessionBias.BULLISH,
            trend_score=0.60,
        )
        result = f.validate(p)
        assert result.no_counter_trend_rule.verdict == CleanRuleVerdict.PASS


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITE — ALL THREE RULES
# ─────────────────────────────────────────────────────────────────────────────

class TestCompositeValidation:

    def test_normal_perfect_setup_all_rules_pass(self):
        """Normal: Ideal conditions → all 3 rules pass → CLEAN."""
        f = make_filter()
        p = clean_proposal()
        result = f.validate(p)
        assert result.is_clean is True
        assert result.failure_count == 0
        assert len(result.failing_rules) == 0

    def test_edge_one_rule_fails_entire_setup_dirty(self):
        """Edge: One rule fails (chasing) → entire setup is DIRTY."""
        f = make_filter()
        p = clean_proposal(
            current_price=4830.0,  # 3 ATR away — chasing
            intended_entry=4800.0, atr=10.0,
        )
        result = f.validate(p)
        assert result.is_clean is False
        assert "NO_CHASE" in result.failing_rules

    def test_conflict_two_rules_fail_both_reported(self):
        """Conflict: Two rules fail → both are in failing_rules list."""
        f = make_filter()
        p = clean_proposal(
            current_price=4830.0,   # Chasing (3 ATR)
            intended_entry=4800.0, atr=10.0,
            direction="short",      # Counter-trend (BULLISH session)
            session_bias=SessionBias.BULLISH,
        )
        result = f.validate(p)
        assert result.is_clean is False
        assert result.failure_count >= 2
        assert "NO_CHASE" in result.failing_rules
        assert "NO_COUNTER_TREND" in result.failing_rules

    def test_normal_exempt_rules_count_as_pass(self):
        """Normal: EXEMPT verdict counts the same as PASS for is_clean."""
        f = make_filter()
        p = make_entry_proposal(
            setup_id="VOL-01",     # Extension exempt, counter-trend exempt
            direction="short",
            current_price=4800.0, intended_entry=4800.0, atr=10.0,
            vwap=4780.0, session_bias=SessionBias.NEUTRAL, trend_score=0.70,
        )
        result = f.validate(p)
        assert result.is_clean is True   # EXEMPT counts as PASS
        assert result.no_extension_rule.verdict == CleanRuleVerdict.EXEMPT


# ─────────────────────────────────────────────────────────────────────────────
# SETUP TYPE CLASSIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupTypeClassifications:

    def test_normal_all_exempt_setups_get_counter_trend_exempt(self):
        """Normal: Every setup in COUNTER_TREND_EXEMPT_SETUPS gets EXEMPT verdict."""
        f = make_filter()
        for sid in COUNTER_TREND_EXEMPT_SETUPS:
            p = make_entry_proposal(
                setup_id=sid, direction="short",
                current_price=4800.0, intended_entry=4800.0, atr=10.0,
                vwap=4790.0, session_bias=SessionBias.BULLISH, trend_score=0.70,
            )
            result = f.validate(p)
            assert result.no_counter_trend_rule.verdict == CleanRuleVerdict.EXEMPT, \
                f"{sid} should be EXEMPT from counter-trend rule"

    def test_edge_sweep_setups_have_chase_threshold_1_5(self):
        """Edge: Every sweep setup allows 1.5 ATR chase distance."""
        f = make_filter()
        for sid in SWEEP_SETUPS:
            p = make_entry_proposal(
                setup_id=sid, direction="long",
                current_price=4814.9, intended_entry=4800.0, atr=10.0,
                # 1.49 ATR — within 1.5 sweep threshold
                vwap=4795.0, session_bias=SessionBias.BULLISH, trend_score=0.75,
            )
            result = f.validate(p)
            assert result.no_chase_rule.verdict == CleanRuleVerdict.PASS, \
                f"{sid} should pass at 1.49 ATR (sweep threshold = 1.5)"

    def test_conflict_momentum_setups_get_extension_3_atr(self):
        """Conflict: Momentum setups get 3 ATR extension tolerance."""
        f = make_filter()
        for sid in MOMENTUM_SETUPS:
            p = make_entry_proposal(
                setup_id=sid, direction="long",
                current_price=4828.0, intended_entry=4828.0, atr=10.0,
                # 2.8 ATR from VWAP — fails standard (2.0), passes momentum (3.0)
                vwap=4800.0, session_bias=SessionBias.BULLISH, trend_score=0.75,
            )
            result = f.validate(p)
            assert result.no_extension_rule.verdict == CleanRuleVerdict.PASS, \
                f"{sid} should pass at 2.8 ATR (momentum threshold = 3.0)"


# ─────────────────────────────────────────────────────────────────────────────
# BATCH AND STATISTICS
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchAndStats:

    def test_normal_batch_returns_all_results(self):
        """Normal: validate_batch returns result for every proposal."""
        f = make_filter()
        proposals = [clean_proposal() for _ in range(5)]
        results = f.validate_batch(proposals)
        assert len(results) == 5

    def test_edge_get_clean_filters_dirty(self):
        """Edge: get_clean_proposals returns only clean setups."""
        f = make_filter()
        clean   = clean_proposal()
        dirty   = clean_proposal(current_price=4860.0, intended_entry=4800.0)  # Chasing
        results = f.get_clean_proposals([clean, dirty])
        assert len(results) == 1
        assert results[0].setup_id == clean.setup_id

    def test_conflict_clean_rate_accurate(self):
        """Conflict: clean_rate reflects actual pass/fail ratio."""
        f = make_filter()
        f.validate(clean_proposal())             # Clean
        f.validate(clean_proposal())             # Clean
        f.validate(clean_proposal(
            current_price=4860.0, intended_entry=4800.0  # Dirty
        ))
        assert abs(f.clean_rate - 2/3) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:

    def test_normal_chase_thresholds_ordered(self):
        """Normal: Sweep > Momentum > Standard chase thresholds."""
        assert CHASE_THRESHOLD_SWEEP > CHASE_THRESHOLD_MOMENTUM
        assert CHASE_THRESHOLD_MOMENTUM > CHASE_THRESHOLD_STANDARD

    def test_edge_extension_thresholds_ordered(self):
        """Edge: Momentum extension > Standard extension."""
        assert EXTENSION_THRESHOLD_MOMENTUM > EXTENSION_THRESHOLD_STANDARD

    def test_conflict_trend_strict_above_minimum(self):
        """Conflict: Strict trend threshold ≥ minimum threshold."""
        assert TREND_ALIGNMENT_STRICT >= TREND_ALIGNMENT_MINIMUM

    def test_normal_exempt_setups_not_in_momentum_setups(self):
        """Normal: Mean reversion setups are not in the momentum category."""
        overlap = COUNTER_TREND_EXEMPT_SETUPS & MOMENTUM_SETUPS
        assert len(overlap) == 0, f"Overlap between exempt and momentum: {overlap}"


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
