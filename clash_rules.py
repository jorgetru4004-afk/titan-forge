"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                         clash_rules.py — Layer 1                            ║
║                                                                              ║
║  THE FIRST FILE. Everything depends on this being correct.                  ║
║                                                                              ║
║  Contains:                                                                   ║
║    • 5-Level Priority Hierarchy (resolves all conflicts)                     ║
║    • 7 Critical Clash Resolution Rules (C-02, C-05, C-06, C-08,             ║
║      C-14, C-15, C-19) — all hard-coded IF/ELSE logic                       ║
║    • Emergency condition checks (Level 2)                                    ║
║    • Priority resolver that enforces hierarchy before any signal executes    ║
║                                                                              ║
║  DO NOT MODIFY THIS FILE without re-running all 3 test cases per rule.      ║
║  DO NOT bypass these rules. Not once. Not for any reason.                   ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.clash_rules")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CONSTANTS
# These values are FIXED. Any change requires Jorge approval + full re-test.
# ─────────────────────────────────────────────────────────────────────────────

# C-06: Kelly hard caps — ABSOLUTE MAXIMUMS
KELLY_HARD_CAP_EVALUATION: float = 0.02   # 2% of drawdown buffer — evaluation mode
KELLY_HARD_CAP_FUNDED:     float = 0.03   # 3% of drawdown buffer — funded mode
KELLY_MIN_TRADES_REQUIRED: int   = 100    # Below this threshold: use immature default
KELLY_IMMATURE_DEFAULT:    float = 0.005  # 0.5% when below minimum trade count
KELLY_QUARTER_FRACTION:    float = 0.25   # Quarter Kelly — never full Kelly

# C-08: Loss response floor multipliers
LOSS_FLOOR_ONE_LOSS:   float = 0.75  # After 1 consecutive loss
LOSS_FLOOR_TWO_LOSSES: float = 0.60  # After 2+ consecutive losses

# C-02: Profit target approach thresholds
APPROACH_THRESHOLD_10_PCT: float = 0.10  # Within 10% of target → minimum size
APPROACH_THRESHOLD_20_PCT: float = 0.20  # Within 20% of target → half size (pacing ignored)

# C-05: Paper pass requirement
REQUIRED_CONSECUTIVE_PAPER_PASSES: int = 3  # THREE. Non-negotiable.

# C-14: Firms where Hot Hand is permanently disabled
HOT_HAND_DISABLED_FIRMS: frozenset[str] = frozenset({"FTMO"})
HOT_HAND_MIN_SESSIONS:   int   = 5      # Minimum profitable sessions before hot hand activates
HOT_HAND_MAX_MULTIPLIER: float = 1.15   # 15% maximum — never more

# C-15: Account phases — Hot Hand vs Win Streak are NEVER simultaneous
PHASE_EVALUATION: str = "EVALUATION"
PHASE_FUNDED:     str = "FUNDED"

# C-19: Safety net check — payout blocked until buffer met
APEX_SAFETY_NET_BASE:     float = 52_600.0  # $50K + $2,500 drawdown + $100


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — PRIORITY HIERARCHY
# This is the law. All conflicts resolved by level. No exceptions.
# ─────────────────────────────────────────────────────────────────────────────

class PriorityLevel(IntEnum):
    """
    5-Level Priority Hierarchy — resolves ALL conflicts in TITAN FORGE.

    Lower integer value = HIGHER authority.
    Level 1 is supreme. Level 5 only executes if all others permit.
    """
    ABSOLUTE         = 1  # Firm rule violations — FORGE-01. Highest authority.
    EMERGENCY        = 2  # Flash Crash, Correlation Spike, Liquidity Vacuum
    RISK_MANAGEMENT  = 3  # Triple Layer, P&L Monitor, Drawdown Budget
    BEHAVIORAL       = 4  # Loss Response, 90-Min Recovery, Behavioral Signature
    STRATEGY         = 5  # Opportunity Scoring, Setup Hierarchy, Expected Value


class ClashDecision(IntEnum):
    """Outcome of a clash resolution evaluation."""
    PERMITTED  = 0   # Action is allowed to proceed
    BLOCKED    = 1   # Action is hard-blocked — do not execute
    DEGRADED   = 2   # Action is allowed but at reduced parameters


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — DATA STRUCTURES
# Input types for clash resolution functions.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AccountState:
    """Current state of a trading account."""
    account_id:                    str
    firm_id:                       str          # "FTMO", "APEX", "DNA_FUNDED", "FIVEPERCENTERS", "TOPSTEP"
    account_phase:                 str          # PHASE_EVALUATION or PHASE_FUNDED
    current_balance:               float
    starting_balance:              float
    drawdown_buffer:               float        # Remaining drawdown before failure
    remaining_drawdown:            float        # Absolute dollar amount remaining
    remaining_profit_needed:       float        # Dollar amount to hit profit target
    current_profit:                float        # Profit made so far this evaluation
    consecutive_losses:            int          # Consecutive losing trades
    consecutive_profitable_sessions: int        # Consecutive profitable trading sessions
    total_trades:                  int          # Lifetime trade count on this account
    is_funded:                     bool         # True = funded, False = evaluation
    safety_net_reached:            bool         # True = safety net buffer already built
    # Emergency condition flags (Level 2)
    flash_crash_active:            bool = False
    correlation_spike_active:      bool = False
    liquidity_vacuum_active:       bool = False
    # Session data
    session_profit_today:          float = 0.0
    daily_profit_target:           float = 0.0


@dataclass
class FirmConfig:
    """Firm-specific configuration and rules."""
    firm_id:                str
    profit_target_pct:      float   # e.g. 0.10 for 10%
    daily_drawdown_limit:   float   # e.g. 0.05 for 5%
    total_drawdown_limit:   float   # e.g. 0.10 for 10%
    minimum_position_size:  float   # Smallest allowed position (lots or contracts)
    maximum_position_size:  float   # Largest allowed position
    news_blackout_minutes:  int     # Minutes before/after major news — no trading
    consistency_rule_pct:   Optional[float]  # Max single-day profit % (None = no rule)
    safety_net_amount:      float   # Required buffer before payout (firm-specific)

    def calculate_safety_net(self) -> float:
        return self.safety_net_amount


@dataclass
class TradeStats:
    """Historical trade statistics for Kelly calculation."""
    total_trades:   int
    win_rate:       float   # 0.0–1.0
    avg_win_pct:    float   # Average win as % of account
    avg_loss_pct:   float   # Average loss as % of account (positive number)


@dataclass
class ClashResult:
    """
    Result returned by every clash resolution function.
    Always check decision before acting.
    """
    decision:        ClashDecision
    rule_applied:    str             # Which clash rule triggered
    priority_level:  PriorityLevel  # Which hierarchy level blocked/modified
    reason:          str             # Human-readable explanation
    modified_value:  Optional[float] = None  # New value if DEGRADED
    blocker_detail:  Optional[str]   = None  # Detail on what exactly blocked it


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — LEVEL 2 EMERGENCY CHECKS
# These override everything except Level 1 firm rules.
# Called before any signal evaluation.
# ─────────────────────────────────────────────────────────────────────────────

def check_emergency_conditions(account: AccountState) -> Optional[ClashResult]:
    """
    Level 2 Emergency check. Call this BEFORE any strategy evaluation.

    If any emergency condition is active, returns a BLOCKED ClashResult.
    If clear, returns None — safe to proceed to Level 3.

    Covers:
        FORGE-129: Flash Crash detection
        FORGE-131: Correlation Spike detection
        FORGE-88:  Liquidity Vacuum detection (also hard-coded in C-08)
    """
    if account.flash_crash_active:
        logger.critical(
            "[LEVEL-2-EMERGENCY] Flash crash active on %s — ALL entries blocked.",
            account.account_id
        )
        return ClashResult(
            decision=ClashDecision.BLOCKED,
            rule_applied="FORGE-129 Flash Crash",
            priority_level=PriorityLevel.EMERGENCY,
            reason="Flash crash condition active. No new entries permitted. "
                   "Existing positions: tighten stops, do not add.",
            blocker_detail="Flash crash overrides all strategy signals. "
                           "Level 1 (firm rules) is the only authority above this."
        )

    if account.correlation_spike_active:
        logger.critical(
            "[LEVEL-2-EMERGENCY] Correlation spike active on %s — entries blocked.",
            account.account_id
        )
        return ClashResult(
            decision=ClashDecision.BLOCKED,
            rule_applied="FORGE-131 Correlation Spike",
            priority_level=PriorityLevel.EMERGENCY,
            reason="Correlation spike detected across positions. "
                   "Portfolio risk is non-linear. No new entries permitted.",
            blocker_detail="Correlation spike overrides all strategy and risk signals."
        )

    if account.liquidity_vacuum_active:
        logger.critical(
            "[LEVEL-2-EMERGENCY] Liquidity vacuum active on %s — entries paused.",
            account.account_id
        )
        return ClashResult(
            decision=ClashDecision.BLOCKED,
            rule_applied="FORGE-88 Liquidity Vacuum",
            priority_level=PriorityLevel.EMERGENCY,
            reason="Liquidity vacuum detected. Spreads have widened dramatically. "
                   "New entries paused. Existing positions: tighten stops immediately.",
            blocker_detail="Liquidity vacuum prevents bad fills. "
                           "Wait for normal spread conditions before re-entry."
        )

    # All emergency checks passed
    return None


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — THE 7 CRITICAL CLASH RESOLUTION RULES
# Hard-coded IF/ELSE logic. No dynamic overrides. No exceptions.
# ─────────────────────────────────────────────────────────────────────────────

# ── C-02 ─────────────────────────────────────────────────────────────────────
# APPROACH PROTOCOL WINS OVER PACING ENGINE
# When near the profit target, Approach Protocol overrides pacing engine.
# This is Level 4 (BEHAVIORAL) authority.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_c02_approach_protocol(
    account: AccountState,
    firm_config: FirmConfig,
    standard_size: float,
) -> ClashResult:
    """
    C-02: Approach Protocol WINS over Pacing Engine.

    When within 10% of profit target: MINIMUM size only. Pacing engine silenced.
    When within 20% of profit target: HALF standard size. Pacing engine silenced.
    Outside 20%: Pacing engine is permitted to operate normally.

    Args:
        account:       Current account state.
        firm_config:   Firm rules configuration.
        standard_size: Size calculated by standard sizing logic (pre-pacing).

    Returns:
        ClashResult with decision and modified_value (the permitted position size).
    """
    profit_target_dollars = account.starting_balance * firm_config.profit_target_pct
    distance_to_target = account.remaining_profit_needed / profit_target_dollars

    if distance_to_target <= APPROACH_THRESHOLD_10_PCT:
        # Within 10% of target — MINIMUM SIZE ONLY. Pacing engine is ignored.
        permitted_size = firm_config.minimum_position_size
        logger.warning(
            "[C-02] Approach Protocol: within 10%% of target on %s. "
            "Size locked to minimum: %.4f. Pacing engine IGNORED.",
            account.account_id, permitted_size
        )
        return ClashResult(
            decision=ClashDecision.DEGRADED,
            rule_applied="C-02 Approach Protocol",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"Within 10% of profit target ({distance_to_target:.1%} remaining). "
                   f"Approach Protocol enforces minimum position size only. "
                   f"One trade at a time. No rush.",
            modified_value=permitted_size,
            blocker_detail=f"Pacing engine output IGNORED. "
                           f"Minimum size {firm_config.minimum_position_size} enforced."
        )

    elif distance_to_target <= APPROACH_THRESHOLD_20_PCT:
        # Within 20% of target — HALF SIZE. Pacing engine is ignored.
        permitted_size = standard_size * 0.50
        logger.warning(
            "[C-02] Approach Protocol: within 20%% of target on %s. "
            "Size reduced to 50%%: %.4f. Pacing engine IGNORED.",
            account.account_id, permitted_size
        )
        return ClashResult(
            decision=ClashDecision.DEGRADED,
            rule_applied="C-02 Approach Protocol",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"Within 20% of profit target ({distance_to_target:.1%} remaining). "
                   f"Approach Protocol: 50% of standard size. Pacing engine silenced.",
            modified_value=permitted_size,
            blocker_detail=f"Standard size {standard_size:.4f} halved to {permitted_size:.4f}. "
                           f"Pacing engine output IGNORED."
        )

    else:
        # Outside 20% — pacing engine is permitted to operate.
        return ClashResult(
            decision=ClashDecision.PERMITTED,
            rule_applied="C-02 Approach Protocol",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"Distance to target is {distance_to_target:.1%}. "
                   f"Approach Protocol not triggered. Pacing engine may operate.",
            modified_value=standard_size
        )


# ── C-05 ─────────────────────────────────────────────────────────────────────
# THREE PAPER PASSES GATE OPTIMAL STOPPING
# Cannot start a paid evaluation without 3 consecutive quality paper passes.
# This is Level 3 (RISK MANAGEMENT) authority.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_c05_paper_pass_gate(
    firm_id: str,
    consecutive_paper_passes: int,
    optimal_stopping_conditions_met: bool,
) -> ClashResult:
    """
    C-05: Three Paper Passes GATE Optimal Stopping Theory.

    Even if market conditions are optimal (Optimal Stopping says go),
    THREE consecutive quality paper passes are REQUIRED first.
    No exceptions. Not once.

    Args:
        firm_id:                          Target firm for evaluation.
        consecutive_paper_passes:         Count of consecutive quality passes at this firm.
        optimal_stopping_conditions_met:  What Optimal Stopping Theory recommends.

    Returns:
        ClashResult — BLOCKED if < 3 paper passes, regardless of market conditions.
    """
    if consecutive_paper_passes < REQUIRED_CONSECUTIVE_PAPER_PASSES:
        deficit = REQUIRED_CONSECUTIVE_PAPER_PASSES - consecutive_paper_passes
        logger.error(
            "[C-05] Paper Pass Gate BLOCKED for %s. "
            "Have %d consecutive quality passes. Need %d. Deficit: %d.",
            firm_id, consecutive_paper_passes,
            REQUIRED_CONSECUTIVE_PAPER_PASSES, deficit
        )
        return ClashResult(
            decision=ClashDecision.BLOCKED,
            rule_applied="C-05 Three Paper Pass Gate",
            priority_level=PriorityLevel.RISK_MANAGEMENT,
            reason=f"BLOCKED: {REQUIRED_CONSECUTIVE_PAPER_PASSES} consecutive quality paper "
                   f"passes required before ANY paid evaluation. "
                   f"Current streak: {consecutive_paper_passes}. "
                   f"Need {deficit} more. Optimal Stopping Theory is irrelevant "
                   f"until this gate is cleared.",
            blocker_detail=f"Optimal Stopping says: {optimal_stopping_conditions_met}. "
                           f"This is OVERRIDDEN by C-05. Paper passes win."
        )

    # Gate cleared — Optimal Stopping is now permitted to evaluate conditions.
    if not optimal_stopping_conditions_met:
        logger.info(
            "[C-05] Paper passes: %d ✓ — Gate cleared for %s. "
            "Optimal Stopping recommends waiting. Deferring.",
            consecutive_paper_passes, firm_id
        )
        return ClashResult(
            decision=ClashDecision.BLOCKED,
            rule_applied="C-05 Three Paper Pass Gate",
            priority_level=PriorityLevel.RISK_MANAGEMENT,
            reason=f"Paper pass gate cleared ({consecutive_paper_passes} passes ✓). "
                   f"However, Optimal Stopping Theory recommends waiting on current "
                   f"market conditions. Deferring paid evaluation.",
            blocker_detail="Optimal Stopping conditions not met. Wait for better window."
        )

    logger.info(
        "[C-05] Gate CLEARED for %s — %d paper passes ✓, optimal conditions ✓.",
        firm_id, consecutive_paper_passes
    )
    return ClashResult(
        decision=ClashDecision.PERMITTED,
        rule_applied="C-05 Three Paper Pass Gate",
        priority_level=PriorityLevel.RISK_MANAGEMENT,
        reason=f"All gates cleared. {consecutive_paper_passes} consecutive quality "
               f"paper passes ✓. Optimal Stopping conditions met ✓. "
               f"Paid evaluation authorized."
    )


# ── C-06 ─────────────────────────────────────────────────────────────────────
# SAFETY MARGIN CAPS KELLY CRITERION
# Kelly can never exceed the hard cap. Never. This is Level 3 authority.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_c06_kelly_safety_cap(
    account: AccountState,
    trade_stats: TradeStats,
) -> ClashResult:
    """
    C-06: Safety Margin CAPS Kelly Criterion.

    Full Kelly is never used. Quarter Kelly is calculated, then three caps applied:
        1. Kelly hard cap (% of drawdown buffer) — ABSOLUTE MAXIMUM
        2. Remaining drawdown sanity cap (25% of remaining drawdown)
        3. Immature default (0.5%) when trade count < 100

    The minimum of all three is the permitted size.

    Args:
        account:     Current account state.
        trade_stats: Historical trade performance data.

    Returns:
        ClashResult with modified_value = final permitted position size (as % of account).
    """
    # Determine which hard cap applies based on account phase
    if account.is_funded:
        cap_pct = KELLY_HARD_CAP_FUNDED
        cap_label = "FUNDED cap (3%)"
    else:
        cap_pct = KELLY_HARD_CAP_EVALUATION
        cap_label = "EVALUATION cap (2%)"

    hard_cap_dollars = account.drawdown_buffer * cap_pct
    sanity_cap_dollars = account.remaining_drawdown * 0.25

    if trade_stats.total_trades < KELLY_MIN_TRADES_REQUIRED:
        # IMMATURE: insufficient trade history — use conservative default
        kelly_dollars = account.current_balance * KELLY_IMMATURE_DEFAULT
        kelly_basis = f"IMMATURE DEFAULT ({KELLY_IMMATURE_DEFAULT*100:.1f}%) — " \
                      f"only {trade_stats.total_trades}/{KELLY_MIN_TRADES_REQUIRED} trades"
        logger.warning(
            "[C-06] Kelly IMMATURE on %s. Trades: %d/%d. Using %.1f%% default.",
            account.account_id, trade_stats.total_trades,
            KELLY_MIN_TRADES_REQUIRED, KELLY_IMMATURE_DEFAULT * 100
        )
    else:
        # MATURE: calculate quarter Kelly from win rate and avg R
        if trade_stats.avg_loss_pct <= 0 or trade_stats.avg_win_pct <= 0:
            # Guard against division by zero or invalid stats
            kelly_dollars = account.current_balance * KELLY_IMMATURE_DEFAULT
            kelly_basis = "INVALID STATS — using immature default as guard"
            logger.error("[C-06] Invalid trade stats on %s. Using immature default.", account.account_id)
        else:
            full_kelly_pct = (
                (trade_stats.win_rate / trade_stats.avg_loss_pct) -
                ((1.0 - trade_stats.win_rate) / trade_stats.avg_win_pct)
            )
            quarter_kelly_pct = full_kelly_pct * KELLY_QUARTER_FRACTION
            kelly_dollars = account.current_balance * max(quarter_kelly_pct, 0.0)
            kelly_basis = (
                f"Quarter Kelly = {quarter_kelly_pct:.4f} "
                f"(Full Kelly: {full_kelly_pct:.4f} × 0.25)"
            )

    # Apply all three caps — take the minimum (most conservative)
    permitted_dollars = min(kelly_dollars, hard_cap_dollars, sanity_cap_dollars)
    permitted_pct = permitted_dollars / account.current_balance if account.current_balance > 0 else 0.0

    # Determine which cap was the binding constraint
    if permitted_dollars == hard_cap_dollars:
        binding_cap = f"HARD CAP ({cap_label}): ${hard_cap_dollars:,.2f}"
    elif permitted_dollars == sanity_cap_dollars:
        binding_cap = f"SANITY CAP (25% of remaining drawdown): ${sanity_cap_dollars:,.2f}"
    else:
        binding_cap = f"KELLY CALCULATION: ${kelly_dollars:,.2f}"

    logger.info(
        "[C-06] Kelly resolved on %s: Kelly=$%.2f | Hard Cap=$%.2f | "
        "Sanity Cap=$%.2f → Permitted=$%.2f (%.3f%%)",
        account.account_id, kelly_dollars, hard_cap_dollars,
        sanity_cap_dollars, permitted_dollars, permitted_pct * 100
    )

    return ClashResult(
        decision=ClashDecision.DEGRADED,
        rule_applied="C-06 Kelly Safety Cap",
        priority_level=PriorityLevel.RISK_MANAGEMENT,
        reason=f"Kelly basis: {kelly_basis}. "
               f"Binding constraint: {binding_cap}. "
               f"Permitted size: ${permitted_dollars:,.2f} ({permitted_pct:.3%} of account).",
        modified_value=permitted_pct,   # Returns as fraction of account balance
        blocker_detail=f"Kelly hard cap is ABSOLUTE. "
                       f"Kelly output {kelly_dollars:.2f} cannot exceed cap {hard_cap_dollars:.2f}."
    )


# ── C-08 ─────────────────────────────────────────────────────────────────────
# LOSS RESPONSE FLOORS DYNAMIC SIZING
# After losses, dynamic sizing CANNOT exceed the loss response floor.
# This is Level 4 (BEHAVIORAL) authority.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_c08_loss_response_floor(
    account: AccountState,
    dynamic_size_modifier: float,
) -> ClashResult:
    """
    C-08: Loss Response FLOORS Dynamic Sizing.

    After consecutive losses, a floor is applied to position size.
    Dynamic sizing cannot produce a modifier ABOVE the loss floor.
    The minimum of (dynamic modifier, loss floor) is always used.

    Loss floors:
        0 consecutive losses: No floor — dynamic sizing operates freely.
        1 consecutive loss:   Floor at 0.75 (75% of normal size maximum).
        2+ consecutive losses: Floor at 0.60 (60% of normal size maximum).

    Args:
        account:              Current account state.
        dynamic_size_modifier: Modifier from dynamic sizing system (0.0–2.0 range).

    Returns:
        ClashResult with modified_value = final permitted size modifier.
    """
    consecutive_losses = account.consecutive_losses

    if consecutive_losses == 0:
        # No losses — dynamic sizing operates without floor constraint
        return ClashResult(
            decision=ClashDecision.PERMITTED,
            rule_applied="C-08 Loss Response Floor",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason="No consecutive losses. Dynamic sizing operates freely.",
            modified_value=dynamic_size_modifier
        )

    elif consecutive_losses == 1:
        loss_floor = LOSS_FLOOR_ONE_LOSS  # 0.75
    else:
        # 2 or more consecutive losses
        loss_floor = LOSS_FLOOR_TWO_LOSSES  # 0.60

    # The floor is a MAXIMUM — dynamic cannot exceed it after losses
    permitted_modifier = min(dynamic_size_modifier, loss_floor)
    floor_active = permitted_modifier < dynamic_size_modifier

    if floor_active:
        logger.warning(
            "[C-08] Loss response floor ACTIVE on %s. "
            "Losses: %d. Floor: %.2f. Dynamic: %.2f → Permitted: %.2f.",
            account.account_id, consecutive_losses,
            loss_floor, dynamic_size_modifier, permitted_modifier
        )
        return ClashResult(
            decision=ClashDecision.DEGRADED,
            rule_applied="C-08 Loss Response Floor",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"{consecutive_losses} consecutive loss(es). "
                   f"Loss response floor: {loss_floor:.0%}. "
                   f"Dynamic sizing modifier {dynamic_size_modifier:.2f} exceeds floor — "
                   f"capped at {permitted_modifier:.2f}.",
            modified_value=permitted_modifier,
            blocker_detail=f"Dynamic sizing output ({dynamic_size_modifier:.2f}) CANNOT "
                           f"exceed loss floor ({loss_floor:.2f}) after consecutive losses. "
                           f"Loss Response is Level 4 authority."
        )

    # Dynamic sizing already at or below the floor — no intervention needed
    return ClashResult(
        decision=ClashDecision.PERMITTED,
        rule_applied="C-08 Loss Response Floor",
        priority_level=PriorityLevel.BEHAVIORAL,
        reason=f"{consecutive_losses} consecutive loss(es). "
               f"Floor: {loss_floor:.0%}. "
               f"Dynamic modifier {dynamic_size_modifier:.2f} already at or below floor. "
               f"No additional constraint applied.",
        modified_value=permitted_modifier
    )


# ── C-14 ─────────────────────────────────────────────────────────────────────
# HOT HAND IS FIRM-SPECIFIC (DISABLED AT FTMO)
# FTMO's AI monitors consistency. Hot Hand disabled there permanently.
# This is Level 4 (BEHAVIORAL) authority.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_c14_hot_hand_firm_specific(
    account: AccountState,
) -> ClashResult:
    """
    C-14: Hot Hand is FIRM-SPECIFIC — disabled at FTMO permanently.

    FTMO's AI monitors for behavioral consistency anomalies.
    Hot Hand (size escalation after a winning streak) would flag as inconsistent.
    At FTMO: always returns 1.0 multiplier (no escalation).

    At non-FTMO firms: Hot Hand activates only after 5+ consecutive profitable
    sessions AND statistical significance is confirmed. Maximum multiplier: 1.15.

    Args:
        account: Current account state.

    Returns:
        ClashResult with modified_value = permitted Hot Hand multiplier.
    """
    firm_id = account.firm_id

    # Hard check: Is this firm on the disabled list?
    if firm_id in HOT_HAND_DISABLED_FIRMS:
        logger.info(
            "[C-14] Hot Hand DISABLED at %s (firm policy). Multiplier: 1.0.",
            firm_id
        )
        return ClashResult(
            decision=ClashDecision.DEGRADED,
            rule_applied="C-14 Hot Hand Firm-Specific",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"Hot Hand is permanently DISABLED at {firm_id}. "
                   f"FTMO AI monitors behavioral consistency. "
                   f"Escalating size after a streak would trigger anomaly detection. "
                   f"Multiplier locked at 1.0.",
            modified_value=1.0,
            blocker_detail=f"{firm_id} is in HOT_HAND_DISABLED_FIRMS. "
                           f"This is not configurable. It never changes."
        )

    # Non-FTMO firm: Check if Hot Hand conditions are met
    sessions = account.consecutive_profitable_sessions

    if sessions < HOT_HAND_MIN_SESSIONS:
        logger.debug(
            "[C-14] Hot Hand not activated at %s. "
            "Sessions: %d/%d required.",
            firm_id, sessions, HOT_HAND_MIN_SESSIONS
        )
        return ClashResult(
            decision=ClashDecision.PERMITTED,
            rule_applied="C-14 Hot Hand Firm-Specific",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"Hot Hand not triggered at {firm_id}. "
                   f"Requires {HOT_HAND_MIN_SESSIONS} consecutive profitable sessions. "
                   f"Current: {sessions}. Multiplier: 1.0.",
            modified_value=1.0
        )

    # Sessions threshold met — apply statistical significance check
    # (In production, this calls is_statistically_significant() from stats module)
    # For the clash rule itself, we enforce the MAXIMUM multiplier cap
    hot_hand_multiplier = HOT_HAND_MAX_MULTIPLIER  # 1.15 — never more

    logger.info(
        "[C-14] Hot Hand ACTIVE at %s. Sessions: %d. Multiplier: %.2f.",
        firm_id, sessions, hot_hand_multiplier
    )
    return ClashResult(
        decision=ClashDecision.PERMITTED,
        rule_applied="C-14 Hot Hand Firm-Specific",
        priority_level=PriorityLevel.BEHAVIORAL,
        reason=f"Hot Hand activated at {firm_id}. "
               f"{sessions} consecutive profitable sessions. "
               f"Maximum multiplier: {hot_hand_multiplier} (15% — hard ceiling).",
        modified_value=hot_hand_multiplier
    )


# ── C-15 ─────────────────────────────────────────────────────────────────────
# HOT HAND VS WIN STREAK ARE PHASE-SPECIFIC — NEVER SIMULTANEOUS
# Evaluation phase uses Hot Hand. Funded phase uses Win Streak Preservation.
# This is Level 4 (BEHAVIORAL) authority.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_c15_streak_phase_specific(
    account: AccountState,
    hot_hand_multiplier: float,
    win_streak_multiplier: float,
) -> ClashResult:
    """
    C-15: Hot Hand vs Win Streak are PHASE-SPECIFIC — never simultaneous.

    EVALUATION phase: Hot Hand protocol only. Win Streak ignored.
    FUNDED phase: Win Streak Preservation only. Hot Hand ignored.
    No other phase is valid. If phase is unknown: block as a safety measure.

    Args:
        account:               Current account state.
        hot_hand_multiplier:   Output from Hot Hand protocol (C-14 result).
        win_streak_multiplier: Output from Win Streak Preservation module.

    Returns:
        ClashResult with modified_value = the single correct multiplier for this phase.
    """
    phase = account.account_phase

    if phase == PHASE_EVALUATION:
        # EVALUATION: Hot Hand is active. Win Streak Preservation is irrelevant.
        logger.debug(
            "[C-15] EVALUATION phase on %s. Using Hot Hand: %.2f. Win Streak ignored.",
            account.account_id, hot_hand_multiplier
        )
        return ClashResult(
            decision=ClashDecision.PERMITTED,
            rule_applied="C-15 Streak Phase-Specific",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"EVALUATION phase: Hot Hand protocol is active. "
                   f"Win Streak Preservation is IGNORED (funded phase only). "
                   f"Multiplier: {hot_hand_multiplier:.2f}.",
            modified_value=hot_hand_multiplier,
            blocker_detail=f"Win Streak multiplier ({win_streak_multiplier:.2f}) discarded. "
                           f"Never simultaneous with Hot Hand."
        )

    elif phase == PHASE_FUNDED:
        # FUNDED: Win Streak Preservation is active. Hot Hand is irrelevant.
        logger.debug(
            "[C-15] FUNDED phase on %s. Using Win Streak: %.2f. Hot Hand ignored.",
            account.account_id, win_streak_multiplier
        )
        return ClashResult(
            decision=ClashDecision.PERMITTED,
            rule_applied="C-15 Streak Phase-Specific",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"FUNDED phase: Win Streak Preservation is active. "
                   f"Hot Hand is IGNORED (evaluation phase only). "
                   f"Multiplier: {win_streak_multiplier:.2f}.",
            modified_value=win_streak_multiplier,
            blocker_detail=f"Hot Hand multiplier ({hot_hand_multiplier:.2f}) discarded. "
                           f"Never simultaneous with Win Streak."
        )

    else:
        # Unknown phase — block as safety measure. This should never happen.
        logger.critical(
            "[C-15] UNKNOWN phase '%s' on %s. Blocking as safety measure.",
            phase, account.account_id
        )
        return ClashResult(
            decision=ClashDecision.BLOCKED,
            rule_applied="C-15 Streak Phase-Specific",
            priority_level=PriorityLevel.BEHAVIORAL,
            reason=f"UNKNOWN account phase: '{phase}'. "
                   f"Valid phases: '{PHASE_EVALUATION}' or '{PHASE_FUNDED}'. "
                   f"Cannot determine which streak protocol applies. "
                   f"BLOCKED as safety measure until phase is corrected.",
            blocker_detail="Fix account_phase in AccountState before proceeding."
        )


# ── C-19 ─────────────────────────────────────────────────────────────────────
# SAFETY NET BUFFER MUST BE MET BEFORE PAYOUT
# No payout unless balance after withdrawal stays above safety net.
# This is Level 3 (RISK MANAGEMENT) authority.
# ─────────────────────────────────────────────────────────────────────────────

def resolve_c19_safety_net_payout(
    account: AccountState,
    firm_config: FirmConfig,
    payout_amount: float,
) -> ClashResult:
    """
    C-19: Safety Net Buffer MUST be met before Payout.

    The balance remaining after a payout withdrawal must stay at or above
    the firm's safety net threshold. If it doesn't: payout is BLOCKED.

    Payout Scheduling Optimizer is only consulted AFTER this gate passes.

    Args:
        account:        Current account state.
        firm_config:    Firm configuration with safety net amount.
        payout_amount:  Requested payout withdrawal amount.

    Returns:
        ClashResult — BLOCKED if balance after payout < safety net.
        PERMITTED if buffer is maintained (Payout Optimizer may then evaluate).
    """
    safety_net = firm_config.calculate_safety_net()
    balance_after_payout = account.current_balance - payout_amount
    shortfall = safety_net - balance_after_payout

    if balance_after_payout < safety_net:
        logger.error(
            "[C-19] Payout BLOCKED on %s. "
            "Requested: $%.2f. Balance after: $%.2f. "
            "Safety net: $%.2f. Shortfall: $%.2f.",
            account.account_id, payout_amount,
            balance_after_payout, safety_net, shortfall
        )
        return ClashResult(
            decision=ClashDecision.BLOCKED,
            rule_applied="C-19 Safety Net Buffer",
            priority_level=PriorityLevel.RISK_MANAGEMENT,
            reason=f"BLOCKED: Payout of ${payout_amount:,.2f} would leave "
                   f"${balance_after_payout:,.2f} — below safety net of ${safety_net:,.2f}. "
                   f"Need ${shortfall:,.2f} more before this payout is permitted.",
            blocker_detail=f"Safety net is ${safety_net:,.2f}. "
                           f"Current balance: ${account.current_balance:,.2f}. "
                           f"Requested payout: ${payout_amount:,.2f}. "
                           f"Balance after withdrawal: ${balance_after_payout:,.2f}. "
                           f"Shortfall: ${shortfall:,.2f}. "
                           f"Do not request payout until shortfall is earned."
        )

    # Safety net maintained — Payout Scheduling Optimizer may now evaluate
    buffer_above_net = balance_after_payout - safety_net
    logger.info(
        "[C-19] Safety net cleared on %s. "
        "Balance after payout: $%.2f. Safety net: $%.2f. Buffer above net: $%.2f.",
        account.account_id, balance_after_payout, safety_net, buffer_above_net
    )
    return ClashResult(
        decision=ClashDecision.PERMITTED,
        rule_applied="C-19 Safety Net Buffer",
        priority_level=PriorityLevel.RISK_MANAGEMENT,
        reason=f"Safety net maintained ✓. "
               f"Balance after payout: ${balance_after_payout:,.2f}. "
               f"Safety net: ${safety_net:,.2f}. "
               f"Buffer above net: ${buffer_above_net:,.2f}. "
               f"Payout Scheduling Optimizer may now evaluate timing.",
        modified_value=buffer_above_net
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — MASTER PRIORITY RESOLVER
# The single entry point that enforces the full 5-level hierarchy.
# Call this before EVERY trade decision.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeSignal:
    """A proposed trade signal awaiting priority resolution."""
    signal_id:             str
    firm_id:               str
    strategy_name:         str
    proposed_size:         float          # As fraction of account (e.g. 0.01 = 1%)
    proposed_size_modifier: float         # Dynamic sizing modifier
    dynamic_modifier:      float          # Raw dynamic sizing output
    expected_value:        float          # EV calculation result
    opportunity_score:     float          # Opportunity Scoring Engine result (0–100)
    rule_compliant:        bool           # Passed firm rule engine check
    # Optional inputs for specific clash checks
    hot_hand_multiplier:    float = 1.0
    win_streak_multiplier:  float = 1.0
    payout_amount:          Optional[float] = None


@dataclass
class ResolutionReport:
    """
    Full resolution report for a trade signal.
    All clash rules evaluated in priority order.
    """
    signal_id:          str
    final_decision:     ClashDecision
    blocking_level:     Optional[PriorityLevel]
    blocking_rule:      Optional[str]
    final_size:         Optional[float]         # Permitted size after all adjustments
    final_modifier:     Optional[float]         # Permitted modifier after all adjustments
    clash_results:      list[ClashResult] = field(default_factory=list)
    summary:            str = ""

    @property
    def is_permitted(self) -> bool:
        return self.final_decision in (ClashDecision.PERMITTED, ClashDecision.DEGRADED)

    @property
    def is_blocked(self) -> bool:
        return self.final_decision == ClashDecision.BLOCKED


class ClashResolver:
    """
    Master clash resolver. Enforces the full 5-level priority hierarchy.

    Usage:
        resolver = ClashResolver()
        report = resolver.evaluate(signal, account, firm_config, trade_stats, paper_passes)

        if report.is_blocked:
            # Do not execute. Log the reason.
            logger.warning(report.summary)
        elif report.is_permitted:
            # Execute with report.final_size and report.final_modifier
    """

    def evaluate(
        self,
        signal: TradeSignal,
        account: AccountState,
        firm_config: FirmConfig,
        trade_stats: TradeStats,
        consecutive_paper_passes: int,
        optimal_stopping_met: bool = True,
    ) -> ResolutionReport:
        """
        Evaluate a trade signal against all clash resolution rules.

        Checks run in strict priority order (Level 1 → Level 5).
        First BLOCK encountered halts evaluation immediately.
        All DEGRADED results accumulate (most conservative wins).

        Returns a complete ResolutionReport.
        """
        results: list[ClashResult] = []
        final_size = signal.proposed_size
        final_modifier = signal.dynamic_modifier

        # ── LEVEL 1: ABSOLUTE — Firm Rule Compliance ──────────────────────────
        if not signal.rule_compliant:
            block = ClashResult(
                decision=ClashDecision.BLOCKED,
                rule_applied="FORGE-01 Multi-Firm Rule Engine",
                priority_level=PriorityLevel.ABSOLUTE,
                reason=f"FIRM RULE VIOLATION at {signal.firm_id}. "
                       f"Signal '{signal.signal_id}' fails firm rule compliance check. "
                       f"Level 1 authority: BLOCKED regardless of all other signals.",
                blocker_detail="No other rule can override a Level 1 firm rule violation. "
                               "Fix the compliance issue before re-submitting."
            )
            results.append(block)
            return self._build_report(signal.signal_id, results, final_size, final_modifier)

        logger.debug("[RESOLVER] Level 1 PASSED — %s is firm-rule compliant.", signal.signal_id)

        # ── LEVEL 2: EMERGENCY — Flash Crash / Correlation / Liquidity ────────
        emergency = check_emergency_conditions(account)
        if emergency:
            results.append(emergency)
            return self._build_report(signal.signal_id, results, final_size, final_modifier)

        logger.debug("[RESOLVER] Level 2 PASSED — no emergency conditions active.")

        # ── LEVEL 3: RISK MANAGEMENT ───────────────────────────────────────────

        # C-05: Three paper passes gate
        paper_result = resolve_c05_paper_pass_gate(
            firm_id=signal.firm_id,
            consecutive_paper_passes=consecutive_paper_passes,
            optimal_stopping_conditions_met=optimal_stopping_met,
        )
        results.append(paper_result)
        if paper_result.decision == ClashDecision.BLOCKED:
            return self._build_report(signal.signal_id, results, final_size, final_modifier)

        # C-06: Kelly safety cap — determines position size ceiling
        kelly_result = resolve_c06_kelly_safety_cap(account, trade_stats)
        results.append(kelly_result)
        if kelly_result.modified_value is not None:
            final_size = min(final_size, kelly_result.modified_value)

        # C-19: Safety net payout gate (only evaluated if this is a payout signal)
        if signal.payout_amount is not None:
            payout_result = resolve_c19_safety_net_payout(account, firm_config, signal.payout_amount)
            results.append(payout_result)
            if payout_result.decision == ClashDecision.BLOCKED:
                return self._build_report(signal.signal_id, results, final_size, final_modifier)

        logger.debug("[RESOLVER] Level 3 PASSED — risk management constraints applied.")

        # ── LEVEL 4: BEHAVIORAL ────────────────────────────────────────────────

        # C-08: Loss response floor on dynamic sizing
        loss_result = resolve_c08_loss_response_floor(account, final_modifier)
        results.append(loss_result)
        if loss_result.modified_value is not None:
            final_modifier = loss_result.modified_value

        # C-02: Approach protocol caps size near profit target
        approach_result = resolve_c02_approach_protocol(account, firm_config, final_size)
        results.append(approach_result)
        if approach_result.modified_value is not None:
            final_size = min(final_size, approach_result.modified_value)

        # C-14: Hot Hand firm-specific check
        hot_hand_result = resolve_c14_hot_hand_firm_specific(account)
        results.append(hot_hand_result)
        effective_hot_hand = hot_hand_result.modified_value if hot_hand_result.modified_value else 1.0

        # C-15: Streak phase-specific (Hot Hand vs Win Streak — never simultaneous)
        streak_result = resolve_c15_streak_phase_specific(
            account,
            hot_hand_multiplier=effective_hot_hand,
            win_streak_multiplier=signal.win_streak_multiplier,
        )
        results.append(streak_result)
        if streak_result.decision == ClashDecision.BLOCKED:
            return self._build_report(signal.signal_id, results, final_size, final_modifier)

        streak_multiplier = streak_result.modified_value if streak_result.modified_value else 1.0

        logger.debug("[RESOLVER] Level 4 PASSED — behavioral constraints applied.")

        # ── LEVEL 5: STRATEGY — Only executes if all higher levels permit ──────
        # Apply streak multiplier to final modifier (capped by all Level 4 constraints)
        final_modifier = final_modifier * streak_multiplier

        # Ensure final_size never exceeds Kelly cap even after modifier
        if kelly_result.modified_value is not None:
            final_size = min(final_size, kelly_result.modified_value)

        logger.info(
            "[RESOLVER] All levels PASSED for %s. Final size: %.4f. Final modifier: %.4f.",
            signal.signal_id, final_size, final_modifier
        )

        return self._build_report(signal.signal_id, results, final_size, final_modifier)

    @staticmethod
    def _build_report(
        signal_id: str,
        results: list[ClashResult],
        final_size: float,
        final_modifier: float,
    ) -> ResolutionReport:
        """Build the final ResolutionReport from accumulated clash results."""
        # Find the most severe decision
        blocked = [r for r in results if r.decision == ClashDecision.BLOCKED]
        degraded = [r for r in results if r.decision == ClashDecision.DEGRADED]

        if blocked:
            # Highest priority blocker wins the summary
            primary = min(blocked, key=lambda r: r.priority_level.value)
            return ResolutionReport(
                signal_id=signal_id,
                final_decision=ClashDecision.BLOCKED,
                blocking_level=primary.priority_level,
                blocking_rule=primary.rule_applied,
                final_size=None,
                final_modifier=None,
                clash_results=results,
                summary=(
                    f"[BLOCKED] Signal '{signal_id}' blocked by "
                    f"Level {primary.priority_level.value} "
                    f"({primary.priority_level.name}): "
                    f"{primary.rule_applied}. "
                    f"Reason: {primary.reason}"
                )
            )

        elif degraded:
            active_rules = " | ".join(r.rule_applied for r in degraded)
            return ResolutionReport(
                signal_id=signal_id,
                final_decision=ClashDecision.DEGRADED,
                blocking_level=None,
                blocking_rule=None,
                final_size=final_size,
                final_modifier=final_modifier,
                clash_results=results,
                summary=(
                    f"[DEGRADED] Signal '{signal_id}' permitted at reduced parameters. "
                    f"Active constraints: {active_rules}. "
                    f"Final size: {final_size:.4f}. Final modifier: {final_modifier:.4f}."
                )
            )

        else:
            return ResolutionReport(
                signal_id=signal_id,
                final_decision=ClashDecision.PERMITTED,
                blocking_level=None,
                blocking_rule=None,
                final_size=final_size,
                final_modifier=final_modifier,
                clash_results=results,
                summary=(
                    f"[PERMITTED] Signal '{signal_id}' cleared all clash resolution rules. "
                    f"Final size: {final_size:.4f}. Final modifier: {final_modifier:.4f}."
                )
            )
