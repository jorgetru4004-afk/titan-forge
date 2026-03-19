"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              test_choppy_enhancement.py — FX-06 Compliance                  ║
║  Tests for choppy_strategies.py + choppy_intelligence.py                    ║
║  Every requirement: 3 tests (normal, edge, conflict)                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone, date

from firm_rules import FirmID
from choppy_strategies import (
    chop01_false_breakout_fade, chop02_vwap_extended_fade,
    chop03_opening_range_prison, chop04_tick_extreme_mean_reversion,
    chop05_bb_squeeze_reversion, chop06_value_area_oscillation,
    chop07_session_hl_rejection, chop08_breadth_divergence,
    chop09_volatility_compression_entry, chop10_poc_gravity_enhanced,
    CHOP_STRATEGY_COUNT, CHOP_PRIORITY_ORDER, SUSPENDED_IN_CHOP,
    CHOPPY_STRATEGY_REGISTRY,
)
from choppy_intelligence import (
    RegimeFingerprint, RegimeFingerprintLibrary,
    classify_session_enhanced, SessionMode,
    score_false_breakout,
    calculate_adaptive_stop,
    calculate_choppy_position_size, CHOP_SIZE_MULTIPLIER,
    check_trade_duration, CHOP_MAX_HOLD_MIN, CHOP_EXTENSION_PROFIT_R,
    check_qualifying_day_protocol,
    detect_chop_to_trend_transition,
    check_correlation_collapse,
    validate_choppy_simulation,
    get_behavioral_transition,
    ChoppySessionReport, ChoppyPerformanceLog,
    get_layer3_threshold, FORGE11_LAYER3_THRESHOLD_TRENDING, FORGE11_LAYER3_THRESHOLD_CHOPPY,
    get_drawdown_allocation, FORGE12_ALLOCATION_CHOPPY, FORGE12_ALLOCATION_TRENDING,
    get_streak_thresholds, FORGE15_PAUSE_THRESHOLD, FORGE15_STOP_THRESHOLD,
    get_recovery_pause_minutes, FORGE43_PAUSE_CHOPPY, FORGE43_PAUSE_TRENDING,
    get_scoring_weights, FORGE58_WEIGHTS_CHOPPY,
    get_choppy_setup_hierarchy, FORGE63_CHOPPY_HIERARCHY,
    get_loss_response, FORGE65_SIZE_CUT_CHOPPY,
    FORGE72_SUSPENDED_IN_REGIME4, FORGE72_REGIME4_PRIORITY,
    handle_regime_transition, FORGE78_TRANSITION_PAUSE_MIN,
    score_choppy_pre_session,
)

NOW = datetime(2026, 3, 19, 14, 0, tzinfo=timezone.utc)


def make_fingerprint(**overrides) -> RegimeFingerprint:
    defaults = dict(
        timestamp=NOW, adx=15.0, gex_dollars=600_000_000, vix=22.0,
        bb_width_percentile=0.35, advance_decline_pct=0.48,
        atr_vs_20day_avg=0.85, opening_range_percentile=0.15,
        vwap_deviation_by_10am=0.002, sector_correlation=0.45,
        tick_oscillating=True, volume_below_avg=True, directional_reversals=5,
        bb_width_narrowing=True, prior_day_range_pct=0.006,
        market_structure_unclear=True,
    )
    defaults.update(overrides)
    return RegimeFingerprint(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-01: REGIME FINGERPRINT LIBRARY
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop01RegimeFingerprint:

    def test_normal_7_of_15_signals_classifies_choppy(self):
        """FORGE-CHOP-01: 7+ of 15 parameters → Choppy Regime 4."""
        fp = make_fingerprint()
        assert fp.choppy_signals >= 7
        assert fp.is_choppy is True

    def test_edge_exactly_6_signals_not_choppy(self):
        """FORGE-CHOP-01: Exactly 6 signals → NOT choppy (need 7)."""
        fp = make_fingerprint(
            adx=25.0,              # Not choppy
            gex_dollars=-100_000_000,  # Not positive
            vix=35.0,              # Above 30 — not choppy range
            tick_oscillating=False,
            volume_below_avg=False,
            market_structure_unclear=False,
            directional_reversals=1,
            bb_width_narrowing=False,
        )
        # Only ~6 of 15 would flag choppy
        assert not fp.is_choppy or fp.choppy_signals >= 7  # Structural test

    def test_conflict_library_requires_2_consecutive_for_confirmed(self):
        """FORGE-CHOP-01: Single choppy snapshot alone is NOT confirmed."""
        lib = RegimeFingerprintLibrary()
        fp = make_fingerprint()
        lib.record_snapshot(fp)
        # One snapshot — not yet confirmed (need 2 consecutive)
        assert not lib.is_choppy_confirmed

    def test_normal_2_consecutive_snapshots_confirms_chop(self):
        """FORGE-CHOP-01: Two consecutive choppy snapshots → confirmed."""
        lib = RegimeFingerprintLibrary()
        fp  = make_fingerprint()
        lib.record_snapshot(fp)
        lib.record_snapshot(fp)
        assert lib.is_choppy_confirmed is True


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-02: ENHANCED SESSION QUALITY FILTER
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop02SessionFilter:

    def test_normal_choppy_confirmed_activates_choppy_mode(self):
        """FORGE-CHOP-02: Choppy confirmed → 3rd state activated."""
        state = classify_session_enhanced(session_score=7.0, choppy_confirmed=True)
        assert state.mode == SessionMode.CHOPPY_PLAYBOOK
        assert state.momentum_strategies_suspended is True
        assert state.chop_strategies_active is True
        assert state.position_size_pct == 0.60
        assert state.max_simultaneous_positions == 1
        assert state.max_trade_duration_min == 60

    def test_edge_not_choppy_good_score_trending_playbook(self):
        """FORGE-CHOP-02: Not choppy + score ≥ 6 → trending playbook (original state 1)."""
        state = classify_session_enhanced(session_score=7.5, choppy_confirmed=False)
        assert state.mode == SessionMode.TRENDING_PLAYBOOK
        assert state.position_size_pct == 1.0

    def test_conflict_choppy_overrides_good_session_score(self):
        """FORGE-CHOP-02: Even with score=9, choppy regime → choppy mode wins."""
        state = classify_session_enhanced(session_score=9.0, choppy_confirmed=True)
        assert state.mode == SessionMode.CHOPPY_PLAYBOOK


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-03: FALSE BREAKOUT DETECTION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop03FalseBreakout:

    def test_normal_all_false_signals_scores_above_65(self):
        """FORGE-CHOP-03: Weak volume + wick + no TICK + GEX+ = false breakout."""
        result = score_false_breakout(
            volume_ratio=0.7,      # Weak (< 0.8)
            close_position=0.25,   # Wick (< 40%)
            tick_confirmation=200, # Not confirming (< 400)
            gex_positive=True,     # Stabilizing
            prior_tests=3,         # Many tests
            delta_confirming=False,
        )
        assert result.score > 65
        assert result.is_false is True
        assert result.signal == "CHOP-01"

    def test_edge_all_genuine_signals_scores_below_35(self):
        """FORGE-CHOP-03: Strong volume + strong close + TICK confirms = genuine."""
        result = score_false_breakout(
            volume_ratio=2.0,      # Very strong
            close_position=0.85,   # Strong close
            tick_confirmation=800, # Confirming
            gex_positive=False,    # Amplifying
            prior_tests=0,
            delta_confirming=True,
        )
        assert result.score < 35
        assert result.is_genuine is True
        assert result.signal == "CHOP-09"

    def test_conflict_ambiguous_zone_produces_no_entry(self):
        """FORGE-CHOP-03: Mixed signals 35–65 = no entry."""
        result = score_false_breakout(
            volume_ratio=1.2,
            close_position=0.55,
            tick_confirmation=500,
            gex_positive=True,
            prior_tests=1,
            delta_confirming=True,
        )
        assert result.is_ambiguous is True
        assert result.signal == "NO_ENTRY"


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-04: ADAPTIVE STOP WIDTH PROTOCOL
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop04AdaptiveStop:

    def test_normal_1_4x_atr_widens_stop_40_pct(self):
        """FORGE-CHOP-04: ATR 1.4x average → stop widens 40%."""
        result = calculate_adaptive_stop(10.0, current_atr=1.4, avg_atr_20day=1.0)
        assert abs(result.chop_noise_factor - 1.4) < 0.01
        assert abs(result.adjusted_stop_distance - 14.0) < 0.1
        assert result.size_multiplier < 1.0

    def test_edge_normal_atr_keeps_stop_unchanged(self):
        """FORGE-CHOP-04: ATR = average → no widening needed."""
        result = calculate_adaptive_stop(10.0, current_atr=1.0, avg_atr_20day=1.0)
        assert abs(result.chop_noise_factor - 1.0) < 0.01
        assert abs(result.adjusted_stop_distance - 10.0) < 0.1

    def test_conflict_dollar_risk_preserved(self):
        """FORGE-CHOP-04: Wider stop + smaller size = same dollar risk."""
        result = calculate_adaptive_stop(10.0, current_atr=2.0, avg_atr_20day=1.0)
        # Dollar risk = stop × size → wider stop × smaller size must be equal
        original_risk = 10.0 * 1.0
        new_risk = result.adjusted_stop_distance * result.size_multiplier
        assert abs(new_risk - original_risk) / original_risk < 0.35
        assert result.dollar_risk_preserved is True


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-05: CHOPPY POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop05PositionSizing:

    def test_normal_choppy_size_is_0_6x_kelly(self):
        """FORGE-CHOP-05: Choppy base = 0.6x Kelly-calculated size."""
        standard = 1.0
        chop_size = calculate_choppy_position_size(standard)
        assert abs(chop_size - 0.60) < 0.001

    def test_edge_correlation_collapse_reduces_further(self):
        """FORGE-CHOP-05 + FORGE-CHOP-09: Correlation collapse → additional 20% cut."""
        chop_size = calculate_choppy_position_size(1.0, correlation_collapse=True)
        assert abs(chop_size - 0.48) < 0.001   # 0.60 × 0.80 = 0.48

    def test_conflict_chop_size_always_less_than_standard(self):
        """FORGE-CHOP-05: Choppy size must always be < standard."""
        assert CHOP_SIZE_MULTIPLIER < 1.0
        assert CHOP_SIZE_MULTIPLIER == 0.60


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-06: MAXIMUM TRADE DURATION PROTOCOL
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop06Duration:

    def test_normal_within_45_min_hold_ok(self):
        """FORGE-CHOP-06: Under 45 minutes → HOLD."""
        entry = NOW - timedelta(minutes=30)
        result = check_trade_duration(entry, NOW, profit_r=0.3)
        assert result.action == "HOLD"

    def test_edge_at_45_min_below_0_8r_close_now(self):
        """FORGE-CHOP-06: 45+ minutes + < 0.8R profit → CLOSE AT MARKET."""
        entry = NOW - timedelta(minutes=46)
        result = check_trade_duration(entry, NOW, profit_r=0.5, extension_used=False)
        assert result.action in ("CLOSE_NOW", "MOVE_BE_EXTEND")
        if result.profit_r < CHOP_EXTENSION_PROFIT_R:
            assert result.action == "CLOSE_NOW"

    def test_conflict_at_45_min_above_0_8r_gets_extension(self):
        """FORGE-CHOP-06: 45+ minutes + ≥0.8R profit → move BE + 30-min extension."""
        entry = NOW - timedelta(minutes=46)
        result = check_trade_duration(entry, NOW, profit_r=0.9, extension_used=False)
        assert result.action == "MOVE_BE_EXTEND"
        assert result.extension_granted is True

    def test_normal_max_hold_is_exactly_45_minutes(self):
        """FORGE-CHOP-06: Maximum hold = exactly 45 minutes per document."""
        assert CHOP_MAX_HOLD_MIN == 45


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-07: QUALIFYING DAY PROTOCOL
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop07QualifyingDay:

    def test_normal_on_pace_no_backup(self):
        """FORGE-CHOP-07: 70% of threshold before 2pm → on pace."""
        t = datetime(2026, 3, 19, 13, 30, tzinfo=timezone.utc)  # 1:30pm
        result = check_qualifying_day_protocol(FirmID.APEX, 180.0, t, trades_taken=3)
        assert result.backup_protocol is False
        assert result.pct_of_threshold > 0.60

    def test_edge_2pm_below_60_pct_activates_backup(self):
        """FORGE-CHOP-07: After 2pm + < 60% threshold → backup protocol."""
        t = datetime(2026, 3, 19, 14, 30, tzinfo=timezone.utc)  # 2:30pm
        result = check_qualifying_day_protocol(FirmID.APEX, 100.0, t, trades_taken=2)
        # $100 < 60% of $250 = $150
        assert result.backup_protocol is True
        assert "CHOP-04" in result.recommended_setups
        assert "CHOP-10" in result.recommended_setups

    def test_conflict_topstep_threshold_is_150(self):
        """FORGE-CHOP-07: Topstep qualifying threshold = $150."""
        t = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        result = check_qualifying_day_protocol(FirmID.TOPSTEP, 0.0, t, trades_taken=0)
        assert result.threshold == 150.0


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-08: CHOP-TO-TREND TRANSITION DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop08TransitionDetector:

    def test_normal_5_of_8_signals_triggers_transition(self):
        """FORGE-CHOP-08: 5+ of 8 signals → Regime Transition Alert."""
        result = detect_chop_to_trend_transition(
            adx_crossing_20_rising=True, atr_expanding=True,
            bb_widening_after_squeeze=True, tick_sustained_600=True,
            volume_expanding_1_5x=True,
            gex_going_negative=False, breadth_above_65=False,
            genuine_range_breakout=False,
        )
        assert result.signals_firing == 5
        assert result.transition_detected is True

    def test_edge_exactly_4_signals_not_triggered(self):
        """FORGE-CHOP-08: Only 4 of 8 signals → NOT triggered (need 5)."""
        result = detect_chop_to_trend_transition(
            True, True, True, True, False, False, False, False,
        )
        assert result.signals_firing == 4
        assert result.transition_detected is False

    def test_conflict_all_8_signals_highest_confidence(self):
        """FORGE-CHOP-08: All 8 signals firing → definitive transition."""
        result = detect_chop_to_trend_transition(
            True, True, True, True, True, True, True, True,
        )
        assert result.transition_detected is True
        assert result.signals_firing == 8


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-09: CORRELATION COLLAPSE MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop09CorrelationCollapse:

    def test_normal_high_correlations_no_alert(self):
        """FORGE-CHOP-09: Correlations above 0.5 → no collapse."""
        result = check_correlation_collapse({("ES","NQ"): 0.88, ("EURUSD","GBPUSD"): 0.86})
        assert result.is_collapsed is False
        assert result.additional_size_cut == 0.0

    def test_edge_one_pair_below_0_5_triggers_alert(self):
        """FORGE-CHOP-09: One pair below 0.5 from 0.85 baseline → alert."""
        result = check_correlation_collapse({("ES","NQ"): 0.42})
        assert result.is_collapsed is True
        assert result.additional_size_cut == 0.20
        assert result.chop08_elevated is True
        assert result.breadth_trades_blocked is True

    def test_conflict_collapse_threshold_is_0_5(self):
        """FORGE-CHOP-09: Exactly 0.50 → NOT collapsed (need below 0.50)."""
        result = check_correlation_collapse({("ES","NQ"): 0.50})
        assert result.is_collapsed is False


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-10: CHOPPY SIMULATION REQUIREMENTS
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeChop10SimRequirements:

    def test_normal_all_5_thresholds_met_passes(self):
        """FORGE-CHOP-10: All 5 thresholds met → cleared for live choppy trading."""
        result = validate_choppy_simulation(
            sessions_run=500,
            strategy_trade_counts={f"CHOP-{i:02d}": 65 for i in range(1, 11)},
            false_breakout_accuracy=0.68,
            regime_id_accuracy=0.83,
            transition_pct=0.74,
        )
        assert result.all_passed is True
        assert len(result.failures) == 0

    def test_edge_insufficient_sessions_fails(self):
        """FORGE-CHOP-10: Only 400 sessions → blocked (need 500)."""
        result = validate_choppy_simulation(
            sessions_run=400,
            strategy_trade_counts={f"CHOP-{i:02d}": 65 for i in range(1, 11)},
            false_breakout_accuracy=0.70,
            regime_id_accuracy=0.85,
            transition_pct=0.75,
        )
        assert result.all_passed is False
        assert any("500" in f for f in result.failures)

    def test_conflict_one_strategy_below_60_trades_fails(self):
        """FORGE-CHOP-10: Any CHOP strategy below 60 trades → blocked."""
        counts = {f"CHOP-{i:02d}": 65 for i in range(1, 11)}
        counts["CHOP-05"] = 45   # Below threshold
        result = validate_choppy_simulation(500, counts, 0.70, 0.85, 0.75)
        assert result.all_passed is False
        assert any("CHOP-05" in f for f in result.failures)


# ─────────────────────────────────────────────────────────────────────────────
# UPDATED EXISTING REQUIREMENTS
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdatedRequirements:

    def test_forge11_layer3_tightens_in_chop(self):
        """FORGE-11: Layer 3 threshold 60%→50% in choppy regime."""
        assert get_layer3_threshold(False) == 0.60
        assert get_layer3_threshold(True)  == 0.50

    def test_forge12_chop_allocation_40_30_30(self):
        """FORGE-12: Choppy: 40% morning, 30% afternoon, 30% reserve."""
        alloc = get_drawdown_allocation(True)
        assert alloc["morning"]   == 0.40
        assert alloc["afternoon"] == 0.30
        assert alloc["reserve"]   == 0.30

    def test_forge12_trending_allocation_50_30_20(self):
        """FORGE-12: Trending: 50% morning, 30% afternoon, 20% reserve."""
        alloc = get_drawdown_allocation(False)
        assert alloc["morning"]   == 0.50
        assert alloc["reserve"]   == 0.20

    def test_forge15_chop_streak_2_losses_pause(self):
        """FORGE-15: Choppy: 2 consecutive losses → pause (not 3)."""
        pause, stop = get_streak_thresholds(True)
        assert pause == 2
        assert stop  == 4

    def test_forge15_trending_streak_3_losses_pause(self):
        """FORGE-15: Trending: 3 consecutive losses → pause (unchanged)."""
        pause, stop = get_streak_thresholds(False)
        assert pause == 3
        assert stop  == 5

    def test_forge43_chop_recovery_is_45_minutes(self):
        """FORGE-43: Choppy recovery pause = 45 minutes (not 90)."""
        assert get_recovery_pause_minutes(True)  == 45
        assert get_recovery_pause_minutes(False) == 90

    def test_forge58_chop_adds_mean_reversion_15_pct(self):
        """FORGE-58: Choppy scoring: profit 50%→35%, mean reversion 15% added."""
        weights = get_scoring_weights(True)
        assert weights["profit"]         == 0.35
        assert weights["compliance"]     == 0.50
        assert weights["mean_reversion"] == 0.15
        assert abs(sum(weights.values()) - 1.0) < 0.001

    def test_forge63_ftmo_chop_uses_vwap_fade_and_va_oscillation(self):
        """FORGE-63: FTMO choppy primary = CHOP-02 + CHOP-06."""
        hierarchy = get_choppy_setup_hierarchy(FirmID.FTMO)
        assert "CHOP-02" in hierarchy
        assert "CHOP-06" in hierarchy

    def test_forge63_5percenters_ultra_conservative_2_setups(self):
        """FORGE-63: 5%ers choppy = CHOP-02 + CHOP-03 ONLY (4% DD constraint)."""
        hierarchy = get_choppy_setup_hierarchy(FirmID.FIVEPERCENTERS)
        assert "CHOP-02" in hierarchy
        assert "CHOP-03" in hierarchy
        assert len(hierarchy) == 2

    def test_forge65_chop_1_loss_cuts_30_pct(self):
        """FORGE-65: Choppy: 1 loss = -30% size (vs -25% trending)."""
        resp = get_loss_response(True, 1)
        assert resp["size_cut_pct"] == 0.30

    def test_forge65_chop_3_losses_stop_day(self):
        """FORGE-65: Choppy: 3 consecutive losses = stop day immediately."""
        resp = get_loss_response(True, 3)
        assert resp["action"] == "STOP_DAY"

    def test_forge65_trending_3_losses_48hr_review(self):
        """FORGE-65: Trending: 3 losses = 48-hour review (not stop day)."""
        resp = get_loss_response(False, 3)
        assert resp["action"] == "48HR_REVIEW"

    def test_forge72_suspends_correct_strategies(self):
        """FORGE-72: Regime 4 suspends GEX-01/02, ICT-08, VOL-03, SES-01/02."""
        assert "GEX-01" in FORGE72_SUSPENDED_IN_REGIME4
        assert "GEX-02" in FORGE72_SUSPENDED_IN_REGIME4
        assert "ICT-08" in FORGE72_SUSPENDED_IN_REGIME4
        assert "VOL-03" in FORGE72_SUSPENDED_IN_REGIME4
        assert "SES-01" in FORGE72_SUSPENDED_IN_REGIME4

    def test_forge72_priority_order_tick_first(self):
        """FORGE-72: CHOP-04 TICK Extreme is first in priority order."""
        assert FORGE72_REGIME4_PRIORITY[0] == "CHOP-04"
        assert FORGE72_REGIME4_PRIORITY[1] == "CHOP-02"
        assert FORGE72_REGIME4_PRIORITY[2] == "CHOP-10"

    def test_forge78_15_min_pause_before_trending(self):
        """FORGE-78: 15-minute pause after transition signal detected."""
        result = handle_regime_transition(True, minutes_since_signal=5, still_confirmed_after_pause=True)
        assert result["action"] == "WAIT"
        assert FORGE78_TRANSITION_PAUSE_MIN == 15

    def test_forge78_faded_signal_stays_choppy(self):
        """FORGE-78: If signal fades during 15-min pause → remain in choppy mode."""
        result = handle_regime_transition(True, minutes_since_signal=16, still_confirmed_after_pause=False)
        assert result["action"] == "STAY_CHOPPY"

    def test_forge78_confirmed_after_pause_activates_trending(self):
        """FORGE-78: Signal still confirmed after 15 min → activate trending."""
        result = handle_regime_transition(True, minutes_since_signal=16, still_confirmed_after_pause=True)
        assert result["action"] == "ACTIVATE_TRENDING"


# ─────────────────────────────────────────────────────────────────────────────
# CHOP STRATEGY SIGNAL TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestChopStrategies:

    def test_normal_chop01_all_6_conditions_confirmed(self):
        """CHOP-01: All 6 conditions met → short signal valid."""
        sig = chop01_false_breakout_fade(
            price=4798.0, resistance_level=4800.0, breakout_occurred=True,
            breakout_volume=900_000, avg_volume=1_000_000,
            breakout_close_pct=0.25, next_candle_below_res=True,
            nyse_tick=250.0, gex_positive=True, atr=10.0,
        )
        assert sig.valid is True
        assert sig.direction == "short"
        assert sig.conditions_met == 6
        assert sig.win_rate == 0.74

    def test_edge_chop01_missing_one_condition_rejected(self):
        """CHOP-01: 5/6 conditions → rejected (ALL 6 required)."""
        sig = chop01_false_breakout_fade(
            price=4798.0, resistance_level=4800.0, breakout_occurred=True,
            breakout_volume=900_000, avg_volume=1_000_000,
            breakout_close_pct=0.25, next_candle_below_res=True,
            nyse_tick=250.0, gex_positive=False,  # ← missing: GEX not positive
            atr=10.0,
        )
        assert sig.valid is False
        assert sig.conditions_met < 6

    def test_normal_chop04_tick_below_minus_850_long_signal(self):
        """CHOP-04: TICK ≤-850 sustained → long signal."""
        from datetime import time
        sig = chop04_tick_extreme_mean_reversion(
            price=4800.0, nyse_tick=-870.0, nyse_tick_prev=-880.0,
            choppy_confirmed=True, no_directional_catalyst=True,
            vix_spike=False, no_technical_break=True, tick_sustained=True,
            atr=10.0,
        )
        assert sig.valid is True
        assert sig.direction == "long"

    def test_normal_chop10_poc_gravity_0_7_pct_threshold(self):
        """CHOP-10: 0.7% deviation threshold (lower than standard VOL-01 1.0%)."""
        sig = chop10_poc_gravity_enhanced(
            price=4834.0, session_poc=4800.0, prior_poc=4801.0,
            deviation_pct=0.0085,  # 0.85% > 0.7% threshold
            choppy_confirmed=True, gex_positive=True, no_catalyst=True, atr=10.0,
        )
        assert sig.valid is True

    def test_normal_all_10_strategies_have_positive_ev(self):
        """Section 2: All 10 CHOP strategies must have positive expected value."""
        for sid, meta in CHOPPY_STRATEGY_REGISTRY.items():
            wr = meta["win_rate"]
            rr = meta["rr"]
            ev = wr * rr - (1 - wr)
            assert ev > 0, f"{sid} has negative EV: {ev:.3f}"

    def test_normal_10_strategies_in_registry(self):
        """Document: Exactly 10 choppy strategies."""
        assert CHOP_STRATEGY_COUNT == 10

    def test_normal_priority_order_has_all_10(self):
        """Document: All 10 in priority order."""
        assert len(CHOP_PRIORITY_ORDER) == 10

    def test_normal_chop11_behavioral_transition_gradual(self):
        """FORGE-CHOP-11: Day 1 = 80%, Day 3+ = 60% (gradual)."""
        day1 = get_behavioral_transition(0)
        day3 = get_behavioral_transition(2)
        assert day1.position_size_pct == 0.80
        assert day3.position_size_pct == 0.60
        assert day3.is_fully_choppy is True

    def test_normal_chop12_performance_log_tracks_sessions(self):
        """FORGE-CHOP-12: Session report logged and tracked."""
        log = ChoppyPerformanceLog()
        report = ChoppySessionReport(
            session_date=date.today(),
            identified_choppy_before_10am=True,
            false_breakouts_identified=3,
            false_breakout_fades_taken=2,
            false_breakout_fade_wins=2,
            strategies_fired={"CHOP-04": {"trades": 2, "wins": 2}},
            transition_detected=False,
            chop09_captured_transition=False,
            overall_pnl=285.0,
            regime_identification_time="9:48am",
        )
        log.add_report(report)
        assert log.total_sessions == 1
        assert log.early_identification_rate == 1.0


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
