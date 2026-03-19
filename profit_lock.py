"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     profit_lock.py — FORGE-64/73 — Layer 2                  ║
║  FORGE-64: Profit Lock Protocol                                              ║
║    Stage 1 at 0.5R: stop to breakeven.                                      ║
║    Stage 2 at 1.5R: close 30%.                                               ║
║    Stage 3 at 3R: trailing stop.                                             ║
║  FORGE-73: Compound Effect Calculator                                        ║
║    Tracks trajectory to scaling milestones.                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.profit_lock")


class LockStage(Enum):
    NONE     = auto()   # Below 0.5R — no lock
    STAGE_1  = auto()   # 0.5R–1.49R: stop → breakeven
    STAGE_2  = auto()   # 1.5R–2.99R: close 30%, trail
    STAGE_3  = auto()   # 3R+: trailing stop
    EXCEEDED = auto()   # Beyond targets — fully managing


@dataclass
class ProfitLockAction:
    """Action to take based on current profit lock stage."""
    stage:              LockStage
    current_r:          float        # Current unrealized R multiple
    move_stop_to:       Optional[float]  # New stop price
    close_pct:          float            # % of position to close (0–1)
    trail_atr:          Optional[float]  # ATR multiple for trailing stop
    reason:             str

    @property
    def requires_action(self) -> bool:
        return self.stage != LockStage.NONE


def calculate_profit_lock(
    entry_price:        float,
    current_price:      float,
    stop_price:         float,
    direction:          str,   # "long" / "short"
    atr:                float,
    current_stage:      LockStage = LockStage.NONE,
) -> ProfitLockAction:
    """
    FORGE-64: Calculate what profit lock action to take.

    Stage 1 at 0.5R: move stop to breakeven (entry price)
    Stage 2 at 1.5R: close 30% of position
    Stage 3 at 3.0R: trailing stop (1.5 ATR trailing)
    """
    # Calculate initial risk (R) in price terms
    risk = abs(entry_price - stop_price)
    if risk <= 0:
        return ProfitLockAction(LockStage.NONE, 0.0, None, 0.0, None,
                                "No valid risk — cannot calculate profit lock.")

    # Current unrealized profit in R terms
    if direction.lower() == "long":
        unrealized = current_price - entry_price
    else:
        unrealized = entry_price - current_price

    current_r = unrealized / risk

    # Stage 3: 3R+ → trailing stop
    if current_r >= 3.0:
        if current_stage in (LockStage.STAGE_3, LockStage.EXCEEDED):
            # Already at stage 3 — maintain trailing stop
            trail_stop = current_price - atr * 1.5 if direction == "long" else current_price + atr * 1.5
        else:
            trail_stop = current_price - atr * 1.5 if direction == "long" else current_price + atr * 1.5

        return ProfitLockAction(
            stage=LockStage.STAGE_3, current_r=round(current_r, 2),
            move_stop_to=round(trail_stop, 4), close_pct=0.0, trail_atr=1.5,
            reason=f"Stage 3 ({current_r:.1f}R): Trailing stop at {trail_stop:.2f} (1.5 ATR)"
        )

    # Stage 2: 1.5R → close 30%, move stop to BE
    if current_r >= 1.5:
        if current_stage == LockStage.STAGE_2:
            return ProfitLockAction(
                stage=LockStage.STAGE_2, current_r=round(current_r, 2),
                move_stop_to=entry_price, close_pct=0.0, trail_atr=None,
                reason=f"Stage 2 maintained ({current_r:.1f}R). Stop at breakeven."
            )
        return ProfitLockAction(
            stage=LockStage.STAGE_2, current_r=round(current_r, 2),
            move_stop_to=entry_price, close_pct=0.30, trail_atr=None,
            reason=f"Stage 2 triggered ({current_r:.1f}R): Close 30%, stop → breakeven."
        )

    # Stage 1: 0.5R → move stop to breakeven
    if current_r >= 0.5:
        if current_stage in (LockStage.STAGE_1, LockStage.STAGE_2, LockStage.STAGE_3):
            return ProfitLockAction(
                stage=LockStage.STAGE_1, current_r=round(current_r, 2),
                move_stop_to=entry_price, close_pct=0.0, trail_atr=None,
                reason=f"Stage 1 maintained ({current_r:.1f}R). Stop at breakeven."
            )
        return ProfitLockAction(
            stage=LockStage.STAGE_1, current_r=round(current_r, 2),
            move_stop_to=entry_price, close_pct=0.0, trail_atr=None,
            reason=f"Stage 1 triggered ({current_r:.1f}R): Move stop to breakeven ({entry_price})."
        )

    # Below 0.5R — no lock yet
    return ProfitLockAction(
        stage=LockStage.NONE, current_r=round(current_r, 2),
        move_stop_to=None, close_pct=0.0, trail_atr=None,
        reason=f"No lock yet ({current_r:.2f}R < 0.5R threshold)."
    )


# ── FORGE-73: Compound Effect Calculator ─────────────────────────────────────

@dataclass
class MilestoneTrajectory:
    """Trajectory to next scaling milestone."""
    current_balance:        float
    target_milestone:       float
    current_profit_rate:    float   # $/day average
    days_to_milestone:      Optional[float]
    is_milestone_reached:   bool
    milestone_name:         str
    recommendation:         str

def calculate_milestone_trajectory(
    current_balance:     float,
    target_milestone:    float,
    milestone_name:      str,
    avg_daily_profit:    float,
) -> MilestoneTrajectory:
    """FORGE-73: Calculate days to next scaling milestone."""
    remaining = target_milestone - current_balance
    is_reached = remaining <= 0

    if is_reached:
        return MilestoneTrajectory(
            current_balance=current_balance,
            target_milestone=target_milestone,
            current_profit_rate=avg_daily_profit,
            days_to_milestone=0.0,
            is_milestone_reached=True,
            milestone_name=milestone_name,
            recommendation=f"✅ Milestone REACHED: {milestone_name}. "
                           f"Request account scaling immediately."
        )

    if avg_daily_profit <= 0:
        return MilestoneTrajectory(
            current_balance=current_balance,
            target_milestone=target_milestone,
            current_profit_rate=avg_daily_profit,
            days_to_milestone=None,
            is_milestone_reached=False,
            milestone_name=milestone_name,
            recommendation=f"No positive daily average — cannot project milestone date."
        )

    days = remaining / avg_daily_profit
    return MilestoneTrajectory(
        current_balance=current_balance,
        target_milestone=target_milestone,
        current_profit_rate=avg_daily_profit,
        days_to_milestone=round(days, 1),
        is_milestone_reached=False,
        milestone_name=milestone_name,
        recommendation=(
            f"{milestone_name}: ${remaining:,.0f} more needed. "
            f"At ${avg_daily_profit:,.0f}/day → ~{int(days)} days."
        )
    )
