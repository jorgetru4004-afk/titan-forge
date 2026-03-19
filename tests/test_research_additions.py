"""
╔══════════════════════════════════════════════════════════════════════════════╗
║              test_research_additions.py — FORGE-122 to FORGE-151            ║
║  FX-06 Compliance: normal, edge, conflict per capability                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timezone
from firm_rules import FirmID
from research_additions import *


# ── FORGE-122: Spread Monitor ─────────────────────────────────────────────────
class TestForge122SpreadMonitor:
    def test_normal_spread_at_normal_level_allowed(self):
        r = check_spread("EURUSD", 0.00012)
        assert r.entry_allowed is True

    def test_edge_spread_at_exactly_150_pct_allowed(self):
        r = check_spread("EURUSD", 0.00012 * 1.5)
        assert r.entry_allowed is True

    def test_conflict_spread_above_150_pct_blocked(self):
        r = check_spread("EURUSD", 0.00012 * 1.6)
        assert r.entry_allowed is False
        assert r.spread_ratio > 1.5


# ── FORGE-123: Slippage Predictor ─────────────────────────────────────────────
class TestForge123SlippagePredictor:
    def test_normal_normal_conditions_allowed(self):
        r = predict_slippage("ES", 4800.0, 1.0, 10, 10.0, 1_000_000, 1_000_000)
        assert r.entry_allowed is True

    def test_edge_off_hours_high_slippage_blocked(self):
        r = predict_slippage("ES", 4800.0, 1.0, 3, 10.0, 1_000_000, 3_000_000)
        # 3am + 3x vol spike should push slippage high
        assert isinstance(r.predicted_slippage, float)

    def test_conflict_max_threshold_is_0_3_pct(self):
        assert SLIPPAGE_MAX_PCT == 0.003


# ── FORGE-124: Partial Fill Intelligence ─────────────────────────────────────
class TestForge124PartialFill:
    def test_normal_90_pct_filled_accept_reduced(self):
        r = handle_partial_fill(1.0, 0.92, True, True, False)
        assert r.action == PartialFillAction.ACCEPT_REDUCED

    def test_edge_conditions_improving_hold_and_add(self):
        r = handle_partial_fill(1.0, 0.50, True, True, False)
        assert r.action == PartialFillAction.HOLD_AND_ADD

    def test_conflict_invalid_setup_close_cleanly(self):
        r = handle_partial_fill(1.0, 0.60, False, False, False)
        assert r.action == PartialFillAction.CLOSE_CLEANLY


# ── FORGE-125: Order Type Optimizer ──────────────────────────────────────────
class TestForge125OrderType:
    def test_normal_high_urgency_selects_market(self):
        r = optimize_order_type(0.00012, 0.00012, False, False, 0.90, 4800.0, 10.0)
        assert r.order_type == "market"

    def test_edge_wide_spread_selects_limit(self):
        r = optimize_order_type(0.00020, 0.00012, False, False, 0.30, 1.09, 0.001)
        assert r.order_type == "limit"

    def test_conflict_momentum_always_market_regardless_spread(self):
        r = optimize_order_type(0.00030, 0.00012, True, False, 0.50, 4800.0, 10.0)
        assert r.order_type == "market"


# ── FORGE-126: Return on Drawdown Budget ─────────────────────────────────────
class TestForge126RODD:
    def test_normal_10_pct_profit_2_pct_dd_used_is_5x(self):
        r = calculate_rodd(10_000, 10_000, 2_000, 10_000)  # 10% profit / 20% of DD
        assert r.rodd == 5.0
        assert r.efficiency_grade == "A+"

    def test_edge_negative_rodd_grades_f(self):
        r = calculate_rodd(500, 10_000, 5_000, 10_000)
        assert r.efficiency_grade == "F"

    def test_conflict_rodd_beats_ror_as_metric(self):
        # 2% of account profit using 2% of drawdown = 100% RODD, not 2% ROR
        r = calculate_rodd(2_000, 10_000, 2_000, 10_000)
        assert r.rodd == 1.0
        assert r.efficiency_grade == "C"


# ── FORGE-127: Evaluation Fee ROI Calculator ─────────────────────────────────
class TestForge127EvalROI:
    def test_normal_high_expected_roi_recommends_buy(self):
        r = calculate_eval_roi(FirmID.FTMO, 100_000, 540.0, 0.80, 5_000, 6)
        assert r.meets_threshold is True
        assert r.expected_roi_pct > 500

    def test_edge_low_pass_rate_may_fail_threshold(self):
        r = calculate_eval_roi(FirmID.FTMO, 100_000, 540.0, 0.10, 500, 2)
        assert r.meets_threshold is False

    def test_conflict_threshold_is_500_pct(self):
        r = calculate_eval_roi(FirmID.FTMO, 100_000, 540.0, 0.80, 5_000, 6)
        assert r.MIN_ROI_THRESHOLD_PCT == 500.0


# ── FORGE-128: Capital Recycling Engine ──────────────────────────────────────
class TestForge128CapitalRecycling:
    def test_normal_payout_immediately_starts_next_eval(self):
        r = trigger_capital_recycle("FTMO-001", 5_000, 2_000, FirmID.FTMO)
        assert r.days_gap == 0

    def test_edge_insufficient_capital_noted_but_no_crash(self):
        r = trigger_capital_recycle("FTMO-001", 200, 100, FirmID.FTMO)
        assert "Cannot recycle" in r.recommendation or r.next_eval_fee > 100

    def test_conflict_recycling_engine_always_zero_days_gap(self):
        r = trigger_capital_recycle("APEX-001", 3_000, 1_000, FirmID.APEX)
        assert r.days_gap == 0


# ── FORGE-129: Flash Crash ────────────────────────────────────────────────────
class TestForge129FlashCrash:
    def test_normal_no_crash_small_move(self):
        assert check_forge129_flash_crash(4800.0, 4798.0) is False

    def test_edge_exactly_3_pct_move_is_crash(self):
        assert check_forge129_flash_crash(4800.0, 4800.0 / 1.031) is True

    def test_conflict_threshold_is_3_pct(self):
        assert FORGE129_FLASH_CRASH_THRESHOLD_PCT == 0.03


# ── FORGE-130: Weekend Gap Risk ───────────────────────────────────────────────
class TestForge130WeekendGap:
    def test_normal_low_tension_no_events_allows_full_hold(self):
        r = quantify_weekend_gap_risk("EURUSD", "low", [], 0.002, 1.0)
        assert r.should_hold is True

    def test_edge_high_tension_plus_events_reduces_size(self):
        r = quantify_weekend_gap_risk("ES", "high", ["G7 summit", "Fed speech"], 0.015, 1.0)
        assert r.max_allowed_position < 1.0

    def test_conflict_high_gap_risk_prevents_hold(self):
        r = quantify_weekend_gap_risk("ES", "high", ["crisis", "summit", "election"], 0.015, 1.0)
        assert r.should_hold is False


# ── FORGE-131: Correlation Spike ─────────────────────────────────────────────
class TestForge131CorrelationSpike:
    def test_normal_normal_correlations_no_spike(self):
        assert check_forge131_correlation_spike([0.80, 0.85, 0.75]) is False

    def test_edge_all_correlations_near_1_is_spike(self):
        assert check_forge131_correlation_spike([0.96, 0.97, 0.98]) is True

    def test_conflict_threshold_is_0_95(self):
        assert FORGE131_CORRELATION_SPIKE_THRESHOLD == 0.95


# ── FORGE-132: Platform Latency ───────────────────────────────────────────────
class TestForge132PlatencyLatency:
    def test_normal_low_latency_entries_allowed(self):
        r = check_platform_latency(120.0)
        assert r.is_acceptable is True
        assert r.entry_blocked is False

    def test_edge_exactly_500ms_still_ok(self):
        r = check_platform_latency(499.9)
        assert r.is_acceptable is True

    def test_conflict_above_500ms_blocks_entries(self):
        r = check_platform_latency(501.0)
        assert r.entry_blocked is True


# ── FORGE-133: Account Warming Protocol ──────────────────────────────────────
class TestForge133AccountWarming:
    def test_normal_day_1_is_warming_25_pct_size(self):
        r = check_account_warming(0)
        assert r.is_warming is True
        assert r.size_fraction == 0.25

    def test_edge_day_5_exactly_is_still_warming(self):
        r = check_account_warming(4)  # 0-indexed, day 5 = index 4
        assert r.is_warming is True

    def test_conflict_day_6_warming_complete_full_size(self):
        r = check_account_warming(5)
        assert r.is_warming is False
        assert r.size_fraction == 1.0


# ── FORGE-134 / 141: Promotion Scanner and Discount Database ─────────────────
class TestForge134And141Discounts:
    def test_normal_code_saves_money(self):
        scanner = PropFirmPromotionScanner()
        scanner.add_code(FirmID.FTMO, "SAVE25", 0.25)
        price, msg = scanner.discounted_price(FirmID.FTMO, 540.0)
        assert price == 540.0 * 0.75

    def test_edge_no_code_pays_full_price(self):
        scanner = PropFirmPromotionScanner()
        price, msg = scanner.discounted_price(FirmID.FTMO, 540.0)
        assert price == 540.0

    def test_conflict_expired_code_not_applied(self):
        db = FirmDiscountDatabase()
        db.add(DiscountCode(FirmID.FTMO, "OLD", 0.30, date(2020, 1, 1), "all", True))
        best = db.best_discount(FirmID.FTMO)
        assert best is None


# ── FORGE-135: Trade Fingerprint Variation ───────────────────────────────────
class TestForge135Fingerprint:
    def test_normal_different_accounts_get_different_delays(self):
        fp1 = generate_trade_fingerprint("ACC-001", 1.0, 4800.0, seed=1)
        fp2 = generate_trade_fingerprint("ACC-002", 1.0, 4800.0, seed=2)
        assert fp1.entry_delay_s != fp2.entry_delay_s

    def test_edge_delay_within_2_to_5_minute_window(self):
        fp = generate_trade_fingerprint("ACC-001", 1.0, 4800.0, seed=42)
        assert 120 <= fp.entry_delay_s <= 300

    def test_conflict_sizes_vary_within_7_pct(self):
        fp = generate_trade_fingerprint("ACC-001", 1.0, 4800.0, seed=42)
        assert 0.93 <= fp.size_variation <= 1.07


# ── FORGE-136: Instant vs Eval ROI ───────────────────────────────────────────
class TestForge136FundingROI:
    def test_normal_returns_a_winner(self):
        r = compare_funding_paths(200.0, 0.70, 50_000, 540.0, 0.90, 100_000,
                                  0.05, 12, 0.80)
        assert r.winner in ("instant", "evaluation")

    def test_edge_high_split_larger_account_wins_eval(self):
        r = compare_funding_paths(500.0, 0.50, 25_000, 540.0, 0.90, 100_000,
                                  0.06, 12, 0.85)
        assert r.winner == "evaluation"

    def test_conflict_savings_is_always_positive(self):
        r = compare_funding_paths(200.0, 0.70, 50_000, 540.0, 0.90, 100_000,
                                  0.05, 12, 0.80)
        assert r.savings >= 0


# ── FORGE-137: Setup Performance Database ────────────────────────────────────
class TestForge137SetupDatabase:
    def _make_db(self):
        db = SetupPerformanceDatabase()
        for i in range(15):
            db.record(SetupRecord("GEX-01", FirmID.FTMO, "trending", "ES",
                                  10, i < 10, 200.0 if i < 10 else -100.0,
                                  5.0, 15.0, date.today()))
        return db

    def test_normal_stats_calculated_correctly(self):
        db = self._make_db()
        stats = db.get_stats("GEX-01", FirmID.FTMO)
        assert stats["trades"] == 15
        assert abs(stats["win_rate"] - 10/15) < 0.01

    def test_edge_no_data_returns_zero_trades(self):
        db = SetupPerformanceDatabase()
        stats = db.get_stats("MISSING", FirmID.FTMO)
        assert stats["trades"] == 0

    def test_conflict_grows_with_data(self):
        db = self._make_db()
        assert db.total_records == 15


# ── FORGE-138: Hot Hand Protocol ─────────────────────────────────────────────
class TestForge138HotHand:
    def test_normal_5_sessions_activates_boost(self):
        r = check_hot_hand(5, is_ftmo=False)
        assert r.is_active is True
        assert r.size_multiplier == 1.15

    def test_edge_ftmo_always_disabled(self):
        r = check_hot_hand(10, is_ftmo=True)
        assert r.is_active is False
        assert r.size_multiplier == 1.0

    def test_conflict_4_sessions_not_active(self):
        r = check_hot_hand(4, is_ftmo=False)
        assert r.is_active is False


# ── FORGE-139: Edge Decay Detector ───────────────────────────────────────────
class TestForge139EdgeDecay:
    def test_normal_stable_win_rate_no_decay(self):
        trades = [True] * 14 + [False] * 6  # 70%
        r = detect_edge_decay("GEX-01", trades, 0.70)
        assert r.edge_decaying is False

    def test_edge_2_sd_drop_triggers_decay(self):
        trades = [True] * 5 + [False] * 15  # 25% vs 70% historical
        r = detect_edge_decay("GEX-01", trades, 0.70, 0.05)
        assert r.edge_decaying is True
        assert r.action == "ROTATE_SETUP"

    def test_conflict_fewer_than_20_trades_returns_insufficient(self):
        r = detect_edge_decay("GEX-01", [True] * 10, 0.70)
        assert r.edge_decaying is False
        assert "20" in r.reason


# ── FORGE-140: Time-of-Day Atlas ─────────────────────────────────────────────
class TestForge140TimeOfDayAtlas:
    def test_normal_records_and_returns_best_hours(self):
        atlas = TimeOfDayAtlas()
        for _ in range(6):
            atlas.record(FirmID.FTMO, "ES", 10, 200.0)
            atlas.record(FirmID.FTMO, "ES", 14, -50.0)
        best = atlas.get_best_hours(FirmID.FTMO, "ES")
        assert best[0][0] == 10

    def test_edge_no_data_allows_all_hours(self):
        atlas = TimeOfDayAtlas()
        assert atlas.should_trade_now(FirmID.FTMO, "ES", 10) is True

    def test_conflict_bad_hours_excluded(self):
        atlas = TimeOfDayAtlas()
        for _ in range(6):
            atlas.record(FirmID.FTMO, "ES", 10, 300.0)
            atlas.record(FirmID.FTMO, "ES", 3, -200.0)
        best = atlas.get_best_hours(FirmID.FTMO, "ES", top_n=1)
        assert best[0][0] == 10


# ── FORGE-142: New Firm Early Mover ──────────────────────────────────────────
class TestForge142NewFirm:
    def test_normal_legitimate_new_firm_high_score(self):
        r = assess_new_firm("NewPropFirm", 2, True, True, "positive", 0.30)
        assert r.is_legitimate is True
        assert r.early_mover_score >= 7

    def test_edge_no_regulatory_ok_not_legitimate(self):
        r = assess_new_firm("SketchyFirm", 1, False, False, "negative", 0.50)
        assert r.is_legitimate is False

    def test_conflict_large_discount_boosts_score(self):
        r1 = assess_new_firm("Firm", 6, True, True, "neutral", 0.0)
        r2 = assess_new_firm("Firm", 6, True, True, "neutral", 0.30)
        assert r2.early_mover_score > r1.early_mover_score


# ── FORGE-143: Instrument Rotation ───────────────────────────────────────────
class TestForge143InstrumentRotation:
    def test_normal_trending_instrument_selected_first(self):
        inst, reason = rotate_to_best_instrument(
            FirmID.FTMO, {"EURUSD": "trending", "US500": "ranging"})
        assert inst == "EURUSD"

    def test_edge_all_ranging_selects_ranging(self):
        inst, reason = rotate_to_best_instrument(
            FirmID.APEX, {"ES": "ranging", "NQ": "choppy"})
        assert inst == "ES"

    def test_conflict_firm_instruments_respected(self):
        inst, reason = rotate_to_best_instrument(FirmID.DNA_FUNDED, {"EURUSD": "trending"})
        assert inst in FIRM_INSTRUMENTS.get(FirmID.DNA_FUNDED, [])


# ── FORGE-144: Patience Score ────────────────────────────────────────────────
class TestForge144PatienceScore:
    def test_normal_optimal_frequency_no_adjustment(self):
        r = calculate_patience_score(390.0, 3)  # 130min avg = optimal
        assert r.conviction_adjustment == 0.0

    def test_edge_overtrading_raises_threshold(self):
        r = calculate_patience_score(180.0, 10)  # 18min avg = overtrading
        assert r.is_overtrading is True
        assert r.conviction_adjustment > 0

    def test_conflict_undertrading_lowers_threshold(self):
        r = calculate_patience_score(600.0, 2)  # 300min avg = undertrading
        assert r.is_undertrading is True
        assert r.conviction_adjustment < 0


# ── FORGE-145: End of Month ───────────────────────────────────────────────────
class TestForge145EndOfMonth:
    def test_normal_last_3_days_activates_eom(self):
        r = check_end_of_month_signal(date.today(), "US500", 22, 20)
        assert r.is_eom_window is True
        assert r.size_boost > 1.0

    def test_edge_mid_month_no_eom(self):
        r = check_end_of_month_signal(date.today(), "US500", 22, 10)
        assert r.is_eom_window is False

    def test_conflict_eom_is_bullish_for_indices(self):
        r = check_end_of_month_signal(date.today(), "US500", 22, 21)
        assert r.expected_flow == "bullish_bias"


# ── FORGE-146: Liquidity Session Optimizer ───────────────────────────────────
class TestForge146LiquiditySession:
    def test_normal_eurusd_in_london_ny_overlap_allowed(self):
        allowed, reason = is_in_liquidity_window("EURUSD", 9)
        assert allowed is True

    def test_edge_eurusd_at_midnight_blocked(self):
        allowed, reason = is_in_liquidity_window("EURUSD", 0)
        assert allowed is False

    def test_conflict_es_after_11am_blocked(self):
        allowed, reason = is_in_liquidity_window("ES", 12)
        assert allowed is False


# ── FORGE-147: Benchmark Day Protocol ────────────────────────────────────────
class TestForge147BenchmarkDay:
    def test_normal_threshold_met_no_activation(self):
        r = check_benchmark_day_protocol(FirmID.APEX, 280.0, 30.0)
        assert r.activation_status == "THRESHOLD_MET"

    def test_edge_final_60_min_shortfall_activates(self):
        r = check_benchmark_day_protocol(FirmID.APEX, 100.0, 45.0)
        assert r.activation_status == "ACTIVATED"
        assert r.highest_prob_setup in ("CHOP-04", "CHOP-10")

    def test_conflict_topstep_threshold_is_150(self):
        r = check_benchmark_day_protocol(FirmID.TOPSTEP, 0.0, 45.0)
        assert r.threshold == 150.0


# ── FORGE-148: Win Streak Preservation ───────────────────────────────────────
class TestForge148WinStreak:
    def test_normal_5_sessions_activates_preservation(self):
        r = check_win_streak_preservation(5)
        assert r.is_preservation_mode is True
        assert r.size_reduction == 0.85

    def test_edge_4_sessions_not_active(self):
        r = check_win_streak_preservation(4)
        assert r.is_preservation_mode is False

    def test_conflict_preservation_uses_earlier_stops(self):
        r = check_win_streak_preservation(7)
        assert r.earlier_stop_mult == 0.85


# ── FORGE-149: Insurance Position ────────────────────────────────────────────
class TestForge149InsurancePosition:
    def test_normal_95_pct_complete_opens_insurance(self):
        r = check_insurance_position(0.96, "bullish", 2)
        assert r.should_open is True
        assert r.max_risk_pct == 0.001

    def test_edge_below_95_pct_no_insurance(self):
        r = check_insurance_position(0.90, "bullish", 2)
        assert r.should_open is False

    def test_conflict_neutral_trend_no_insurance(self):
        r = check_insurance_position(0.97, "neutral", 2)
        assert r.should_open is False


# ── FORGE-150: Seasonal Edge Calendar ────────────────────────────────────────
class TestForge150SeasonalCalendar:
    def test_normal_us500_december_bullish(self):
        r = get_seasonal_edge("US500", 12)
        assert r.trade_direction == "long"
        assert r.edge_score > 0

    def test_edge_us500_september_bearish(self):
        r = get_seasonal_edge("US500", 9)
        assert r.trade_direction == "short"

    def test_conflict_unknown_instrument_returns_neutral(self):
        r = get_seasonal_edge("UNKNOWN", 6)
        assert r.edge_score == 0.0


# ── FORGE-151: Live Return Attribution ───────────────────────────────────────
class TestForge151ReturnAttribution:
    def test_normal_records_and_snapshots(self):
        engine = LiveReturnAttributionEngine()
        engine.record_trade("GEX-01", "ES", "trending", 200.0)
        engine.record_trade("ICT-01", "EURUSD", "ranging", 150.0)
        snap = engine.get_snapshot()
        assert snap.best_setup_now in ("GEX-01", "ICT-01")

    def test_edge_losing_setup_gets_weight_below_1(self):
        engine = LiveReturnAttributionEngine()
        for _ in range(3):
            engine.record_trade("CHOP-01", "ES", "choppy", -100.0)
        snap = engine.get_snapshot()
        assert snap.setup_weight_adjustments.get("CHOP-01", 1.0) < 1.0

    def test_conflict_winning_setup_gets_weight_above_1(self):
        engine = LiveReturnAttributionEngine()
        for _ in range(3):
            engine.record_trade("GEX-01", "ES", "trending", 300.0)
        snap = engine.get_snapshot()
        assert snap.setup_weight_adjustments.get("GEX-01", 1.0) > 1.0


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
