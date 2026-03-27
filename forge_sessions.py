"""
FORGE v21 — 24-Hour Session System
====================================
Session detection, parameters, risk budgets, FTMO account type handling,
global economic calendar, daily break management.

Sunday 18:00 ET → Friday 17:00 ET with 17:00-18:00 ET daily break.

NEXUS Capital — Jorge Trujillo | Claude — AI Architect | March 2026
"""

import os
import logging
from datetime import datetime, time as dtime, timedelta, date
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("FORGE.sessions")

ET = ZoneInfo("America/New_York")


# ─────────────────────────────────────────────────────────────────
# SESSION DETECTION
# ─────────────────────────────────────────────────────────────────

def now_et() -> datetime:
    return datetime.now(ET)

def now_et_time() -> dtime:
    return now_et().time()

def get_current_session() -> str:
    """Return the current trading session name."""
    t = now_et_time()
    if dtime(19, 0) <= t or t < dtime(3, 0):
        return "ASIAN"
    elif t < dtime(8, 0):
        return "LONDON"
    elif t < dtime(9, 30):
        return "PRE_MARKET"
    elif t < dtime(16, 0):
        return "RTH"
    elif t < dtime(17, 0):
        return "EXTENDED"
    elif t < dtime(18, 0):
        return "BREAK"
    elif t < dtime(19, 0):
        return "EXTENDED"
    else:
        return "CLOSED"

def is_market_open() -> bool:
    """NQ futures trade Sunday 18:00 ET → Friday 17:00 ET, break 17:00-18:00 daily."""
    dt = now_et()
    t = dt.time()
    weekday = dt.weekday()  # 0=Monday

    # Saturday = closed
    if weekday == 5:
        return False
    # Sunday: only open after 18:00
    if weekday == 6:
        return t >= dtime(18, 0)
    # Friday: close at 17:00
    if weekday == 4:
        return t < dtime(17, 0)
    # Mon-Thu: open except 17:00-18:00 break
    if dtime(17, 0) <= t < dtime(18, 0):
        return False
    return True

def is_in_daily_break() -> bool:
    t = now_et_time()
    return dtime(17, 0) <= t < dtime(18, 0)

def minutes_until_break() -> int:
    """Minutes until the daily 17:00 ET break."""
    dt = now_et()
    break_time = dt.replace(hour=17, minute=0, second=0, microsecond=0)
    if dt.time() >= dtime(17, 0):
        break_time += timedelta(days=1)
    return int((break_time - dt).total_seconds() / 60)


# ─────────────────────────────────────────────────────────────────
# SESSION PARAMETERS
# ─────────────────────────────────────────────────────────────────

SESSION_PARAMS: Dict[str, dict] = {
    "ASIAN": {
        "size_mult": 0.50,
        "min_conviction": "STANDARD",
        "spread_est": 6.0,
        "description": "Low liquidity overnight. Gold trends, NQ chops.",
    },
    "LONDON": {
        "size_mult": 0.70,
        "min_conviction": "REDUCED",
        "spread_est": 4.0,
        "description": "Europe opens. Gold/FX breakouts. NQ range breaks.",
    },
    "PRE_MARKET": {
        "size_mult": 0.60,
        "min_conviction": "REDUCED",
        "spread_est": 5.0,
        "description": "US data releases. Pre-market range formation.",
    },
    "RTH": {
        "size_mult": 1.00,
        "min_conviction": "SCALP",
        "spread_est": 3.0,
        "description": "Full arsenal. Maximum liquidity and edge.",
    },
    "EXTENDED": {
        "size_mult": 0.50,
        "min_conviction": "STANDARD",
        "spread_est": 5.0,
        "description": "Post-RTH wind-down. Mean reversion only.",
    },
    "BREAK": {
        "size_mult": 0.00,
        "min_conviction": "REJECT",
        "spread_est": 99.0,
        "description": "Daily break 17:00-18:00 ET. NO TRADING.",
    },
}

# Total = $4,600 (leaves $400 buffer from FTMO $5,000 daily limit)
SESSION_RISK_BUDGET: Dict[str, float] = {
    "ASIAN":      500.0,
    "LONDON":     800.0,
    "PRE_MARKET": 500.0,
    "RTH":       2500.0,
    "EXTENDED":   300.0,
}


# ─────────────────────────────────────────────────────────────────
# FTMO ACCOUNT TYPE HANDLING
# ─────────────────────────────────────────────────────────────────

FTMO_ACCOUNT_TYPE = os.environ.get("FTMO_ACCOUNT_TYPE", "standard")

def must_close_before_break() -> bool:
    """Standard FTMO accounts cannot hold positions overnight."""
    return FTMO_ACCOUNT_TYPE == "standard"

def should_force_close_nq() -> bool:
    """Force close NQ at 16:55 ET on standard accounts."""
    if not must_close_before_break():
        return False
    t = now_et_time()
    return t >= dtime(16, 55) and t < dtime(17, 0)

def can_open_new_position(instrument: str) -> Tuple[bool, str]:
    """Check if new positions can be opened given session/break rules."""
    session = get_current_session()

    if session == "BREAK":
        return False, "Daily break 17:00-18:00 ET"

    if not is_market_open():
        return False, "Market closed"

    # Standard account: no new NQ trades after 16:50
    if must_close_before_break() and instrument == "NAS100":
        t = now_et_time()
        if t >= dtime(16, 50):
            return False, "Standard account: NQ closing for daily break"

    # Don't open trades within 5 minutes of break
    if minutes_until_break() <= 5 and instrument == "NAS100":
        return False, "Too close to daily break"

    return True, "OK"


# ─────────────────────────────────────────────────────────────────
# SESSION RISK TRACKER
# ─────────────────────────────────────────────────────────────────

class SessionRiskTracker:
    """Track cumulative realized loss per session. Independent budgets."""

    def __init__(self):
        self.session_pnl: Dict[str, float] = {s: 0.0 for s in SESSION_RISK_BUDGET}
        self.session_trades: Dict[str, int] = {s: 0 for s in SESSION_RISK_BUDGET}
        self._blown_sessions: set = set()

    def record_trade(self, session: str, pnl: float):
        if session in self.session_pnl:
            self.session_pnl[session] += pnl
            self.session_trades[session] = self.session_trades.get(session, 0) + 1
            budget = SESSION_RISK_BUDGET.get(session, 500)
            if self.session_pnl[session] <= -budget:
                self._blown_sessions.add(session)
                logger.warning("[SESSION RISK] %s budget blown: $%.0f / -$%.0f",
                               session, self.session_pnl[session], budget)

    def can_trade_session(self, session: str) -> Tuple[bool, str]:
        if session in self._blown_sessions:
            return False, f"{session} risk budget exhausted"
        budget = SESSION_RISK_BUDGET.get(session, 500)
        remaining = budget + self.session_pnl.get(session, 0)
        if remaining <= 0:
            return False, f"{session} risk budget exhausted"
        return True, f"${remaining:.0f} remaining"

    def get_remaining_budget(self, session: str) -> float:
        budget = SESSION_RISK_BUDGET.get(session, 500)
        return budget + self.session_pnl.get(session, 0)

    def reset_daily(self):
        self.session_pnl = {s: 0.0 for s in SESSION_RISK_BUDGET}
        self.session_trades = {s: 0 for s in SESSION_RISK_BUDGET}
        self._blown_sessions.clear()

    def summary(self) -> str:
        lines = []
        for s in ["ASIAN", "LONDON", "PRE_MARKET", "RTH", "EXTENDED"]:
            pnl = self.session_pnl.get(s, 0)
            budget = SESSION_RISK_BUDGET.get(s, 0)
            trades = self.session_trades.get(s, 0)
            status = "BLOWN" if s in self._blown_sessions else "OK"
            lines.append(f"  {s}: ${pnl:+.0f} / -${budget:.0f} ({trades} trades) [{status}]")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# GLOBAL ECONOMIC CALENDAR
# ─────────────────────────────────────────────────────────────────

# Approximate times (ET) for major releases. Per-instrument blackouts.
GLOBAL_NEWS: Dict[str, dict] = {
    "US": {
        "times": ["08:30", "10:00", "14:00", "14:30"],
        "affects": ["NAS100", "US500", "CL"],
        "blackout_minutes_before": 5,
        "blackout_minutes_after": 5,
    },
    "UK": {
        "times": ["04:30", "07:00"],
        "affects": ["XAUUSD"],
        "blackout_minutes_before": 3,
        "blackout_minutes_after": 3,
    },
    "EU": {
        "times": ["05:00", "08:45"],
        "affects": ["EURUSD"],
        "blackout_minutes_before": 3,
        "blackout_minutes_after": 3,
    },
    "JP": {
        "times": ["19:00"],
        "affects": ["XAUUSD"],
        "blackout_minutes_before": 3,
        "blackout_minutes_after": 3,
    },
    "CN": {
        "times": ["21:00"],
        "affects": ["XAUUSD", "CL"],
        "blackout_minutes_before": 3,
        "blackout_minutes_after": 3,
    },
}

def is_news_blackout(instrument: str) -> Tuple[bool, str]:
    """Check if instrument is in a news blackout window."""
    t = now_et()
    current_minutes = t.hour * 60 + t.minute

    for region, info in GLOBAL_NEWS.items():
        if instrument not in info["affects"]:
            continue
        before = info["blackout_minutes_before"]
        after = info["blackout_minutes_after"]
        for time_str in info["times"]:
            h, m = map(int, time_str.split(":"))
            event_minutes = h * 60 + m
            if event_minutes - before <= current_minutes <= event_minutes + after:
                return True, f"{region} news at {time_str} ET"

    return False, ""


# ─────────────────────────────────────────────────────────────────
# SESSION STATE — for hourly Telegram updates
# ─────────────────────────────────────────────────────────────────

def get_session_state() -> dict:
    """Comprehensive session state snapshot."""
    session = get_current_session()
    params = SESSION_PARAMS.get(session, SESSION_PARAMS["BREAK"])
    dt = now_et()
    return {
        "session": session,
        "time_et": dt.strftime("%H:%M:%S ET"),
        "day_of_week": dt.strftime("%A"),
        "size_mult": params["size_mult"],
        "min_conviction": params["min_conviction"],
        "spread_est": params["spread_est"],
        "market_open": is_market_open(),
        "minutes_to_break": minutes_until_break(),
        "account_type": FTMO_ACCOUNT_TYPE,
    }
