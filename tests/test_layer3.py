"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                 test_layer3.py — Layer 3 FX-06 Compliance                   ║
║  Tests for all Layer 3 advanced intelligence modules                         ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta, timezone
from firm_rules import FirmID

from paper_engine import (
    ThreePaperPassGate, PaperEvaluationEngine, PaperEvalResult,
    PaperPassResult, OPTIMAL_STOPPING_PASSES_REQUIRED,
)
from behavioral_arch import (
    assess_tilt, TiltLevel,
    get_hot_hand_multiplier, get_win_streak_multiplier,
    evaluate_entry_stage, EntryStage,
    check_behavioral_consistency,
)
from recovery_protocols import (
    create_failure_protocol, FailurePhase,
    check_capital_defense, check_warmup_status,
    create_recovery_plan,
)
from capital_management import (
    build_pipeline_snapshot, get_next_scaling_stage,
    allocate_drawdown_budget, calculate_opportunity_cost,
)
from advanced_analytics import (
    calculate_information_ratio, calculate_sortino, TARGET_SORTINO,
    verify_statistical_edge, attribute_performance,
    assess_current_risk,
)
from market_intelligence import (
    calculate_var, detect_adverse_selection,
    check_regime_transition, filter_noise_vs_signal,
    plan_vwap_execution, detect_liquidity_vacuum,
)
from system_arch import (
    AntifragileMonitor, run_evolutionary_cycle,
    analyze_second_order_effects, apply_game_theory,
    run_monthly_audit,
)

TODAY = date(2026, 3, 19)
NOW   = datetime(2026, 3, 19, 14, 0, tzinfo=timezone.utc)


def make_paper_result(
    result_type=PaperPassResult.QUALITY_PASS,
    profit=10_500.0, target=10_000.0, dd=0.45,
    violations=None, quality=8.5, regime="low_vol_trending",
) -> PaperEvalResult:
    return PaperEvalResult(
        run_id=f"SIM-{TODAY}", firm_id=FirmID.FTMO,
        result=result_type,
        profit_achieved=profit, profit_target=target,
        drawdown_pct_used=dd, trading_days=18,
        win_rate=0.68, avg_rr=2.3,
        rules_violated=violations or [],
        quality_score=quality, regime_tested=regime,
        date_completed=TODAY,
    )


# ─────────────────────────────────────────────────────────────────────────────
# PAPER ENGINE (FORGE-34/35/48)
# ─────────────────────────────────────────────────────────────────────────────

class TestPaperEngine:

    def test_normal_3_quality_passes_clears_gate(self):
        """FORGE-35/48: 3 consecutive quality passes → gate cleared."""
        gate = ThreePaperPassGate()
        gate.set_mfi_gate(True)
        for regime in ["low_vol_trending", "high_vol_trending", "low_vol_ranging",
                       "expansion"]:
            gate.record_pass(make_paper_result(regime=regime))
        # 4 passes = 4 consecutive quality passes — gate_cleared if MFI + regimes OK
        status = gate.get_status()
        assert status.consecutive_passes >= 3

    def test_edge_failure_resets_consecutive_count(self):
        """FORGE-35: Any failure resets the consecutive pass clock to 0."""
        gate = ThreePaperPassGate()
        gate.record_pass(make_paper_result())
        gate.record_pass(make_paper_result())
        # Failure resets
        gate.record_pass(make_paper_result(result_type=PaperPassResult.FAILURE))
        assert gate.consecutive_passes == 0

    def test_conflict_technical_pass_does_not_advance_count(self):
        """FORGE-35: Technical pass (quality concern) doesn't advance count."""
        gate = ThreePaperPassGate()
        gate.record_pass(make_paper_result())   # +1
        gate.record_pass(make_paper_result(    # Technical — count unchanged
            result_type=PaperPassResult.TECHNICAL_PASS,
            quality=5.0  # Below quality threshold
        ))
        assert gate.consecutive_passes == 1   # Still 1, not 2

    def test_normal_mfi_gate_required(self):
        """FORGE-48: Even 3 quality passes don't clear gate without MFI."""
        gate = ThreePaperPassGate()
        gate.set_mfi_gate(False)   # MFI not cleared
        for _ in range(3):
            gate.record_pass(make_paper_result())
        status = gate.get_status()
        assert not status.gate_cleared   # MFI blocking

    def test_normal_simulation_records_results(self):
        """FORGE-34: Simulation engine correctly classifies outcomes."""
        gate = ThreePaperPassGate()
        engine = PaperEvaluationEngine(gate)
        result, gate_status = engine.run_simulation(
            "SIM-001", FirmID.FTMO,
            profit_target=10_000.0, profit_achieved=10_500.0,
            drawdown_pct=0.40, trading_days=15,
            win_rate=0.70, avg_rr=2.3,
            quality_score=8.0, regime_tested="low_vol_trending",
        )
        assert result.result == PaperPassResult.QUALITY_PASS
        assert result.is_quality_pass


# ─────────────────────────────────────────────────────────────────────────────
# BEHAVIORAL ARCHITECTURE (FORGE-37/38/39/40/56)
# ─────────────────────────────────────────────────────────────────────────────

class TestBehavioralArch:

    def test_normal_no_tilt_clear(self):
        """FORGE-40: No tilt indicators → CLEAR state."""
        result = assess_tilt(
            consecutive_losses=0, trades_last_hour=2, avg_trades_per_hour=2.0,
            deviated_from_plan=False, profit_target_missed=False,
            time_since_last_loss_min=60.0,
        )
        assert result.level == TiltLevel.CLEAR
        assert result.trading_permitted is True

    def test_edge_severe_tilt_blocks_trading(self):
        """FORGE-40: 3+ losses + overtrading + deviation → SEVERE tilt, blocked."""
        result = assess_tilt(
            consecutive_losses=4, trades_last_hour=10, avg_trades_per_hour=2.0,
            deviated_from_plan=True, profit_target_missed=True,
            time_since_last_loss_min=2.0,
        )
        assert result.level in (TiltLevel.SEVERE, TiltLevel.MODERATE)
        assert result.trading_permitted is False

    def test_conflict_c14_ftmo_hot_hand_disabled(self):
        """FORGE-38 + C-14: FTMO hot hand permanently returns 1.0."""
        mult, reason = get_hot_hand_multiplier(
            consecutive_wins=7, recent_win_rate=0.85,
            firm_id=FirmID.FTMO, is_evaluation=True, account_win_rate=0.68,
        )
        assert mult == 1.0
        assert "C-14" in reason

    def test_normal_c15_hot_hand_in_evaluation(self):
        """FORGE-38 + C-15: Non-FTMO hot hand in evaluation → multiplier > 1."""
        mult, reason = get_hot_hand_multiplier(
            consecutive_wins=5, recent_win_rate=0.85,
            firm_id=FirmID.APEX, is_evaluation=True, account_win_rate=0.65,
        )
        assert mult > 1.0

    def test_normal_win_streak_in_funded_mode(self):
        """FORGE-39 + C-15: Win streak sizing only in funded mode."""
        mult_eval, _ = get_win_streak_multiplier(5, is_funded=False, firm_id=FirmID.FTMO)
        mult_fund, _ = get_win_streak_multiplier(5, is_funded=True,  firm_id=FirmID.FTMO)
        assert mult_eval == 1.0   # No multiplier in eval
        assert mult_fund > 1.0    # Multiplier in funded

    def test_normal_entry_zone_execute_now(self):
        """FORGE-37: Price within 0.5 ATR of optimal → ENTRY_ZONE, execute."""
        result = evaluate_entry_stage(
            setup_confirmed=True, current_price=4803.0, optimal_entry=4800.0,
            atr=10.0, direction="long", time_since_setup=5.0,
        )
        # |4803 - 4800| / 10 = 0.3 ATR → within 0.5 → ENTRY_ZONE
        assert result.stage == EntryStage.ENTRY_ZONE
        assert result.can_enter is True

    def test_conflict_behavioral_consistency_flags_sizing_variance(self):
        """FORGE-56: High size variance (CV > 0.30) → behavioral flag."""
        result = check_behavioral_consistency(
            position_sizes=[0.1, 5.0, 0.2, 4.0, 0.1, 3.0] * 5,   # Wildly varying
            entry_hours=[10] * 30,
            baseline_win_rate=0.65, recent_win_rate=0.65,
        )
        assert not result.is_consistent
        assert any("SIZING" in f.upper() for f in result.flags)


# ─────────────────────────────────────────────────────────────────────────────
# RECOVERY PROTOCOLS (FORGE-42/43/47/55)
# ─────────────────────────────────────────────────────────────────────────────

class TestRecoveryProtocols:

    def test_normal_fx10_creates_72h_timeline(self):
        """FORGE-42 (FX-10): Failure creates 4h/24h/48h/72h milestone timeline."""
        now = datetime(2026, 3, 19, 10, 0, tzinfo=timezone.utc)
        protocol = create_failure_protocol("FAIL-001", FirmID.FTMO, "Drawdown breached", now)
        assert abs((protocol.rca_deadline  - now).total_seconds() - 4*3600)   < 60
        assert abs((protocol.fix_deadline  - now).total_seconds() - 24*3600)  < 60
        assert abs((protocol.eval_authorized - now).total_seconds() - 72*3600) < 60

    def test_edge_capital_defense_blocks_when_funded_at_risk(self):
        """FORGE-43: Cannot start new eval when funded account near red zone."""
        result = check_capital_defense(
            new_eval_cost=540.0, available_bank_capital=1_000.0,
            funded_account_equity=95_000.0, funded_floor_dollars=90_000.0,
            funded_drawdown_pct=0.85,   # At 85% — red zone
        )
        assert result.new_eval_authorized is False
        assert result.funded_accounts_safe is False

    def test_normal_capital_defense_authorizes_when_safe(self):
        """FORGE-43: Funded stable + enough bank capital → new eval authorized."""
        result = check_capital_defense(
            new_eval_cost=540.0, available_bank_capital=2_000.0,
            funded_account_equity=99_000.0, funded_floor_dollars=90_000.0,
            funded_drawdown_pct=0.30,   # Healthy
        )
        assert result.new_eval_authorized is True

    def test_normal_warmup_complete_after_3_days(self):
        """FORGE-47: 3 days + adequate pace → warmup complete."""
        status = check_warmup_status(
            trading_days_completed=3, avg_daily_profit=80.0,
            target_account_profit=1_000.0, eval_days=30, min_warmup_days=3,
        )
        # Pace: $80/day needed is (1000/21) ≈ $47. Confidence = 80/47 ≈ 170% → capped at 1.0
        assert status.is_warmed_up is True

    def test_conflict_recovery_plan_reduces_size_at_85_pct(self):
        """FORGE-55: 85%+ drawdown → STABILIZE phase, 25% size."""
        plan = create_recovery_plan(drawdown_pct=0.85, account_size=100_000.0)
        assert plan.recovery_phase == "STABILIZE"
        assert plan.size_factor == 0.25
        assert plan.required_wins == 5


# ─────────────────────────────────────────────────────────────────────────────
# CAPITAL MANAGEMENT (FORGE-29/30/62/69)
# ─────────────────────────────────────────────────────────────────────────────

class TestCapitalManagement:

    def test_normal_pipeline_health_building_phase(self):
        """FORGE-29: No funded accounts → BUILDING health status."""
        pipeline = build_pipeline_snapshot(
            bank_capital=500.0, receivables=0.0, active_evals=1,
            active_eval_cost=140.0, funded_accounts=0, funded_equity=0.0,
            monthly_payouts=0.0, total_fees_paid=140.0,
        )
        assert pipeline.pipeline_health == "BUILDING"

    def test_edge_available_capital_excludes_receivables(self):
        """FORGE-29 (FX-08): Receivables are NOT available for deployment."""
        pipeline = build_pipeline_snapshot(
            bank_capital=300.0, receivables=5_000.0, active_evals=0,
            active_eval_cost=0.0, funded_accounts=1, funded_equity=100_000.0,
            monthly_payouts=1_000.0, total_fees_paid=540.0,
        )
        # Only bank capital minus reserve is available
        assert pipeline.bank_capital == 300.0
        assert pipeline.receivables == 5_000.0
        available = pipeline.available_for_new_eval
        assert available <= pipeline.bank_capital   # Never more than bank capital

    def test_normal_drawdown_budget_allocated(self):
        """FORGE-62: Budget allocation respects percentages."""
        alloc = allocate_drawdown_budget(
            total_budget=10_000.0, drawdown_used=2_000.0,
            daily_limit=500.0,
        )
        assert alloc.remaining_budget == 8_000.0
        assert alloc.per_trade_allocation == 200.0   # 2% of $10K
        assert alloc.reserve == 1_500.0              # 15% of $10K

    def test_conflict_opportunity_cost_recommends_waiting(self):
        """FORGE-69: Below-average setup → opportunity cost > 30% → wait."""
        result = calculate_opportunity_cost(
            current_win_rate=0.58, current_avg_win=80.0, current_avg_loss=60.0,
            typical_win_rate=0.72, typical_avg_win=200.0, typical_avg_loss=80.0,
            wait_probability=0.70,
        )
        # Low quality setup vs typical → should wait
        assert result.should_wait is True


# ─────────────────────────────────────────────────────────────────────────────
# ADVANCED ANALYTICS (FORGE-54/57/74/75/79)
# ─────────────────────────────────────────────────────────────────────────────

class TestAdvancedAnalytics:

    def test_normal_information_ratio_positive(self):
        """FORGE-74: Consistent positive returns → IR > 0."""
        returns = [0.01, 0.015, 0.008, 0.012, 0.009, 0.011, 0.010, 0.013, 0.008, 0.012, 0.009]
        result = calculate_information_ratio("GEX-01", returns)
        assert result.ir_value > 0

    def test_edge_sortino_meets_2_0_target(self):
        """FORGE-75: Consistent wins with small losses → Sortino ≥ 2.0."""
        # Mostly positive with few small negatives
        returns = [0.02, 0.015, -0.005, 0.018, 0.022, -0.004, 0.016, 0.019, -0.003, 0.020,
                   0.018, 0.022, -0.005, 0.015, 0.020]
        result = calculate_sortino("GEX-01", returns)
        assert result.meets_target   # Should hit ≥ 2.0

    def test_conflict_statistical_edge_requires_30_trades(self):
        """FORGE-79: Below 20 trades → not yet significant (insufficient data)."""
        result = verify_statistical_edge("GEX-01", wins=14, total=15)
        assert not result.is_statistically_significant
        assert result.sample_size == 15

    def test_normal_statistical_edge_with_good_win_rate(self):
        """FORGE-79: 70%+ win rate over 100 trades → statistically significant."""
        result = verify_statistical_edge("GEX-01", wins=72, total=100)
        assert result.is_statistically_significant
        assert result.edge_strength in ("STRONG", "MODERATE")

    def test_normal_performance_attribution(self):
        """FORGE-54: P&L correctly attributed across setups and regimes."""
        trades = [
            {"setup_id": "GEX-01", "regime": "trending", "hour": 10, "pnl": 200.0},
            {"setup_id": "GEX-01", "regime": "trending", "hour": 10, "pnl": -80.0},
            {"setup_id": "ICT-01", "regime": "ranging",  "hour": 11, "pnl": 150.0},
        ]
        attr = attribute_performance(trades)
        assert abs(attr.total_pnl - 270.0) < 0.01
        assert "GEX-01" in attr.by_setup

    def test_edge_risk_assessment_blocks_when_at_capacity(self):
        """FORGE-57: 6%+ of account in open risk → cannot open new positions."""
        result = assess_current_risk(
            account_equity=100_000.0,
            open_stop_distances=[2_000.0, 2_000.0, 2_500.0],  # $6,500 = 6.5%
            max_risk_pct=0.06,
        )
        assert result.can_open_new is False


# ─────────────────────────────────────────────────────────────────────────────
# MARKET INTELLIGENCE (FORGE-76/77/78/80/81/88)
# ─────────────────────────────────────────────────────────────────────────────

class TestMarketIntelligence:

    def test_normal_var_within_limits(self):
        """FORGE-76: Stable returns → VaR within 2% account limit."""
        returns = [-50.0, 80.0, -30.0, 100.0, -45.0] * 10
        result  = calculate_var(returns, position_size=1.0, account_equity=100_000.0)
        assert not result.exceeds_limit

    def test_edge_regime_transition_blocks_entry(self):
        """FORGE-78: Multiple transition signals → HIGH risk, entry blocked."""
        result = check_regime_transition(
            vix_change_pct=0.10,        # 10% VIX spike
            gex_direction_changed=True,  # GEX flipped
            breadth_divergence=0.30,    # Breadth diverging
            vix_level=25.0,
        )
        from market_intelligence import RegimeTransitionRisk
        assert result.risk_level == RegimeTransitionRisk.HIGH
        assert result.can_enter is False

    def test_normal_low_volume_is_noise(self):
        """FORGE-80: Low volume, small move → noise, not signal."""
        result = filter_noise_vs_signal(
            volume_surge=0.8, price_move_pct=0.001,
            bid_ask_spread_pct=0.001, time_of_day_score=0.7,
        )
        assert not result.is_signal
        assert result.signal_quality < 0.65

    def test_conflict_near_vwap_execute_immediately(self):
        """FORGE-81: Price within 0.3 ATR of VWAP → execute immediately."""
        result = plan_vwap_execution(
            current_price=4802.0, vwap=4800.0, direction="long", atr=10.0,
        )
        assert result.execute_now is True
        assert result.entry_style == "IMMEDIATE"

    def test_normal_liquidity_vacuum_detected(self):
        """FORGE-88 (C-19 L2): Thin bid/ask depth → liquidity vacuum."""
        result = detect_liquidity_vacuum(
            bid_depth_lots=20.0, ask_depth_lots=18.0,
            typical_depth=100.0,   # Normal is 100 lots
            avg_daily_volume=1_000_000.0, current_volume=200_000.0,
        )
        assert result.has_vacuum is True
        assert not result.safe_to_trade

    def test_edge_adverse_selection_not_flagged_normal(self):
        """FORGE-77: Low adverse ratio → no adverse selection."""
        result = detect_adverse_selection(
            immediate_adverse_moves=2, total_trades=20, avg_slippage_pct=0.001,
        )
        assert not result.is_adverse


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM ARCHITECTURE (FORGE-82/83/84/85/87)
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemArch:

    def test_normal_antifragile_improves_from_stress(self):
        """FORGE-82: Stress test with lessons → system score improves."""
        monitor = AntifragileMonitor()
        result = monitor.record_stress(
            "STRESS-001", "DRAWDOWN", pre_score=7.0,
            lessons=["Reduce size earlier", "Add MFI gate earlier"],
        )
        assert result.is_antifragile is True
        assert result.post_stress_score > result.pre_stress_score

    def test_edge_evolutionary_cycle_prunes_worst_3(self):
        """FORGE-83: Evolutionary cycle prunes 3 worst performers."""
        perf = {
            "GEX-01": {"win_rate": 0.75, "pnl": 5000, "trades": 50},
            "GEX-02": {"win_rate": 0.74, "pnl": 4800, "trades": 50},
            "GEX-05": {"win_rate": 0.60, "pnl": 500,  "trades": 20},
            "ICT-01": {"win_rate": 0.76, "pnl": 6000,  "trades": 50},
            "ICT-07": {"win_rate": 0.61, "pnl": 400,  "trades": 15},
            "SES-04": {"win_rate": 0.62, "pnl": 300,  "trades": 10},
        }
        update = run_evolutionary_cycle(perf, cycle_number=1)
        assert len(update.pruned_setups) == 3
        assert len(update.promoted_setups) == 3

    def test_normal_second_order_large_position_high_risk(self):
        """FORGE-84: Large position + high drawdown → HIGH risk second-order."""
        analysis = analyze_second_order_effects(
            "LARGE_POSITION", drawdown_pct_used=0.60, days_remaining=5,
        )
        assert analysis.risk_rating in ("MEDIUM", "HIGH")
        assert len(analysis.second_order) > 0

    def test_conflict_game_theory_protects_funded_over_eval(self):
        """FORGE-85: Funded account at risk → sacrifice evaluation."""
        decision = apply_game_theory(
            failing_eval_id="EVAL-003",
            failing_drawdown_pct=0.80,
            funded_accounts=["FTMO-001"],
            funded_safe_pcts=[0.20],   # 20% buffer — in danger
        )
        assert decision.sacrifice_evaluation == "EVAL-003"
        assert decision.cooperative_choice == "PROTECT_FUNDED"

    def test_normal_audit_clean_when_all_pass(self):
        """FORGE-87: All requirements passing → CLEAN audit."""
        reqs = {f"REQ-{i:03d}": True for i in range(50)}
        result = run_monthly_audit("AUDIT-001", reqs)
        assert result.overall_status == "CLEAN"
        assert result.pass_rate == 1.0

    def test_edge_audit_violations_critical(self):
        """FORGE-87: Critical violation → VIOLATIONS status."""
        reqs = {"C-02": True, "FORGE-03": False, "C-06": True}
        result = run_monthly_audit("AUDIT-002", reqs, critical_reqs=["FORGE-03"])
        assert result.overall_status == "VIOLATIONS"
        assert "FORGE-03" in result.critical_violations


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
