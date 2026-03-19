"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   test_clash_rules.py — FX-06 Compliance                    ║
║                                                                              ║
║  FX-06 requires THREE test cases per capability:                             ║
║    (1) Normal case                                                           ║
║    (2) Edge case at exact boundary                                           ║
║    (3) Conflict case confirming priority hierarchy                           ║
║                                                                              ║
║  No capability deploys without all 3 passing.                                ║
║  Run: python -m pytest tests/test_clash_rules.py -v                         ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clash_rules import (
    # Data structures
    AccountState, FirmConfig, TradeStats, TradeSignal,
    # Clash resolution functions
    check_emergency_conditions,
    resolve_c02_approach_protocol,
    resolve_c05_paper_pass_gate,
    resolve_c06_kelly_safety_cap,
    resolve_c08_loss_response_floor,
    resolve_c14_hot_hand_firm_specific,
    resolve_c15_streak_phase_specific,
    resolve_c19_safety_net_payout,
    # Master resolver
    ClashResolver,
    # Enums and constants
    ClashDecision, PriorityLevel,
    KELLY_HARD_CAP_EVALUATION, KELLY_HARD_CAP_FUNDED,
    KELLY_MIN_TRADES_REQUIRED, KELLY_IMMATURE_DEFAULT,
    LOSS_FLOOR_ONE_LOSS, LOSS_FLOOR_TWO_LOSSES,
    HOT_HAND_DISABLED_FIRMS, HOT_HAND_MAX_MULTIPLIER,
    PHASE_EVALUATION, PHASE_FUNDED,
    REQUIRED_CONSECUTIVE_PAPER_PASSES,
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES — Reusable test data
# ─────────────────────────────────────────────────────────────────────────────

def make_account(
    firm_id="FTMO",
    phase=PHASE_EVALUATION,
    balance=100_000.0,
    starting_balance=100_000.0,
    drawdown_buffer=10_000.0,
    remaining_drawdown=10_000.0,
    remaining_profit_needed=5_000.0,
    current_profit=5_000.0,
    consecutive_losses=0,
    consecutive_profitable_sessions=0,
    total_trades=150,
    is_funded=False,
    safety_net_reached=False,
    flash_crash_active=False,
    correlation_spike_active=False,
    liquidity_vacuum_active=False,
) -> AccountState:
    return AccountState(
        account_id="TEST-001",
        firm_id=firm_id,
        account_phase=phase,
        current_balance=balance,
        starting_balance=starting_balance,
        drawdown_buffer=drawdown_buffer,
        remaining_drawdown=remaining_drawdown,
        remaining_profit_needed=remaining_profit_needed,
        current_profit=current_profit,
        consecutive_losses=consecutive_losses,
        consecutive_profitable_sessions=consecutive_profitable_sessions,
        total_trades=total_trades,
        is_funded=is_funded,
        safety_net_reached=safety_net_reached,
        flash_crash_active=flash_crash_active,
        correlation_spike_active=correlation_spike_active,
        liquidity_vacuum_active=liquidity_vacuum_active,
    )


def make_firm(
    firm_id="FTMO",
    profit_target_pct=0.10,
    daily_drawdown_limit=0.05,
    total_drawdown_limit=0.10,
    minimum_position_size=0.01,
    maximum_position_size=5.0,
    news_blackout_minutes=2,
    consistency_rule_pct=None,
    safety_net_amount=10_500.0,
) -> FirmConfig:
    return FirmConfig(
        firm_id=firm_id,
        profit_target_pct=profit_target_pct,
        daily_drawdown_limit=daily_drawdown_limit,
        total_drawdown_limit=total_drawdown_limit,
        minimum_position_size=minimum_position_size,
        maximum_position_size=maximum_position_size,
        news_blackout_minutes=news_blackout_minutes,
        consistency_rule_pct=consistency_rule_pct,
        safety_net_amount=safety_net_amount,
    )


def make_trade_stats(
    total_trades=150,
    win_rate=0.65,
    avg_win_pct=0.015,
    avg_loss_pct=0.008,
) -> TradeStats:
    return TradeStats(
        total_trades=total_trades,
        win_rate=win_rate,
        avg_win_pct=avg_win_pct,
        avg_loss_pct=avg_loss_pct,
    )


def make_signal(
    signal_id="SIG-001",
    firm_id="FTMO",
    strategy_name="GEX-01",
    proposed_size=0.01,
    proposed_size_modifier=1.0,
    dynamic_modifier=1.0,
    expected_value=0.25,
    opportunity_score=75.0,
    rule_compliant=True,
    hot_hand_multiplier=1.0,
    win_streak_multiplier=1.0,
    payout_amount=None,
) -> TradeSignal:
    return TradeSignal(
        signal_id=signal_id,
        firm_id=firm_id,
        strategy_name=strategy_name,
        proposed_size=proposed_size,
        proposed_size_modifier=proposed_size_modifier,
        dynamic_modifier=dynamic_modifier,
        expected_value=expected_value,
        opportunity_score=opportunity_score,
        rule_compliant=rule_compliant,
        hot_hand_multiplier=hot_hand_multiplier,
        win_streak_multiplier=win_streak_multiplier,
        payout_amount=payout_amount,
    )


# ─────────────────────────────────────────────────────────────────────────────
# LEVEL 2 — EMERGENCY CONDITION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestEmergencyConditions:

    def test_normal_no_emergency(self):
        """Normal: No emergency conditions active — returns None."""
        account = make_account()
        result = check_emergency_conditions(account)
        assert result is None, "No emergency active — must return None"

    def test_edge_flash_crash_boundary(self):
        """Edge: Flash crash flag is True — immediately blocked at Level 2."""
        account = make_account(flash_crash_active=True)
        result = check_emergency_conditions(account)
        assert result is not None
        assert result.decision == ClashDecision.BLOCKED
        assert result.priority_level == PriorityLevel.EMERGENCY

    def test_conflict_all_emergency_flags(self):
        """Conflict: Multiple emergency flags — first one (flash crash) wins."""
        account = make_account(
            flash_crash_active=True,
            correlation_spike_active=True,
            liquidity_vacuum_active=True,
        )
        result = check_emergency_conditions(account)
        assert result is not None
        assert result.decision == ClashDecision.BLOCKED
        assert result.priority_level == PriorityLevel.EMERGENCY
        assert "Flash" in result.rule_applied

    def test_correlation_spike_alone(self):
        """Edge: Correlation spike alone — blocked at Level 2."""
        account = make_account(correlation_spike_active=True)
        result = check_emergency_conditions(account)
        assert result.decision == ClashDecision.BLOCKED
        assert "Correlation" in result.rule_applied

    def test_liquidity_vacuum_alone(self):
        """Edge: Liquidity vacuum alone — blocked at Level 2."""
        account = make_account(liquidity_vacuum_active=True)
        result = check_emergency_conditions(account)
        assert result.decision == ClashDecision.BLOCKED
        assert "Liquidity" in result.rule_applied


# ─────────────────────────────────────────────────────────────────────────────
# C-02 — APPROACH PROTOCOL TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestC02ApproachProtocol:

    def test_normal_far_from_target(self):
        """Normal: 50% from target — pacing engine permitted to operate."""
        account = make_account(remaining_profit_needed=5_000.0)
        firm = make_firm(profit_target_pct=0.10)  # $10K target on $100K
        result = resolve_c02_approach_protocol(account, firm, standard_size=1.0)
        assert result.decision == ClashDecision.PERMITTED
        assert result.modified_value == 1.0

    def test_edge_exactly_20_pct_boundary(self):
        """Edge: Exactly 20% of target remaining — 50% size, pacing ignored."""
        # $10K target on $100K account → 20% = $2,000 remaining
        account = make_account(remaining_profit_needed=2_000.0)
        firm = make_firm(profit_target_pct=0.10)
        result = resolve_c02_approach_protocol(account, firm, standard_size=1.0)
        assert result.decision == ClashDecision.DEGRADED
        assert abs(result.modified_value - 0.50) < 0.001  # Half size

    def test_edge_exactly_10_pct_boundary(self):
        """Edge: Exactly 10% of target remaining — minimum size only."""
        # $10K target → 10% = $1,000 remaining
        account = make_account(remaining_profit_needed=1_000.0)
        firm = make_firm(profit_target_pct=0.10, minimum_position_size=0.01)
        result = resolve_c02_approach_protocol(account, firm, standard_size=1.0)
        assert result.decision == ClashDecision.DEGRADED
        assert result.modified_value == firm.minimum_position_size

    def test_conflict_approach_overrides_pacing(self):
        """Conflict: Pacing engine wants large size, approach protocol wins."""
        # $9,500 remaining on $10K target → distance = 9,500/10,000 = 95% → not triggered
        # But $500 remaining → distance = 500/10,000 = 5% → minimum size wins
        account = make_account(remaining_profit_needed=500.0)
        firm = make_firm(profit_target_pct=0.10, minimum_position_size=0.01)
        pacing_wants = 2.0  # Pacing engine aggressively wants large size
        result = resolve_c02_approach_protocol(account, firm, standard_size=pacing_wants)
        # Approach Protocol must override pacing — minimum size only
        assert result.decision == ClashDecision.DEGRADED
        assert result.modified_value == firm.minimum_position_size
        assert result.modified_value < pacing_wants  # Approach Protocol wins

    def test_between_10_and_20_pct(self):
        """Normal: Between 10% and 20% remaining — half size applied."""
        account = make_account(remaining_profit_needed=1_500.0)  # 15% of $10K
        firm = make_firm(profit_target_pct=0.10)
        result = resolve_c02_approach_protocol(account, firm, standard_size=1.0)
        assert result.decision == ClashDecision.DEGRADED
        assert abs(result.modified_value - 0.50) < 0.001


# ─────────────────────────────────────────────────────────────────────────────
# C-05 — THREE PAPER PASS GATE TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestC05PaperPassGate:

    def test_normal_zero_passes_blocked(self):
        """Normal: 0 paper passes — blocked regardless of market conditions."""
        result = resolve_c05_paper_pass_gate("FTMO", 0, optimal_stopping_conditions_met=True)
        assert result.decision == ClashDecision.BLOCKED
        assert result.priority_level == PriorityLevel.RISK_MANAGEMENT

    def test_edge_exactly_2_passes_still_blocked(self):
        """Edge: 2 passes (one short) — still blocked. Boundary is 3."""
        result = resolve_c05_paper_pass_gate("FTMO", 2, optimal_stopping_conditions_met=True)
        assert result.decision == ClashDecision.BLOCKED
        assert "3" in result.reason  # Must mention the requirement

    def test_edge_exactly_3_passes_gate_cleared(self):
        """Edge: Exactly 3 passes + optimal conditions — permitted."""
        result = resolve_c05_paper_pass_gate("FTMO", 3, optimal_stopping_conditions_met=True)
        assert result.decision == ClashDecision.PERMITTED

    def test_conflict_optimal_stopping_cannot_override_gate(self):
        """Conflict: Optimal Stopping says go, but only 2 passes — paper passes WIN."""
        result = resolve_c05_paper_pass_gate(
            "FTMO",
            consecutive_paper_passes=2,
            optimal_stopping_conditions_met=True  # Market is perfect — irrelevant
        )
        assert result.decision == ClashDecision.BLOCKED
        # The paper pass rule must be the blocker — not the market condition
        assert "C-05" in result.rule_applied or "Paper" in result.rule_applied

    def test_3_passes_but_bad_market_blocked_by_optimal_stopping(self):
        """Normal: 3 passes but market conditions poor — blocked by Optimal Stopping."""
        result = resolve_c05_paper_pass_gate(
            "FTMO",
            consecutive_paper_passes=3,
            optimal_stopping_conditions_met=False  # Market says wait
        )
        assert result.decision == ClashDecision.BLOCKED


# ─────────────────────────────────────────────────────────────────────────────
# C-06 — KELLY SAFETY CAP TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestC06KellySafetyCap:

    def test_normal_mature_kelly_capped(self):
        """Normal: Mature stats produce Kelly — must not exceed hard cap."""
        account = make_account(drawdown_buffer=10_000.0, remaining_drawdown=10_000.0, is_funded=False)
        stats = make_trade_stats(total_trades=150, win_rate=0.65, avg_win_pct=0.015, avg_loss_pct=0.008)
        result = resolve_c06_kelly_safety_cap(account, stats)
        assert result.decision == ClashDecision.DEGRADED
        # Cap is 2% of $10K drawdown buffer = $200 = 0.2% of $100K balance
        max_allowed_pct = KELLY_HARD_CAP_EVALUATION  # 0.02 of drawdown_buffer / balance
        # The result should be <= the hard cap as a fraction of account
        cap_as_pct_of_balance = (account.drawdown_buffer * KELLY_HARD_CAP_EVALUATION) / account.current_balance
        assert result.modified_value <= cap_as_pct_of_balance + 1e-9

    def test_edge_exactly_at_trade_threshold(self):
        """Edge: Exactly 100 trades — should use Kelly formula, not immature default."""
        account = make_account(drawdown_buffer=10_000.0, remaining_drawdown=10_000.0)
        stats = make_trade_stats(total_trades=KELLY_MIN_TRADES_REQUIRED, win_rate=0.65)
        result = resolve_c06_kelly_safety_cap(account, stats)
        assert result.decision == ClashDecision.DEGRADED
        # Should NOT mention "IMMATURE" since we're exactly at threshold
        assert "IMMATURE" not in result.reason

    def test_edge_below_trade_threshold(self):
        """Edge: 99 trades — must use immature default, not Kelly formula."""
        account = make_account(drawdown_buffer=10_000.0, remaining_drawdown=10_000.0)
        stats = make_trade_stats(total_trades=KELLY_MIN_TRADES_REQUIRED - 1)
        result = resolve_c06_kelly_safety_cap(account, stats)
        assert result.decision == ClashDecision.DEGRADED
        assert "IMMATURE" in result.reason or "immature" in result.reason.lower()

    def test_conflict_funded_vs_evaluation_cap(self):
        """Conflict: Funded uses 3% cap — evaluation uses 2% cap. Must be different."""
        account_eval = make_account(drawdown_buffer=10_000.0, remaining_drawdown=10_000.0, is_funded=False)
        account_fund = make_account(drawdown_buffer=10_000.0, remaining_drawdown=10_000.0, is_funded=True)
        stats = make_trade_stats(total_trades=200, win_rate=0.80, avg_win_pct=0.020, avg_loss_pct=0.005)
        result_eval = resolve_c06_kelly_safety_cap(account_eval, stats)
        result_fund = resolve_c06_kelly_safety_cap(account_fund, stats)
        # Funded cap (3%) is more generous than evaluation cap (2%)
        # When Kelly is high, funded will permit more than evaluation
        eval_cap = (account_eval.drawdown_buffer * KELLY_HARD_CAP_EVALUATION) / account_eval.current_balance
        fund_cap = (account_fund.drawdown_buffer * KELLY_HARD_CAP_FUNDED) / account_fund.current_balance
        assert result_eval.modified_value <= eval_cap + 1e-9
        assert result_fund.modified_value <= fund_cap + 1e-9
        assert KELLY_HARD_CAP_FUNDED > KELLY_HARD_CAP_EVALUATION  # Funded is more generous

    def test_sanity_cap_overrides_hard_cap(self):
        """Edge: When remaining drawdown is tiny, sanity cap (25%) is binding."""
        # Tiny remaining drawdown means sanity cap will be smaller than hard cap
        account = make_account(
            drawdown_buffer=10_000.0,
            remaining_drawdown=100.0,   # Only $100 left — sanity cap = $25
            is_funded=False
        )
        stats = make_trade_stats(total_trades=200, win_rate=0.80, avg_win_pct=0.020, avg_loss_pct=0.005)
        result = resolve_c06_kelly_safety_cap(account, stats)
        # Sanity cap = $25, hard cap = $200 — sanity cap should win
        sanity_cap = 100.0 * 0.25 / account.current_balance  # = 0.025% of account
        assert result.modified_value <= sanity_cap + 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# C-08 — LOSS RESPONSE FLOOR TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestC08LossResponseFloor:

    def test_normal_no_losses(self):
        """Normal: 0 consecutive losses — dynamic sizing operates freely."""
        account = make_account(consecutive_losses=0)
        result = resolve_c08_loss_response_floor(account, dynamic_size_modifier=1.5)
        assert result.decision == ClashDecision.PERMITTED
        assert result.modified_value == 1.5

    def test_edge_exactly_one_loss(self):
        """Edge: Exactly 1 consecutive loss — floor at 0.75."""
        account = make_account(consecutive_losses=1)
        result = resolve_c08_loss_response_floor(account, dynamic_size_modifier=1.0)
        assert result.modified_value <= LOSS_FLOOR_ONE_LOSS + 1e-9

    def test_edge_exactly_two_losses(self):
        """Edge: Exactly 2 consecutive losses — floor at 0.60."""
        account = make_account(consecutive_losses=2)
        result = resolve_c08_loss_response_floor(account, dynamic_size_modifier=1.0)
        assert result.modified_value <= LOSS_FLOOR_TWO_LOSSES + 1e-9

    def test_conflict_dynamic_exceeds_floor(self):
        """Conflict: Dynamic sizing says 1.5x after 2 losses — floor WINS at 0.60."""
        account = make_account(consecutive_losses=2)
        dynamic_wants = 1.5  # Dynamic is aggressive — MUST be overridden
        result = resolve_c08_loss_response_floor(account, dynamic_size_modifier=dynamic_wants)
        assert result.decision == ClashDecision.DEGRADED
        assert result.modified_value == LOSS_FLOOR_TWO_LOSSES
        assert result.modified_value < dynamic_wants  # Loss Response wins

    def test_five_consecutive_losses_still_uses_two_plus_floor(self):
        """Edge: 5 consecutive losses — still uses 2+ floor (0.60). No further reduction."""
        account = make_account(consecutive_losses=5)
        result = resolve_c08_loss_response_floor(account, dynamic_size_modifier=1.0)
        assert result.modified_value == LOSS_FLOOR_TWO_LOSSES

    def test_dynamic_already_below_floor(self):
        """Normal: Dynamic sizing already below floor — no modification needed."""
        account = make_account(consecutive_losses=1)
        result = resolve_c08_loss_response_floor(account, dynamic_size_modifier=0.50)
        # 0.50 < floor (0.75) — dynamic is already conservative enough
        assert result.decision == ClashDecision.PERMITTED
        assert result.modified_value == 0.50


# ─────────────────────────────────────────────────────────────────────────────
# C-14 — HOT HAND FIRM-SPECIFIC TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestC14HotHandFirmSpecific:

    def test_normal_ftmo_disabled(self):
        """Normal: FTMO firm — Hot Hand permanently disabled. Returns 1.0."""
        account = make_account(firm_id="FTMO", consecutive_profitable_sessions=10)
        result = resolve_c14_hot_hand_firm_specific(account)
        assert result.decision == ClashDecision.DEGRADED
        assert result.modified_value == 1.0  # No hot hand escalation at FTMO

    def test_edge_ftmo_regardless_of_streak(self):
        """Edge: FTMO with 20-session winning streak — still returns 1.0."""
        account = make_account(firm_id="FTMO", consecutive_profitable_sessions=20)
        result = resolve_c14_hot_hand_firm_specific(account)
        assert result.modified_value == 1.0
        assert "FTMO" in result.reason

    def test_normal_apex_below_threshold(self):
        """Normal: Apex, 3 sessions (below 5) — Hot Hand not triggered. Returns 1.0."""
        account = make_account(firm_id="APEX", consecutive_profitable_sessions=3)
        result = resolve_c14_hot_hand_firm_specific(account)
        assert result.modified_value == 1.0

    def test_normal_apex_above_threshold(self):
        """Normal: Apex, 6 sessions — Hot Hand can activate. Returns 1.15 max."""
        account = make_account(firm_id="APEX", consecutive_profitable_sessions=6)
        result = resolve_c14_hot_hand_firm_specific(account)
        assert result.modified_value <= HOT_HAND_MAX_MULTIPLIER + 1e-9
        assert result.modified_value >= 1.0

    def test_conflict_hot_hand_cannot_exceed_max_at_any_firm(self):
        """Conflict: Even if logic tries to return > 1.15 — hard ceiling enforced."""
        account = make_account(firm_id="DNA_FUNDED", consecutive_profitable_sessions=50)
        result = resolve_c14_hot_hand_firm_specific(account)
        assert result.modified_value <= HOT_HAND_MAX_MULTIPLIER + 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# C-15 — STREAK PHASE-SPECIFIC TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestC15StreakPhaseSpecific:

    def test_normal_evaluation_uses_hot_hand(self):
        """Normal: Evaluation phase — Hot Hand multiplier used. Win Streak ignored."""
        account = make_account(phase=PHASE_EVALUATION)
        result = resolve_c15_streak_phase_specific(account, hot_hand_multiplier=1.15, win_streak_multiplier=0.80)
        assert result.decision == ClashDecision.PERMITTED
        assert result.modified_value == 1.15  # Hot Hand wins

    def test_normal_funded_uses_win_streak(self):
        """Normal: Funded phase — Win Streak multiplier used. Hot Hand ignored."""
        account = make_account(phase=PHASE_FUNDED)
        result = resolve_c15_streak_phase_specific(account, hot_hand_multiplier=1.15, win_streak_multiplier=0.80)
        assert result.decision == ClashDecision.PERMITTED
        assert result.modified_value == 0.80  # Win Streak wins

    def test_edge_unknown_phase_blocked(self):
        """Edge: Unknown phase string — blocked as safety measure."""
        account = make_account()
        account.account_phase = "UNKNOWN_PHASE"
        result = resolve_c15_streak_phase_specific(account, hot_hand_multiplier=1.0, win_streak_multiplier=1.0)
        assert result.decision == ClashDecision.BLOCKED

    def test_conflict_never_simultaneous(self):
        """Conflict: Both Hot Hand and Win Streak want to fire — phase determines winner."""
        eval_account = make_account(phase=PHASE_EVALUATION)
        fund_account = make_account(phase=PHASE_FUNDED)

        eval_result = resolve_c15_streak_phase_specific(
            eval_account, hot_hand_multiplier=1.15, win_streak_multiplier=1.10
        )
        fund_result = resolve_c15_streak_phase_specific(
            fund_account, hot_hand_multiplier=1.15, win_streak_multiplier=1.10
        )

        # They should NEVER return the same combined value — one is always discarded
        assert eval_result.modified_value == 1.15  # Evaluation: hot hand
        assert fund_result.modified_value == 1.10  # Funded: win streak

        # Prove they are different (not simultaneous)
        assert eval_result.modified_value != fund_result.modified_value or \
               "Hot Hand" in eval_result.reason


# ─────────────────────────────────────────────────────────────────────────────
# C-19 — SAFETY NET PAYOUT GATE TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestC19SafetyNetPayout:

    def test_normal_payout_blocked_insufficient_buffer(self):
        """Normal: Payout would drop below safety net — BLOCKED."""
        account = make_account(balance=105_000.0)
        firm = make_firm(safety_net_amount=100_000.0)
        # Requesting $6,000 payout → balance after = $99,000 < $100,000 safety net
        result = resolve_c19_safety_net_payout(account, firm, payout_amount=6_000.0)
        assert result.decision == ClashDecision.BLOCKED
        assert result.priority_level == PriorityLevel.RISK_MANAGEMENT

    def test_edge_payout_exactly_at_safety_net(self):
        """Edge: Payout leaves balance exactly at safety net — PERMITTED."""
        account = make_account(balance=105_000.0)
        firm = make_firm(safety_net_amount=100_000.0)
        # Requesting $5,000 → balance after = exactly $100,000 = safety net
        result = resolve_c19_safety_net_payout(account, firm, payout_amount=5_000.0)
        assert result.decision == ClashDecision.PERMITTED

    def test_edge_one_dollar_below_safety_net(self):
        """Edge: One dollar below safety net — must be BLOCKED (no rounding grace)."""
        account = make_account(balance=105_000.0)
        firm = make_firm(safety_net_amount=100_000.0)
        # Requesting $5,000.01 → balance = $99,999.99 → below by $0.01
        result = resolve_c19_safety_net_payout(account, firm, payout_amount=5_000.01)
        assert result.decision == ClashDecision.BLOCKED

    def test_conflict_payout_optimizer_cannot_override_safety_net(self):
        """Conflict: Payout optimizer recommends payout, but safety net not met — net WINS."""
        account = make_account(balance=103_000.0)
        firm = make_firm(safety_net_amount=100_000.0)
        # Even if optimizer thinks timing is perfect, $4,000 payout → $99,000 < net
        result = resolve_c19_safety_net_payout(account, firm, payout_amount=4_000.0)
        assert result.decision == ClashDecision.BLOCKED
        assert result.priority_level == PriorityLevel.RISK_MANAGEMENT

    def test_shortfall_amount_in_reason(self):
        """Normal: Blocked result must show exact shortfall amount."""
        account = make_account(balance=105_000.0)
        firm = make_firm(safety_net_amount=100_000.0)
        result = resolve_c19_safety_net_payout(account, firm, payout_amount=6_000.0)
        assert result.decision == ClashDecision.BLOCKED
        # Shortfall = $100,000 - $99,000 = $1,000
        assert "1,000" in result.reason or "1000" in result.reason


# ─────────────────────────────────────────────────────────────────────────────
# MASTER RESOLVER — PRIORITY HIERARCHY INTEGRATION TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestClashResolver:

    def setup_method(self):
        self.resolver = ClashResolver()

    def _base_resolve(self, signal=None, account=None, firm=None, stats=None, paper_passes=3):
        return self.resolver.evaluate(
            signal=signal or make_signal(),
            account=account or make_account(),
            firm_config=firm or make_firm(),
            trade_stats=stats or make_trade_stats(),
            consecutive_paper_passes=paper_passes,
            optimal_stopping_met=True,
        )

    def test_normal_clean_signal_fully_permitted(self):
        """Normal: Clean signal with no issues — PERMITTED through all levels."""
        report = self._base_resolve()
        assert report.is_permitted
        assert report.final_size is not None
        assert report.final_modifier is not None

    def test_level1_blocks_everything(self):
        """Integration: Level 1 firm rule violation — blocked before all other checks."""
        signal = make_signal(rule_compliant=False)
        account = make_account()  # No emergency, no behavioral issues
        report = self._base_resolve(signal=signal, account=account)
        assert report.is_blocked
        assert report.blocking_level == PriorityLevel.ABSOLUTE

    def test_level2_emergency_blocks_compliant_signal(self):
        """Integration: Even a rule-compliant signal is blocked by Level 2 emergency."""
        signal = make_signal(rule_compliant=True)
        account = make_account(flash_crash_active=True)
        report = self._base_resolve(signal=signal, account=account)
        assert report.is_blocked
        assert report.blocking_level == PriorityLevel.EMERGENCY

    def test_level3_paper_gate_blocks_before_strategy(self):
        """Integration: Paper pass gate (Level 3) blocks before strategy executes."""
        signal = make_signal(rule_compliant=True)
        account = make_account()
        # Only 1 paper pass — gate blocks
        report = self._base_resolve(signal=signal, account=account, paper_passes=1)
        assert report.is_blocked
        assert report.blocking_level == PriorityLevel.RISK_MANAGEMENT

    def test_level1_beats_level2_in_priority_value(self):
        """Conflict: Level 1 is higher priority than Level 2 (lower integer value)."""
        assert PriorityLevel.ABSOLUTE < PriorityLevel.EMERGENCY
        assert PriorityLevel.ABSOLUTE < PriorityLevel.RISK_MANAGEMENT
        assert PriorityLevel.ABSOLUTE < PriorityLevel.BEHAVIORAL
        assert PriorityLevel.ABSOLUTE < PriorityLevel.STRATEGY

    def test_full_hierarchy_order(self):
        """Conflict: Full hierarchy integrity — levels 1 through 5 are ordered correctly."""
        levels = [
            PriorityLevel.ABSOLUTE,
            PriorityLevel.EMERGENCY,
            PriorityLevel.RISK_MANAGEMENT,
            PriorityLevel.BEHAVIORAL,
            PriorityLevel.STRATEGY,
        ]
        for i in range(len(levels) - 1):
            assert levels[i] < levels[i + 1], (
                f"Level {levels[i].name} must be higher priority than {levels[i+1].name}"
            )

    def test_degraded_signal_has_reduced_size(self):
        """
        Normal: DEGRADED signal still executes but at reduced parameters.
        Within 10% of profit target → Approach Protocol fires (C-02).
        The report must be DEGRADED (not BLOCKED) and final_size must be reduced
        from the original proposed_size. Kelly cap is also binding — we verify
        that final_size is strictly less than the original proposed size.
        """
        # 800/10,000 = 8% of target remaining → within 10% threshold
        account = make_account(remaining_profit_needed=800.0)
        signal = make_signal(proposed_size=0.05)   # Proposed 5% of account
        report = self.resolver.evaluate(
            signal=signal,
            account=account,
            firm_config=make_firm(minimum_position_size=0.001),
            trade_stats=make_trade_stats(),
            consecutive_paper_passes=3,
            optimal_stopping_met=True,
        )
        # Must be permitted (DEGRADED = still executes, at reduced size)
        assert report.is_permitted, f"Expected PERMITTED/DEGRADED, got: {report.summary}"
        assert report.final_decision == ClashDecision.DEGRADED
        # Final size must be smaller than the 5% originally proposed
        assert report.final_size is not None
        assert report.final_size < signal.proposed_size, (
            f"Expected final_size < {signal.proposed_size}, got {report.final_size}"
        )

    def test_report_has_summary(self):
        """Normal: Every report must have a non-empty summary."""
        report = self._base_resolve()
        assert report.summary
        assert len(report.summary) > 0

    def test_blocked_report_has_no_final_size(self):
        """Normal: BLOCKED report must not provide a position size to execute."""
        signal = make_signal(rule_compliant=False)
        report = self._base_resolve(signal=signal)
        assert report.is_blocked
        assert report.final_size is None
        assert report.final_modifier is None


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:
    """Verify that all hard-coded constants match the specification exactly."""

    def test_kelly_caps(self):
        assert KELLY_HARD_CAP_EVALUATION == 0.02
        assert KELLY_HARD_CAP_FUNDED == 0.03
        assert KELLY_HARD_CAP_FUNDED > KELLY_HARD_CAP_EVALUATION

    def test_kelly_minimum_trades(self):
        assert KELLY_MIN_TRADES_REQUIRED == 100

    def test_kelly_immature_default(self):
        assert KELLY_IMMATURE_DEFAULT == 0.005  # 0.5%

    def test_loss_floors(self):
        assert LOSS_FLOOR_ONE_LOSS == 0.75
        assert LOSS_FLOOR_TWO_LOSSES == 0.60
        assert LOSS_FLOOR_TWO_LOSSES < LOSS_FLOOR_ONE_LOSS  # 2+ losses = tighter floor

    def test_hot_hand_disabled_firms(self):
        assert "FTMO" in HOT_HAND_DISABLED_FIRMS

    def test_hot_hand_max_multiplier(self):
        assert HOT_HAND_MAX_MULTIPLIER == 1.15  # 15% max — never more

    def test_paper_pass_requirement(self):
        assert REQUIRED_CONSECUTIVE_PAPER_PASSES == 3  # THREE. Non-negotiable.

    def test_approach_thresholds(self):
        from clash_rules import APPROACH_THRESHOLD_10_PCT, APPROACH_THRESHOLD_20_PCT
        assert APPROACH_THRESHOLD_10_PCT == 0.10
        assert APPROACH_THRESHOLD_20_PCT == 0.20
        assert APPROACH_THRESHOLD_10_PCT < APPROACH_THRESHOLD_20_PCT


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    passed = failed = 0
    failures = []
    import sys
    for cls_name in sorted(dir()):
        cls = eval(cls_name)
        if not (isinstance(cls, type) and cls_name.startswith("Test")):
            continue
        inst = cls()
        for meth_name in sorted(dir(inst)):
            if not meth_name.startswith("test_"):
                continue
            try:
                if hasattr(inst, 'setup_method'):
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
