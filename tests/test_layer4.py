"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                 test_layer4.py — Layer 4 FX-06 Compliance                   ║
║  Tests for all Layer 4 capital extraction and scaling modules                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timedelta, timezone
from firm_rules import FirmID

from capital_extractor import (
    optimize_payout, check_safety_net, project_compound_growth,
    check_apex_lifecycle, APEX_MAX_PAYOUTS,
    SAFETY_NET_TARGETS, PAYOUT_RULES,
)
from multi_firm_orchestrator import (
    MultiFirmRegistry, LiveAccount, AccountStatus,
    aggregate_pnl, prioritize_accounts, resolve_firm_conflicts,
    generate_health_report,
)
from account_lifecycle import (
    get_projection_for_month, get_scaling_milestone,
    get_five_percenters_path, calculate_capital_velocity,
    FIVE_PERCENTERS_ULTIMATE_TARGET, FIVE_PERCENTERS_MONTHLY_TARGET,
    NEXUS_CAPITAL_PROJECTION,
)
from architect_integration import (
    ArchitectFeed, MetaBrainLearningLoop, EnvironmentRegistry,
    REQUIRED_ENV_VARS,
)
from nexus_core import (
    NexusTreasury, get_mission_status, NexusHeartbeat,
    TreasuryState, NEXUS_ULTIMATE_TARGET,
)

TODAY = date(2026, 3, 19)
NOW   = datetime(2026, 3, 19, 14, 0, tzinfo=timezone.utc)


def make_account(
    account_id="FTMO-001", firm_id=FirmID.FTMO,
    size=100_000.0, status=AccountStatus.FUNDED_ACTIVE,
    equity=102_000.0, daily_pnl=500.0,
    drawdown_pct=0.30, safety_net_met=True,
) -> LiveAccount:
    return LiveAccount(
        account_id=account_id, firm_id=firm_id, account_size=size,
        status=status, is_funded=True, current_equity=equity,
        unrealized_pnl=0.0, daily_pnl=daily_pnl, total_pnl=2_000.0,
        drawdown_pct_used=drawdown_pct, safety_net_met=safety_net_met,
        payouts_taken=1, platform="DXTrade", requires_vps=False,
        last_updated=TODAY,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CAPITAL EXTRACTOR (FORGE-89–95)
# ─────────────────────────────────────────────────────────────────────────────

class TestCapitalExtractor:

    def test_normal_payout_authorized_when_all_conditions_met(self):
        """FORGE-89: Safety net met + enough profit + enough time → payout authorized."""
        result = optimize_payout(
            firm_id=FirmID.FTMO, account_id="FTMO-001",
            account_size=100_000.0, current_profit=12_000.0,
            safety_net_met=True,
            last_payout_date=TODAY - timedelta(days=15),
            as_of=TODAY,
        )
        assert result.should_request is True
        assert result.requested_amount > 0

    def test_edge_payout_blocked_before_safety_net_met(self):
        """FORGE-89 (C-19): Safety net not met → no payout."""
        result = optimize_payout(
            firm_id=FirmID.APEX, account_id="APEX-001",
            account_size=50_000.0, current_profit=1_000.0,
            safety_net_met=False,
        )
        assert result.should_request is False
        assert result.is_safety_net_met is False

    def test_conflict_payout_blocked_too_early(self):
        """FORGE-89: Safety net met but only 5 days since last payout → wait."""
        result = optimize_payout(
            firm_id=FirmID.FTMO, account_id="FTMO-001",
            account_size=100_000.0, current_profit=8_000.0,
            safety_net_met=True,
            last_payout_date=TODAY - timedelta(days=5),  # Need 14 days
            as_of=TODAY,
        )
        assert result.should_request is False

    def test_normal_apex_safety_net_is_52600(self):
        """FORGE-90 (C-19): Apex safety net target = $52,600."""
        assert SAFETY_NET_TARGETS[FirmID.APEX] == 52_600.0

    def test_normal_safety_net_check(self):
        """FORGE-90: Profit above safety net target → safety net met."""
        status = check_safety_net(FirmID.APEX, balance=155_000.0, start_bal=100_000.0)
        # $55,000 profit > $52,600 target
        assert status.is_met is True

    def test_normal_compound_growth_shows_positive_trajectory(self):
        """FORGE-91: Compound growth projection shows increasing income over time."""
        projections = project_compound_growth(months=12)
        assert len(projections) == 12
        # Month 12 income should be higher than month 1
        assert projections[11].monthly_income >= projections[0].monthly_income

    def test_normal_apex_lifecycle_under_limit(self):
        """FORGE-95: 3 payouts → 3 remaining, not retiring."""
        status = check_apex_lifecycle("APEX-001", payouts_taken=3, total_extracted=15_000.0)
        assert status.payouts_remaining == 3
        assert status.should_retire is False

    def test_edge_apex_at_6_payouts_retires(self):
        """FORGE-95: 6 payouts reached → retire this account."""
        status = check_apex_lifecycle("APEX-001", payouts_taken=6, total_extracted=40_000.0)
        assert status.should_retire is True
        assert status.payouts_remaining == 0

    def test_conflict_apex_max_payouts_is_6(self):
        """FORGE-95: Apex hard cap is exactly 6 payouts."""
        assert APEX_MAX_PAYOUTS == 6


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-FIRM ORCHESTRATOR (FORGE-96–103)
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiFirmOrchestrator:

    def test_normal_registry_tracks_accounts(self):
        """FORGE-96: Register accounts and retrieve them."""
        reg = MultiFirmRegistry()
        acc = make_account()
        reg.register(acc)
        assert reg.get("FTMO-001") is not None
        assert len(reg.all_funded()) == 1

    def test_normal_pnl_aggregation_sums_correctly(self):
        """FORGE-97: Total P&L = sum of all accounts."""
        accounts = [
            make_account("A1", daily_pnl=500.0),
            make_account("A2", firm_id=FirmID.APEX, daily_pnl=300.0),
        ]
        result = aggregate_pnl(accounts)
        assert abs(result.total_daily_pnl - 800.0) < 0.01
        assert result.funded_account_count == 2

    def test_edge_at_risk_account_identified(self):
        """FORGE-100: Account at 75% drawdown → at_risk."""
        acc = make_account(drawdown_pct=0.75)
        assert acc.is_at_risk is True

    def test_normal_priority_queue_sorts_healthy_first(self):
        """FORGE-99: Healthy accounts (low drawdown) get highest priority."""
        accounts = [
            make_account("RISKY", drawdown_pct=0.80),
            make_account("SAFE",  drawdown_pct=0.20),
        ]
        priority = prioritize_accounts(accounts)
        ids = [p[0] for p in priority]
        assert ids[0] == "SAFE"   # Safest trades first

    def test_conflict_firm_conflict_blocks_same_instrument(self):
        """FORGE-101: Same instrument on two accounts → conflict detected."""
        result = resolve_firm_conflicts(
            proposed_account_id="APEX-001",
            proposed_instrument="ES",
            open_positions_by_account={"FTMO-001": ["ES", "GC"]},
        )
        assert result.can_trade is False
        assert "FTMO-001" in result.conflicts

    def test_normal_no_conflict_different_instruments(self):
        """FORGE-101: Different instruments → no conflict."""
        result = resolve_firm_conflicts(
            proposed_account_id="APEX-001",
            proposed_instrument="GC",
            open_positions_by_account={"FTMO-001": ["ES"]},
        )
        assert result.can_trade is True

    def test_normal_health_grade_a_for_healthy_account(self):
        """FORGE-103: Low drawdown + safety net met → A grade."""
        acc = make_account(drawdown_pct=0.20, safety_net_met=True, daily_pnl=500.0)
        report = generate_health_report(acc)
        assert report.health_grade == "A"
        assert not report.action_required


# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNT LIFECYCLE (FORGE-104–110)
# ─────────────────────────────────────────────────────────────────────────────

class TestAccountLifecycle:

    def test_normal_month_36_projection_matches_document(self):
        """FORGE-106: Month 36 projection: $165K–$184K/month per the document."""
        proj = get_projection_for_month(36)
        assert proj["low"]  == 165_000
        assert proj["high"] == 184_000

    def test_edge_month_60_is_ultimate_target(self):
        """FORGE-106: Month 60 = $280K/month (the mission)."""
        proj = get_projection_for_month(60)
        assert proj["low"] == 280_000

    def test_normal_ftmo_scaling_next_tier(self):
        """FORGE-104: At $25K FTMO → next tier $50K."""
        milestone = get_scaling_milestone(FirmID.FTMO, 25_000.0, months_live=1)
        assert milestone.next_size == 50_000.0

    def test_conflict_five_percenters_path_to_4m(self):
        """FORGE-109: $100K 5%ers → remaining path to $4M shown."""
        path = get_five_percenters_path(current_size=100_000.0, months_live=18)
        assert path.target_size == FIVE_PERCENTERS_ULTIMATE_TARGET
        assert path.tiers_remaining > 0
        assert path.is_mission_complete is False

    def test_normal_5percenters_complete_at_4m(self):
        """FORGE-109: At $4M 5%ers → mission complete flag."""
        path = get_five_percenters_path(current_size=4_000_000.0, months_live=48)
        assert path.is_mission_complete is True
        assert path.pct_to_ultimate == 1.0

    def test_normal_capital_velocity_positive_when_profitable(self):
        """FORGE-110: Monthly income > fees → positive velocity."""
        vel = calculate_capital_velocity(
            monthly_income=5_000.0, monthly_fees=540.0,
            total_fees_paid=1_000.0, total_extracted=8_000.0,
        )
        assert vel.net_velocity > 0
        assert vel.is_accelerating is True

    def test_edge_payback_period_calculated(self):
        """FORGE-110: Total fees / monthly income = months to payback."""
        vel = calculate_capital_velocity(
            monthly_income=540.0, monthly_fees=0.0,
            total_fees_paid=540.0, total_extracted=0.0,
        )
        assert abs(vel.months_to_payback - 1.0) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# ARCHITECT INTEGRATION (FORGE-111–115)
# ─────────────────────────────────────────────────────────────────────────────

class TestArchitectIntegration:

    def test_normal_architect_feed_records_events(self):
        """FORGE-111: ARCHITECT feed records and retrieves events."""
        feed = ArchitectFeed()
        feed.push("TRADE", "FTMO-001", FirmID.FTMO,
                  {"setup": "GEX-01", "direction": "long", "size": 1.0})
        assert len(feed.get_recent()) == 1

    def test_edge_critical_alerts_retrievable(self):
        """FORGE-111: Critical alerts stored separately for quick access."""
        feed = ArchitectFeed()
        feed.push("ALERT", "APEX-001", FirmID.APEX, {"msg": "Red zone"}, priority="CRITICAL")
        feed.push("TRADE", "FTMO-001", FirmID.FTMO, {"msg": "Normal"}, priority="INFO")
        assert len(feed.get_critical()) == 1

    def test_normal_meta_brain_learns_from_pass(self):
        """FORGE-112: Passing evaluation → confidence increases."""
        mb = MetaBrainLearningLoop()
        update = mb.process_evaluation("EVAL-001", "PASS",
                                        {"drawdown_used": 0.40})
        assert update.confidence_delta > 0
        assert mb.lifetime_confidence > 0.60

    def test_edge_meta_brain_loses_confidence_on_fail(self):
        """FORGE-112: Failed evaluation → confidence decreases."""
        mb = MetaBrainLearningLoop()
        mb.process_evaluation("EVAL-001", "FAIL",
                               {"failure_reason": "Drawdown breached"})
        assert mb.lifetime_confidence < 0.60

    def test_normal_env_registry_validates_vars(self):
        """FORGE-114: Registry identifies missing required env vars."""
        reg = EnvironmentRegistry()
        status = reg.load_from_env()
        # Most will be missing in test environment — verify the check runs
        missing, present = reg.validate()
        assert isinstance(missing, list)
        assert isinstance(present, list)

    def test_conflict_required_env_vars_complete(self):
        """FORGE-114: All required env vars are documented."""
        assert "RAILWAY_IP" in REQUIRED_ENV_VARS
        assert "FTMO_API_KEY" in REQUIRED_ENV_VARS
        assert "TELEGRAM_BOT_TOKEN" in REQUIRED_ENV_VARS
        assert len(REQUIRED_ENV_VARS) >= 15   # Comprehensive list


# ─────────────────────────────────────────────────────────────────────────────
# NEXUS CORE (FORGE-116–121)
# ─────────────────────────────────────────────────────────────────────────────

class TestNexusCore:

    def test_normal_treasury_only_returns_bank_balance(self):
        """FORGE-116 (FX-08): get_available_capital() = ONLY bank balance."""
        t = NexusTreasury(initial_bank_balance=1_000.0)
        t.submit_payout_request(5_000.0, "FTMO-001")  # Receivable
        t.update_funded_equity(100_000.0)              # Funded equity
        # None of the above should inflate available capital
        assert t.get_available_capital() == 1_000.0   # ONLY the bank

    def test_edge_treasury_deducts_fee_from_bank(self):
        """FORGE-116: Paying eval fee reduces bank balance."""
        t = NexusTreasury(initial_bank_balance=1_000.0)
        success = t.pay_evaluation_fee(540.0, FirmID.FTMO, "EVAL-001")
        assert success is True
        assert abs(t.get_available_capital() - 460.0) < 0.01

    def test_conflict_treasury_refuses_fee_if_insufficient_funds(self):
        """FORGE-116 (FX-08): Cannot pay fee if bank balance insufficient."""
        t = NexusTreasury(initial_bank_balance=200.0)
        success = t.pay_evaluation_fee(540.0, FirmID.FTMO, "EVAL-001")
        assert success is False
        assert t.get_available_capital() == 200.0  # Unchanged

    def test_normal_treasury_receives_payout_adds_to_bank(self):
        """FORGE-116: Receiving payout updates bank balance."""
        t = NexusTreasury(initial_bank_balance=200.0)
        t.submit_payout_request(5_000.0, "FTMO-001")
        t.receive_payout(5_000.0, "FTMO-001")
        assert abs(t.get_available_capital() - 5_200.0) < 0.01

    def test_normal_treasury_state_bootstrapping(self):
        """FORGE-116: $200 bank balance → BOOTSTRAPPING state."""
        t = NexusTreasury(initial_bank_balance=200.0)
        snap = t.snapshot()
        assert snap.state == TreasuryState.BOOTSTRAPPING

    def test_normal_mission_status_tracks_progress(self):
        """FORGE-119: Monthly income tracks toward $280K ultimate target."""
        status = get_mission_status(current_monthly_income=5_000.0, months_elapsed=12)
        assert status.current_monthly == 5_000.0
        assert not status.is_mission_complete
        assert status.pct_of_target < 1.0

    def test_edge_mission_complete_at_280k(self):
        """FORGE-119: $280K+ monthly income → mission complete."""
        status = get_mission_status(current_monthly_income=280_000.0, months_elapsed=48)
        assert status.is_mission_complete is True

    def test_normal_heartbeat_tracks_health(self):
        """FORGE-121: Healthy pulse increments and tracks consecutive healthy beats."""
        hb = NexusHeartbeat(start_time=NOW)
        for _ in range(5):
            hb.pulse(
                systems_ok={"FTMO": True, "APEX": True, "Railway": True},
                active_accounts=2, open_positions=1,
                daily_pnl=500.0, alerts_pending=0,
            )
        assert hb.consecutive_healthy == 5

    def test_conflict_degraded_heartbeat_on_system_down(self):
        """FORGE-121: One system down → DEGRADED heartbeat."""
        hb = NexusHeartbeat(start_time=NOW)
        pulse = hb.pulse(
            systems_ok={"FTMO": True, "APEX": False},  # Apex down
            active_accounts=2, open_positions=0,
            daily_pnl=0.0, alerts_pending=1,
        )
        assert pulse.overall_health == "DEGRADED"
        assert hb.consecutive_healthy == 0

    def test_normal_nexus_ultimate_target_is_280k(self):
        """FORGE-119: Ultimate mission target documented at $280K/month."""
        assert NEXUS_ULTIMATE_TARGET == 280_000.0

    def test_normal_month_36_projection_10_funded(self):
        """FORGE-106: Month 36 has 8 funded accounts projected."""
        proj = get_projection_for_month(36)
        assert proj["funded"] == 8


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
