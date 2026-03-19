"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                    recovery_protocols.py — Layer 3                          ║
║  FORGE-42: Failure Fast-Forward Protocol (FX-10)                            ║
║    RCA in 4h. Fix in 24h. Validated in 48h. New eval in 72h.               ║
║  FORGE-43: Capital Defense Architecture                                     ║
║    Never risk a funded account to fund a new evaluation.                    ║
║  FORGE-47: Evaluation Warmup Protocol                                       ║
║    FTMO $10K warmup before $100K. 3 days minimum.                          ║
║  FORGE-55: Drawdown Recovery Protocol                                       ║
║    After drawdown event: systematic recovery without overcompensating.      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.recovery_protocols")


# ── FORGE-42: Failure Fast-Forward Protocol (FX-10) ──────────────────────────

class FailurePhase(Enum):
    INITIAL          = auto()   # Just failed
    RCA_IN_PROGRESS  = auto()   # Root cause analysis (0–4h)
    FIX_IN_PROGRESS  = auto()   # Implementing fix (4h–24h)
    VALIDATION       = auto()   # Validating fix (24h–48h)
    READY_FOR_EVAL   = auto()   # New evaluation authorized (72h+)
    COMPLETE         = auto()   # Passed the re-evaluation

@dataclass
class FailureProtocol:
    """FX-10: Failure Fast-Forward Protocol tracker."""
    failure_id:     str
    firm_id:        str
    failed_at:      datetime
    failure_reason: str
    # Milestones
    rca_deadline:   datetime    # +4h
    fix_deadline:   datetime    # +24h
    validate_deadline: datetime # +48h
    eval_authorized: datetime   # +72h
    # Current state
    current_phase:  FailurePhase
    rca_completed:  bool
    fix_implemented: bool
    fix_validated:  bool
    notes:          str

    @property
    def hours_since_failure(self) -> float:
        return (datetime.now(timezone.utc) - self.failed_at).total_seconds() / 3600

    def advance_phase(self, as_of: Optional[datetime] = None) -> FailurePhase:
        now = as_of or datetime.now(timezone.utc)
        if now >= self.eval_authorized and self.fix_validated:
            self.current_phase = FailurePhase.READY_FOR_EVAL
        elif now >= self.validate_deadline and self.fix_implemented:
            self.current_phase = FailurePhase.VALIDATION
        elif now >= self.fix_deadline and self.rca_completed:
            self.current_phase = FailurePhase.FIX_IN_PROGRESS
        elif now >= self.rca_deadline:
            self.current_phase = FailurePhase.RCA_IN_PROGRESS
        return self.current_phase

    def status_line(self) -> str:
        h = self.hours_since_failure
        return (
            f"FX-10 [{self.failure_id}] Phase: {self.current_phase.name} "
            f"| {h:.1f}h since failure "
            f"| RCA: {'✓' if self.rca_completed else f'due +4h'} "
            f"| Fix: {'✓' if self.fix_implemented else f'due +24h'} "
            f"| Validated: {'✓' if self.fix_validated else f'due +48h'} "
            f"| New eval: +72h"
        )


def create_failure_protocol(
    failure_id:     str,
    firm_id:        str,
    reason:         str,
    failed_at:      Optional[datetime] = None,
) -> FailureProtocol:
    """FX-10: Initialize a Failure Fast-Forward Protocol."""
    now = failed_at or datetime.now(timezone.utc)
    logger.error(
        "[FORGE-42][FX-10] ❌ Evaluation failed: %s at %s. "
        "FX-10 activated. RCA due: %s. New eval authorized: %s.",
        reason, firm_id,
        (now + timedelta(hours=4)).strftime("%H:%M UTC"),
        (now + timedelta(hours=72)).strftime("%H:%M UTC"),
    )
    return FailureProtocol(
        failure_id=failure_id, firm_id=firm_id,
        failed_at=now, failure_reason=reason,
        rca_deadline=now + timedelta(hours=4),
        fix_deadline=now + timedelta(hours=24),
        validate_deadline=now + timedelta(hours=48),
        eval_authorized=now + timedelta(hours=72),
        current_phase=FailurePhase.INITIAL,
        rca_completed=False, fix_implemented=False, fix_validated=False,
        notes="",
    )


# ── FORGE-43: Capital Defense Architecture ────────────────────────────────────
# A funded account is sacred. Never risk it to fund a new evaluation.

@dataclass
class CapitalDefenseCheck:
    """FORGE-43: Ensures capital deployment never risks funded accounts."""
    new_eval_authorized:    bool
    funded_accounts_safe:   bool
    available_eval_capital: float   # From bank only — FX-08
    blocked_reason:         Optional[str]
    recommendation:         str

def check_capital_defense(
    new_eval_cost:          float,   # Cost of new evaluation (e.g. $140 FTMO)
    available_bank_capital: float,   # Only funds IN Jorge's bank
    funded_account_equity:  float,   # Equity in funded accounts
    funded_floor_dollars:   float,   # Floor of funded account (must preserve)
    funded_drawdown_pct:    float,   # How much funded account has consumed
) -> CapitalDefenseCheck:
    """
    FORGE-43: Capital Defense.
    Can ONLY pay for new evaluation from actual bank capital.
    NEVER from funded account profits or pending payouts.
    """
    # Funded account must have ≥ 20% drawdown buffer remaining
    funded_safe = funded_drawdown_pct <= 0.80   # Not in red zone

    # Can afford new eval from bank?
    can_afford = available_bank_capital >= new_eval_cost

    if not funded_safe:
        return CapitalDefenseCheck(
            new_eval_authorized=False,
            funded_accounts_safe=False,
            available_eval_capital=available_bank_capital,
            blocked_reason=(
                f"Funded account needs attention first. "
                f"Drawdown at {funded_drawdown_pct:.0%} (80% threshold). "
                f"Stabilize funded account before starting new evaluation."
            ),
            recommendation="Focus on funded account. No new evaluations until funded is stable.",
        )

    if not can_afford:
        return CapitalDefenseCheck(
            new_eval_authorized=False,
            funded_accounts_safe=True,
            available_eval_capital=available_bank_capital,
            blocked_reason=(
                f"Insufficient bank capital: ${available_bank_capital:,.0f} < "
                f"${new_eval_cost:,.0f} evaluation cost."
            ),
            recommendation=(
                f"Wait for payout to arrive in bank. "
                f"Need ${new_eval_cost - available_bank_capital:,.0f} more."
            ),
        )

    return CapitalDefenseCheck(
        new_eval_authorized=True,
        funded_accounts_safe=True,
        available_eval_capital=available_bank_capital,
        blocked_reason=None,
        recommendation=(
            f"✅ New evaluation authorized. "
            f"Bank capital: ${available_bank_capital:,.0f}. "
            f"Evaluation cost: ${new_eval_cost:,.0f}. "
            f"Funded account: STABLE ({funded_drawdown_pct:.0%} drawdown used)."
        ),
    )


# ── FORGE-47: Evaluation Warmup Protocol ─────────────────────────────────────
# FTMO: start with $10K warmup (cheapest eval) before $100K evaluation.
# 3 minimum trading days at target pace before starting real evaluation.

@dataclass
class WarmupStatus:
    """FORGE-47: Evaluation warmup protocol status."""
    is_warmed_up:           bool
    trading_days_completed: int
    min_required:           int
    daily_profit_rate:      float      # $ per day average
    target_daily_rate:      float      # What we need per day at full scale
    pace_confidence:        float      # 0–1: confidence we can hit targets
    recommendation:         str

def check_warmup_status(
    trading_days_completed: int,
    avg_daily_profit:       float,
    target_account_profit:  float,   # E.g. $1,000 for $10K FTMO
    eval_days:              int      = 30,
    min_warmup_days:        int      = 3,
) -> WarmupStatus:
    """FORGE-47: Check if warmup period is complete."""
    required_daily = target_account_profit / (eval_days * 0.70)   # 70% trading days
    warmed = trading_days_completed >= min_warmup_days

    pace_conf = min(1.0, avg_daily_profit / required_daily) if required_daily > 0 else 0.0

    if not warmed:
        rec = (f"Complete warmup: {trading_days_completed}/{min_warmup_days} days done. "
               f"Need {min_warmup_days - trading_days_completed} more days.")
    elif pace_conf < 0.70:
        rec = (f"Warmup days met but pace confidence low ({pace_conf:.0%}). "
               f"Current avg ${avg_daily_profit:.0f}/day vs ${required_daily:.0f} needed. "
               f"Calibrate further before paid eval.")
    else:
        rec = (f"✅ Warmup complete. {trading_days_completed} days, "
               f"${avg_daily_profit:.0f}/day avg. Ready for paid evaluation.")

    return WarmupStatus(
        is_warmed_up=warmed and pace_conf >= 0.70,
        trading_days_completed=trading_days_completed,
        min_required=min_warmup_days,
        daily_profit_rate=avg_daily_profit,
        target_daily_rate=required_daily,
        pace_confidence=round(pace_conf, 4),
        recommendation=rec,
    )


# ── FORGE-55: Drawdown Recovery Protocol ─────────────────────────────────────
# After a drawdown event: systematic recovery without gambling.

@dataclass
class DrawdownRecoveryPlan:
    """FORGE-55: Step-by-step recovery plan after drawdown event."""
    drawdown_pct:       float
    recovery_phase:     str    # "STABILIZE" / "RECOVER" / "NORMAL"
    size_factor:        float  # Reduced size during recovery
    required_wins:      int    # Wins needed before returning to normal size
    wins_completed:     int
    is_complete:        bool
    daily_target:       float  # Conservative daily target during recovery
    recommendation:     str

def create_recovery_plan(
    drawdown_pct:       float,   # Fraction of budget used (e.g. 0.60 = 60%)
    account_size:       float,
    wins_completed:     int = 0,
) -> DrawdownRecoveryPlan:
    """FORGE-55: Create a recovery plan after a drawdown event."""
    if drawdown_pct >= 0.85:
        phase = "STABILIZE"
        size_factor = 0.25
        wins_needed = 5
        daily_target = account_size * 0.002   # 0.2% daily target
    elif drawdown_pct >= 0.70:
        phase = "RECOVER"
        size_factor = 0.50
        wins_needed = 3
        daily_target = account_size * 0.003
    elif drawdown_pct >= 0.60:
        phase = "RECOVER"
        size_factor = 0.75
        wins_needed = 2
        daily_target = account_size * 0.004
    else:
        phase = "NORMAL"
        size_factor = 1.00
        wins_needed = 0
        daily_target = account_size * 0.007

    complete = wins_completed >= wins_needed

    if complete or phase == "NORMAL":
        rec = "✅ Recovery complete. Return to normal position sizing."
    else:
        remaining = wins_needed - wins_completed
        rec = (f"Recovery phase {phase}: {size_factor:.0%} size. "
               f"Need {remaining} more quality wins before returning to full size.")

    return DrawdownRecoveryPlan(
        drawdown_pct=drawdown_pct, recovery_phase=phase,
        size_factor=size_factor, required_wins=wins_needed,
        wins_completed=wins_completed, is_complete=complete,
        daily_target=round(daily_target, 2), recommendation=rec,
    )
