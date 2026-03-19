"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║             test_firm_rules.py — FORGE-01 — FX-06 Compliance                ║
║                                                                              ║
║  Three test cases per capability:                                            ║
║    (1) Normal case                                                           ║
║    (2) Edge case at exact boundary                                           ║
║    (3) Conflict case confirming priority hierarchy                           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import time
from firm_rules import (
    MultiFirmRuleEngine, FirmID, DrawdownType, AccountPhase,
    FIRM_DATABASE, SAFETY_NET_MAP,
)


def make_engine(firm=FirmID.FTMO) -> MultiFirmRuleEngine:
    return MultiFirmRuleEngine(active_firm_id=firm)


# ─────────────────────────────────────────────────────────────────────────────
# FIRM DATABASE INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestFirmDatabaseIntegrity:

    def test_normal_all_5_firms_present(self):
        """Normal: All 5 firms in database."""
        for fid in [FirmID.FTMO, FirmID.APEX, FirmID.DNA_FUNDED,
                    FirmID.FIVEPERCENTERS, FirmID.TOPSTEP]:
            assert fid in FIRM_DATABASE, f"Missing firm: {fid}"

    def test_edge_ftmo_has_no_consistency_rule(self):
        """Edge: FTMO no_consistency_rule flag must be True — biggest advantage."""
        assert FIRM_DATABASE[FirmID.FTMO].no_consistency_rule is True

    def test_conflict_5ers_tightest_drawdown(self):
        """Conflict: 5%ers must have tightest drawdown of all 5 firms."""
        dd_pcts = {fid: FIRM_DATABASE[fid].total_drawdown_pct for fid in FIRM_DATABASE}
        assert dd_pcts[FirmID.FIVEPERCENTERS] == min(dd_pcts.values()), \
            f"5%ers must have tightest drawdown. Got: {dd_pcts}"

    def test_normal_apex_trailing_drawdown(self):
        """Normal: Apex must be TRAILING_UNREALIZED — the most dangerous rule."""
        assert FIRM_DATABASE[FirmID.APEX].drawdown_type == DrawdownType.TRAILING_UNREALIZED

    def test_normal_ftmo_static_drawdown(self):
        """Normal: FTMO must be STATIC drawdown — most trader-friendly."""
        assert FIRM_DATABASE[FirmID.FTMO].drawdown_type == DrawdownType.STATIC

    def test_normal_dna_eod_snapshot(self):
        """Normal: DNA Funded uses EOD snapshot at 10pm UTC."""
        rules = FIRM_DATABASE[FirmID.DNA_FUNDED]
        assert rules.drawdown_type == DrawdownType.STATIC_EOD_SNAPSHOT
        assert rules.drawdown_snapshot_time_utc == "22:00"

    def test_edge_topstep_hard_close_required(self):
        """Edge: Topstep requires EOD close. TITAN FORGE enforces at 15:00 CT."""
        rules = FIRM_DATABASE[FirmID.TOPSTEP]
        assert rules.requires_eod_close is True
        assert rules.eod_close_time_ct == "15:00"
        assert rules.requires_weekend_close is True

    def test_normal_apex_6_payout_max(self):
        """Normal: Apex has 6-payout maximum per PA."""
        assert FIRM_DATABASE[FirmID.APEX].max_payout_count == 6

    def test_normal_dna_funded_tradelocker_only(self):
        """Normal: DNA Funded only supports TradeLocker — no MT4/MT5."""
        rules = FIRM_DATABASE[FirmID.DNA_FUNDED]
        assert len(rules.platforms) == 1
        assert "TradeLocker" in rules.platforms[0]

    def test_conflict_5ers_highest_ceiling(self):
        """Conflict: 5%ers $4M ceiling must be highest of all firms."""
        ceilings = {
            fid: FIRM_DATABASE[fid].max_total_allocation
            for fid in FIRM_DATABASE
            if FIRM_DATABASE[fid].max_total_allocation is not None
        }
        assert ceilings[FirmID.FIVEPERCENTERS] == max(ceilings.values())
        assert ceilings[FirmID.FIVEPERCENTERS] == 4_000_000.0

    def test_edge_apex_mae_limit_is_30_pct(self):
        """Edge: Apex MAE limit is exactly 30%."""
        assert FIRM_DATABASE[FirmID.APEX].mae_limit_pct == 0.30

    def test_normal_dna_first_3_withdrawals_capped(self):
        """Normal: DNA Funded first 3 withdrawals capped at 5%."""
        rules = FIRM_DATABASE[FirmID.DNA_FUNDED]
        assert rules.first_n_withdrawals_cap_pct == 0.05
        assert rules.first_n_withdrawals_count == 3

    def test_normal_topstep_news_permissive(self):
        """Normal: Topstep has no news blackout — most permissive."""
        rules = FIRM_DATABASE[FirmID.TOPSTEP]
        assert rules.news_blackout_minutes_before == 0
        assert rules.news_blackout_minutes_after == 0
        assert rules.news_can_hold_through is True


# ─────────────────────────────────────────────────────────────────────────────
# FIRM SWITCHING
# ─────────────────────────────────────────────────────────────────────────────

class TestFirmSwitching:

    def test_normal_switch_changes_rules(self):
        """Normal: Switching firm changes the active rule set."""
        engine = make_engine(FirmID.FTMO)
        assert engine.active_firm_id == FirmID.FTMO
        engine.set_active_firm(FirmID.APEX)
        assert engine.active_firm_id == FirmID.APEX
        assert engine.rules.firm_id == FirmID.APEX

    def test_edge_invalid_firm_raises(self):
        """Edge: Invalid firm ID must raise ValueError — never silently proceed."""
        engine = make_engine()
        raised = False
        try:
            engine.set_active_firm("UNKNOWN_FIRM_XYZ")
        except ValueError:
            raised = True
        assert raised, "Must raise ValueError for unknown firm"

    def test_conflict_switch_updates_all_params(self):
        """Conflict: After switching, EVERY parameter reflects new firm — no stale data."""
        engine = make_engine(FirmID.FTMO)
        ftmo_dd = engine.rules.total_drawdown_pct
        engine.set_active_firm(FirmID.FIVEPERCENTERS)
        fivepercenters_dd = engine.rules.total_drawdown_pct
        # FTMO 10% vs 5%ers 4%
        assert ftmo_dd != fivepercenters_dd
        assert fivepercenters_dd == 0.04
        assert engine.rules.firm_id == FirmID.FIVEPERCENTERS


# ─────────────────────────────────────────────────────────────────────────────
# DRAWDOWN STATUS CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestDrawdownCalculations:

    def test_normal_ftmo_static_floor(self):
        """Normal: FTMO $100K account — floor at $90K forever."""
        engine = make_engine(FirmID.FTMO)
        status = engine.calculate_drawdown_status(
            starting_balance=100_000, current_equity=96_000,
            peak_unrealized=0, daily_start_equity=96_000,
        )
        assert abs(status.firm_floor - 90_000) < 0.01
        assert status.distance_to_floor == 6_000.0

    def test_edge_ftmo_floor_never_moves(self):
        """Edge: FTMO static floor doesn't change when equity rises."""
        engine = make_engine(FirmID.FTMO)
        # Start at $100K, rise to $110K — floor still $90K
        status = engine.calculate_drawdown_status(
            starting_balance=100_000, current_equity=110_000,
            peak_unrealized=10_000, daily_start_equity=110_000,
        )
        assert abs(status.firm_floor - 90_000) < 0.01  # NEVER moves for FTMO

    def test_conflict_apex_trailing_floor_rises_with_unrealized(self):
        """Conflict: Apex floor RISES with peak unrealized P&L — FTMO floor does NOT."""
        ftmo_engine = make_engine(FirmID.FTMO)
        apex_engine = make_engine(FirmID.APEX)

        # Both start at $100K, reach $106K unrealized peak
        ftmo_status = ftmo_engine.calculate_drawdown_status(
            starting_balance=100_000, current_equity=102_000,
            peak_unrealized=6_000, daily_start_equity=102_000,
        )
        apex_status = apex_engine.calculate_drawdown_status(
            starting_balance=100_000, current_equity=102_000,
            peak_unrealized=6_000, daily_start_equity=102_000,
        )

        # FTMO: floor stays at $90K
        assert abs(ftmo_status.firm_floor - 90_000) < 0.01
        # Apex: floor rises by $6K unrealized peak = $94K + $6K = $100K floor
        # (starting $100K - $6K drawdown + $6K unrealized peak = $100K)
        assert apex_status.firm_floor > ftmo_status.firm_floor

    def test_normal_yellow_at_50_pct(self):
        """Normal: Yellow warning triggers at 50% drawdown used."""
        engine = make_engine(FirmID.FTMO)
        # FTMO $100K: floor $90K, budget $10K. 50% used = $5K used = $95K equity
        status = engine.calculate_drawdown_status(
            starting_balance=100_000, current_equity=95_000,
            peak_unrealized=0, daily_start_equity=95_000,
        )
        assert status.at_yellow is True
        assert status.at_orange is False
        assert status.at_red is False

    def test_edge_red_at_85_pct(self):
        """Edge: RED alert at exactly 85% drawdown used — close all."""
        engine = make_engine(FirmID.FTMO)
        # $10K budget, 85% used = $8,500 used → equity = $91,500
        status = engine.calculate_drawdown_status(
            starting_balance=100_000, current_equity=91_500,
            peak_unrealized=0, daily_start_equity=91_500,
        )
        assert status.at_red is True

    def test_normal_topstep_eod_trailing_locks_at_breakeven(self):
        """Normal: Topstep EOD trailing — locks at breakeven once 10% above start."""
        engine = make_engine(FirmID.TOPSTEP)
        # Account 10% above start — floor should lock at starting balance
        status = engine.calculate_drawdown_status(
            starting_balance=100_000, current_equity=112_000,
            peak_unrealized=0, daily_start_equity=112_000,
        )
        # Lock condition: daily_start_equity >= starting * 1.10 → floor = starting_balance
        assert abs(status.firm_floor - 100_000) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# NEWS BLACKOUT CHECKS
# ─────────────────────────────────────────────────────────────────────────────

class TestNewsBlackout:

    def test_normal_dna_blocked_before_event(self):
        """Normal: DNA Funded — blocked within 10 minutes before major event."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_news_blackout(minutes_to_event=5.0, minutes_since_event=None)
        assert result.is_violation is True
        assert "Pre-Event" in result.rule_name

    def test_edge_dna_exactly_10_minutes_blocked(self):
        """Edge: DNA Funded — exactly 10 minutes before event = blocked (boundary inclusive)."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_news_blackout(minutes_to_event=10.0, minutes_since_event=None)
        assert result.is_violation is True

    def test_conflict_topstep_never_blocked(self):
        """Conflict: Topstep — even 1 minute before major event = PERMITTED (most permissive)."""
        engine = make_engine(FirmID.TOPSTEP)
        result = engine.check_news_blackout(minutes_to_event=0.5, minutes_since_event=0.5)
        assert result.is_violation is False
        assert result.compliant is True

    def test_normal_ftmo_evaluation_no_restriction(self):
        """Normal: FTMO evaluation phase — no news blackout (only funded has 2-min rule)."""
        engine = make_engine(FirmID.FTMO)
        result = engine.check_news_blackout(
            minutes_to_event=1.0, minutes_since_event=None,
            phase=AccountPhase.EVALUATION
        )
        assert result.compliant is True

    def test_normal_dna_after_event_blocked(self):
        """Normal: DNA Funded — blocked within 10 minutes AFTER major event."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_news_blackout(minutes_to_event=None, minutes_since_event=7.0)
        assert result.is_violation is True
        assert "Post-Event" in result.rule_name

    def test_edge_clear_of_blackout(self):
        """Edge: 11 minutes from event — outside DNA's 10-minute window = clear."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_news_blackout(minutes_to_event=11.0, minutes_since_event=None)
        assert result.compliant is True


# ─────────────────────────────────────────────────────────────────────────────
# CONSISTENCY RULE CHECKS
# ─────────────────────────────────────────────────────────────────────────────

class TestConsistencyRule:

    def test_normal_ftmo_always_compliant(self):
        """Normal: FTMO — no consistency rule, always compliant regardless of profit."""
        engine = make_engine(FirmID.FTMO)
        result = engine.check_consistency_rule(
            total_profit_since_last_payout=10_000,
            todays_profit=9_000,   # 90% of total — would violate any consistency rule
        )
        assert result.compliant is True
        assert "No consistency rule" in result.reason

    def test_edge_apex_exactly_at_50_pct(self):
        """Edge: Apex — exactly at 50% of total profit — compliant (boundary)."""
        engine = make_engine(FirmID.APEX)
        result = engine.check_consistency_rule(
            total_profit_since_last_payout=10_000,
            todays_profit=5_000,   # Exactly 50%
        )
        assert result.compliant is True

    def test_edge_apex_one_dollar_over_50_pct(self):
        """Edge: Apex — $1 over 50% limit — VIOLATION."""
        engine = make_engine(FirmID.APEX)
        result = engine.check_consistency_rule(
            total_profit_since_last_payout=10_000,
            todays_profit=5_000.01,
        )
        assert result.is_violation is True

    def test_conflict_dna_40_pct_cap_stricter_than_apex(self):
        """Conflict: DNA at 40% cap is stricter than Apex 50% — same profit, different results."""
        apex_engine = make_engine(FirmID.APEX)
        dna_engine  = make_engine(FirmID.DNA_FUNDED)

        # $4,500 profit on $10K total = 45% — fine for Apex, violation for DNA
        apex_result = apex_engine.check_consistency_rule(10_000, 4_500)
        dna_result  = dna_engine.check_consistency_rule(10_000, 4_500)

        assert apex_result.compliant is True   # 45% < Apex's 50% cap
        assert dna_result.is_violation is True # 45% > DNA's 40% cap

    def test_normal_warning_near_limit(self):
        """Normal: Near the limit — compliant but warning issued."""
        engine = make_engine(FirmID.APEX)
        # $4,200 on $10K total = 42% — within 20% of 50% cap → warning
        result = engine.check_consistency_rule(10_000, 4_200)
        assert result.compliant is True
        assert result.warning is True


# ─────────────────────────────────────────────────────────────────────────────
# TOPSTEP EOD CLOSE CHECKS
# ─────────────────────────────────────────────────────────────────────────────

class TestEODClose:

    def test_normal_topstep_forces_close_at_1500(self):
        """Normal: Topstep with open positions at 15:00 CT — VIOLATION."""
        engine = make_engine(FirmID.TOPSTEP)
        result = engine.check_eod_close_required(
            current_time_ct=time(15, 0),
            has_open_positions=True,
        )
        assert result.is_violation is True

    def test_edge_topstep_1459_no_violation(self):
        """Edge: Topstep at 14:59 CT — still safe, no violation yet."""
        engine = make_engine(FirmID.TOPSTEP)
        result = engine.check_eod_close_required(
            current_time_ct=time(14, 59),
            has_open_positions=True,
        )
        assert result.compliant is True

    def test_conflict_ftmo_no_eod_close(self):
        """Conflict: FTMO at 15:30 with positions — NO violation (no EOD rule)."""
        engine = make_engine(FirmID.FTMO)
        result = engine.check_eod_close_required(
            current_time_ct=time(15, 30),
            has_open_positions=True,
        )
        assert result.compliant is True
        assert "not required" in result.reason.lower() or "No mandatory" in result.reason

    def test_normal_warning_at_1445(self):
        """Normal: 14:45 CT with positions — compliant but warning."""
        engine = make_engine(FirmID.TOPSTEP)
        result = engine.check_eod_close_required(
            current_time_ct=time(14, 45),
            has_open_positions=True,
        )
        assert result.compliant is True
        assert result.warning is True


# ─────────────────────────────────────────────────────────────────────────────
# WEEKEND HOLD CHECKS
# ─────────────────────────────────────────────────────────────────────────────

class TestWeekendHold:

    def test_normal_topstep_weekend_prohibited(self):
        """Normal: Topstep — weekend positions are prohibited."""
        engine = make_engine(FirmID.TOPSTEP)
        result = engine.check_weekend_hold(is_weekend_or_friday_eod=True, has_open_positions=True)
        assert result.is_violation is True

    def test_edge_topstep_no_positions_ok(self):
        """Edge: Topstep on weekend with no open positions — compliant."""
        engine = make_engine(FirmID.TOPSTEP)
        result = engine.check_weekend_hold(is_weekend_or_friday_eod=True, has_open_positions=False)
        assert result.compliant is True

    def test_conflict_ftmo_weekend_permitted(self):
        """Conflict: FTMO on weekend with positions — permitted (no weekend rule)."""
        engine = make_engine(FirmID.FTMO)
        result = engine.check_weekend_hold(is_weekend_or_friday_eod=True, has_open_positions=True)
        assert result.compliant is True


# ─────────────────────────────────────────────────────────────────────────────
# FUNDED RESTRICTIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestFundedRestrictions:

    def test_normal_dna_funded_no_scalping(self):
        """Normal: DNA Funded — scalping banned in funded phase."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_funded_restrictions(
            strategy_type="SCALP", hold_seconds=15.0,
            phase=AccountPhase.FUNDED
        )
        assert result.is_violation is True
        assert "Scalping" in result.rule_name

    def test_edge_dna_funded_below_30s_hold(self):
        """Edge: DNA Funded — 29 seconds hold violates minimum 30s rule."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_funded_restrictions(
            strategy_type="STANDARD", hold_seconds=29.0,
            phase=AccountPhase.FUNDED
        )
        assert result.is_violation is True
        assert "30" in result.rule_name

    def test_edge_dna_funded_exactly_30s_hold(self):
        """Edge: DNA Funded — exactly 30 seconds hold = compliant (boundary)."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_funded_restrictions(
            strategy_type="STANDARD", hold_seconds=30.0,
            phase=AccountPhase.FUNDED
        )
        assert result.compliant is True

    def test_conflict_funded_only_restrictions(self):
        """Conflict: DNA Funded restrictions ONLY apply in funded phase, not evaluation."""
        engine = make_engine(FirmID.DNA_FUNDED)
        # In evaluation phase — scalping not banned
        eval_result = engine.check_funded_restrictions(
            strategy_type="SCALP", hold_seconds=5.0,
            phase=AccountPhase.EVALUATION
        )
        fund_result = engine.check_funded_restrictions(
            strategy_type="SCALP", hold_seconds=5.0,
            phase=AccountPhase.FUNDED
        )
        assert eval_result.compliant is True   # Evaluation: no restriction
        assert fund_result.is_violation is True # Funded: banned

    def test_normal_dna_funded_no_martingale(self):
        """Normal: DNA Funded — martingale banned in funded."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_funded_restrictions(
            strategy_type="MARTINGALE", hold_seconds=60.0,
            phase=AccountPhase.FUNDED
        )
        assert result.is_violation is True

    def test_normal_ftmo_no_funded_restrictions(self):
        """Normal: FTMO — no scalping/grid/martingale bans in funded."""
        engine = make_engine(FirmID.FTMO)
        for strategy in ["SCALP", "GRID", "MARTINGALE"]:
            result = engine.check_funded_restrictions(
                strategy_type=strategy, hold_seconds=5.0,
                phase=AccountPhase.FUNDED
            )
            assert result.compliant is True, f"FTMO should permit {strategy} in funded"


# ─────────────────────────────────────────────────────────────────────────────
# DNA FUNDED WITHDRAWAL CAP
# ─────────────────────────────────────────────────────────────────────────────

class TestWithdrawalCap:

    def test_normal_dna_first_withdrawal_blocked(self):
        """Normal: DNA Funded — $6K requested on $100K balance, 1st withdrawal = blocked ($5K cap)."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_payout_withdrawal_cap(
            account_balance=100_000, requested_amount=6_000, withdrawal_number=1
        )
        assert result.is_violation is True
        assert "5%" in result.rule_name or "5%" in result.reason

    def test_edge_dna_exactly_5_pct(self):
        """Edge: Exactly $5,000 (5% of $100K) — compliant."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_payout_withdrawal_cap(
            account_balance=100_000, requested_amount=5_000, withdrawal_number=1
        )
        assert result.compliant is True

    def test_conflict_dna_4th_withdrawal_no_cap(self):
        """Conflict: 4th withdrawal — cap no longer applies (first 3 only)."""
        engine = make_engine(FirmID.DNA_FUNDED)
        result = engine.check_payout_withdrawal_cap(
            account_balance=100_000, requested_amount=20_000, withdrawal_number=4
        )
        assert result.compliant is True  # 4th withdrawal = no cap

    def test_normal_ftmo_no_withdrawal_cap(self):
        """Normal: FTMO — no withdrawal cap rule."""
        engine = make_engine(FirmID.FTMO)
        result = engine.check_payout_withdrawal_cap(
            account_balance=100_000, requested_amount=50_000, withdrawal_number=1
        )
        assert result.compliant is True


# ─────────────────────────────────────────────────────────────────────────────
# APEX-SPECIFIC RULES
# ─────────────────────────────────────────────────────────────────────────────

class TestApexSpecificRules:

    def test_normal_apex_trailing_lock_warning(self):
        """Normal: Apex — near trailing floor triggers warning."""
        engine = make_engine(FirmID.APEX)
        # $100K account, $6K trailing drawdown → floor = $94K
        # With $6K peak unrealized → floor = $100K (at breakeven)
        # Now equity drops to $100,500 — only $500 above floor of $100K
        result = engine.check_apex_trailing_lock(
            current_equity=100_500,
            starting_balance=100_000,
            peak_unrealized_pnl=6_000,
            has_open_positions=True,
        )
        assert result.compliant is True
        assert result.warning is True

    def test_edge_apex_mae_exactly_30_pct(self):
        """Edge: Apex MAE — exactly at 30% of profit balance — compliant."""
        engine = make_engine(FirmID.APEX)
        result = engine.check_apex_mae_limit(
            current_profit_balance=10_000,
            open_trade_drawdown=3_000,  # Exactly 30%
        )
        assert result.compliant is True

    def test_conflict_apex_mae_over_30_pct_violation(self):
        """Conflict: Apex MAE — $3,001 drawdown on $10K profit = violation."""
        engine = make_engine(FirmID.APEX)
        result = engine.check_apex_mae_limit(
            current_profit_balance=10_000,
            open_trade_drawdown=3_001,
        )
        assert result.is_violation is True

    def test_normal_apex_half_contracts_pre_safety_net(self):
        """Normal: Apex — half contracts before safety net reached (self-policed)."""
        engine = make_engine(FirmID.APEX)
        full_max = 10.0
        pre_net_max = engine.get_max_contracts_pre_safety_net(full_max, FirmID.APEX)
        assert pre_net_max == 5.0  # Half of full max

    def test_normal_ftmo_full_contracts(self):
        """Normal: FTMO — no half-contract restriction."""
        engine = make_engine(FirmID.FTMO)
        full_max = 10.0
        result = engine.get_max_contracts_pre_safety_net(full_max, FirmID.FTMO)
        assert result == full_max

    def test_normal_apex_no_trailing_concern_without_positions(self):
        """Normal: Apex trailing — no open positions, no concern."""
        engine = make_engine(FirmID.APEX)
        result = engine.check_apex_trailing_lock(
            current_equity=105_000, starting_balance=100_000,
            peak_unrealized_pnl=0, has_open_positions=False,
        )
        assert result.compliant is True
        assert result.warning is False


# ─────────────────────────────────────────────────────────────────────────────
# SAFETY NET MAP
# ─────────────────────────────────────────────────────────────────────────────

class TestSafetyNet:

    def test_normal_apex_safety_net_52600(self):
        """Normal: Apex $50K/100K PA — safety net is $52,600."""
        engine = make_engine(FirmID.APEX)
        net = engine.get_safety_net(50_000.0, FirmID.APEX)
        assert net == 52_600.0

    def test_edge_unknown_account_size_fallback(self):
        """Edge: Unknown account size — falls back to 5% above floor."""
        engine = make_engine(FirmID.FTMO)
        net = engine.get_safety_net(75_000.0, FirmID.FTMO)  # Not in map
        # Floor = $75K * (1 - 0.10) = $67.5K. Net = $67.5K + ($75K * 5%) = $71.25K
        assert net > 67_500.0
        assert net > 0

    def test_conflict_apex_net_same_for_50k_and_100k(self):
        """Conflict: Apex safety net is the same dollar amount regardless of PA size."""
        engine = make_engine(FirmID.APEX)
        net_50k  = engine.get_safety_net(50_000.0,  FirmID.APEX)
        net_100k = engine.get_safety_net(100_000.0, FirmID.APEX)
        assert net_50k == net_100k == 52_600.0

    def test_normal_firm_summary(self):
        """Normal: get_firm_summary returns a non-empty dict for all firms."""
        engine = make_engine(FirmID.FTMO)
        for fid in [FirmID.FTMO, FirmID.APEX, FirmID.DNA_FUNDED,
                    FirmID.FIVEPERCENTERS, FirmID.TOPSTEP]:
            summary = engine.get_firm_summary(fid)
            assert summary["firm_id"] == fid
            assert summary["drawdown_type"]
            assert summary["total_dd_pct"]


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
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
