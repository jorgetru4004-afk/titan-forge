"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                 test_layer1_final6.py — FX-06 Compliance                    ║
║  Tests for FORGE-13, 14, 15, 16, 33, 44 — all final Layer 1 files           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta, timezone
from firm_rules import FirmID, MultiFirmRuleEngine

from news_protocol import (
    NewsProtocol, NewsEvent, NewsImpact, NewsAction,
)
from weekend_protocol import (
    WeekendProtocol, WeekendAction,
)
from streak_detector import (
    StreakDetector, StreakState, TradeResult,
    PAUSE_LOSSES, DAY_STOP_LOSSES,
)
from funded_rule_correlation import FundedRuleCorrelation
from payout_alert import PayoutAlertSystem, AlertType, AlertPriority
from ip_consistency import IPConsistencyManager, IP_SENSITIVE_FIRMS

ENGINE = MultiFirmRuleEngine(active_firm_id=FirmID.FTMO)
TODAY  = date(2026, 3, 19)
NOW    = datetime(2026, 3, 19, 14, 0, 0, tzinfo=timezone.utc)


def make_trade(is_win: bool, d: date = TODAY, pnl: float = 100.0) -> TradeResult:
    ts = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    return TradeResult(
        trade_id=f"T-{d}-{is_win}",
        is_win=is_win, pnl=pnl if is_win else -abs(pnl),
        timestamp=ts, session_date=d,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-13: NEWS PROTOCOL
# ─────────────────────────────────────────────────────────────────────────────

class TestNewsProtocol:

    def _make_protocol(self) -> NewsProtocol:
        return NewsProtocol(ENGINE)

    def test_normal_no_events_clear(self):
        """Normal: No news events loaded → CLEAR for all firms."""
        p = self._make_protocol()
        result = p.check(FirmID.FTMO, "FUNDED", as_of=NOW)
        assert result.action == NewsAction.CLEAR
        assert result.can_open is True

    def test_edge_ftmo_eval_no_blackout(self):
        """Edge: FTMO evaluation phase — no news blackout (funded only)."""
        p = self._make_protocol()
        event_in_5min = NewsEvent(
            name="CPI", scheduled_utc=NOW + timedelta(minutes=5),
            impact=NewsImpact.HIGH, currencies=()
        )
        p.load_events([event_in_5min])
        result = p.check(FirmID.FTMO, "EVALUATION", as_of=NOW)
        # FTMO eval: 0-minute blackout → no restriction
        assert result.action == NewsAction.CLEAR

    def test_normal_dna_10min_blackout_blocks(self):
        """Normal: DNA Funded — event in 8 min → HOLD_PERMITTED (can hold, not open/close)."""
        p = self._make_protocol()
        event = NewsEvent(
            name="FOMC", scheduled_utc=NOW + timedelta(minutes=8),
            impact=NewsImpact.HIGH, currencies=()
        )
        p.load_events([event])
        result = p.check(FirmID.DNA_FUNDED, "FUNDED", as_of=NOW)
        assert result.action == NewsAction.HOLD_PERMITTED
        assert result.can_open is False
        assert result.can_close is False

    def test_conflict_topstep_never_restricted(self):
        """Conflict: Topstep — even extreme event 1 min away → CLEAR."""
        p = self._make_protocol()
        event = NewsEvent(
            name="NFP", scheduled_utc=NOW + timedelta(minutes=1),
            impact=NewsImpact.EXTREME, currencies=()
        )
        p.load_events([event])
        result = p.check(FirmID.TOPSTEP, "EVALUATION", as_of=NOW)
        assert result.action == NewsAction.CLEAR
        assert result.can_open is True

    def test_normal_firm_blackout_minutes_correct(self):
        """Normal: DNA blackout = 10 min each side, FTMO funded = 2 min."""
        p = self._make_protocol()
        dna_before, dna_after = p.get_firm_blackout_minutes(FirmID.DNA_FUNDED, "FUNDED")
        ftmo_before, ftmo_after = p.get_firm_blackout_minutes(FirmID.FTMO, "FUNDED")
        assert dna_before == 10 and dna_after == 10
        assert ftmo_before == 2 and ftmo_after == 2


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-14: WEEKEND PROTOCOL
# ─────────────────────────────────────────────────────────────────────────────

class TestWeekendProtocol:

    def _make_protocol(self) -> WeekendProtocol:
        return WeekendProtocol(ENGINE)

    def test_normal_weekday_hold_permitted(self):
        """Normal: Wednesday → no restriction."""
        p = self._make_protocol()
        wednesday = datetime(2026, 3, 18, 10, 0, tzinfo=timezone.utc)
        result = p.check(FirmID.FTMO, as_of=wednesday)
        assert result.action == WeekendAction.HOLD_PERMITTED
        assert not result.must_close

    def test_edge_saturday_topstep_close_required(self):
        """Edge: Saturday + Topstep (weekend prohibited) → CLOSE_REQUIRED."""
        p = self._make_protocol()
        saturday = datetime(2026, 3, 21, 10, 0, tzinfo=timezone.utc)
        result = p.check(FirmID.TOPSTEP, as_of=saturday)
        assert result.must_close is True

    def test_conflict_saturday_ftmo_hold_permitted(self):
        """Conflict: Saturday + FTMO (no weekend rule) → HOLD_PERMITTED."""
        p = self._make_protocol()
        saturday = datetime(2026, 3, 21, 10, 0, tzinfo=timezone.utc)
        result = p.check(FirmID.FTMO, as_of=saturday)
        assert not result.must_close

    def test_normal_holiday_topstep_blocks(self):
        """Normal: Market holiday + Topstep → HOLIDAY_CLOSE."""
        p = self._make_protocol()
        xmas = datetime(2026, 12, 25, 10, 0, tzinfo=timezone.utc)
        result = p.check(FirmID.TOPSTEP, as_of=xmas)
        assert result.must_close is True
        assert result.is_holiday is True

    def test_normal_market_open_weekday(self):
        """Normal: Wednesday = market open."""
        p = self._make_protocol()
        assert p.is_market_open(datetime(2026, 3, 18, 14, 0, tzinfo=timezone.utc)) is True

    def test_edge_market_closed_saturday(self):
        """Edge: Saturday = market closed."""
        p = self._make_protocol()
        assert p.is_market_open(datetime(2026, 3, 21, 14, 0, tzinfo=timezone.utc)) is False


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-15: STREAK DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

class TestStreakDetector:

    def test_normal_3_losses_triggers_pause(self):
        """Normal: 3 consecutive losses → 2-hour mandatory pause."""
        sd = StreakDetector()
        # Record trades AT 2pm UTC so the 2-hour pause (expires 4pm) is still active
        for i in range(3):
            t = TradeResult(
                trade_id=f"T-loss-{i}", is_win=False, pnl=-100.0,
                timestamp=NOW + timedelta(minutes=i), session_date=TODAY,
            )
            sd.record_trade(t)
        # Check immediately after 3rd loss
        status = sd.get_status(NOW + timedelta(minutes=3))
        assert status.state == StreakState.PAUSED
        assert status.trading_permitted is False
        assert status.minutes_remaining is not None

    def test_edge_5_losses_triggers_day_stop(self):
        """Edge: 5 consecutive losses → stopped for the day."""
        sd = StreakDetector()
        for _ in range(5):
            sd.record_trade(make_trade(False))
        status = sd.get_status(NOW)
        assert status.state == StreakState.DAY_STOPPED
        assert status.trading_permitted is False

    def test_conflict_win_resets_loss_streak(self):
        """Conflict: 2 losses + 1 win → streak resets, no pause triggered."""
        sd = StreakDetector()
        sd.record_trade(make_trade(False))
        sd.record_trade(make_trade(False))
        sd.record_trade(make_trade(True))   # Win resets
        status = sd.get_status(NOW)
        assert status.state == StreakState.CLEAR
        assert status.consecutive_losses == 0

    def test_normal_pause_expires_after_2_hours(self):
        """Normal: Pause auto-expires after 2 hours."""
        sd = StreakDetector()
        t0 = NOW
        for i in range(3):
            sd.record_trade(make_trade(False, d=TODAY))
        # Check 2h 1min later
        future = t0 + timedelta(hours=2, minutes=1)
        status = sd.check_resume(as_of=future)
        assert status.state == StreakState.RESUMED
        assert status.trading_permitted is True

    def test_normal_new_session_resets_day_stop(self):
        """Normal: New trading day resets the day-stop."""
        sd = StreakDetector()
        for _ in range(5):
            sd.record_trade(make_trade(False, d=TODAY))
        assert not sd.is_trading_permitted
        sd.advance_session(TODAY + timedelta(days=1))
        assert sd.is_trading_permitted is True

    def test_edge_4_losses_not_day_stop_yet(self):
        """Edge: 4 consecutive losses — paused but NOT day-stopped (threshold is 5)."""
        sd = StreakDetector()
        for _ in range(4):
            sd.record_trade(make_trade(False))
        status = sd.get_status(NOW)
        assert status.state != StreakState.DAY_STOPPED
        # Should be paused
        assert status.consecutive_losses == 4


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-16: FUNDED RULE CORRELATION
# ─────────────────────────────────────────────────────────────────────────────

class TestFundedRuleCorrelation:

    def test_normal_high_water_mark_tracked(self):
        """Normal: Balance rises → HWM updates."""
        frc = FundedRuleCorrelation(ENGINE)
        frc.initialize_account("ACC-001", FirmID.FTMO, 100_000.0, is_funded=True)
        frc.update_equity("ACC-001", 102_000.0, 102_000.0)
        hwm = frc.get_hwm("ACC-001")
        assert hwm.hwm_balance == 102_000.0
        assert hwm.is_at_hwm is True

    def test_edge_hwm_never_decreases(self):
        """Edge: Balance drops after reaching HWM — HWM stays at peak."""
        frc = FundedRuleCorrelation(ENGINE)
        frc.initialize_account("ACC-001", FirmID.FTMO, 100_000.0, is_funded=True)
        frc.update_equity("ACC-001", 105_000.0, 105_000.0)
        frc.update_equity("ACC-001", 102_000.0, 102_000.0)
        hwm = frc.get_hwm("ACC-001")
        assert hwm.hwm_balance == 105_000.0   # Never decreases
        assert hwm.is_at_hwm is False
        assert abs(hwm.drawdown_from_hwm - 3_000.0) < 0.01

    def test_conflict_funded_rules_activate_immediately_on_confirm(self):
        """Conflict: confirm_funded() switches rules IMMEDIATELY — not on next trade."""
        frc = FundedRuleCorrelation(ENGINE)
        rs = frc.initialize_account("ACC-001", FirmID.DNA_FUNDED, 100_000.0, is_funded=False)
        assert rs.no_scalping is False   # Eval mode: no restriction

        rs_funded = frc.confirm_funded("ACC-001")
        assert rs_funded.is_funded is True
        assert rs_funded.no_scalping is True   # DNA: scalping banned in funded
        assert rs_funded.payout_optimization_active is True

    def test_normal_drawdown_from_hwm_calculated(self):
        """Normal: After drawdown, pct from HWM is correct."""
        frc = FundedRuleCorrelation(ENGINE)
        frc.initialize_account("ACC-001", FirmID.FTMO, 100_000.0)
        frc.update_equity("ACC-001", 110_000.0, 110_000.0)  # HWM = 110K
        frc.update_equity("ACC-001", 104_500.0, 104_500.0)  # 5% drawdown from HWM
        hwm = frc.get_hwm("ACC-001")
        assert abs(hwm.drawdown_pct - 5_500.0/110_000.0) < 0.0001


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-33: PAYOUT ALERT SYSTEM
# ─────────────────────────────────────────────────────────────────────────────

class TestPayoutAlertSystem:

    def test_normal_eval_passed_raises_critical_alert(self):
        """Normal: Evaluation pass → CRITICAL alert."""
        ps = PayoutAlertSystem()
        alert = ps.alert_eval_passed("FTMO-001", FirmID.FTMO, 10_500.0)
        assert alert.alert_type == AlertType.EVAL_PASSED
        assert alert.priority == AlertPriority.CRITICAL

    def test_edge_received_capital_only_counts_bank_funds(self):
        """Edge: FX-08 — only received capital is available. Receivables are NOT."""
        ps = PayoutAlertSystem()
        ps.alert_payout_due("FTMO-001", FirmID.FTMO, 5_000.0)
        # After submitting payout: receivable but not available
        assert ps.available_capital == 0.0
        assert ps.total_receivables == 5_000.0

        # After receiving in bank:
        ps.record_payout_received("FTMO-001-PAY-20260319", 5_000.0)
        assert ps.available_capital == 5_000.0

    def test_conflict_payout_submitted_creates_receivable_not_available(self):
        """Conflict: Submitted payout = receivable. FX-08: NEVER fund deployment from this."""
        ps = PayoutAlertSystem()
        ps.alert_payout_due("ACC-001", FirmID.APEX, 8_000.0)
        assert ps.available_capital == 0.0
        assert ps.total_receivables == 8_000.0

    def test_normal_pipeline_summary_accurate(self):
        """Normal: Pipeline summary shows correct totals."""
        ps = PayoutAlertSystem()
        ps.alert_eval_passed("A1", FirmID.FTMO, 10_000.0)
        ps.alert_payout_due("A1", FirmID.FTMO, 7_000.0)
        summary = ps.pipeline_summary()
        assert summary["total_receivables"] == 7_000.0
        assert summary["available_capital"] == 0.0
        assert summary["unacknowledged_alerts"] == 2

    def test_normal_alert_acknowledged(self):
        """Normal: Alert can be acknowledged to clear it from pending list."""
        ps = PayoutAlertSystem()
        alert = ps.alert_eval_passed("A1", FirmID.FTMO, 10_000.0)
        assert len(ps.pending_alerts()) == 1
        ps.acknowledge_alert(alert.alert_id)
        assert len(ps.pending_alerts()) == 0


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-44: IP CONSISTENCY MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class TestIPConsistencyManager:

    def test_normal_matching_ip_is_consistent(self):
        """Normal: Connection from registered Railway IP → consistent."""
        mgr = IPConsistencyManager(railway_ip="10.0.0.1")
        result = mgr.check_connection("10.0.0.1", FirmID.FTMO)
        assert result.is_consistent is True

    def test_edge_mismatched_ip_flagged(self):
        """Edge: Different IP detected → NOT consistent."""
        mgr = IPConsistencyManager(railway_ip="10.0.0.1")
        result = mgr.check_connection("10.0.0.2", FirmID.FTMO)
        assert result.is_consistent is False
        assert mgr.violation_count == 1

    def test_conflict_ftmo_is_ip_sensitive(self):
        """Conflict: IP mismatch at FTMO (IP-sensitive firm) → flagged as critical."""
        mgr = IPConsistencyManager(railway_ip="10.0.0.1")
        result = mgr.check_connection("192.168.1.1", FirmID.FTMO)
        assert result.is_consistent is False
        assert result.is_ip_sensitive is True

    def test_normal_topstep_not_ip_sensitive(self):
        """Normal: Topstep not IP-sensitive — mismatch still logged but not critical."""
        mgr = IPConsistencyManager(railway_ip="10.0.0.1")
        result = mgr.check_connection("10.0.0.2", FirmID.TOPSTEP)
        assert result.is_consistent is False
        assert result.is_ip_sensitive is False   # Topstep not in sensitive list

    def test_edge_no_registered_ip_auto_registers_first(self):
        """Edge: No registered IP → first connection auto-registers."""
        mgr = IPConsistencyManager()
        result = mgr.check_connection("10.0.0.5", FirmID.FTMO)
        assert result.is_consistent is True
        assert mgr.registered_ip == "10.0.0.5"

    def test_normal_compliant_check_quick(self):
        """Normal: is_compliant() quick check works."""
        mgr = IPConsistencyManager(railway_ip="10.0.0.1")
        assert mgr.is_compliant("10.0.0.1") is True
        assert mgr.is_compliant("10.0.0.2") is False

    def test_normal_status_summary(self):
        """Normal: Status summary has all required keys."""
        mgr = IPConsistencyManager(railway_ip="10.0.0.1")
        mgr.check_connection("10.0.0.1", FirmID.FTMO)
        summary = mgr.status_summary()
        assert "registered_ip" in summary
        assert "violation_count" in summary
        assert "is_configured" in summary
        assert summary["is_configured"] is True


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
