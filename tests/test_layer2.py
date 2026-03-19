"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                 test_layer2.py — Layer 2 FX-06 Compliance                   ║
║  Tests for all Layer 2 trading intelligence modules                          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, time, timezone

# ── Imports ────────────────────────────────────────────────────────────────

from signal_generators import (
    check_opening_range_breakout, check_vwap_reclaim,
    check_london_session_forex, check_trend_day_momentum,
    check_mean_reversion, SignalVerdict, MAJOR_FOREX_PAIRS,
)
from catalyst_stack import (
    build_catalyst_score, CatalystType, rank_setups_for_firm,
    CATALYST_MIN_EVALUATION, CONFLUENCE_SIZE_MULTIPLIERS,
)
from futures_context import build_futures_context
from evaluation_analytics import (
    FirmPerformanceDatabase, build_post_mortem,
    CalibrationRatchet, calculate_pass_probability,
)
from evaluation_timing import (
    MarketFavorabilityIndex, calculate_mfi,
    MFI_GATE_THRESHOLD, MFI_GATE_DAYS_REQUIRED,
)
from opportunity_scoring import score_opportunity, MIN_PROFIT_SCORE, MIN_COMPLIANCE_SCORE
from dynamic_sizing import (
    calculate_dynamic_size, calculate_asymmetry_modifier,
    AccountPhase, determine_phase,
)
from profit_lock import (
    calculate_profit_lock, LockStage, calculate_milestone_trajectory,
)
from losing_trade_response import (
    get_loss_response, LossResponseAction,
    get_news_harvest_strategy, NEWS_PERMISSIVE_FIRMS,
)
from regime_deployment import (
    deploy_for_regime, detect_regime, calculate_expected_value,
    MarketRegimeType, REGIME_STRATEGIES,
)
from strategy_library import (
    StrategyRegistry, strategy_gamma_flip_breakout,
    strategy_ny_kill_zone, strategy_order_block_fvg,
)
from firm_rules import FirmID

TODAY = date(2026, 3, 19)
NOW   = datetime(2026, 3, 19, 14, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL GENERATORS (FORGE-17–21)
# ─────────────────────────────────────────────────────────────────────────────

class TestSignalGenerators:

    def test_normal_orb_long_with_volume(self):
        """FORGE-17: ORB long — price above range high, 2× volume → CONFIRMED."""
        sig = check_opening_range_breakout(
            current_price=4810.0, range_high=4805.0, range_low=4795.0,
            current_time_et=time(10, 0), current_volume=2_000_000.0,
            avg_volume=1_000_000.0, atr=10.0,
        )
        assert sig.is_confirmed
        assert sig.direction == "long"

    def test_edge_orb_before_945_pending(self):
        """FORGE-17: Before 9:45am ET → PENDING regardless of price."""
        sig = check_opening_range_breakout(
            current_price=4810.0, range_high=4805.0, range_low=4795.0,
            current_time_et=time(9, 30), current_volume=2_000_000.0,
            avg_volume=1_000_000.0, atr=10.0,
        )
        assert sig.verdict == SignalVerdict.PENDING

    def test_conflict_orb_insufficient_volume_rejected(self):
        """FORGE-17: Price breaks range but volume < 2× → REJECTED (not confirmed)."""
        sig = check_opening_range_breakout(
            current_price=4810.0, range_high=4805.0, range_low=4795.0,
            current_time_et=time(10, 0), current_volume=1_500_000.0,
            avg_volume=1_000_000.0, atr=10.0,
        )
        assert sig.verdict == SignalVerdict.REJECTED

    def test_normal_vwap_reclaim_with_volume(self):
        """FORGE-18: VWAP reclaim with 1.3× volume → CONFIRMED long."""
        sig = check_vwap_reclaim(
            current_price=4805.0, prior_close=4800.0, vwap=4800.0,
            dipped_below=True, volume_at_reclaim=1_400_000.0,
            avg_volume=1_000_000.0, atr=10.0,
        )
        assert sig.is_confirmed
        assert sig.direction == "long"

    def test_normal_london_forex_in_window(self):
        """FORGE-19: EURUSD at 9am ET → CONFIRMED (in London-NY window)."""
        sig = check_london_session_forex("EURUSD", time(9, 0), is_evaluation=True)
        assert sig.is_confirmed

    def test_edge_forex_outside_window_rejected(self):
        """FORGE-19: EURUSD at 1pm ET → REJECTED (outside window during evaluation)."""
        sig = check_london_session_forex("EURUSD", time(13, 0), is_evaluation=True)
        assert sig.verdict == SignalVerdict.REJECTED

    def test_normal_trend_day_gex_negative(self):
        """FORGE-20: GEX negative + bullish → trend day confirmed."""
        sig = check_trend_day_momentum(
            gex_negative=True, price_direction="bullish",
            current_price=4810.0, vwap=4800.0, atr=10.0, is_first_pullback=True,
        )
        assert sig.is_confirmed
        assert sig.direction == "long"
        assert sig.confidence >= 0.80

    def test_conflict_mean_reversion_needs_gex_positive(self):
        """FORGE-21: GEX negative → mean reversion REJECTED (trend day, not ranging)."""
        sig = check_mean_reversion(
            gex_positive=False, current_price=4810.0,
            vwap=4800.0, upper_band=4815.0, lower_band=4785.0, atr=10.0,
        )
        assert sig.verdict == SignalVerdict.REJECTED

    def test_normal_mean_reversion_at_upper_band(self):
        """FORGE-21: GEX positive, price at upper band → confirmed short."""
        sig = check_mean_reversion(
            gex_positive=True, current_price=4816.0,
            vwap=4800.0, upper_band=4815.0, lower_band=4785.0, atr=10.0,
        )
        assert sig.is_confirmed
        assert sig.direction == "short"


# ─────────────────────────────────────────────────────────────────────────────
# CATALYST STACK (FORGE-22/63)
# ─────────────────────────────────────────────────────────────────────────────

class TestCatalystStack:

    def test_normal_4_catalysts_meets_evaluation(self):
        """FORGE-22: 4 catalysts → meets evaluation threshold."""
        score = build_catalyst_score([
            CatalystType.GEX_DIRECTION, CatalystType.ICT_ORDER_BLOCK,
            CatalystType.VOLUME_PROFILE, CatalystType.ORDER_FLOW_DELTA,
        ], is_evaluation=True)
        assert score.meets_evaluation is True
        assert score.total_score == 4

    def test_edge_3_catalysts_fails_evaluation(self):
        """FORGE-22: Only 3 catalysts → does NOT meet evaluation threshold."""
        score = build_catalyst_score([
            CatalystType.GEX_DIRECTION, CatalystType.ICT_ORDER_BLOCK,
            CatalystType.VOLUME_PROFILE,
        ], is_evaluation=True)
        assert score.meets_evaluation is False

    def test_conflict_size_multiplier_scales_with_stack(self):
        """FORGE-22: 4+ stack = 1.75× size multiplier."""
        score = build_catalyst_score([
            CatalystType.GEX_DIRECTION, CatalystType.ICT_ORDER_BLOCK,
            CatalystType.VOLUME_PROFILE, CatalystType.ORDER_FLOW_DELTA,
        ])
        assert score.size_multiplier == CONFLUENCE_SIZE_MULTIPLIERS[4]

    def test_normal_firm_hierarchy_ranks_setups(self):
        """FORGE-63: FTMO setup hierarchy returns ranked list."""
        ranked = rank_setups_for_firm(
            ["FORGE-20", "ICT-01", "GEX-01"], FirmID.FTMO, catalyst_score=3
        )
        assert len(ranked) == 3
        # FORGE-20 is first in FTMO hierarchy — should rank highest
        ids = [r[0] for r in ranked]
        scores = [r[1] for r in ranked]
        assert scores == sorted(scores, reverse=True)


# ─────────────────────────────────────────────────────────────────────────────
# FUTURES CONTEXT (FORGE-23)
# ─────────────────────────────────────────────────────────────────────────────

class TestFuturesContext:

    def test_normal_bullish_gap_strong_bull(self):
        """FORGE-23: 0.7% overnight gap up → STRONG_BULL or BULL bias."""
        ctx = build_futures_context("ES", 4800.0, 4840.0, 4820.0, 4834.0, 4828.0)
        assert ctx.bias_label in ("STRONG_BULL", "BULL")
        assert ctx.direction == "bullish"

    def test_edge_flat_overnight_neutral(self):
        """FORGE-23: Flat overnight (<0.1% change) → NEUTRAL bias."""
        ctx = build_futures_context("ES", 4800.0, 4803.0, 4798.0, 4801.0, 4800.5)
        assert ctx.bias_label == "NEUTRAL"

    def test_conflict_gap_day_flag(self):
        """FORGE-23: 0.5%+ gap → is_gap_day = True."""
        ctx = build_futures_context("ES", 4800.0, 4850.0, 4840.0, 4845.0, 4843.0)
        assert ctx.is_gap_day is True
        assert ctx.gap_pct >= 0.003


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION ANALYTICS (FORGE-24/25/27/32)
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluationAnalytics:

    def test_normal_firm_db_records_pass(self):
        """FORGE-25: Recording a pass updates pass rate."""
        db = FirmPerformanceDatabase()
        db.record_evaluation_result(FirmID.FTMO, passed=True, pass_days=15,
                                    drawdown_used=0.45, daily_profit=700.0)
        assert db.get_pass_rate(FirmID.FTMO) == 1.0

    def test_edge_pass_rate_after_mixed_results(self):
        """FORGE-25: 2 passes + 1 fail = 66.7% pass rate."""
        db = FirmPerformanceDatabase()
        db.record_evaluation_result(FirmID.FTMO, passed=True)
        db.record_evaluation_result(FirmID.FTMO, passed=True)
        db.record_evaluation_result(FirmID.FTMO, passed=False)
        assert abs(db.get_pass_rate(FirmID.FTMO) - 2/3) < 0.01

    def test_normal_calibration_ratchet_adds_guard_on_failure(self):
        """FORGE-27: Post-mortem failure → guard added to calibration."""
        cr = CalibrationRatchet()
        pm = build_post_mortem("EVAL-001", FirmID.FTMO, "FAILED",
                               4_500.0, 10_000.0, 0.75, 12, "Drawdown limit breached")
        cr.add_guard_from_failure(pm)
        assert len(cr.get_active_guards()) == 1
        assert cr.version == 1

    def test_conflict_pass_probability_below_30_survival_mode(self):
        """FORGE-32: Very low profit + few days left → < 30% probability."""
        prob = calculate_pass_probability(
            current_profit=500.0, profit_target=10_000.0,
            days_elapsed=25, days_remaining=2,
            drawdown_pct_used=0.60, trading_days_completed=20,
        )
        assert prob < 0.30


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION TIMING / MFI (FORGE-26/46)
# ─────────────────────────────────────────────────────────────────────────────

class TestEvaluationTiming:

    def test_normal_mfi_calculated_correctly(self):
        """FORGE-46: MFI calculation returns 0–100 score."""
        mfi = calculate_mfi(
            vix_level=14.0, gex_negative=True, regime_trending=True,
            advance_decline=0.68, trend_strength=0.70,
        )
        assert 0.0 <= mfi <= 100.0
        assert mfi > 55.0   # Good conditions should score above gate

    def test_edge_mfi_gate_5_of_7_days(self):
        """FORGE-26: Gate clears only when 5+ of last 7 days > 55."""
        mfi_system = MarketFavorabilityIndex()
        # Record 5 good days + 2 bad days
        for i in range(5):
            mfi_system.record_daily(
                TODAY, vix_level=14.0, gex_negative=True,
                regime_trending=True, advance_decline=0.70, trend_strength=0.70,
            )
        for i in range(2):
            mfi_system.record_daily(
                TODAY, vix_level=35.0, gex_negative=False,
                regime_trending=False, advance_decline=0.35, trend_strength=0.20,
            )
        result = mfi_system.check_gate(as_of_month=3)
        assert result.gate_passed is True

    def test_conflict_august_requires_higher_threshold(self):
        """FORGE-46: August → MFI threshold raised 30% (unfavorable month)."""
        mfi_system = MarketFavorabilityIndex()
        # Good readings but checking August
        for _ in range(7):
            mfi_system.record_daily(
                TODAY, vix_level=14.0, gex_negative=True,
                regime_trending=True, advance_decline=0.70, trend_strength=0.70,
            )
        result = mfi_system.check_gate(as_of_month=8)   # August
        # Even with good readings, August seasonal check fails
        assert result.gate_passed is False
        assert result.adjusted_threshold > MFI_GATE_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# OPPORTUNITY SCORING (FORGE-58)
# ─────────────────────────────────────────────────────────────────────────────

class TestOpportunityScoring:

    def test_normal_high_quality_setup_approved(self):
        """FORGE-58: 75% win rate + good conditions → approved."""
        score = score_opportunity(
            setup_id="GEX-01", firm_id=FirmID.FTMO,
            win_rate=0.75, avg_rr=2.5, session_quality=8.0, catalyst_stack=4,
            drawdown_pct_used=0.30, days_remaining=20, profit_pct_complete=0.40,
        )
        assert score.execute_approved is True
        assert score.profit_score >= MIN_PROFIT_SCORE
        assert score.compliance_score >= MIN_COMPLIANCE_SCORE

    def test_edge_low_win_rate_blocked(self):
        """FORGE-58: 55% win rate (too low) → profit score below minimum."""
        score = score_opportunity(
            setup_id="POOR-01", firm_id=FirmID.FTMO,
            win_rate=0.55, avg_rr=1.2, session_quality=4.0, catalyst_stack=1,
            drawdown_pct_used=0.30, days_remaining=20, profit_pct_complete=0.20,
        )
        assert score.execute_approved is False

    def test_conflict_both_gates_must_pass(self):
        """FORGE-58: Good profit score but bad compliance → still blocked."""
        score = score_opportunity(
            setup_id="GEX-01", firm_id=FirmID.FTMO,
            win_rate=0.75, avg_rr=2.5, session_quality=8.0, catalyst_stack=4,
            drawdown_pct_used=0.88,   # Near RED — compliance terrible
            days_remaining=1, profit_pct_complete=0.90,
        )
        # Profit score great, compliance terrible → blocked
        assert not score.execute_approved


# ─────────────────────────────────────────────────────────────────────────────
# DYNAMIC SIZING (FORGE-59/86)
# ─────────────────────────────────────────────────────────────────────────────

class TestDynamicSizing:

    def test_normal_early_eval_slight_boost(self):
        """FORGE-59: Early evaluation → 1.10× phase modifier."""
        result = calculate_dynamic_size(
            base_size=1.0, profit_pct_complete=0.20,
            is_funded=False, consecutive_losses=0,
        )
        assert result.account_phase == AccountPhase.EARLY_EVAL
        assert result.phase_modifier == 1.10

    def test_edge_near_target_half_size(self):
        """FORGE-59: Within 20% of target → 0.50× phase modifier (C-02)."""
        result = calculate_dynamic_size(
            base_size=1.0, profit_pct_complete=0.85,
            is_funded=False, consecutive_losses=0,
        )
        assert result.account_phase == AccountPhase.APPROACH_TARGET
        assert result.phase_modifier == 0.50

    def test_conflict_asymmetry_reduces_size_after_loss(self):
        """FORGE-86: After 2% loss → asymmetry modifier < 1.0."""
        modifier = calculate_asymmetry_modifier(consecutive_losses=1, loss_pct=0.02)
        assert modifier < 1.0   # Must recover 2.04% to break even — reduce size


# ─────────────────────────────────────────────────────────────────────────────
# PROFIT LOCK (FORGE-64/73)
# ─────────────────────────────────────────────────────────────────────────────

class TestProfitLock:

    def test_normal_stage1_at_half_R(self):
        """FORGE-64: 0.5R profit → Stage 1: move stop to breakeven."""
        action = calculate_profit_lock(
            entry_price=4800.0, current_price=4805.0,
            stop_price=4790.0, direction="long", atr=10.0,
        )
        # Risk = 10. 0.5R = 5. current_price - entry = 5 → exactly 0.5R
        assert action.stage == LockStage.STAGE_1
        assert action.move_stop_to == 4800.0   # Breakeven

    def test_edge_stage2_at_1_5R(self):
        """FORGE-64: 1.5R profit → Stage 2: close 30%."""
        action = calculate_profit_lock(
            entry_price=4800.0, current_price=4815.0,
            stop_price=4790.0, direction="long", atr=10.0,
        )
        # Risk = 10. 1.5R = 15. Profit = 15 → 1.5R exactly
        assert action.stage == LockStage.STAGE_2
        assert action.close_pct == 0.30

    def test_conflict_stage3_at_3R(self):
        """FORGE-64: 3R profit → Stage 3: trailing stop."""
        action = calculate_profit_lock(
            entry_price=4800.0, current_price=4830.0,
            stop_price=4790.0, direction="long", atr=10.0,
        )
        # Risk = 10. 3R = 30. Profit = 30 → exactly 3R
        assert action.stage == LockStage.STAGE_3
        assert action.trail_atr == 1.5

    def test_normal_milestone_days_calculated(self):
        """FORGE-73: $5K remaining at $500/day → 10 days to milestone."""
        traj = calculate_milestone_trajectory(95_000.0, 100_000.0, "Safety Net", 500.0)
        assert abs(traj.days_to_milestone - 10.0) < 0.1


# ─────────────────────────────────────────────────────────────────────────────
# LOSING TRADE RESPONSE (FORGE-65/68)
# ─────────────────────────────────────────────────────────────────────────────

class TestLosingTradeResponse:

    def test_normal_1_loss_reduce_size(self):
        """FORGE-65: 1 consecutive loss → -25% size."""
        resp = get_loss_response(1)
        assert resp.action == LossResponseAction.REDUCE_SIZE
        assert resp.size_modifier == 0.75

    def test_edge_2_losses_session_pause(self):
        """FORGE-65: 2 consecutive losses → session pause."""
        resp = get_loss_response(2)
        assert resp.pause_session is True

    def test_conflict_3_losses_architect_alert(self):
        """FORGE-65: 3+ losses → review required + ARCHITECT alert."""
        resp = get_loss_response(3)
        assert resp.review_required is True
        assert resp.architect_alert is True

    def test_normal_topstep_news_permissive(self):
        """FORGE-68: Topstep — can trade with trend before events."""
        decision = get_news_harvest_strategy(
            FirmID.TOPSTEP, "bullish", minutes_to_event=10.0,
            event_impact="high", is_evaluation=True,
        )
        assert decision.can_trade_before is True

    def test_edge_ftmo_flat_required_before_event(self):
        """FORGE-68: FTMO funded — flat required 10+ min before high impact."""
        decision = get_news_harvest_strategy(
            FirmID.FTMO, "bullish", minutes_to_event=8.0,
            event_impact="high", is_evaluation=True,
        )
        assert decision.can_trade_before is False


# ─────────────────────────────────────────────────────────────────────────────
# REGIME DEPLOYMENT (FORGE-72/60)
# ─────────────────────────────────────────────────────────────────────────────

class TestRegimeDeployment:

    def test_normal_high_vol_trending_deploys_momentum(self):
        """FORGE-72: High-vol trending → momentum setups (GEX-01, ORD-01)."""
        deploy = deploy_for_regime(MarketRegimeType.HIGH_VOL_TRENDING)
        assert "GEX-01" in deploy.active_strategies
        assert deploy.size_multiplier > 1.0   # Trend days → size up

    def test_edge_low_vol_ranging_deploys_reversion(self):
        """FORGE-72: Low-vol ranging → mean reversion setups."""
        deploy = deploy_for_regime(MarketRegimeType.LOW_VOL_RANGING)
        assert "VOL-01" in deploy.active_strategies
        assert deploy.size_multiplier < 1.0   # Ranging = conservative

    def test_conflict_ev_positive_in_good_conditions(self):
        """FORGE-60: 70% win rate × 2.5 R:R → positive EV."""
        result = calculate_expected_value(
            win_rate=0.70, avg_win=250.0, avg_loss=100.0,
        )
        assert result.is_positive
        assert result.raw_ev > 0

    def test_normal_regime_detection(self):
        """FORGE-72: Low VIX + negative GEX → LOW_VOL_TRENDING."""
        regime = detect_regime(vix_level=14.0, vix_rising=False,
                               gex_negative=True, adr_pct=0.008)
        assert regime == MarketRegimeType.LOW_VOL_TRENDING


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY LIBRARY
# ─────────────────────────────────────────────────────────────────────────────

class TestStrategyLibrary:

    def test_normal_gamma_flip_confirmed_on_flip(self):
        """FORGE-TS-01: GEX flips from positive to negative → confirmed."""
        sig = strategy_gamma_flip_breakout(
            prior_gex=500.0, current_gex=-300.0,
            gex_flip_price=4800.0, current_price=4802.0,
            atr=10.0, vwap=4798.0, direction="long",
        )
        assert sig.valid is True
        assert sig.win_rate_hist == 0.75

    def test_edge_gamma_flip_no_flip_rejected(self):
        """FORGE-TS-01: GEX still positive → no flip → rejected."""
        sig = strategy_gamma_flip_breakout(
            prior_gex=300.0, current_gex=200.0,  # Still positive
            gex_flip_price=4800.0, current_price=4802.0,
            atr=10.0, vwap=4798.0, direction="long",
        )
        assert sig.valid is False

    def test_normal_ny_kill_zone_in_window(self):
        """FORGE-TS-23: 10am ET in kill zone + bullish → confirmed long."""
        sig = strategy_ny_kill_zone(
            current_time_et=time(10, 0), current_price=4810.0,
            open_price=4800.0, vwap=4802.0, atr=10.0,
            session_bias="bullish", prior_day_high=4820.0, prior_day_low=4790.0,
            gex_direction="negative",
        )
        assert sig.valid is True
        assert sig.direction == "long"

    def test_edge_ny_kill_zone_outside_window(self):
        """FORGE-TS-23: 1:30pm ET — outside kill zone → rejected."""
        sig = strategy_ny_kill_zone(
            current_time_et=time(13, 30), current_price=4810.0,
            open_price=4800.0, vwap=4802.0, atr=10.0,
            session_bias="bullish", prior_day_high=4820.0, prior_day_low=4790.0,
            gex_direction="negative",
        )
        assert sig.valid is False

    def test_normal_30_strategies_in_registry(self):
        """Registry contains all 30 documented strategies."""
        assert len(StrategyRegistry.all_ids()) == 30

    def test_conflict_registry_average_wr_matches_document(self):
        """Registry average win rate ≈ 71.8% per document."""
        avg = StrategyRegistry.average_win_rate()
        assert abs(avg - 0.718) < 0.005

    def test_normal_ev_positive_for_all_strategies(self):
        """All 30 strategies have positive expected value (WR > 60%, RR > 1.5)."""
        for sid, meta in StrategyRegistry.METADATA.items():
            wr = meta["win_rate"]
            rr = meta["rr"]
            ev = (wr * rr) - (1.0 - wr)
            assert ev > 0, f"{sid} has negative EV: {ev:.3f}"


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
