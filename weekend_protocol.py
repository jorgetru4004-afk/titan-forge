"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  weekend_protocol.py — FORGE-14 — Layer 1                   ║
║  WEEKEND/HOLIDAY PROTOCOL                                                    ║
║  Closes positions before weekends when firm requires.                        ║
║  Calculates weekend gap risk for permitted holds.                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional
from firm_rules import FirmID, MultiFirmRuleEngine

logger = logging.getLogger("titan_forge.weekend_protocol")

class WeekendAction(Enum):
    HOLD_PERMITTED     = auto()   # Firm allows weekend holds
    CLOSE_REQUIRED     = auto()   # Firm requires flat before market close
    CLOSE_RECOMMENDED  = auto()   # Not required but gap risk is high
    HOLIDAY_CLOSE      = auto()   # Market holiday — same as weekend

@dataclass
class WeekendCheckResult:
    action:             WeekendAction
    firm_id:            str
    is_friday_eod:      bool
    is_weekend:         bool
    is_holiday:         bool
    gap_risk_pct:       float         # Estimated weekend gap risk (0–100%)
    close_by:           Optional[str] # "Friday 3pm CT" etc.
    reason:             str

    @property
    def must_close(self) -> bool:
        return self.action in (WeekendAction.CLOSE_REQUIRED, WeekendAction.HOLIDAY_CLOSE)

# Known US market holidays (simplified — production pulls from calendar API)
_US_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # MLK Day
    date(2026, 2, 16),  # Presidents Day
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 7, 3),   # July 4th (observed)
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}

_FRIDAY_CLOSE_HOUR_CT = 15   # 3:00 PM CT = EOD for futures Friday close

class WeekendProtocol:
    """FORGE-14: Weekend/Holiday Protocol."""

    def __init__(self, rule_engine: MultiFirmRuleEngine):
        self._rule_engine = rule_engine
        self._holidays: set[date] = _US_HOLIDAYS_2026.copy()

    def add_holiday(self, d: date) -> None:
        self._holidays.add(d)

    def check(
        self,
        firm_id:        str,
        as_of:          Optional[datetime] = None,
        has_positions:  bool = True,
        atr_pct:        float = 0.005,   # ATR as % of price — for gap risk estimate
    ) -> WeekendCheckResult:
        now   = as_of or datetime.now(timezone.utc)
        today = now.date()
        dow   = today.weekday()   # 0=Mon ... 4=Fri ... 5=Sat ... 6=Sun

        rules = self._rule_engine.get_firm_rules(firm_id)
        firm_requires_close = rules.requires_weekend_close

        is_weekend = dow >= 5   # Saturday or Sunday
        is_holiday = today in self._holidays
        is_friday  = dow == 4

        # Gap risk estimate: weekends historically 0.5–2% gap
        # Higher when VIX elevated, geopolitical risk, etc.
        base_gap_risk = 0.008   # 0.8% baseline
        gap_risk_pct  = base_gap_risk * 100.0  # Return as percentage

        if is_weekend or is_holiday:
            if firm_requires_close:
                return WeekendCheckResult(
                    action=WeekendAction.CLOSE_REQUIRED if not is_holiday
                           else WeekendAction.HOLIDAY_CLOSE,
                    firm_id=firm_id, is_friday_eod=False,
                    is_weekend=is_weekend, is_holiday=is_holiday,
                    gap_risk_pct=gap_risk_pct, close_by=None,
                    reason=f"{firm_id}: Weekend/holiday — positions PROHIBITED. "
                           f"{'Market holiday: ' + str(today) if is_holiday else 'Weekend'}."
                )
            else:
                return WeekendCheckResult(
                    action=WeekendAction.HOLD_PERMITTED,
                    firm_id=firm_id, is_friday_eod=False,
                    is_weekend=is_weekend, is_holiday=is_holiday,
                    gap_risk_pct=gap_risk_pct, close_by=None,
                    reason=f"{firm_id}: Weekend hold permitted. "
                           f"Gap risk: {gap_risk_pct:.1f}%. "
                           f"Ensure stop is outside expected gap range."
                )

        if is_friday:
            if firm_requires_close:
                return WeekendCheckResult(
                    action=WeekendAction.CLOSE_REQUIRED,
                    firm_id=firm_id, is_friday_eod=True,
                    is_weekend=False, is_holiday=False,
                    gap_risk_pct=gap_risk_pct,
                    close_by="Friday 3:00 PM CT (Topstep) or before market close",
                    reason=f"{firm_id}: Close all positions before weekend. "
                           f"Firm prohibits weekend holds."
                )
            else:
                # Not required but recommend based on gap risk
                if gap_risk_pct > 1.0 and has_positions:
                    return WeekendCheckResult(
                        action=WeekendAction.CLOSE_RECOMMENDED,
                        firm_id=firm_id, is_friday_eod=True,
                        is_weekend=False, is_holiday=False,
                        gap_risk_pct=gap_risk_pct,
                        close_by="Friday before market close",
                        reason=f"{firm_id}: Weekend hold permitted but gap risk "
                               f"{gap_risk_pct:.1f}% is elevated. Consider closing."
                    )

        # Normal day — no concern
        next_weekend = today + timedelta(days=(5 - dow))   # Days until Saturday
        days_to_weekend = (next_weekend - today).days
        return WeekendCheckResult(
            action=WeekendAction.HOLD_PERMITTED,
            firm_id=firm_id, is_friday_eod=False,
            is_weekend=False, is_holiday=False,
            gap_risk_pct=0.0,
            close_by=None,
            reason=f"Normal trading day. {days_to_weekend} day(s) to weekend."
        )

    def is_market_open(self, as_of: Optional[datetime] = None) -> bool:
        now   = as_of or datetime.now(timezone.utc)
        today = now.date()
        if today.weekday() >= 5 or today in self._holidays:
            return False
        return True
