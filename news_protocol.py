"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  news_protocol.py — FORGE-13 — Layer 1                      ║
║  NEWS EVENT PROTOCOL                                                         ║
║  Complete calendar of firm-specific blackout windows.                        ║
║  Auto-closes/avoids positions during restricted periods.                     ║
║  DNA: 10 min. FTMO funded: 2 min (5-min Risk-Off recommended).              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional
from firm_rules import FirmID, MultiFirmRuleEngine, AccountPhase

logger = logging.getLogger("titan_forge.news_protocol")

class NewsImpact(Enum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"; EXTREME = "extreme"

class NewsAction(Enum):
    CLEAR          = auto()   # No restrictions
    RISK_OFF       = auto()   # Reduce risk — 5 min before high-impact
    FLAT_REQUIRED  = auto()   # Must be flat — within blackout window
    HOLD_PERMITTED = auto()   # Can hold but cannot open/close (DNA rule)

@dataclass(frozen=True)
class NewsEvent:
    name:        str
    scheduled_utc: datetime
    impact:      NewsImpact
    currencies:  tuple[str, ...]    # Affected pairs; () = all markets

@dataclass
class NewsCheckResult:
    action:               NewsAction
    firm_id:              str
    minutes_to_event:     Optional[float]
    minutes_since_event:  Optional[float]
    triggering_event:     Optional[NewsEvent]
    blackout_minutes:     int
    can_open:             bool
    can_close:            bool
    reason:               str

    @property
    def is_restricted(self) -> bool:
        return self.action != NewsAction.CLEAR

# Firm-specific blackout rules from Section 9 of the document
_FIRM_BLACKOUT: dict[str, dict] = {
    FirmID.FTMO: {
        "eval_minutes_before": 0, "eval_minutes_after": 0,    # No restriction in eval
        "fund_minutes_before": 2, "fund_minutes_after": 2,    # Funded: 2 min
        "risk_off_before": 5,   # Implement 5-min Risk-Off protocol
        "can_hold_through": False,
        "impacts": [NewsImpact.HIGH, NewsImpact.EXTREME],
    },
    FirmID.DNA_FUNDED: {
        "eval_minutes_before": 10, "eval_minutes_after": 10,
        "fund_minutes_before": 10, "fund_minutes_after": 10,
        "risk_off_before": 15,
        "can_hold_through": True,   # DNA: can hold, cannot open/close
        "impacts": [NewsImpact.HIGH, NewsImpact.EXTREME],
    },
    FirmID.APEX: {
        "eval_minutes_before": 0, "eval_minutes_after": 0,
        "fund_minutes_before": 0, "fund_minutes_after": 0,
        "risk_off_before": 0,
        "can_hold_through": True,
        "impacts": [],   # No news restriction
    },
    FirmID.FIVEPERCENTERS: {
        "eval_minutes_before": 0, "eval_minutes_after": 0,
        "fund_minutes_before": 0, "fund_minutes_after": 0,
        "risk_off_before": 0,
        "can_hold_through": True,
        "impacts": [],
    },
    FirmID.TOPSTEP: {
        "eval_minutes_before": 0, "eval_minutes_after": 0,
        "fund_minutes_before": 0, "fund_minutes_after": 0,
        "risk_off_before": 0,
        "can_hold_through": True,
        "impacts": [],   # Most permissive — trade through announcements
    },
}

class NewsProtocol:
    """FORGE-13: News Event Protocol."""

    def __init__(self, rule_engine: MultiFirmRuleEngine):
        self._rule_engine = rule_engine
        self._calendar: list[NewsEvent] = []

    def load_events(self, events: list[NewsEvent]) -> None:
        self._calendar = sorted(events, key=lambda e: e.scheduled_utc)
        logger.info("[FORGE-13] Loaded %d news events.", len(self._calendar))

    def check(
        self,
        firm_id:   str,
        phase:     str,
        as_of:     Optional[datetime] = None,
    ) -> NewsCheckResult:
        now = as_of or datetime.now(timezone.utc)
        cfg = _FIRM_BLACKOUT.get(firm_id, _FIRM_BLACKOUT[FirmID.FTMO])

        is_funded    = phase == AccountPhase.FUNDED
        before_mins  = cfg["fund_minutes_before"] if is_funded else cfg["eval_minutes_before"]
        after_mins   = cfg["fund_minutes_after"]  if is_funded else cfg["eval_minutes_after"]
        can_hold     = cfg["can_hold_through"]
        risk_off_before = cfg["risk_off_before"]
        impacts      = cfg["impacts"]

        if not impacts:
            return NewsCheckResult(
                action=NewsAction.CLEAR, firm_id=firm_id,
                minutes_to_event=None, minutes_since_event=None,
                triggering_event=None, blackout_minutes=0,
                can_open=True, can_close=True,
                reason=f"{firm_id}: No news restrictions."
            )

        closest_future  = None
        closest_past    = None
        mins_to_future  = float("inf")
        mins_since_past = float("inf")

        for event in self._calendar:
            if event.impact not in impacts:
                continue
            delta_seconds = (event.scheduled_utc - now).total_seconds()
            delta_minutes = delta_seconds / 60.0

            if delta_minutes >= 0 and delta_minutes < mins_to_future:
                mins_to_future  = delta_minutes
                closest_future  = event
            elif delta_minutes < 0 and abs(delta_minutes) < mins_since_past:
                mins_since_past = abs(delta_minutes)
                closest_past    = event

        mins_to   = mins_to_future  if closest_future else None
        mins_since = mins_since_past if closest_past  else None

        # Check blackout windows
        in_pre_blackout  = mins_to   is not None and mins_to   <= before_mins
        in_post_blackout = mins_since is not None and mins_since <= after_mins
        # Risk-off only applies when the firm has an actual blackout in this phase
        in_risk_off = (
            before_mins > 0 and
            mins_to is not None and
            mins_to <= risk_off_before and
            not in_pre_blackout
        )
        triggering = closest_future if in_pre_blackout else (closest_past if in_post_blackout else None)
        if in_pre_blackout or in_post_blackout:
            if can_hold:
                action = NewsAction.HOLD_PERMITTED
                can_open = False; can_close = False
                reason = (
                    f"{firm_id}: Blackout active — cannot open/close. "
                    f"{'Pre-event' if in_pre_blackout else 'Post-event'} "
                    f"({before_mins if in_pre_blackout else after_mins} min window)."
                )
            else:
                action = NewsAction.FLAT_REQUIRED
                can_open = False; can_close = True  # Can close to get flat
                reason = (
                    f"{firm_id}: Must be FLAT. "
                    f"{'Pre-event' if in_pre_blackout else 'Post-event'} "
                    f"{before_mins if in_pre_blackout else after_mins}-min blackout."
                )
        elif in_risk_off:
            action = NewsAction.RISK_OFF
            can_open = False; can_close = True
            reason = (
                f"{firm_id}: Risk-Off — {mins_to:.1f} min to {closest_future.name}. "
                f"No new entries. Reduce open positions."
            )
        else:
            action = NewsAction.CLEAR
            can_open = True; can_close = True
            reason = (
                f"Clear. Next event: "
                f"{closest_future.name} in {mins_to:.1f} min" if closest_future
                else "No upcoming events."
            )

        return NewsCheckResult(
            action=action, firm_id=firm_id,
            minutes_to_event=mins_to if mins_to != float("inf") else None,
            minutes_since_event=mins_since if mins_since != float("inf") else None,
            triggering_event=triggering,
            blackout_minutes=before_mins if in_pre_blackout else after_mins,
            can_open=can_open, can_close=can_close,
            reason=reason,
        )

    def get_firm_blackout_minutes(self, firm_id: str, phase: str) -> tuple[int, int]:
        cfg = _FIRM_BLACKOUT.get(firm_id, {})
        is_funded = phase == AccountPhase.FUNDED
        before = cfg.get("fund_minutes_before" if is_funded else "eval_minutes_before", 0)
        after  = cfg.get("fund_minutes_after"  if is_funded else "eval_minutes_after",  0)
        return before, after
