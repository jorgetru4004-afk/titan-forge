"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║         test_evaluation_state.py — FORGE-02 — FX-06 Compliance             ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta, timezone
from evaluation_state import (
    EvaluationStateMachine, EvaluationOrchestrator, EvalState, EvalPhase,
    SuspendReason, TradeRecord, SessionSnapshot,
)
from firm_rules import FirmID, MultiFirmRuleEngine


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

START = date(2026, 3, 1)
ENGINE = MultiFirmRuleEngine(active_firm_id=FirmID.FTMO)


def make_esm(
    firm=FirmID.FTMO,
    size=100_000.0,
    phase=EvalPhase.PHASE_1,
    start=START,
) -> EvaluationStateMachine:
    esm = EvaluationStateMachine(
        firm_id=firm, account_size=size, phase=phase,
        start_date=start, rule_engine=ENGINE,
    )
    esm.start()
    return esm


def make_trade(
    pnl: float,
    session_date: date = START,
    strategy: str = "GEX-01",
    unrealized_peak: float = 0.0,
    hold_seconds: float = 120.0,
) -> TradeRecord:
    now = datetime.combine(session_date, datetime.min.time(), tzinfo=timezone.utc)
    return TradeRecord(
        trade_id=f"T-{abs(int(pnl))}",
        strategy_name=strategy,
        entry_time=now,
        exit_time=now + timedelta(seconds=hold_seconds),
        pnl=pnl,
        unrealized_peak=unrealized_peak,
        position_size=1.0,
        setup_type=strategy,
        is_win=pnl > 0,
        session_date=session_date,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STATE TRANSITIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestStateTransitions:

    def test_normal_idle_to_active_on_start(self):
        """Normal: New ESM starts in IDLE, transitions to ACTIVE on start()."""
        esm = EvaluationStateMachine(
            firm_id=FirmID.FTMO, account_size=100_000,
            phase=EvalPhase.PHASE_1, start_date=START, rule_engine=ENGINE
        )
        assert esm.state == EvalState.IDLE
        esm.start()
        assert esm.state == EvalState.ACTIVE

    def test_edge_double_start_raises(self):
        """Edge: Calling start() twice raises RuntimeError — no double starts."""
        esm = make_esm()
        raised = False
        try:
            esm.start()
        except RuntimeError:
            raised = True
        assert raised, "Double start must raise RuntimeError"

    def test_conflict_terminal_states_irreversible(self):
        """Conflict: Once PASSED or FAILED, state cannot change."""
        esm = make_esm()
        # Force pass by injecting a large enough profit
        # FTMO Phase 1 = 10% = $10,000
        for i in range(5):
            esm.record_trade(make_trade(2_100.0, session_date=START + timedelta(days=i)))
        assert esm.state == EvalState.PASSED
        # Attempting to record more trades has no effect
        before_trades = len(esm.trades)
        esm.record_trade(make_trade(500.0, session_date=START + timedelta(days=10)))
        assert len(esm.trades) == before_trades  # No change — terminal
        assert esm.state == EvalState.PASSED

    def test_normal_active_to_suspended(self):
        """Normal: Active evaluation can be suspended."""
        esm = make_esm()
        esm.suspend(SuspendReason.STREAK_DETECTOR)
        assert esm.state == EvalState.SUSPENDED
        assert esm.snapshot().suspend_reason == SuspendReason.STREAK_DETECTOR

    def test_normal_suspended_to_active_on_resume(self):
        """Normal: Suspended evaluation resumes to ACTIVE."""
        esm = make_esm()
        esm.suspend(SuspendReason.MANUAL_REVIEW)
        esm.resume()
        assert esm.state == EvalState.ACTIVE

    def test_edge_resume_before_suspend_time_blocked(self):
        """Edge: Cannot resume before suspend_until time."""
        esm = make_esm()
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        esm.suspend(SuspendReason.RECOVERY_PROTOCOL, resume_at=future)
        esm.resume()  # Should be blocked
        assert esm.state == EvalState.SUSPENDED  # Still suspended


# ─────────────────────────────────────────────────────────────────────────────
# PASS CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestPassConditions:

    def test_normal_pass_when_profit_target_hit(self):
        """Normal: FTMO $100K Phase 1 — $10,000 profit = PASSED."""
        esm = make_esm(firm=FirmID.FTMO, size=100_000)
        # $10K profit over multiple trades — no min trading day requirement for FTMO
        for i in range(5):
            esm.record_trade(make_trade(2_100.0, session_date=START + timedelta(days=i)))
        assert esm.state == EvalState.PASSED

    def test_edge_one_dollar_short_no_pass(self):
        """Edge: $9,999 profit on $10,000 target — NOT passed."""
        esm = make_esm(firm=FirmID.FTMO, size=100_000)
        esm.record_trade(make_trade(9_999.0, session_date=START))
        assert esm.state == EvalState.ACTIVE
        snap = esm.snapshot()
        assert snap.profit_gate_met is False
        assert snap.profit_remaining == pytest_approx(1.0, abs=0.01)

    def test_conflict_profit_alone_not_enough_if_drawdown_breached(self):
        """
        Conflict: If floor breach occurs, the evaluation FAILS — even if profit target
        would have been hit in the same run. Once FAILED, terminal state is locked.
        Drawdown protection is absolute — no amount of profit overrides a failed floor.
        """
        esm = make_esm(firm=FirmID.FTMO, size=100_000)
        # Record partial profit — not enough to pass
        esm.record_trade(make_trade(5_000.0, session_date=START))
        assert esm.state == EvalState.ACTIVE

        # Floor breach — FAILED
        alert = esm.update_equity(89_000.0)  # Below $90K FTMO floor
        assert esm.state == EvalState.FAILED
        assert "FAILED" in (alert or "")

        # Now try to record a large winning trade — must NOT revive the evaluation
        before_trades = len(esm.trades)
        esm.record_trade(make_trade(50_000.0, session_date=START + timedelta(days=1)))
        assert esm.state == EvalState.FAILED   # Still FAILED — terminal is irreversible
        assert len(esm.trades) == before_trades  # No trade added to terminal eval


# ─────────────────────────────────────────────────────────────────────────────
# FAILURE CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestFailureConditions:

    def test_normal_floor_breach_fails_evaluation(self):
        """Normal: Equity drops below FTMO floor ($90K) — FAILED immediately."""
        esm = make_esm(firm=FirmID.FTMO, size=100_000)
        alert = esm.update_equity(89_999.99)
        assert esm.state == EvalState.FAILED
        assert "FAILED" in (alert or "")

    def test_edge_exactly_at_floor_fails(self):
        """Edge: Equity exactly at $90,000 floor — fails (floor is the limit)."""
        esm = make_esm(firm=FirmID.FTMO, size=100_000)
        alert = esm.update_equity(90_000.0)
        assert esm.state == EvalState.FAILED

    def test_conflict_apex_daily_limit_is_circuit_breaker_not_failure(self):
        """Conflict: Apex daily limit hit = circuit breaker (suspended), NOT account failed."""
        esm = make_esm(firm=FirmID.APEX, size=100_000, phase=EvalPhase.SINGLE)
        # Apex daily limit: $1,500 on $100K
        # Drop equity by $1,500 exactly
        alert = esm.update_equity(98_500.0)   # $1,500 below session open
        # Apex: SUSPENDED (circuit breaker), NOT FAILED
        assert esm.state == EvalState.SUSPENDED
        assert "CIRCUIT BREAKER" in (alert or "").upper() or esm.snapshot().suspend_reason == SuspendReason.CIRCUIT_BREAKER
        assert esm.state != EvalState.FAILED

    def test_normal_realized_loss_below_floor_fails(self):
        """Normal: Realized P&L loss drives balance below floor — FAILED."""
        esm = make_esm(firm=FirmID.FTMO, size=100_000)
        # FTMO floor = $90K. Record $10,001 loss.
        esm.record_trade(make_trade(-10_001.0, session_date=START))
        assert esm.state == EvalState.FAILED

    def test_normal_daily_limit_other_firms_fails_account(self):
        """Normal: DNA Funded daily limit (4%) breached = FAILED (not circuit breaker)."""
        esm = make_esm(firm=FirmID.DNA_FUNDED, size=100_000)
        # DNA daily limit: 4% = $4,000
        alert = esm.update_equity(95_999.0)   # $4,001 drop = over 4% limit
        assert esm.state == EvalState.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# APEX TRAILING DRAWDOWN
# ─────────────────────────────────────────────────────────────────────────────

class TestApexTrailingDrawdown:

    def test_normal_apex_floor_rises_with_peak_unrealized(self):
        """Normal: Apex floor rises when unrealized peak hits $3K — floor goes up by $3K."""
        esm = make_esm(firm=FirmID.APEX, size=100_000, phase=EvalPhase.SINGLE)
        initial_floor = esm._get_current_floor()

        # Update equity with $3K unrealized peak
        esm.update_equity(103_000.0, peak_unrealized=3_000.0)
        new_floor = esm._get_current_floor()

        assert new_floor > initial_floor
        assert abs(new_floor - (initial_floor + 3_000.0)) < 0.01

    def test_edge_apex_floor_monotonically_increases(self):
        """Edge: Apex trailing floor can only go UP, never down."""
        esm = make_esm(firm=FirmID.APEX, size=100_000, phase=EvalPhase.SINGLE)
        esm.update_equity(105_000.0, peak_unrealized=5_000.0)
        floor_after_peak = esm._get_current_floor()

        # Equity drops back — floor should NOT drop
        esm.update_equity(101_000.0, peak_unrealized=0.0)
        floor_after_drop = esm._get_current_floor()

        assert floor_after_drop >= floor_after_peak  # Monotonically non-decreasing

    def test_conflict_apex_floor_higher_than_ftmo_after_peak(self):
        """Conflict: Same equity, but Apex floor > FTMO floor after unrealized peak."""
        ftmo = make_esm(firm=FirmID.FTMO, size=100_000)
        apex = make_esm(firm=FirmID.APEX, size=100_000, phase=EvalPhase.SINGLE)

        # Both reach $6K unrealized peak
        ftmo.update_equity(106_000.0, peak_unrealized=6_000.0)
        apex.update_equity(106_000.0, peak_unrealized=6_000.0)

        # Equity drops to $97K for both
        ftmo.update_equity(97_000.0)
        apex.update_equity(97_000.0)

        # FTMO floor = $90K (static). Apex floor = $94K + $6K = $100K
        # So same equity that's safe for FTMO might fail Apex
        assert apex._get_current_floor() > ftmo._get_current_floor()


# ─────────────────────────────────────────────────────────────────────────────
# SNAPSHOT ACCURACY
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotAccuracy:

    def test_normal_snapshot_reflects_all_metrics(self):
        """Normal: Snapshot has non-None values for all critical fields."""
        esm = make_esm()
        snap = esm.snapshot()
        assert snap.eval_id is not None
        assert snap.profit_target_dollars == 10_000.0   # 10% of $100K
        assert snap.firm_floor == 90_000.0              # 10% drawdown
        assert snap.current_profit == 0.0               # No trades yet
        assert snap.pass_probability > 0.0

    def test_edge_profit_pct_caps_at_100(self):
        """Edge: Even with 200% profit, profit_pct_complete caps at 1.0."""
        esm = make_esm()
        esm.record_trade(make_trade(50_000.0, session_date=START))
        snap = esm.snapshot()
        assert snap.profit_pct_complete <= 1.0

    def test_conflict_snapshot_shows_suspended_state(self):
        """Conflict: Suspended ESM snapshot reflects suspension correctly."""
        esm = make_esm()
        esm.suspend(SuspendReason.EMERGENCY)
        snap = esm.snapshot()
        assert snap.is_suspended is True
        assert snap.suspend_reason == SuspendReason.EMERGENCY
        assert snap.state == EvalState.SUSPENDED

    def test_normal_drawdown_pct_increases_with_losses(self):
        """Normal: After losses, drawdown_pct_used increases accurately."""
        esm = make_esm()
        snap_before = esm.snapshot()
        esm.update_equity(95_000.0)  # $5K used of $10K budget = 50%
        snap_after = esm.snapshot()
        assert snap_after.drawdown_pct_used > snap_before.drawdown_pct_used
        assert snap_after.at_yellow is True

    def test_normal_trading_days_count_increments(self):
        """Normal: Each day with trades increments trading_days_completed."""
        esm = make_esm()
        assert esm.snapshot().trading_days_completed == 0
        esm.record_trade(make_trade(100.0, session_date=START))
        assert esm.snapshot().trading_days_completed == 1
        esm.record_trade(make_trade(100.0, session_date=START + timedelta(days=1)))
        assert esm.snapshot().trading_days_completed == 2

    def test_edge_same_day_trades_count_as_one_day(self):
        """Edge: Multiple trades on same day = 1 trading day, not 3."""
        esm = make_esm()
        for _ in range(3):
            esm.record_trade(make_trade(50.0, session_date=START))
        assert esm.snapshot().trading_days_completed == 1


# ─────────────────────────────────────────────────────────────────────────────
# STREAK TRACKING
# ─────────────────────────────────────────────────────────────────────────────

class TestStreakTracking:

    def test_normal_consecutive_losses_tracked(self):
        """Normal: Three losses in a row — consecutive_losses = 3."""
        esm = make_esm()
        for i in range(3):
            esm.record_trade(make_trade(-100.0, session_date=START + timedelta(days=i)))
        assert esm.snapshot().consecutive_losses == 3

    def test_edge_win_resets_loss_streak(self):
        """Edge: One win after two losses resets consecutive_losses to 0."""
        esm = make_esm()
        esm.record_trade(make_trade(-100.0, session_date=START))
        esm.record_trade(make_trade(-100.0, session_date=START + timedelta(days=1)))
        esm.record_trade(make_trade(+200.0, session_date=START + timedelta(days=2)))
        snap = esm.snapshot()
        assert snap.consecutive_losses == 0
        assert snap.consecutive_wins == 1

    def test_conflict_win_and_loss_streaks_never_simultaneously_nonzero(self):
        """Conflict: consecutive_wins and consecutive_losses cannot both be > 0."""
        esm = make_esm()
        esm.record_trade(make_trade(100.0, session_date=START))
        esm.record_trade(make_trade(100.0, session_date=START + timedelta(days=1)))
        snap = esm.snapshot()
        # After 2 wins: losses must be 0
        assert snap.consecutive_wins == 2
        assert snap.consecutive_losses == 0


# ─────────────────────────────────────────────────────────────────────────────
# ALERT THRESHOLDS (FORGE-67 integration)
# ─────────────────────────────────────────────────────────────────────────────

class TestAlertThresholds:

    def test_normal_yellow_at_50_pct_drawdown(self):
        """Normal: FTMO $100K — $5K lost of $10K budget = 50% = Yellow."""
        esm = make_esm()
        esm.update_equity(95_000.0)
        snap = esm.snapshot()
        assert snap.at_yellow is True
        assert snap.at_orange is False
        assert snap.at_red is False

    def test_edge_orange_at_70_pct_drawdown(self):
        """Edge: $7K lost of $10K budget = 70% = Orange."""
        esm = make_esm()
        esm.update_equity(93_000.0)
        snap = esm.snapshot()
        assert snap.at_orange is True
        assert snap.at_red is False

    def test_conflict_red_at_85_pct_overrides_lower_alerts(self):
        """Conflict: 85%+ is RED — yellow and orange are also true (cumulative)."""
        esm = make_esm()
        esm.update_equity(91_500.0)  # $8,500 of $10K = 85%
        snap = esm.snapshot()
        assert snap.at_red is True
        assert snap.at_orange is True   # 85% is also > 70%
        assert snap.at_yellow is True   # 85% is also > 50%


# ─────────────────────────────────────────────────────────────────────────────
# TIME-BASED EXPIRY (APEX 30-DAY LIMIT)
# ─────────────────────────────────────────────────────────────────────────────

class TestTimeExpiry:

    def test_normal_apex_expires_after_30_days(self):
        """Normal: Apex evaluation expires if 30+ calendar days pass without passing."""
        esm = make_esm(firm=FirmID.APEX, size=100_000, phase=EvalPhase.SINGLE)
        # Advance 31 days
        esm.advance_session(START + timedelta(days=31), closing_equity=100_500.0)
        assert esm.state == EvalState.EXPIRED

    def test_edge_apex_day_30_not_yet_expired(self):
        """Edge: Day 30 exactly — not yet expired (expired after day 30)."""
        esm = make_esm(firm=FirmID.APEX, size=100_000, phase=EvalPhase.SINGLE)
        esm.advance_session(START + timedelta(days=30), closing_equity=100_500.0)
        assert esm.state != EvalState.EXPIRED

    def test_conflict_ftmo_never_expires(self):
        """Conflict: FTMO has no time limit — never expires regardless of days."""
        esm = make_esm(firm=FirmID.FTMO, size=100_000)
        # Advance 200 days
        for i in range(1, 201, 10):
            esm.advance_session(START + timedelta(days=i), closing_equity=100_100.0)
        assert esm.state != EvalState.EXPIRED
        assert esm.state == EvalState.ACTIVE


# ─────────────────────────────────────────────────────────────────────────────
# PASS PROBABILITY (FORGE-32)
# ─────────────────────────────────────────────────────────────────────────────

class TestPassProbability:

    def test_normal_probability_increases_with_profit(self):
        """Normal: More profit = higher pass probability."""
        esm = make_esm()
        p0 = esm.snapshot().pass_probability
        esm.record_trade(make_trade(3_000.0, session_date=START))
        p1 = esm.snapshot().pass_probability
        assert p1 > p0

    def test_edge_passed_eval_probability_is_1(self):
        """Edge: PASSED evaluation = pass_probability is 1.0."""
        esm = make_esm()
        for i in range(5):
            esm.record_trade(make_trade(2_100.0, session_date=START + timedelta(days=i)))
        assert esm.state == EvalState.PASSED
        assert esm.snapshot().pass_probability == 1.0

    def test_conflict_failed_eval_probability_is_0(self):
        """Conflict: FAILED evaluation = pass_probability is 0.0."""
        esm = make_esm()
        esm.update_equity(89_000.0)  # Below floor
        assert esm.state == EvalState.FAILED
        assert esm.snapshot().pass_probability == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR — FORGE-28
# ─────────────────────────────────────────────────────────────────────────────

class TestOrchestrator:

    def setup_method(self):
        self.orch = EvaluationOrchestrator(ENGINE)

    def test_normal_multiple_simultaneous_evaluations(self):
        """Normal: Two firms running simultaneously — tracked independently."""
        id1 = self.orch.start_evaluation(FirmID.FTMO, 100_000, EvalPhase.PHASE_1, START)
        id2 = self.orch.start_evaluation(FirmID.APEX, 50_000, EvalPhase.SINGLE, START)
        assert self.orch.active_count == 2
        snap1 = self.orch.snapshot(id1)
        snap2 = self.orch.snapshot(id2)
        assert snap1.firm_id == FirmID.FTMO
        assert snap2.firm_id == FirmID.APEX
        assert snap1.eval_id != snap2.eval_id

    def test_edge_rules_never_bleed_across_evaluations(self):
        """Edge: FTMO rules don't contaminate Apex evaluation and vice versa."""
        id_ftmo = self.orch.start_evaluation(FirmID.FTMO, 100_000, EvalPhase.PHASE_1, START)
        id_apex = self.orch.start_evaluation(FirmID.APEX, 100_000, EvalPhase.SINGLE, START)

        ftmo_snap = self.orch.snapshot(id_ftmo)
        apex_snap = self.orch.snapshot(id_apex)

        # FTMO: 10% target = $10K. Apex: 6% target = $6K. Different targets.
        assert ftmo_snap.profit_target_dollars == 10_000.0
        assert apex_snap.profit_target_dollars == 6_000.0

        # FTMO: 10% drawdown. Apex: 6% drawdown.
        assert ftmo_snap.total_drawdown_limit == 10_000.0
        assert apex_snap.total_drawdown_limit == 6_000.0

    def test_conflict_one_failure_does_not_affect_other(self):
        """Conflict: Failing one evaluation does not affect the other's state."""
        id1 = self.orch.start_evaluation(FirmID.FTMO, 100_000, EvalPhase.PHASE_1, START)
        id2 = self.orch.start_evaluation(FirmID.FTMO, 100_000, EvalPhase.PHASE_1, START)

        # Fail eval 1 — breach its floor
        self.orch.update_equity(id1, 89_000.0)

        # Eval 2 should be unaffected
        snap2 = self.orch.snapshot(id2)
        esm1  = self.orch.get(id1)

        assert esm1.state == EvalState.FAILED
        assert snap2.state == EvalState.ACTIVE   # Unaffected

    def test_normal_all_snapshots_returns_active_only(self):
        """Normal: all_snapshots() only returns non-terminal evaluations."""
        id1 = self.orch.start_evaluation(FirmID.FTMO, 100_000, EvalPhase.PHASE_1, START)
        id2 = self.orch.start_evaluation(FirmID.FTMO, 100_000, EvalPhase.PHASE_1, START)
        # Fail eval 1
        self.orch.update_equity(id1, 89_000.0)
        # Only id2 should be in all_snapshots
        active_snaps = self.orch.all_snapshots()
        active_ids = [s.eval_id for s in active_snaps]
        assert id2 in active_ids
        assert id1 not in active_ids

    def test_normal_counters_accurate(self):
        """Normal: active_count, passed_count, failed_count track correctly."""
        assert self.orch.active_count == 0
        assert self.orch.passed_count == 0
        assert self.orch.failed_count == 0

        id1 = self.orch.start_evaluation(FirmID.FTMO, 100_000, EvalPhase.PHASE_1, START)
        assert self.orch.active_count == 1

        # Pass it
        for i in range(5):
            self.orch.record_trade(id1, make_trade(2_100.0, session_date=START + timedelta(days=i)))
        assert self.orch.passed_count == 1
        assert self.orch.active_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

# Tiny approximation helper (no pytest)
class pytest_approx:
    def __init__(self, val, abs=1e-6):
        self.val = val
        self.abs = abs
    def __eq__(self, other):
        return abs(self.val - other) <= self.abs
    def __repr__(self):
        return f"≈{self.val}"


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
