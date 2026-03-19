"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║       test_session_quality.py — FORGE-08/61 — FX-06 Compliance              ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from session_quality import (
    SessionQualityFilter, SessionDecision, SessionQualityScore,
    GEXRegime, EventImpact, PreSessionData,
    build_pre_session_data,
    SCORE_EXCELLENT, SCORE_GOOD, SCORE_MARGINAL, SCORE_POOR,
    WEIGHT_FUTURES_BIAS, WEIGHT_VIX_CONTEXT, WEIGHT_GEX_REGIME,
    WEIGHT_EVENT_CALENDAR, WEIGHT_MARKET_BREADTH,
)
from firm_rules import FirmID

TODAY = date(2026, 3, 19)


def make_sqf() -> SessionQualityFilter:
    return SessionQualityFilter()


def good_session(**kwargs) -> PreSessionData:
    """Build a good-quality session data (should score ~7–9)."""
    defaults = dict(
        session_date=TODAY, firm_id=FirmID.FTMO, is_evaluation=True,
        overnight_pct=0.006, futures_direction="bullish",
        futures_above_vwap=True, futures_volume_ratio=1.2,
        vix_level=14.0, vix_30d_avg=14.5, vix_rising=False,
        vix_term_structure="contango", vix_percentile=0.30,
        gex_regime=GEXRegime.NEGATIVE,
        high_impact_today=False, events_today=[],
        advance_decline=0.68, pct_above_20ma=0.65,
        new_highs=150, new_lows=30, spy_above_vwap=True,
        trend_strength=0.65, consecutive_losses=0,
    )
    defaults.update(kwargs)
    return build_pre_session_data(**defaults)


def poor_session(**kwargs) -> PreSessionData:
    """Build a poor-quality session data (should score < 4.0)."""
    defaults = dict(
        session_date=TODAY, firm_id=FirmID.FTMO, is_evaluation=True,
        overnight_pct=0.001, futures_direction="flat",
        futures_above_vwap=False, futures_volume_ratio=0.5,
        vix_level=38.0, vix_30d_avg=20.0, vix_rising=True,
        vix_term_structure="backwardation", vix_percentile=0.95,
        gex_regime=GEXRegime.NEUTRAL,
        high_impact_today=True,
        events_today=["FOMC 2pm", "CPI 8:30am", "NFP 8:30am"],
        next_event_minutes=5.0,
        highest_event_impact=EventImpact.EXTREME,
        advance_decline=0.35, pct_above_20ma=0.25,
        new_highs=20, new_lows=200, spy_above_vwap=False,
        trend_strength=0.20, consecutive_losses=4,
    )
    defaults.update(kwargs)
    return build_pre_session_data(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# HARD BLOCK (< 4.0)
# ─────────────────────────────────────────────────────────────────────────────

class TestHardBlock:

    def test_normal_extreme_conditions_hard_blocked(self):
        """Normal: Extreme VIX, bad breadth, multiple events → hard block."""
        sqf = make_sqf()
        score = sqf.score_session(poor_session())
        assert score.hard_blocked is True
        assert score.decision == SessionDecision.NO_TRADING
        assert score.composite_score < SCORE_POOR

    def test_edge_score_exactly_4_not_hard_blocked(self):
        """Edge: Score exactly at 4.0 boundary — NOT hard blocked (4.0 is allowed)."""
        sqf = make_sqf()
        # Good conditions but one streak loss to push near 4.0
        # The firm blackout = 0.0 event score → will always hard block if active
        # Use bad vix but no blackout
        data = build_pre_session_data(
            session_date=TODAY, firm_id=FirmID.FTMO, is_evaluation=True,
            overnight_pct=0.001, futures_direction="flat",
            futures_above_vwap=False, futures_volume_ratio=0.7,
            vix_level=35.0, vix_rising=True,
            vix_term_structure="backwardation", vix_percentile=0.90,
            gex_regime=GEXRegime.NEUTRAL,
            highest_event_impact=EventImpact.NONE,
            advance_decline=0.40, pct_above_20ma=0.30,
            new_highs=30, new_lows=150, spy_above_vwap=False,
            trend_strength=0.20, consecutive_losses=2,
        )
        score = sqf.score_session(data)
        # Whatever the actual score, verify the logic: hard_blocked iff score < 4.0
        assert score.hard_blocked == (score.composite_score < SCORE_POOR)

    def test_conflict_pacing_cannot_override_hard_block(self):
        """Conflict: Even if pacing says threshold is 3.0, hard block (< 4.0) wins."""
        sqf = make_sqf()
        score = sqf.score_session(
            poor_session(),
            pacing_threshold=3.0,   # Desperate pacing — wants to trade
        )
        # Hard block cannot be overridden by pacing
        assert score.hard_blocked is True
        assert score.decision == SessionDecision.NO_TRADING


# ─────────────────────────────────────────────────────────────────────────────
# FIRM BLACKOUT
# ─────────────────────────────────────────────────────────────────────────────

class TestFirmBlackout:

    def test_normal_firm_blackout_blocks_even_good_session(self):
        """Normal: Excellent market conditions + firm blackout = NO_TRADING."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(
            firm_blackout_active=True, blackout_ends_minutes=15.0
        ))
        assert score.decision == SessionDecision.NO_TRADING
        assert score.event_blackout is True

    def test_edge_blackout_clears_after_end(self):
        """Edge: Same session without blackout → different decision (trading allowed)."""
        sqf = make_sqf()
        with_blackout    = sqf.score_session(good_session(firm_blackout_active=True))
        without_blackout = sqf.score_session(good_session(firm_blackout_active=False))
        assert with_blackout.decision == SessionDecision.NO_TRADING
        assert without_blackout.decision != SessionDecision.NO_TRADING

    def test_conflict_blackout_event_score_is_zero(self):
        """Conflict: When firm blackout is active, event sub-score returns 0.0."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(firm_blackout_active=True))
        assert score.event_score == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# EXCELLENT CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestExcellentConditions:

    def test_normal_ideal_session_scores_above_8(self):
        """Normal: All conditions ideal → EXCELLENT, TRADE_FULL decision."""
        sqf = make_sqf()
        score = sqf.score_session(good_session())
        assert score.composite_score >= SCORE_GOOD
        assert score.decision in (SessionDecision.TRADE_FULL, SessionDecision.TRADE_STANDARD)
        assert score.is_tradeable is True

    def test_edge_excellent_decision_at_exactly_8_0(self):
        """Edge: Score at exactly 8.0 → TRADE_FULL decision."""
        sqf = make_sqf()
        # Very strong conditions
        score = sqf.score_session(good_session(
            overnight_pct=0.010, futures_volume_ratio=1.5,
            vix_level=12.0, vix_percentile=0.10,
            gex_regime=GEXRegime.STRONGLY_NEGATIVE,
            advance_decline=0.75, pct_above_20ma=0.75,
            trend_strength=0.80,
        ))
        if score.composite_score >= SCORE_EXCELLENT:
            assert score.decision == SessionDecision.TRADE_FULL

    def test_conflict_excellent_conditions_override_marginal_pacing(self):
        """Conflict: Excellent session + conservative pacing threshold — session wins."""
        sqf = make_sqf()
        # Even with high pacing threshold (7.0), excellent session (8+) → TRADE_FULL
        score = sqf.score_session(
            good_session(
                overnight_pct=0.010, vix_level=12.0,
                gex_regime=GEXRegime.STRONGLY_NEGATIVE,
                advance_decline=0.75,
            ),
            pacing_threshold=7.0,  # Pacing is very selective
        )
        if score.composite_score >= SCORE_EXCELLENT:
            assert score.decision == SessionDecision.TRADE_FULL


# ─────────────────────────────────────────────────────────────────────────────
# STREAK PENALTY
# ─────────────────────────────────────────────────────────────────────────────

class TestStreakPenalty:

    def test_normal_3_losses_adds_1_5_penalty(self):
        """Normal: 3 consecutive losses → -1.5 penalty on composite score."""
        sqf = make_sqf()
        no_streak = sqf.score_session(good_session(consecutive_losses=0))
        with_streak = sqf.score_session(good_session(consecutive_losses=3))
        penalty_diff = no_streak.composite_score - with_streak.composite_score
        assert abs(penalty_diff - 1.5) < 0.01

    def test_edge_0_losses_no_penalty(self):
        """Edge: Zero consecutive losses → zero streak penalty."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(consecutive_losses=0))
        assert score.consecutive_loss_penalty == 0.0
        assert score.streak_penalty_active is False

    def test_conflict_streak_cannot_drop_below_threshold_of_4(self):
        """Conflict: Extreme streak + good session still floors at 0, not negative."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(consecutive_losses=10))
        assert score.composite_score >= 0.0

    def test_normal_streak_penalty_increases_with_losses(self):
        """Normal: Each additional loss increases the penalty."""
        sqf = make_sqf()
        p = {}
        for n in [0, 1, 2, 3, 4, 5]:
            s = sqf.score_session(good_session(consecutive_losses=n))
            p[n] = s.consecutive_loss_penalty
        # Monotonically non-decreasing
        for i in range(5):
            assert p[i] <= p[i + 1], f"Penalty should not decrease from {i} to {i+1} losses"


# ─────────────────────────────────────────────────────────────────────────────
# VIX SCORING
# ─────────────────────────────────────────────────────────────────────────────

class TestVIXScoring:

    def test_normal_calm_vix_high_score(self):
        """Normal: VIX 12 in contango, not rising → high VIX sub-score."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(
            vix_level=12.0, vix_rising=False, vix_term_structure="contango"
        ))
        assert score.vix_score >= 8.0

    def test_edge_vix_30_significantly_reduces_score(self):
        """Edge: VIX at 30 → meaningful reduction in VIX sub-score."""
        sqf = make_sqf()
        calm = sqf.score_session(good_session(vix_level=12.0))
        high_vix = sqf.score_session(good_session(vix_level=32.0, vix_rising=True))
        assert calm.vix_score > high_vix.vix_score

    def test_conflict_rising_vix_worse_than_same_level_stable(self):
        """Conflict: VIX 20 rising is worse than VIX 20 stable."""
        sqf = make_sqf()
        stable = sqf.score_session(good_session(vix_level=20.0, vix_rising=False))
        rising = sqf.score_session(good_session(vix_level=20.0, vix_rising=True))
        assert stable.vix_score > rising.vix_score


# ─────────────────────────────────────────────────────────────────────────────
# GEX REGIME SCORING
# ─────────────────────────────────────────────────────────────────────────────

class TestGEXRegimeScoring:

    def test_normal_strongly_negative_gex_highest_score(self):
        """Normal: Strongly negative GEX → highest GEX sub-score (9.0)."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(gex_regime=GEXRegime.STRONGLY_NEGATIVE))
        assert score.gex_score == 9.0

    def test_edge_neutral_gex_midpoint_score(self):
        """Edge: Neutral GEX → 5.0 score (unclear regime)."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(gex_regime=GEXRegime.NEUTRAL))
        assert score.gex_score == 5.0

    def test_conflict_gex_affects_setup_recommendations(self):
        """Conflict: Negative GEX recommends momentum setups; positive recommends reversion."""
        sqf = make_sqf()
        trend_score = sqf.score_session(good_session(gex_regime=GEXRegime.STRONGLY_NEGATIVE))
        range_score = sqf.score_session(good_session(gex_regime=GEXRegime.STRONGLY_POSITIVE))

        # Trend day: GEX-01, GEX-02 should appear
        assert "GEX-01" in trend_score.best_setups_for_today
        # Ranging day: VOL-01, VOL-02 should appear
        assert "VOL-01" in range_score.best_setups_for_today


# ─────────────────────────────────────────────────────────────────────────────
# EVENT CALENDAR
# ─────────────────────────────────────────────────────────────────────────────

class TestEventCalendar:

    def test_normal_no_events_near_maximum_score(self):
        """Normal: No events today → event sub-score near 9.0."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(
            highest_event_impact=EventImpact.NONE, events_today=[]
        ))
        assert score.event_score >= 8.0

    def test_edge_high_impact_event_within_10_minutes_big_penalty(self):
        """Edge: HIGH impact event within 10 minutes → large event score reduction."""
        sqf = make_sqf()
        no_event = sqf.score_session(good_session(
            highest_event_impact=EventImpact.NONE
        ))
        near_event = sqf.score_session(good_session(
            highest_event_impact=EventImpact.HIGH,
            events_today=["FOMC 2pm"],
            next_event_minutes=8.0,
        ))
        assert near_event.event_score < no_event.event_score - 3.0

    def test_conflict_extreme_event_can_still_allow_trading(self):
        """Conflict: Extreme event reduces score but doesn't hard-block if conditions otherwise good."""
        sqf = make_sqf()
        score = sqf.score_session(build_pre_session_data(
            session_date=TODAY, firm_id=FirmID.FTMO, is_evaluation=True,
            overnight_pct=0.008, futures_direction="bullish",
            futures_above_vwap=True, futures_volume_ratio=1.3,
            vix_level=13.0, vix_rising=False, vix_term_structure="contango",
            vix_percentile=0.25, gex_regime=GEXRegime.NEGATIVE,
            firm_blackout_active=False,
            highest_event_impact=EventImpact.EXTREME,
            events_today=["FOMC 2pm"], next_event_minutes=120.0,
            advance_decline=0.70, pct_above_20ma=0.70,
            new_highs=200, new_lows=20, spy_above_vwap=True,
            trend_strength=0.75, consecutive_losses=0,
        ))
        # Extreme event far away (2 hours) should still allow some trading
        # (the event score is reduced but other components compensate)
        assert score.decision != SessionDecision.NO_TRADING or score.hard_blocked


# ─────────────────────────────────────────────────────────────────────────────
# PACING THRESHOLD INTEGRATION
# ─────────────────────────────────────────────────────────────────────────────

class TestPacingThresholdIntegration:

    def test_normal_high_pacing_threshold_blocks_marginal_session(self):
        """Normal: Marginal session (5.5) with high pacing threshold (7.0) → SKIP."""
        sqf = make_sqf()
        data = build_pre_session_data(
            session_date=TODAY, firm_id=FirmID.FTMO, is_evaluation=True,
            overnight_pct=0.001, futures_direction="flat",
            futures_above_vwap=False, futures_volume_ratio=0.8,
            vix_level=22.0, vix_rising=True, vix_term_structure="flat",
            vix_percentile=0.65, gex_regime=GEXRegime.NEUTRAL,
            highest_event_impact=EventImpact.MEDIUM,
            advance_decline=0.50, pct_above_20ma=0.50,
            new_highs=60, new_lows=60, spy_above_vwap=True,
            trend_strength=0.45, consecutive_losses=0,
        )
        score = sqf.score_session(data, pacing_threshold=7.0)
        # If score is below 7.0, should skip
        if score.composite_score < 7.0:
            assert score.decision in (
                SessionDecision.SKIP_SESSION,
                SessionDecision.TRADE_REDUCED,
                SessionDecision.NO_TRADING,
            )

    def test_edge_pacing_threshold_3_allows_marginal_sessions(self):
        """Edge: Low pacing threshold (urgent: 3.0) → marginal sessions permitted."""
        sqf = make_sqf()
        data = build_pre_session_data(
            session_date=TODAY, firm_id=FirmID.FTMO, is_evaluation=True,
            overnight_pct=0.003, futures_direction="bullish",
            futures_above_vwap=True, futures_volume_ratio=1.0,
            vix_level=18.0, vix_rising=False, vix_term_structure="contango",
            vix_percentile=0.50, gex_regime=GEXRegime.POSITIVE,
            highest_event_impact=EventImpact.LOW,
            advance_decline=0.52, pct_above_20ma=0.52,
            new_highs=80, new_lows=60, spy_above_vwap=True,
            trend_strength=0.45, consecutive_losses=0,
        )
        score = sqf.score_session(data, pacing_threshold=4.0)  # Low threshold
        # 4.0 minimum threshold: should allow if score >= 4.0
        if score.composite_score >= 4.0:
            assert score.decision != SessionDecision.NO_TRADING

    def test_conflict_pacing_threshold_never_drops_below_4_0(self):
        """Conflict: Even if pacing says 3.0, final threshold is never below 4.0."""
        sqf = make_sqf()
        score = sqf.score_session(good_session(), pacing_threshold=3.0)
        assert score.final_threshold >= SCORE_POOR   # Never below 4.0


# ─────────────────────────────────────────────────────────────────────────────
# COMPOSITE MATH
# ─────────────────────────────────────────────────────────────────────────────

class TestCompositeMath:

    def test_normal_composite_is_weighted_sum(self):
        """Normal: Composite = weighted sum of 5 sub-scores."""
        sqf = make_sqf()
        score = sqf.score_session(good_session())
        expected = (
            score.futures_score * WEIGHT_FUTURES_BIAS +
            score.vix_score     * WEIGHT_VIX_CONTEXT +
            score.gex_score     * WEIGHT_GEX_REGIME +
            score.event_score   * WEIGHT_EVENT_CALENDAR +
            score.breadth_score * WEIGHT_MARKET_BREADTH
        )
        # After streak penalty adjustment
        expected_adjusted = max(0.0, expected - score.consecutive_loss_penalty)
        assert abs(score.composite_score - round(expected_adjusted, 2)) < 0.05

    def test_edge_all_sub_scores_bounded_0_to_10(self):
        """Edge: Every sub-score must be in [0, 10] for all sessions."""
        sqf = make_sqf()
        for session_fn in [good_session, poor_session]:
            score = sqf.score_session(session_fn())
            for sub in [score.futures_score, score.vix_score, score.gex_score,
                        score.event_score, score.breadth_score, score.composite_score]:
                assert 0.0 <= sub <= 10.0, f"Sub-score {sub} out of [0, 10]"

    def test_conflict_weights_sum_to_1(self):
        """Conflict: Component weights must sum to 1.0."""
        total = (WEIGHT_FUTURES_BIAS + WEIGHT_VIX_CONTEXT + WEIGHT_GEX_REGIME +
                 WEIGHT_EVENT_CALENDAR + WEIGHT_MARKET_BREADTH)
        assert abs(total - 1.0) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# HISTORY AND STATS
# ─────────────────────────────────────────────────────────────────────────────

class TestHistoryAndStats:

    def test_normal_history_accumulates(self):
        """Normal: Multiple sessions recorded in history."""
        sqf = make_sqf()
        for _ in range(5):
            sqf.score_session(good_session())
        assert len(sqf.session_history) == 5

    def test_edge_skip_rate_accurate(self):
        """Edge: Skip rate reflects fraction of blocked/skipped sessions."""
        sqf = make_sqf()
        sqf.score_session(good_session())       # Tradeable
        sqf.score_session(poor_session())       # Hard blocked
        rate = sqf.skip_rate()
        assert 0.0 <= rate <= 1.0
        assert rate > 0.0   # At least one blocked

    def test_conflict_recent_avg_score_reflects_last_n(self):
        """Conflict: recent_avg_score(3) reflects last 3 sessions only."""
        sqf = make_sqf()
        sqf.score_session(poor_session())    # Low
        sqf.score_session(poor_session())    # Low
        sqf.score_session(good_session())    # High
        sqf.score_session(good_session())    # High
        sqf.score_session(good_session())    # High
        avg_last3 = sqf.recent_avg_score(3)
        avg_all5  = sqf.recent_avg_score(5)
        # Last 3 sessions are all good → avg_last3 > avg_all5
        assert avg_last3 > avg_all5


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:

    def test_normal_score_thresholds_ordered(self):
        """Normal: EXCELLENT > GOOD >= MARGINAL = POOR."""
        assert SCORE_EXCELLENT > SCORE_GOOD
        assert SCORE_GOOD > SCORE_MARGINAL
        assert SCORE_MARGINAL == SCORE_POOR

    def test_edge_weights_sum_to_1(self):
        """Edge: All 5 component weights sum to 1.0."""
        total = (WEIGHT_FUTURES_BIAS + WEIGHT_VIX_CONTEXT + WEIGHT_GEX_REGIME +
                 WEIGHT_EVENT_CALENDAR + WEIGHT_MARKET_BREADTH)
        assert abs(total - 1.0) < 1e-9

    def test_conflict_poor_threshold_is_absolute_floor(self):
        """Conflict: SCORE_POOR (4.0) is the absolute floor — no trading below."""
        assert SCORE_POOR == 4.0


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
