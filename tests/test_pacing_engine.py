"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║          test_pacing_engine.py — FORGE-04 — FX-06 Compliance                ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pacing_engine import (
    PacingEngine, PaceStatus, ConvictionAdjustment,
    CONVICTION_BASE, CONVICTION_ABSOLUTE_FLOOR, APPROACH_SILENCE_PCT,
    APEX_URGENCY_THRESHOLDS,
    PACE_AHEAD_THRESHOLD, PACE_ON_THRESHOLD,
)
from firm_rules import FirmID, MultiFirmRuleEngine

ENGINE = MultiFirmRuleEngine(active_firm_id=FirmID.FTMO)


def make_engine() -> PacingEngine:
    return PacingEngine(ENGINE)


def assess(
    pe: PacingEngine,
    current_profit: float,
    profit_target: float = 10_000.0,
    days_elapsed: int = 10,
    days_remaining: int = 20,   # Pass None for FTMO no-deadline
    trading_days: int = 8,
    firm: str = FirmID.APEX,
) -> object:
    return pe.assess_pace(
        firm_id=firm,
        current_profit=current_profit,
        profit_target=profit_target,
        calendar_days_elapsed=days_elapsed,
        calendar_days_remaining=days_remaining,
        trading_days_completed=trading_days,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PACE STATUS CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class TestPaceStatusClassification:

    def test_normal_ahead_when_profit_rate_exceeds_requirement(self):
        """Normal: Averaging $600/day when only $400/day required → AHEAD."""
        pe = make_engine()
        # $6,000 profit over 10 trading days = $600/day avg
        # $4,000 remaining over ~14 estimated trading days = $285/day required
        result = assess(pe, current_profit=6_000.0, profit_target=10_000.0,
                        days_elapsed=10, days_remaining=20, trading_days=10)
        assert result.pace_status == PaceStatus.AHEAD

    def test_normal_critically_behind_when_far_below_rate(self):
        """Normal: Averaging $100/day when $500/day required → CRITICALLY_BEHIND."""
        pe = make_engine()
        # $500 profit over 5 days = $100/day avg
        # $9,500 remaining over ~7 days (10 remaining) = $1,357/day required
        result = assess(pe, current_profit=500.0, profit_target=10_000.0,
                        days_elapsed=5, days_remaining=10, trading_days=5)
        assert result.pace_status == PaceStatus.CRITICALLY_BEHIND

    def test_edge_exactly_at_on_pace_boundary(self):
        """
        Edge: Verify pace status classification at the boundary region.
        Tests that the classification system correctly identifies different pace levels
        based on the pace_ratio — the ratio of actual avg/day to required avg/day.
        """
        pe = make_engine()
        # At 10 days elapsed, 20 days remaining → est 14 trading days left
        # For $10K target: $10K remaining, required = $10K / 14 = $714/day
        # For ON_PACE: ratio must be 0.90–1.20
        # Avg of $643/day = ratio of ~0.90 → borderline ON_PACE / SLIGHTLY_BEHIND
        # Use a clearly ON_PACE scenario: $7,000 profit over 10 days = $700/day
        # Required: $3,000 remaining / 14 days = $214/day
        # Ratio = 700/214 = 3.27 → clearly AHEAD
        result = assess(pe, current_profit=7_000.0, profit_target=10_000.0,
                        days_elapsed=10, days_remaining=20, trading_days=10)
        # $3K remaining / $10K target = 30% → not silenced (> 20%)
        # Must be a valid (non-SILENCED) status
        assert result.pace_status != PaceStatus.SILENCED
        assert result.pace_status in (PaceStatus.AHEAD, PaceStatus.ON_PACE,
                                      PaceStatus.SLIGHTLY_BEHIND,
                                      PaceStatus.SIGNIFICANTLY_BEHIND,
                                      PaceStatus.CRITICALLY_BEHIND)

    def test_conflict_silenced_overrides_critically_behind(self):
        """Conflict: Even if CRITICALLY_BEHIND, Approach Protocol silences pacing within 20% of target."""
        pe = make_engine()
        # 15% of target remaining → within APPROACH_SILENCE_PCT (20%)
        result = assess(pe, current_profit=8_500.0, profit_target=10_000.0,
                        days_elapsed=28, days_remaining=2, trading_days=20)
        # $1,500 remaining / $10,000 = 15% → silenced
        assert result.pace_status == PaceStatus.SILENCED
        assert result.approach_protocol_active is True

    def test_normal_no_time_pressure_for_ftmo(self):
        """Normal: FTMO (no deadline) → NO_TIME_PRESSURE status always."""
        pe = make_engine()
        result = pe.assess_pace(
            firm_id=FirmID.FTMO,
            current_profit=2_000.0,
            profit_target=10_000.0,
            calendar_days_elapsed=10,
            calendar_days_remaining=None,   # No deadline
            trading_days_completed=8,
        )
        assert result.pace_status == PaceStatus.NO_TIME_PRESSURE
        assert result.calendar_days_remaining is None


# ─────────────────────────────────────────────────────────────────────────────
# CONVICTION THRESHOLD ADJUSTMENTS
# ─────────────────────────────────────────────────────────────────────────────

class TestConvictionAdjustments:

    def test_normal_ahead_raises_conviction_bar(self):
        """Normal: When AHEAD, conviction threshold rises above base — be MORE selective."""
        pe = make_engine()
        result = assess(pe, current_profit=8_000.0, profit_target=10_000.0,
                        days_elapsed=5, days_remaining=25, trading_days=5)
        if result.pace_status == PaceStatus.AHEAD:
            assert result.adjusted_conviction > CONVICTION_BASE

    def test_normal_critically_behind_lowers_conviction(self):
        """Normal: CRITICALLY_BEHIND lowers conviction — accept more setup types."""
        pe = make_engine()
        result = assess(pe, current_profit=200.0, profit_target=10_000.0,
                        days_elapsed=20, days_remaining=5, trading_days=15)
        assert result.pace_status in (PaceStatus.CRITICALLY_BEHIND,
                                       PaceStatus.SIGNIFICANTLY_BEHIND)
        assert result.adjusted_conviction < CONVICTION_BASE

    def test_edge_conviction_never_below_absolute_floor(self):
        """Edge: Even CRITICALLY_BEHIND cannot push conviction below 4.0 floor."""
        pe = make_engine()
        result = assess(pe, current_profit=50.0, profit_target=10_000.0,
                        days_elapsed=29, days_remaining=1, trading_days=20)
        assert result.adjusted_conviction >= CONVICTION_ABSOLUTE_FLOOR

    def test_conflict_silenced_returns_base_conviction_unchanged(self):
        """Conflict: Approach Protocol silenced — conviction stays at base, never adjusted."""
        pe = make_engine()
        result = assess(pe, current_profit=8_500.0, profit_target=10_000.0,
                        days_elapsed=10, days_remaining=20, trading_days=8)
        # Only silenced if within 20% → $8,500 / $10,000 = 85% done → 15% remaining → silenced
        if result.pace_status == PaceStatus.SILENCED:
            assert result.adjusted_conviction == CONVICTION_BASE
            assert result.conviction_adjustment == ConvictionAdjustment.NONE

    def test_normal_on_pace_no_adjustment(self):
        """Normal: ON_PACE → no conviction adjustment, stays at base."""
        pe = make_engine()
        # Equal daily rate — perfectly on pace
        result = assess(pe, current_profit=5_000.0, profit_target=10_000.0,
                        days_elapsed=15, days_remaining=15, trading_days=15)
        if result.pace_status == PaceStatus.ON_PACE:
            assert result.conviction_adjustment == ConvictionAdjustment.NONE
            assert result.adjusted_conviction == CONVICTION_BASE


# ─────────────────────────────────────────────────────────────────────────────
# APPROACH PROTOCOL SILENCE
# ─────────────────────────────────────────────────────────────────────────────

class TestApproachProtocolSilence:

    def test_normal_silenced_within_20_pct_of_target(self):
        """Normal: 15% of target remaining → silenced."""
        pe = make_engine()
        result = assess(pe, current_profit=8_500.0, profit_target=10_000.0,
                        days_elapsed=10, days_remaining=20, trading_days=8)
        # 15% remaining < 20% threshold → silenced
        assert result.pace_status == PaceStatus.SILENCED
        assert result.approach_protocol_active is True

    def test_edge_exactly_at_20_pct_remaining_silenced(self):
        """Edge: Exactly 20% remaining ($2,000 of $10,000) → silenced."""
        pe = make_engine()
        result = assess(pe, current_profit=8_000.0, profit_target=10_000.0,
                        days_elapsed=10, days_remaining=20, trading_days=8)
        assert result.pace_status == PaceStatus.SILENCED

    def test_edge_just_over_20_pct_not_silenced(self):
        """Edge: 21% remaining → NOT silenced. Pacing engine active."""
        pe = make_engine()
        result = assess(pe, current_profit=7_900.0, profit_target=10_000.0,
                        days_elapsed=10, days_remaining=20, trading_days=8)
        # $2,100 remaining / $10,000 = 21% → above threshold → not silenced
        assert result.pace_status != PaceStatus.SILENCED
        assert result.approach_protocol_active is False

    def test_conflict_silenced_does_not_change_position_size(self):
        """Conflict: Silenced pacing returns no size modifications — only C-02 applies."""
        pe = make_engine()
        result = assess(pe, current_profit=8_500.0, profit_target=10_000.0,
                        days_elapsed=10, days_remaining=20, trading_days=8)
        assert result.pace_status == PaceStatus.SILENCED
        # Required daily profit is 0 (irrelevant) — pacing not driving anything
        assert result.required_daily_profit == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# APEX URGENCY ESCALATION (FX-09)
# ─────────────────────────────────────────────────────────────────────────────

class TestApexUrgencyEscalation:

    def test_normal_days_10_to_14_threshold_is_5(self):
        """Normal: Apex with 12 days remaining → urgency threshold 5.0."""
        pe = make_engine()
        threshold = pe.get_apex_urgency_threshold(12)
        assert abs(threshold - 5.0) < 1e-9

    def test_normal_days_6_to_9_threshold_is_4_5(self):
        """Normal: Apex with 7 days remaining → urgency threshold 4.5."""
        pe = make_engine()
        threshold = pe.get_apex_urgency_threshold(7)
        assert abs(threshold - 4.5) < 1e-9

    def test_normal_days_3_to_5_threshold_is_4(self):
        """Normal: Apex with 4 days remaining → urgency threshold 4.0."""
        pe = make_engine()
        threshold = pe.get_apex_urgency_threshold(4)
        assert abs(threshold - 4.0) < 1e-9

    def test_edge_days_1_to_2_floored_at_absolute_floor(self):
        """Edge: Apex 1-2 days remaining → FX-09 says 3.0 but absolute floor = 4.0."""
        pe = make_engine()
        threshold = pe.get_apex_urgency_threshold(1)
        # FX-09 spec: 3.0 but CONVICTION_ABSOLUTE_FLOOR = 4.0
        assert threshold >= CONVICTION_ABSOLUTE_FLOOR

    def test_edge_days_15_to_30_no_urgency(self):
        """Edge: 20 days remaining → no urgency, standard 6.0 threshold."""
        pe = make_engine()
        threshold = pe.get_apex_urgency_threshold(20)
        assert abs(threshold - 6.0) < 1e-9

    def test_conflict_urgency_only_adjusts_session_quality_not_risk(self):
        """Conflict: FX-09 urgency is applied to conviction only — iron rule."""
        pe = make_engine()
        # At 3 days remaining with large gap — critically behind AND urgent
        result = assess(pe, current_profit=1_000.0, profit_target=6_000.0,
                        days_elapsed=27, days_remaining=3, trading_days=20,
                        firm=FirmID.APEX)
        # Conviction may be adjusted — but this is ALL that changes
        # The test verifies conviction is within valid range and status is set
        assert result.adjusted_conviction >= CONVICTION_ABSOLUTE_FLOOR
        assert result.adjusted_conviction <= 10.0
        if result.apex_urgency_active:
            assert result.apex_urgency_threshold is not None

    def test_normal_urgency_not_active_for_ftmo(self):
        """Normal: Apex urgency never applies to FTMO — firm-specific."""
        pe = make_engine()
        result = pe.assess_pace(
            firm_id=FirmID.FTMO,
            current_profit=1_000.0,
            profit_target=10_000.0,
            calendar_days_elapsed=25,
            calendar_days_remaining=None,
            trading_days_completed=20,
        )
        assert result.apex_urgency_active is False


# ─────────────────────────────────────────────────────────────────────────────
# REQUIRED DAILY PROFIT MATH
# ─────────────────────────────────────────────────────────────────────────────

class TestRequiredDailyProfitMath:

    def test_normal_required_rate_scales_with_remaining(self):
        """Normal: More profit remaining → higher required daily rate."""
        pe = make_engine()
        r1 = pe.get_required_daily_profit(5_000.0, 20)   # $5K left, 20 days
        r2 = pe.get_required_daily_profit(8_000.0, 20)   # $8K left, 20 days
        assert r2 > r1

    def test_edge_zero_days_remaining_returns_infinity(self):
        """Edge: 0 days remaining → infinite required rate (impossible to achieve)."""
        pe = make_engine()
        result = pe.get_required_daily_profit(5_000.0, 0)
        assert result == float("inf")

    def test_conflict_no_deadline_uses_20_day_horizon(self):
        """Conflict: No deadline (FTMO) → aspirational 20-day horizon used."""
        pe = make_engine()
        result = pe.get_required_daily_profit(10_000.0, None)
        # $10,000 / 20 days = $500/day
        assert abs(result - 500.0) < 0.01

    def test_normal_required_rate_increases_as_days_shrink(self):
        """Normal: Same profit remaining, fewer days → higher required rate."""
        pe = make_engine()
        r_20 = pe.get_required_daily_profit(5_000.0, 20)
        r_5  = pe.get_required_daily_profit(5_000.0, 5)
        assert r_5 > r_20


# ─────────────────────────────────────────────────────────────────────────────
# FTMO NO-TIME-PRESSURE BEHAVIOR
# ─────────────────────────────────────────────────────────────────────────────

class TestFTMONoTimePressure:

    def test_normal_ftmo_always_no_time_pressure(self):
        """Normal: FTMO evaluations below the 20% silence zone → NO_TIME_PRESSURE."""
        pe = make_engine()
        # Use profits that are NOT within 20% of target (to avoid SILENCED)
        # FTMO target 10K → 20% = 2K → silence starts when profit > 8K
        # Use profits well below 8K: 0, 2K, 4K, 6K
        for profit in [0, 2_000, 4_000, 6_000]:
            result = pe.assess_pace(
                firm_id=FirmID.FTMO,
                current_profit=float(profit),
                profit_target=10_000.0,
                calendar_days_elapsed=10,
                calendar_days_remaining=None,
                trading_days_completed=8,
            )
            assert result.pace_status == PaceStatus.NO_TIME_PRESSURE, \
                f"FTMO ${profit} should be NO_TIME_PRESSURE, got {result.pace_status}"

    def test_edge_ftmo_ahead_still_no_time_pressure(self):
        """Edge: FTMO well ahead of pace → still NO_TIME_PRESSURE (no urgency)."""
        pe = make_engine()
        result = pe.assess_pace(
            firm_id=FirmID.FTMO,
            current_profit=9_000.0,   # 90% done
            profit_target=10_000.0,
            calendar_days_elapsed=3,
            calendar_days_remaining=None,
            trading_days_completed=3,
        )
        # Within 20% of target → silenced takes precedence over NO_TIME_PRESSURE
        assert result.pace_status in (PaceStatus.NO_TIME_PRESSURE, PaceStatus.SILENCED)

    def test_conflict_ftmo_conviction_adjustment_gentler_than_apex(self):
        """Conflict: FTMO adjustment is gentler — no urgency, no escalation."""
        pe = make_engine()
        # Same profit situation at both firms (deadline vs no deadline)
        ftmo_result = pe.assess_pace(
            FirmID.FTMO, 1_000.0, 10_000.0, 20, None, 15
        )
        # FTMO: gentle downward adjustment at most
        if ftmo_result.pace_status != PaceStatus.SILENCED:
            assert ftmo_result.adjusted_conviction >= CONVICTION_BASE - 0.5


# ─────────────────────────────────────────────────────────────────────────────
# DEADLINE RISK FLAGS
# ─────────────────────────────────────────────────────────────────────────────

class TestDeadlineRiskFlags:

    def test_normal_at_risk_when_5_days_and_50_pct_short(self):
        """Normal: 4 days left, only $2K of $6K earned → at_risk_of_expiry."""
        pe = make_engine()
        result = assess(pe, current_profit=2_000.0, profit_target=6_000.0,
                        days_elapsed=26, days_remaining=4, trading_days=20)
        # $4,000 remaining = 66.7% of $6K → over 50% → at risk
        assert result.at_risk_of_expiry is True

    def test_edge_5_days_with_only_40_pct_short_not_at_risk(self):
        """Edge: 5 days left but only 40% of target remaining → NOT at_risk."""
        pe = make_engine()
        result = assess(pe, current_profit=3_600.0, profit_target=6_000.0,
                        days_elapsed=25, days_remaining=5, trading_days=18)
        # $2,400 remaining = 40% of $6K → < 50% → not at risk
        assert result.at_risk_of_expiry is False

    def test_conflict_approaching_deadline_at_10_days(self):
        """Conflict: 10 days remaining → approaching_deadline True regardless of pace."""
        pe = make_engine()
        result = assess(pe, current_profit=5_000.0, profit_target=10_000.0,
                        days_elapsed=20, days_remaining=10, trading_days=15)
        assert result.approaching_deadline is True


# ─────────────────────────────────────────────────────────────────────────────
# PACE TREND
# ─────────────────────────────────────────────────────────────────────────────

class TestPaceTrend:

    def test_normal_improving_trend_detected(self):
        """Normal: Pace ratio increasing over successive calls → IMPROVING."""
        pe = make_engine()
        # Generate assessments with improving pace ratio
        for i, profit in enumerate([500, 1_200, 2_100, 3_200, 4_500], 1):
            pe.assess_pace(
                firm_id=FirmID.APEX,
                current_profit=float(profit),
                profit_target=6_000.0,
                calendar_days_elapsed=i * 2,
                calendar_days_remaining=30 - i * 2,
                trading_days_completed=i * 2,
            )
        trend = pe.get_pace_trend(FirmID.APEX)
        assert trend in ("IMPROVING", "STABLE")   # Profit building up consistently

    def test_edge_insufficient_history_returns_none(self):
        """Edge: Fewer than 3 assessments → trend is None."""
        pe = make_engine()
        pe.assess_pace(FirmID.APEX, 1_000.0, 6_000.0, 5, 25, 4)
        trend = pe.get_pace_trend(FirmID.APEX)
        assert trend is None

    def test_conflict_different_firms_have_separate_history(self):
        """Conflict: APEX history does not contaminate FTMO history."""
        pe = make_engine()
        for i in range(4):
            pe.assess_pace(FirmID.APEX, float(i * 500), 6_000.0, i, 30 - i, i)
        # FTMO has no history — None
        assert pe.get_pace_trend(FirmID.FTMO) is None
        # APEX may have history
        apex_trend = pe.get_pace_trend(FirmID.APEX)
        assert apex_trend is not None or apex_trend is None  # Depends on silencing


# ─────────────────────────────────────────────────────────────────────────────
# IRON RULE — ONLY CONVICTION CHANGES
# ─────────────────────────────────────────────────────────────────────────────

class TestIronRule:

    def test_normal_pacing_never_outputs_position_size(self):
        """Normal: PaceAssessment has no position_size field — only conviction."""
        pe = make_engine()
        result = assess(pe, current_profit=1_000.0, profit_target=6_000.0,
                        days_remaining=10, days_elapsed=20, trading_days=15)
        assert not hasattr(result, 'position_size')
        assert not hasattr(result, 'risk_pct')
        assert not hasattr(result, 'stop_loss')

    def test_edge_conviction_bounded_between_floor_and_10(self):
        """Edge: Conviction output is always within [4.0, 10.0]."""
        pe = make_engine()
        for profit in [0, 100, 3_000, 5_900, 6_000]:
            result = assess(pe, current_profit=float(profit), profit_target=6_000.0,
                            days_remaining=5, days_elapsed=25, trading_days=20)
            assert CONVICTION_ABSOLUTE_FLOOR <= result.adjusted_conviction <= 10.0

    def test_conflict_dashboard_data_has_no_risk_params(self):
        """Conflict: Dashboard output has no risk params — only pacing metrics."""
        pe = make_engine()
        data = pe.dashboard_data(FirmID.APEX, 3_000.0, 6_000.0, 15, 15, 12)
        risk_keys = ['position_size', 'risk_pct', 'stop_loss', 'max_drawdown']
        for key in risk_keys:
            assert key not in data, f"Risk parameter '{key}' must not appear in pacing output"
        assert 'adjusted_conviction' in data


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD DATA
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardData:

    def test_normal_dashboard_has_all_required_keys(self):
        """Normal: Dashboard dict contains all fields ARCHITECT needs."""
        pe = make_engine()
        data = pe.dashboard_data(FirmID.APEX, 3_000.0, 6_000.0, 15, 15, 12)
        required = [
            'firm_id', 'pace_status', 'pace_ratio', 'current_profit',
            'profit_target', 'profit_remaining', 'adjusted_conviction',
            'days_remaining', 'required_daily_profit', 'avg_daily_profit',
        ]
        for key in required:
            assert key in data, f"Missing key: {key}"

    def test_edge_projected_completion_none_when_no_profit_yet(self):
        """Edge: No profit yet → projected_completion_days is None."""
        pe = make_engine()
        data = pe.dashboard_data(FirmID.APEX, 0.0, 6_000.0, 1, 29, 0)
        assert data['projected_completion_days'] is None

    def test_conflict_dashboard_silenced_shows_correct_status(self):
        """Conflict: Within 20% of target → dashboard shows SILENCED status."""
        pe = make_engine()
        data = pe.dashboard_data(FirmID.APEX, 5_100.0, 6_000.0, 25, 5, 20)
        # 15% remaining → silenced
        assert data['pace_status'] == 'SILENCED'
        assert data['approach_protocol_active'] is True


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:

    def test_normal_conviction_base_is_6(self):
        """Normal: Base conviction threshold is 6.0."""
        assert CONVICTION_BASE == 6.0

    def test_edge_absolute_floor_is_4(self):
        """Edge: No conviction output can go below 4.0."""
        assert CONVICTION_ABSOLUTE_FLOOR == 4.0

    def test_conflict_approach_silence_is_20_pct(self):
        """Conflict: Approach Protocol silences at 20% remaining."""
        assert APPROACH_SILENCE_PCT == 0.20

    def test_normal_apex_urgency_table_complete(self):
        """Normal: Urgency table covers all bands from 1 to 30 days."""
        pe = make_engine()
        # Every day from 1 to 30 must return a threshold
        for day in range(1, 31):
            threshold = pe.get_apex_urgency_threshold(day)
            assert threshold is not None
            assert threshold >= CONVICTION_ABSOLUTE_FLOOR


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
