"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              losing_trade_response.py — FORGE-65/68 — Layer 2               ║
║  FORGE-65: Losing Trade Response                                             ║
║    1 loss: -25% size, +1 conviction level.                                  ║
║    2 losses: session pause. 3 losses: 48-hour review + ARCHITECT alert.     ║
║  FORGE-68: News Event Profit Harvesting                                      ║
║    Topstep: trade with trend before events.                                  ║
║    Restrictive firms: completely flat 10-15 min before.                     ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.losing_trade_response")

# ── FORGE-65 ──────────────────────────────────────────────────────────────────

class LossResponseAction(Enum):
    CONTINUE          = auto()   # 0 losses — normal
    REDUCE_SIZE       = auto()   # 1 loss: -25% size
    SESSION_PAUSE     = auto()   # 2 losses: pause rest of session
    REVIEW_48H        = auto()   # 3+ losses: 48-hour review
    ARCHITECT_ALERT   = auto()   # 3+ losses: notify ARCHITECT

@dataclass
class LossResponseResult:
    consecutive_losses: int
    action:             LossResponseAction
    size_modifier:      float          # Applied to position size
    conviction_increase: int           # +N conviction threshold required
    pause_session:      bool
    review_required:    bool
    architect_alert:    bool
    resume_at:          Optional[datetime]
    reason:             str

def get_loss_response(
    consecutive_losses: int,
    as_of:              Optional[datetime] = None,
) -> LossResponseResult:
    """FORGE-65: Apply losing trade response protocol."""
    now = as_of or datetime.now(timezone.utc)

    if consecutive_losses == 0:
        return LossResponseResult(
            consecutive_losses=0,
            action=LossResponseAction.CONTINUE,
            size_modifier=1.0, conviction_increase=0,
            pause_session=False, review_required=False,
            architect_alert=False, resume_at=None,
            reason="No consecutive losses. Normal operation."
        )

    if consecutive_losses == 1:
        return LossResponseResult(
            consecutive_losses=1,
            action=LossResponseAction.REDUCE_SIZE,
            size_modifier=0.75, conviction_increase=1,
            pause_session=False, review_required=False,
            architect_alert=False, resume_at=None,
            reason="1 consecutive loss: -25% size, +1 conviction threshold."
        )

    if consecutive_losses == 2:
        return LossResponseResult(
            consecutive_losses=2,
            action=LossResponseAction.SESSION_PAUSE,
            size_modifier=0.0, conviction_increase=2,
            pause_session=True, review_required=False,
            architect_alert=False, resume_at=None,
            reason="2 consecutive losses: Session pause. No more trades today."
        )

    # 3+ losses
    resume_at = now + timedelta(hours=48)
    logger.error(
        "[FORGE-65] 🛑 3+ losses: 48-hour review required. ARCHITECT alerted."
    )
    return LossResponseResult(
        consecutive_losses=consecutive_losses,
        action=LossResponseAction.REVIEW_48H,
        size_modifier=0.0, conviction_increase=3,
        pause_session=True, review_required=True,
        architect_alert=True,
        resume_at=resume_at,
        reason=(f"3+ consecutive losses ({consecutive_losses}): 48-hour review. "
                f"ARCHITECT alerted. Root cause analysis required before resuming.")
    )


# ── FORGE-68: News Event Profit Harvesting ────────────────────────────────────

from firm_rules import FirmID

# Firms that allow news trading vs. restrictive firms
NEWS_PERMISSIVE_FIRMS = frozenset({FirmID.TOPSTEP, FirmID.APEX})
NEWS_RESTRICTIVE_FIRMS = frozenset({FirmID.FTMO, FirmID.DNA_FUNDED, FirmID.FIVEPERCENTERS})

@dataclass
class NewsHarvestDecision:
    firm_id:            str
    is_permissive:      bool
    can_trade_before:   bool
    can_trade_after:    bool
    recommended_action: str
    pre_event_strategy: Optional[str]   # Trade direction if permissive

def get_news_harvest_strategy(
    firm_id:              str,
    trend_direction:      str,    # "bullish" / "bearish" — current trend
    minutes_to_event:     float,
    event_impact:         str,    # "high" / "extreme"
    is_evaluation:        bool = True,
) -> NewsHarvestDecision:
    """
    FORGE-68: News event profit harvesting strategy.
    Permissive firms (Topstep): trade WITH trend before events.
    Restrictive firms: completely flat 10-15 min before.
    """
    is_permissive = firm_id in NEWS_PERMISSIVE_FIRMS

    if is_permissive and minutes_to_event > 5:
        # Topstep: trade with trend before the event
        strategy = (f"Trade WITH trend ({trend_direction}) before {event_impact} event. "
                    f"Exit 2-3 min before event. Profit from pre-event momentum.")
        return NewsHarvestDecision(
            firm_id=firm_id, is_permissive=True,
            can_trade_before=True, can_trade_after=True,
            recommended_action="TRADE_WITH_TREND",
            pre_event_strategy=strategy,
        )
    elif not is_permissive and minutes_to_event <= 15:
        return NewsHarvestDecision(
            firm_id=firm_id, is_permissive=False,
            can_trade_before=False, can_trade_after=False,
            recommended_action="GO_FLAT",
            pre_event_strategy=None,
        )
    else:
        return NewsHarvestDecision(
            firm_id=firm_id, is_permissive=is_permissive,
            can_trade_before=True, can_trade_after=True,
            recommended_action="NORMAL",
            pre_event_strategy=None,
        )
