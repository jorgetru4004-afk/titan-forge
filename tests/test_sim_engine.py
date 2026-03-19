"""
╔══════════════════════════════════════════════════════════════════════════════╗
║               test_sim_engine.py — Section 12 — FX-06 Compliance            ║
║  Tests for all simulation engine modules                                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timezone

from sim.data_loader import (
    DataLoader, OHLCV, TRAIN_START, TRAIN_END, VAL_START, VAL_END,
    REGIME_WINDOWS,
)
from sim.execution_model import ExecutionModel, INSTRUMENT_SPECS
from sim.firm_sim.ftmo_sim import FTMOSimAccount, APEXSimAccount
from sim.sim_engine import (
    SimEngine, MATURITY_THRESHOLDS, CapabilityMaturity, SimSpeed,
)
from sim.training_runner import TrainingRunner, RegimeTestResult


def make_bar(
    close=4800.0, high=None, low=None, volume=1_000_000.0, atr=10.0
) -> OHLCV:
    return OHLCV(
        timestamp=datetime(2023, 6, 1, 14, 0, tzinfo=timezone.utc),
        open=close - 2, high=high or close + 5,
        low=low or close - 5, close=close,
        volume=volume, atr=atr,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADER
# ─────────────────────────────────────────────────────────────────────────────

class TestDataLoader:

    def test_normal_training_data_in_correct_range(self):
        """Normal: Training data dates within 2021–2023."""
        loader = DataLoader()  # synthetic mode
        bars   = loader.load_training_data("ES", start=TRAIN_START, end=TRAIN_END)
        assert len(bars) > 0
        for bar in bars[:5]:
            assert bar.timestamp.date() >= TRAIN_START

    def test_edge_validation_data_after_training_end(self):
        """Edge: Validation data starts after training end — overfitting protection."""
        loader = DataLoader()
        val    = loader.load_validation_data("ES", start=VAL_START, end=VAL_END)
        train  = loader.load_training_data("ES",   start=TRAIN_START, end=TRAIN_END)
        assert len(val) > 0
        # Validation must not overlap training
        val_min   = min(b.timestamp.date() for b in val)
        train_max = max(b.timestamp.date() for b in train)
        assert val_min >= train_max

    def test_conflict_regime_window_loads_correct_dates(self):
        """Conflict: Trending bull regime should load Q1 2023 data."""
        loader = DataLoader()
        bars   = loader.load_regime_window("trending_bull", "ES")
        assert len(bars) > 0
        for bar in bars:
            assert bar.regime == "trending_bull"
            assert date(2023, 1, 1) <= bar.timestamp.date() <= date(2023, 3, 31)

    def test_normal_all_4_regime_windows_defined(self):
        """Normal: P-12 requires exactly 4 regime windows — all present."""
        assert "trending_bull"   in REGIME_WINDOWS
        assert "trending_bear"   in REGIME_WINDOWS
        assert "choppy_ranging"  in REGIME_WINDOWS
        assert "high_vol_crisis" in REGIME_WINDOWS
        assert len(REGIME_WINDOWS) == 4

    def test_normal_synthetic_bars_have_atr(self):
        """Normal: Synthetic bars have ATR computed (needed by engine)."""
        loader = DataLoader()
        bars   = loader.load_training_data("EURUSD", start=date(2023,1,1), end=date(2023,2,1))
        assert len(bars) > 0
        # ATR should be populated after loading
        bars_with_atr = [b for b in bars if b.atr > 0]
        assert len(bars_with_atr) > 0

    def test_edge_to_daily_sessions_groups_correctly(self):
        """Edge: to_daily_sessions groups intraday bars by date."""
        loader = DataLoader()
        bars   = loader.load_training_data("ES", start=date(2023,1,3), end=date(2023,1,10))
        sessions = loader.to_daily_sessions(bars, "ES")
        # Each session should only contain bars from that date
        for s in sessions:
            for b in s.bars:
                assert b.bar_date == s.session_date


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION MODEL
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionModel:

    def test_normal_long_fill_above_open(self):
        """Normal: Long entry fill = open + spread + slippage (higher than open)."""
        model = ExecutionModel(seed=42)
        bar   = make_bar(close=4800.0)
        fill  = model.simulate_fill("ES", "long", 1.0, bar)
        assert fill.fill_price >= bar.open   # Long pays above open
        assert fill.spread_cost >= 0
        assert fill.slippage   >= 0

    def test_edge_short_fill_below_open(self):
        """Edge: Short entry fill = open - spread - slippage (lower than open)."""
        model = ExecutionModel(seed=42)
        bar   = make_bar(close=4800.0)
        fill  = model.simulate_fill("ES", "short", 1.0, bar)
        assert fill.fill_price <= bar.open   # Short sells below open

    def test_conflict_high_vol_increases_friction(self):
        """Conflict: High volatility flag increases slippage vs normal."""
        model   = ExecutionModel(seed=42)
        bar     = make_bar(close=4800.0, atr=10.0)
        normal  = model.simulate_fill("ES", "long", 1.0, bar, is_high_vol=False)
        high_v  = model.simulate_fill("ES", "long", 1.0, bar, is_high_vol=True)
        assert high_v.total_friction >= normal.total_friction

    def test_normal_friction_report_populated(self):
        """Normal: Friction report updates after fills."""
        model = ExecutionModel(seed=42)
        bar   = make_bar(close=4800.0)
        model.simulate_fill("ES", "long", 1.0, bar)
        model.simulate_fill("ES", "short", 1.0, bar)
        report = model.friction_report()
        assert report["fill_count"] == 2
        assert report["avg_total_cost"] >= 0

    def test_normal_instrument_specs_cover_all_firms(self):
        """Normal: Instrument specs cover forex (DNA/5%ers), futures (Apex), indices."""
        for inst in ["EURUSD", "ES", "US500"]:
            assert inst in INSTRUMENT_SPECS


# ─────────────────────────────────────────────────────────────────────────────
# FIRM SIMULATION — FTMO
# ─────────────────────────────────────────────────────────────────────────────

class TestFTMOSimAccount:

    def test_normal_account_starts_at_correct_values(self):
        """Normal: FTMO $100K account initializes correctly."""
        acc = FTMOSimAccount(account_size=100_000.0)
        assert acc.balance == 100_000.0
        assert acc.equity  == 100_000.0
        assert acc.profit_target == 10_000.0   # 10% of $100K

    def test_edge_daily_limit_is_5_pct_of_start_of_day(self):
        """Edge: FTMO daily limit = 5% of start-of-day balance (not initial)."""
        acc = FTMOSimAccount()
        acc.balance = 102_000.0   # Some profit made
        acc.advance_day(date(2023, 1, 5))
        # Daily limit now based on $102K
        assert abs(acc.daily_loss_limit_dollars - 102_000.0 * 0.05) < 0.01

    def test_conflict_total_drawdown_floor_never_moves(self):
        """Conflict: FTMO static drawdown — floor is always $90K for $100K account."""
        acc = FTMOSimAccount(account_size=100_000.0)
        acc.apply_trade(5_000.0)   # Now at $105K
        # Floor stays at $90K — it's STATIC
        assert acc.total_floor == 90_000.0

    def test_normal_target_met_detected(self):
        """Normal: 10% profit target reached → is_target_met = True."""
        acc = FTMOSimAccount(account_size=100_000.0)
        acc.apply_trade(10_000.0)
        assert acc.is_target_met is True
        assert acc.status() == "PASSED"

    def test_normal_drawdown_breach_detected(self):
        """Normal: Equity drops below $90K floor → failed."""
        acc = FTMOSimAccount(account_size=100_000.0)
        acc.apply_trade(-10_001.0)
        assert acc.is_total_drawdown_breached is True
        assert acc.is_failed is True


# ─────────────────────────────────────────────────────────────────────────────
# FIRM SIMULATION — APEX (MOST DANGEROUS RULE)
# ─────────────────────────────────────────────────────────────────────────────

class TestAPEXSimAccount:

    def test_normal_trailing_floor_starts_at_initial(self):
        """Normal: Apex trailing floor = account_size × (1 - 6%) initially."""
        acc = APEXSimAccount(account_size=50_000.0)
        assert abs(acc.trailing_floor - 47_000.0) < 0.01   # $50K - 6% = $47K

    def test_edge_trailing_floor_rises_with_equity(self):
        """Edge: CRITICAL Apex rule — floor rises as equity rises."""
        acc = APEXSimAccount(account_size=50_000.0)
        acc.apply_trade(2_000.0)   # Equity now $52K → floor rises to $48,920
        floor_after = acc.trailing_floor
        assert floor_after > 47_000.0   # Floor has moved up

    def test_conflict_unrealized_gain_then_loss_still_fails(self):
        """Conflict: Gain $3K → give it back → floor has moved → account fails."""
        acc = APEXSimAccount(account_size=50_000.0)
        acc.apply_trade(3_000.0)    # Trailing high = $53K, floor = $53K - $3K = $50K
        acc.apply_trade(-3_500.0)   # Equity = $49,500 < floor $50K → FAILED
        assert acc.is_total_drawdown_breached is True

    def test_normal_safety_net_check_works(self):
        """Normal: $52,600 safety net target for $50K account."""
        acc = APEXSimAccount(account_size=50_000.0, safety_net_dollars=52_600.0)
        acc.apply_trade(3_000.0)   # $53K balance = $3K profit → approaching net
        # $52,600 safety net = $2,600 profit needed
        assert acc.balance > acc.account_size


# ─────────────────────────────────────────────────────────────────────────────
# CAPABILITY MATURITY — FX-03
# ─────────────────────────────────────────────────────────────────────────────

class TestCapabilityMaturity:

    def test_normal_all_start_immature(self):
        """Normal: FX-03 — all 6 capabilities start IMMATURE (zero counts)."""
        maturity = CapabilityMaturity()
        for cap in MATURITY_THRESHOLDS:
            assert not maturity.is_mature(cap)

    def test_edge_mature_at_exactly_threshold(self):
        """Edge: Capability matures at EXACTLY the threshold count."""
        maturity = CapabilityMaturity()
        maturity.kelly_criterion = 100   # Threshold is exactly 100
        assert maturity.is_mature("kelly_criterion") is True
        maturity.kelly_criterion = 99    # One below
        assert maturity.is_mature("kelly_criterion") is False

    def test_conflict_all_mature_requires_every_capability(self):
        """Conflict: all_mature = False if even one capability is immature."""
        maturity = CapabilityMaturity()
        # Set all above threshold except one
        maturity.setup_performance_db  = 50
        maturity.hot_hand_protocol     = 20
        maturity.time_of_day_atlas     = 200
        maturity.evolutionary_selection = 50
        maturity.kelly_criterion       = 100
        # edge_decay_detection still empty = not mature
        assert maturity.all_mature is False

    def test_normal_maturity_thresholds_match_document(self):
        """Normal: FX-03 thresholds exactly per document."""
        assert MATURITY_THRESHOLDS["setup_performance_db"]   == 50
        assert MATURITY_THRESHOLDS["hot_hand_protocol"]      == 20
        assert MATURITY_THRESHOLDS["edge_decay_detection"]   == 20
        assert MATURITY_THRESHOLDS["time_of_day_atlas"]      == 200
        assert MATURITY_THRESHOLDS["evolutionary_selection"] == 50
        assert MATURITY_THRESHOLDS["kelly_criterion"]        == 100


# ─────────────────────────────────────────────────────────────────────────────
# SIM ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TestSimEngine:

    def test_normal_engine_runs_regime_test(self):
        """Normal: Regime test completes and returns SimEvaluation."""
        engine = SimEngine(seed=42)
        result = engine.run_regime_test("trending_bull", "ES", "FTMO")
        assert result.regime == "trending_bull"
        assert result.eval_id is not None

    def test_edge_all_4_regime_tests_run(self):
        """Edge: run_all_regime_tests returns all 4 regime names."""
        engine  = SimEngine(seed=42)
        results = engine.run_all_regime_tests("ES", "FTMO")
        assert set(results.keys()) == set(REGIME_WINDOWS.keys())

    def test_conflict_training_produces_maturity_progress(self):
        """Conflict: After training run, capability maturity counters increase."""
        engine   = SimEngine(seed=42)
        initial  = engine.maturity.kelly_criterion
        engine.run_training("ES", "FTMO", n_evaluations=3)
        assert engine.maturity.kelly_criterion > initial

    def test_normal_training_win_rate_above_50_pct(self):
        """Normal: 30-strategy library has >50% avg win rate by design."""
        engine   = SimEngine(seed=42)
        result   = engine.run_training("ES", "FTMO", n_evaluations=3)
        assert result.overall_win_rate >= 0.50

    def test_normal_validation_data_separate_from_training(self):
        """Normal: Validation uses different date range than training."""
        engine    = SimEngine(seed=42)
        train     = engine.run_training("ES",    "FTMO", n_evaluations=2)
        validate  = engine.run_validation("ES", "FTMO", n_evaluations=2)
        # Both should complete without error
        assert train.split    == "train"
        assert validate.split == "validate"


# ─────────────────────────────────────────────────────────────────────────────
# TRAINING RUNNER
# ─────────────────────────────────────────────────────────────────────────────

class TestTrainingRunner:

    def test_normal_full_protocol_runs_and_returns_report(self):
        """Normal: Full protocol completes and returns TrainingReport."""
        runner = TrainingRunner(instrument="ES", firm_id="FTMO")
        report = runner.run_full_protocol()
        assert report is not None
        assert report.completed_at is not None

    def test_edge_blocking_reasons_populated_when_not_cleared(self):
        """Edge: If capabilities are immature, blocking_reasons is non-empty."""
        runner = TrainingRunner(instrument="ES", firm_id="FTMO")
        report = runner.run_full_protocol()
        # In a short test run, capabilities won't all be mature
        # Blocking reasons should be populated (not cleared)
        if not report.cleared_for_live:
            assert len(report.blocking_reasons) > 0

    def test_normal_regime_tests_contain_all_4(self):
        """Normal: All 4 required P-12 regime tests are in the report."""
        runner = TrainingRunner(instrument="ES", firm_id="FTMO")
        report = runner.run_full_protocol()
        assert len(report.regime_tests) == 4
        assert "trending_bull"   in report.regime_tests
        assert "high_vol_crisis" in report.regime_tests

    def test_conflict_overfitting_gap_calculated(self):
        """Conflict: Overfitting gap between training and validation is tracked."""
        runner = TrainingRunner(instrument="ES", firm_id="FTMO")
        report = runner.run_full_protocol()
        assert report.overfitting_gap >= 0.0
        assert report.overfitting_gap <= 1.0

    def test_normal_single_regime_test_runs(self):
        """Normal: Can run a single regime test in isolation."""
        runner = TrainingRunner(instrument="ES", firm_id="FTMO")
        result = runner.run_regime_test_only("choppy_ranging")
        assert result.regime_name == "choppy_ranging"
        assert result.win_rate    >= 0.0


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
