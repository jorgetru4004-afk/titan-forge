"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   dynamic_sizing.py — FORGE-59/86 — Layer 2                 ║
║  FORGE-59: Dynamic Position Sizing                                           ║
║    Continuously updates based on phase: early=larger, near target=smaller,  ║
║    post-Safety Net=extraction mode.                                          ║
║  FORGE-86: Drawdown Asymmetry Sizing                                         ║
║    10% loss requires 11.1% to recover. Sizes are reduced extra after        ║
║    losses to account for compounding asymmetry.                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.dynamic_sizing")


class AccountPhase(Enum):
    EARLY_EVAL          = auto()   # First half — profit target far
    MID_EVAL            = auto()   # Progress made but not near target
    APPROACH_TARGET     = auto()   # Within 20% of target — C-02 applies
    SAFETY_NET_BUILDING = auto()   # Funded — building buffer first
    EXTRACTION_MODE     = auto()   # Funded — above safety net


@dataclass
class DynamicSizeResult:
    """Result from the dynamic sizing calculation."""
    base_size:          float   # Input base size (lots/contracts)
    final_size:         float   # Output after all adjustments
    phase_modifier:     float   # Phase-based adjustment
    asymmetry_modifier: float   # Drawdown asymmetry adjustment
    total_modifier:     float   # Combined modifier
    account_phase:      AccountPhase
    reason:             str


def determine_phase(
    profit_pct_complete:    float,   # 0–1: progress toward target
    is_funded:              bool,
    safety_net_reached:     bool = False,
    days_elapsed:           int  = 0,
    total_days:             Optional[int] = None,
) -> AccountPhase:
    """Determine the current account phase for sizing purposes."""
    if is_funded:
        return AccountPhase.EXTRACTION_MODE if safety_net_reached else AccountPhase.SAFETY_NET_BUILDING

    if profit_pct_complete >= 0.80:   # Within 20% of target — C-02 zone
        return AccountPhase.APPROACH_TARGET
    elif profit_pct_complete >= 0.40 or (total_days and days_elapsed > total_days * 0.5):
        return AccountPhase.MID_EVAL
    return AccountPhase.EARLY_EVAL


# Phase-based modifiers
PHASE_MODIFIERS: dict[AccountPhase, float] = {
    AccountPhase.EARLY_EVAL:          1.10,   # Early: slight boost (room to recover)
    AccountPhase.MID_EVAL:            1.00,   # Normal
    AccountPhase.APPROACH_TARGET:     0.50,   # Near target: half size (C-02)
    AccountPhase.SAFETY_NET_BUILDING: 0.70,   # Funded early: conservative
    AccountPhase.EXTRACTION_MODE:     1.00,   # Funded extraction: normal
}


def calculate_asymmetry_modifier(consecutive_losses: int, loss_pct: float) -> float:
    """
    FORGE-86: Drawdown Asymmetry Sizing.
    After a loss, account needs MORE than the loss to recover.
    A 10% loss needs 11.1% gain. A 20% loss needs 25% gain.
    We reduce size EXTRA after losses to account for this compounding effect.

    Args:
        consecutive_losses: Recent consecutive loss count.
        loss_pct:           Recent loss as fraction of account (e.g. 0.02 = 2%).
    """
    if consecutive_losses == 0 and loss_pct == 0:
        return 1.0

    # Recovery ratio: how much we need to earn to break even
    recovery_needed = 1.0 / (1.0 - loss_pct) - 1.0 if loss_pct < 1.0 else 1.0

    # Asymmetry penalty: larger the loss, more we reduce size
    asymmetry_penalty = recovery_needed * 0.5   # Scale down aggressiveness

    # Additional streak penalty
    streak_penalty = consecutive_losses * 0.05

    total_reduction = asymmetry_penalty + streak_penalty
    modifier = max(0.40, 1.0 - total_reduction)

    return round(modifier, 4)


def calculate_dynamic_size(
    base_size:              float,
    profit_pct_complete:    float,
    is_funded:              bool,
    safety_net_reached:     bool = False,
    consecutive_losses:     int  = 0,
    recent_loss_pct:        float = 0.0,
    days_elapsed:           int  = 0,
    total_days:             Optional[int] = None,
) -> DynamicSizeResult:
    """
    FORGE-59 + FORGE-86: Calculate the dynamically adjusted position size.
    Combines phase-based sizing with drawdown asymmetry protection.
    """
    phase         = determine_phase(profit_pct_complete, is_funded, safety_net_reached,
                                    days_elapsed, total_days)
    phase_mod     = PHASE_MODIFIERS[phase]
    asym_mod      = calculate_asymmetry_modifier(consecutive_losses, recent_loss_pct)
    total_mod     = phase_mod * asym_mod
    final_size    = round(base_size * total_mod, 6)

    reason = (f"Phase: {phase.name} ({phase_mod:.2f}×) | "
              f"Asymmetry: {asym_mod:.2f}× | Total: {total_mod:.2f}× | "
              f"Size: {base_size:.4f} → {final_size:.4f}")

    return DynamicSizeResult(
        base_size=base_size,
        final_size=final_size,
        phase_modifier=phase_mod,
        asymmetry_modifier=asym_mod,
        total_modifier=round(total_mod, 4),
        account_phase=phase,
        reason=reason,
    )
