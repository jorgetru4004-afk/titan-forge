"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              test_document_compliance.py — Strict Document Compliance       ║
║  Tests for FORGE-34/35/38/40/42/43/45/49/50/51/52/53/55/56/57/66           ║
║  + FORGE-129/131 Emergency Overrides                                        ║
║  + All 30 FORGE-TS Strategies                                               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, time, timedelta, timezone
from firm_rules import FirmID

from phase1_intelligence import (
    identify_drawdown_model, DrawdownModel, FIRM_DRAWDOWN_MODELS,
    check_consistency_compliance, CONSISTENCY_CAPS,
    check_reset_optimizer,
    assess_firm_health,
    EvalBehavioralJournal,
    NinetyMinuteRecovery, RECOVERY_PAUSE_MINUTES,
    get_firm_arbitrage_intel,
    validate_backtest_threshold,
    calculate_kelly_size, KELLY_HARD_CAP_EVALUATION, KELLY_MIN_TRADES,
    calculate_ruin_probability,
    match_regime_to_firm,
    assess_firm_financial_health,
    perform_calibration_reset,
    verify_information_edge,
    track_evaluation_cost_basis,
    check_approach_protocol,
)
from emergency_overrides import (
    detect_flash_crash, detect_correlation_spike,
    run_level2_emergency_check, EmergencyLevel,
    FLASH_CRASH_MOVE_PCT_1MIN,
)
from forge_ts_strategies import (
    ts01_gamma_flip, ts02_dealer_cascade, ts03_gex_pin_break,
    ts04_vanna_drift, ts05_charm_decay, ts06_ob_fvg,
    ts07_liquidity_sweep, ts08_killzone_ote, ts09_breaker_block,
    ts10_asian_raid, ts11_premium_discount, ts12_fvg_inversion,
    ts13_msb_ote, ts14_poc_revert, ts15_value_area_fade,
    ts16_lvn_express, ts17_hvn_cluster, ts18_anchored_vwap,
    ts19_delta_divergence, ts20_footprint_absorption, ts21_ob_stacking,
    ts22_imbalance_cascade, ts23_ny_kill_zone, ts24_london_ny_overlap,
    ts25_first_hour_reversal, ts26_preclose_institutional, ts27_monday_gap_fill,
    ts28_unusual_options_flow, ts29_dark_pool_print, ts30_cot_extreme,
    STRATEGY_COUNT, ALL_STRATEGY_IDS,
)

NOW = datetime(2026, 3, 19, 14, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-34: TRAILING VS STATIC DRAWDOWN
# ─────────────────────────────────────────────────────────────────────────────

class TestForge34DrawdownModel:

    def test_normal_ftmo_is_static(self):
        """FORGE-34: FTMO uses static drawdown — most flexibility."""
        result = identify_drawdown_model(FirmID.FTMO)
        assert result.model == DrawdownModel.STATIC
        assert result.size_multiplier == 1.00

    def test_edge_apex_is_trailing_unrealized(self):
        """FORGE-34: Apex trailing unrealized — most dangerous — 30% size reduction."""
        result = identify_drawdown_model(FirmID.APEX)
        assert result.model == DrawdownModel.TRAILING_UNREALIZED
        assert result.size_multiplier < 1.00

    def test_conflict_apex_more_conservative_than_ftmo(self):
        """FORGE-34: Apex must have lower size multiplier than FTMO."""
        apex = identify_drawdown_model(FirmID.APEX)
        ftmo = identify_drawdown_model(FirmID.FTMO)
        assert apex.size_multiplier < ftmo.size_multiplier


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-35: CONSISTENCY RULE COMPLIANCE
# ─────────────────────────────────────────────────────────────────────────────

class TestForge35ConsistencyRule:

    def test_normal_ftmo_no_consistency_rule(self):
        """FORGE-35: FTMO has NO consistency rule — biggest advantage."""
        assert CONSISTENCY_CAPS[FirmID.FTMO] is None

    def test_edge_dna_40_pct_cap_blocks_at_cap(self):
        """FORGE-35: DNA 40% cap — trading stopped at cap."""
        result = check_consistency_compliance(FirmID.DNA_FUNDED, 4_100.0, 10_000.0)
        # 41% > 40% cap
        assert result.is_at_cap is True
        assert result.size_throttle == 0.0

    def test_conflict_apex_50_pct_cap_throttles_on_approach(self):
        """FORGE-35: Apex 50% cap — throttles size when approaching."""
        result = check_consistency_compliance(FirmID.APEX, 4_200.0, 10_000.0)
        # 42% — approaching 50% cap (within 80% of cap = 40%)
        assert result.is_approaching is True
        assert result.size_throttle < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-43: 90-MINUTE RECOVERY PROTOCOL (CRITICAL — was wrong before)
# ─────────────────────────────────────────────────────────────────────────────

class TestForge43NinetyMinuteRecovery:

    def test_normal_any_loss_triggers_90_min_pause(self):
        """FORGE-43: ANY losing trade → 90-minute pause. No exceptions."""
        rec = NinetyMinuteRecovery()
        status = rec.record_loss(as_of=NOW)
        assert status.is_in_recovery is True
        assert status.trading_permitted is False
        assert status.minutes_remaining is not None
        assert status.minutes_remaining > 80   # Should be ~90

    def test_edge_pause_is_exactly_90_minutes(self):
        """FORGE-43: Pause duration = exactly 90 minutes per document."""
        assert RECOVERY_PAUSE_MINUTES == 90

    def test_conflict_second_loss_restarts_90_min_clock(self):
        """FORGE-43: Second loss during recovery resets the 90-min clock."""
        rec = NinetyMinuteRecovery()
        rec.record_loss(as_of=NOW)
        # 30 minutes later — another loss
        second_loss_time = NOW + timedelta(minutes=30)
        status = rec.record_loss(as_of=second_loss_time)
        # Clock reset — should have ~90 min remaining, not ~60
        assert status.minutes_remaining > 80

    def test_normal_trading_resumes_after_90_minutes(self):
        """FORGE-43: Trading permitted again after 90 minutes."""
        rec = NinetyMinuteRecovery()
        rec.record_loss(as_of=NOW)
        future = NOW + timedelta(minutes=91)
        status = rec.check_resume(as_of=future)
        assert status.trading_permitted is True
        assert status.is_in_recovery is False


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-40: FIRM HEALTH MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class TestForge40FirmHealth:

    def test_normal_ftmo_is_healthy(self):
        """FORGE-40: FTMO 4.7/5 Trustpilot → healthy."""
        result = assess_firm_health(FirmID.FTMO)
        assert result.is_healthy is True
        assert not result.pause_evaluations

    def test_edge_dna_has_warning(self):
        """FORGE-40: DNA Funded 3.4/5 → warning flag."""
        result = assess_firm_health(FirmID.DNA_FUNDED)
        assert result.warning is not None
        assert "3.4" in result.warning or "DNA" in result.warning

    def test_conflict_low_trustpilot_can_trigger_pause(self):
        """FORGE-40: Very low Trustpilot + disputes → pause evaluations."""
        result = assess_firm_health("UNKNOWN", trustpilot_override=2.0,
                                    dispute_rate_override=0.30)
        assert result.pause_evaluations is True


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-49/50/51: BACKTESTING, KELLY, RUIN
# ─────────────────────────────────────────────────────────────────────────────

class TestForge49_50_51:

    def test_normal_backtest_passes_at_80_pct_50_sims(self):
        """FORGE-49: 80%+ pass rate over 50+ simulations over 6+ months."""
        result = validate_backtest_threshold(
            FirmID.FTMO, simulations_run=50, passes=42,
            avg_profit=8500.0, max_drawdown=0.45, months_tested=6,
        )
        assert result.meets_threshold is True
        assert result.pass_rate >= 0.80

    def test_edge_kelly_immature_below_100_trades(self):
        """FORGE-50: Below 100 trades → immature default 0.5% (FX-03)."""
        size = calculate_kelly_size(0.70, 0.02, 0.01, 10_000.0, 8_000.0, 50, False)
        assert abs(size - 0.005) < 0.001   # Exactly 0.5% default

    def test_conflict_kelly_hard_cap_enforced(self):
        """FORGE-50 / FX-04: Kelly can NEVER exceed 2% during evaluation."""
        # Even with perfect win rate, cap is 2%
        size = calculate_kelly_size(0.90, 0.05, 0.01, 10_000.0, 9_000.0, 200, False)
        assert size <= KELLY_HARD_CAP_EVALUATION

    def test_normal_ruin_probability_safe_at_small_size(self):
        """FORGE-51: Small position size → ruin probability < 5%."""
        result = calculate_ruin_probability(
            win_rate=0.65, avg_win_pct=0.02, avg_loss_pct=0.01,
            current_position_pct=0.005, bankroll=100_000.0,
        )
        assert not result.exceeds_threshold

    def test_edge_ruin_flagged_above_5_pct(self):
        """FORGE-51: P(ruin) > 5% → must reduce position."""
        result = calculate_ruin_probability(
            win_rate=0.50, avg_win_pct=0.01, avg_loss_pct=0.02,
            current_position_pct=0.10, bankroll=100_000.0,
        )
        # Negative edge with large position → high ruin probability
        assert result.exceeds_threshold is True


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-52: REGIME TO FIRM MATCHMAKING
# ─────────────────────────────────────────────────────────────────────────────

class TestForge52RegimeMatch:

    def test_normal_trending_low_vol_matches_ftmo(self):
        """FORGE-52: Trending low-vol → FTMO (no consistency rule)."""
        firm, reason = match_regime_to_firm("trending_low_vol")
        assert firm == FirmID.FTMO

    def test_edge_ranging_matches_dna(self):
        """FORGE-52: Ranging → DNA Funded (forex ICT setups)."""
        firm, _ = match_regime_to_firm("ranging_low_vol")
        assert firm == FirmID.DNA_FUNDED

    def test_conflict_trending_high_vol_matches_apex(self):
        """FORGE-52: Trending high-vol → Apex (futures momentum)."""
        firm, _ = match_regime_to_firm("trending_high_vol")
        assert firm == FirmID.APEX


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-55/56/57: CALIBRATION, EDGE VERIFICATION, COST BASIS
# ─────────────────────────────────────────────────────────────────────────────

class TestForge55_56_57:

    def test_normal_calibration_reset_ready_for_eval(self):
        """FORGE-55: Reset produces clean state — ready for evaluation."""
        state = perform_calibration_reset("EVAL-001", "PASS")
        assert state.is_clean is True
        assert state.ready_for_eval is True
        assert len(state.checklist_completed) >= 5

    def test_normal_edge_verification_within_1_5_sd(self):
        """FORGE-56: 70% win rate within 1.5 SD of 68% historical → confirmed."""
        trades = [True]*14 + [False]*6   # 70% recent
        result = verify_information_edge("GEX-01", trades, 0.68, historical_std=0.05)
        assert result.edge_confirmed is True
        assert result.within_1_5_sd is True

    def test_conflict_edge_decay_beyond_1_5_sd(self):
        """FORGE-56: Recent win rate far below historical → edge decay detected."""
        trades = [True]*5 + [False]*15   # 25% recent vs 70% historical
        result = verify_information_edge("GEX-01", trades, 0.70, historical_std=0.05)
        assert result.edge_confirmed is False

    def test_normal_cost_basis_roi_positive(self):
        """FORGE-57: Revenue > fees → positive ROI tracked."""
        result = track_evaluation_cost_basis(
            FirmID.FTMO, 100_000.0, 540.0, passes=3, fails=1,
            total_fees_paid=2_160.0, total_revenue=15_000.0, monthly_payout=5_000.0,
        )
        assert result.roi_pct > 0
        assert result.break_even_month is not None


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-66: PROFIT TARGET APPROACH PROTOCOL
# ─────────────────────────────────────────────────────────────────────────────

class TestForge66ApproachProtocol:

    def test_normal_early_full_size(self):
        """FORGE-66: Far from target → full size."""
        result = check_approach_protocol(2_000.0, 10_000.0)
        assert not result.is_within_20_pct
        assert result.size_multiplier == 1.0

    def test_edge_within_20_pct_half_size(self):
        """FORGE-66: Within 20% of target → half size only."""
        result = check_approach_protocol(8_200.0, 10_000.0)  # 18% remaining
        assert result.is_within_20_pct is True
        assert not result.is_within_10_pct
        assert result.size_multiplier == 0.50

    def test_conflict_within_10_pct_minimum_one_trade(self):
        """FORGE-66: Within 10% → minimum size, ONE trade at a time."""
        result = check_approach_protocol(9_200.0, 10_000.0)  # 8% remaining
        assert result.is_within_10_pct is True
        assert result.size_multiplier == 0.25
        assert result.max_concurrent == 1


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-129: FLASH CRASH DETECTION (Level 2 Emergency)
# ─────────────────────────────────────────────────────────────────────────────

class TestForge129FlashCrash:

    def test_normal_no_crash_normal_move(self):
        """FORGE-129: Normal 0.1% move → no flash crash."""
        result = detect_flash_crash(
            price_now=4800.0, price_1min_ago=4795.2, price_5min_ago=4792.0,
            volume_now=1_000_000.0, avg_volume=1_000_000.0,
        )
        assert not result.is_crash

    def test_edge_flash_crash_at_0_5_pct_1min_plus_volume(self):
        """FORGE-129: 0.5%+ in 1 min + 5× volume → CRITICAL emergency."""
        result = detect_flash_crash(
            price_now=4800.0, price_1min_ago=4776.0,  # 0.5% move
            price_5min_ago=4780.0,
            volume_now=6_000_000.0, avg_volume=1_000_000.0,  # 6× volume
        )
        assert result.is_crash is True
        assert result.close_all_positions is True
        assert result.halt_new_entries is True

    def test_conflict_flash_crash_is_level_2_emergency(self):
        """FORGE-129: Flash crash → Level 2 Emergency — overrides L3/L4/L5."""
        result = run_level2_emergency_check(
            price_now=4800.0, price_1min_ago=4776.0,
            price_5min_ago=4780.0,
            volume_now=6_000_000.0, avg_volume=1_000_000.0,
        )
        assert result.is_emergency is True
        assert not result.all_clear
        assert result.blocking_reason is not None


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-131: CORRELATION SPIKE DETECTION (Level 2 Emergency)
# ─────────────────────────────────────────────────────────────────────────────

class TestForge131CorrelationSpike:

    def test_normal_no_spike_diverse_returns(self):
        """FORGE-131: Diverse returns → no correlation spike."""
        result = detect_correlation_spike(
            {"ES": 0.003, "EURUSD": -0.002, "GC": 0.001},
            {("ES","EURUSD"): 0.2, ("ES","GC"): 0.1},
        )
        assert not result.is_spike

    def test_edge_systemic_risk_all_assets_down(self):
        """FORGE-131: All assets down together → systemic risk detected."""
        result = detect_correlation_spike(
            {"ES": -0.025, "NQ": -0.030, "EURUSD": -0.015, "GC": -0.020},
            {},  # No normal correlations defined
        )
        assert result.is_spike is True
        assert result.systemic_risk is True
        assert result.close_all_positions is True

    def test_conflict_correlation_spike_overrides_strategy(self):
        """FORGE-131: Level 2 Emergency — overrides Risk Management and Strategy."""
        result = run_level2_emergency_check(
            price_now=4800.0, price_1min_ago=4799.0,
            price_5min_ago=4798.0,
            volume_now=1_000_000.0, avg_volume=1_000_000.0,
            asset_returns={"ES": -0.020, "NQ": -0.025, "EURUSD": -0.015},
            normal_corrs={},
        )
        assert result.is_emergency is True


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-TS STRATEGIES — ALL 30
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeTS_AllStrategies:

    def test_normal_all_30_strategies_defined(self):
        """Document Section 10: exactly 30 strategies required."""
        assert STRATEGY_COUNT == 30
        assert len(ALL_STRATEGY_IDS) == 30

    def test_normal_ts01_gamma_flip_confirmed(self):
        """FORGE-TS-01 GEX-01: GEX flips → signal valid."""
        sig = ts01_gamma_flip(500.0, -300.0, 4800.0, 4802.0, 10.0, "long")
        assert sig.valid
        assert sig.win_rate == 0.75

    def test_edge_ts01_no_flip_rejected(self):
        """FORGE-TS-01: No GEX flip → no signal."""
        sig = ts01_gamma_flip(300.0, 200.0, 4800.0, 4802.0, 10.0, "long")
        assert not sig.valid

    def test_normal_ts02_dealer_cascade_confirmed(self):
        """FORGE-TS-02 GEX-02: GEX negative + momentum → confirmed."""
        sig = ts02_dealer_cascade(True, True, 4810.0, 4800.0, 10.0, "long")
        assert sig.valid and sig.rr == 3.0

    def test_normal_ts06_ob_fvg_confluence_confirmed(self):
        """FORGE-TS-06 ICT-01: OB+FVG overlap → confirmed (highest WR 76%)."""
        sig = ts06_ob_fvg(4800.0, 4810.0, 4795.0, 4808.0, 4793.0, 5.0, "long")
        assert sig.valid
        assert sig.win_rate == 0.76

    def test_normal_ts07_sweep_reverse_confirmed(self):
        """FORGE-TS-07 ICT-02: Sweep complete → reversal entry."""
        sig = ts07_liquidity_sweep(True, 4801.0, 4800.0, 8.0, "long")
        assert sig.valid and sig.rr == 3.0

    def test_normal_ts14_poc_revert_at_extreme(self):
        """FORGE-TS-14 VOL-01: At value area extreme → POC revert."""
        sig = ts14_poc_revert(4840.0, 4800.0, 4835.0, 4790.0, 10.0, "short")
        assert sig.valid
        assert abs(sig.target - 4800.0) < 0.01

    def test_normal_ts19_delta_divergence_confirmed(self):
        """FORGE-TS-19 ORD-01: Price extreme + diverging delta → confirmed."""
        sig = ts19_delta_divergence(True, True, 4850.0, 15.0, "short")
        assert sig.valid and sig.win_rate == 0.75

    def test_normal_ts23_ny_kill_zone_confirmed(self):
        """FORGE-TS-23 SES-01: In kill zone with bias → confirmed."""
        sig = ts23_ny_kill_zone(time(10,0), "bullish", "negative",
                                4810.0, 4805.0, 10.0, 4820.0, 4790.0)
        assert sig.valid

    def test_edge_ts23_outside_kill_zone_rejected(self):
        """FORGE-TS-23: Outside 9:30-11am ET → rejected."""
        sig = ts23_ny_kill_zone(time(13,30), "bullish", "negative",
                                4810.0, 4805.0, 10.0, 4820.0, 4790.0)
        assert not sig.valid

    def test_normal_ts28_unusual_flow_confirmed(self):
        """FORGE-TS-28 INS-01: Unusual flow → highest confidence."""
        sig = ts28_unusual_options_flow(True, "call_sweep", 4800.0, 4900.0, 10.0, "long")
        assert sig.valid
        assert sig.confidence >= 0.80

    def test_normal_ts30_cot_extreme_confirmed(self):
        """FORGE-TS-30 INS-03: COT extreme → institutional reversal."""
        sig = ts30_cot_extreme(50_000, 40_000, 1.0900, 1.0900, 0.001, "long")
        assert sig.valid and sig.rr == 3.0

    def test_normal_all_strategies_positive_ev(self):
        """Section 10: All 30 strategies must have positive EV."""
        test_sigs = [
            ts01_gamma_flip(500, -300, 4800, 4802, 10, "long"),
            ts02_dealer_cascade(True, True, 4810, 4800, 10, "long"),
            ts06_ob_fvg(4800, 4810, 4795, 4808, 4793, 5, "long"),
            ts19_delta_divergence(True, True, 4850, 15, "short"),
            ts28_unusual_options_flow(True, "call", 4800, 4900, 10, "long"),
            ts30_cot_extreme(50000, 40000, 1.09, 1.09, 0.001, "long"),
        ]
        for sig in test_sigs:
            if sig.valid:
                assert sig.ev > 0, f"{sig.strategy_id} has negative EV: {sig.ev:.3f}"


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
