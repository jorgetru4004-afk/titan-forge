"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  streak_detector.py — FORGE-15 — Layer 1                    ║
║  STREAK DETECTOR                                                             ║
║  3 consecutive losses: 2-hour pause. 5 consecutive losses: stop for day.    ║
║  No exceptions. No override. Behavioral protection.                          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.streak_detector")

PAUSE_LOSSES:    int = 3   # 3 consecutive losses = 2-hour pause
DAY_STOP_LOSSES: int = 5   # 5 consecutive losses = stop for the day
PAUSE_HOURS:     float = 2.0

class StreakState(Enum):
    CLEAR          = auto()   # No streak concern
    PAUSED         = auto()   # 3-loss streak: 2-hour mandatory pause
    DAY_STOPPED    = auto()   # 5-loss streak: no trading rest of day
    RESUMED        = auto()   # Pause complete, trading allowed

@dataclass
class TradeResult:
    trade_id:     str
    is_win:       bool
    pnl:          float
    timestamp:    datetime
    session_date: date

@dataclass
class StreakStatus:
    state:                  StreakState
    consecutive_losses:     int
    consecutive_wins:       int
    resume_at:              Optional[datetime]
    session_date:           date
    trading_permitted:      bool
    reason:                 str
    minutes_remaining:      Optional[float]   # If paused

class StreakDetector:
    """FORGE-15: Streak Detector — mandatory cool-down after loss streaks."""

    def __init__(self):
        self._consecutive_losses = 0
        self._consecutive_wins   = 0
        self._state              = StreakState.CLEAR
        self._pause_start:  Optional[datetime] = None
        self._resume_at:    Optional[datetime] = None
        self._session_date: Optional[date]     = None
        self._day_stopped_date: Optional[date] = None
        self._trade_history: list[TradeResult] = []

    def record_trade(self, result: TradeResult) -> StreakStatus:
        """Record a completed trade and update streak state."""
        self._trade_history.append(result)
        self._session_date = result.session_date

        # If day-stopped — no tracking until new session
        if (self._state == StreakState.DAY_STOPPED and
                self._day_stopped_date == result.session_date):
            return self.get_status(result.timestamp)

        if result.is_win:
            self._consecutive_losses = 0
            self._consecutive_wins  += 1
        else:
            self._consecutive_losses += 1
            self._consecutive_wins   = 0
            self._handle_loss_streak(result.timestamp, result.session_date)

        return self.get_status(result.timestamp)

    def _handle_loss_streak(self, now: datetime, session_date: date) -> None:
        if self._consecutive_losses >= DAY_STOP_LOSSES:
            self._state = StreakState.DAY_STOPPED
            self._day_stopped_date = session_date
            self._resume_at = None
            logger.error(
                "[FORGE-15] 🛑 DAY STOP: %d consecutive losses. No trading today.",
                self._consecutive_losses,
            )
        elif self._consecutive_losses >= PAUSE_LOSSES:
            if self._state not in (StreakState.PAUSED, StreakState.DAY_STOPPED):
                self._state      = StreakState.PAUSED
                self._pause_start = now
                self._resume_at  = now + timedelta(hours=PAUSE_HOURS)
                logger.warning(
                    "[FORGE-15] ⏸ PAUSE: %d consecutive losses. Resume at %s.",
                    self._consecutive_losses,
                    self._resume_at.strftime("%H:%M UTC"),
                )

    def check_resume(self, as_of: Optional[datetime] = None) -> StreakStatus:
        """Check if a pause has expired and update state."""
        now = as_of or datetime.now(timezone.utc)
        if self._state == StreakState.PAUSED and self._resume_at:
            if now >= self._resume_at:
                self._state = StreakState.RESUMED
                logger.info("[FORGE-15] ▶ Pause complete — trading RESUMED.")
        return self.get_status(now)

    def advance_session(self, new_date: date) -> None:
        """New trading day — reset streak state (except day-stop tracking)."""
        if self._state in (StreakState.PAUSED, StreakState.RESUMED):
            self._state = StreakState.CLEAR
        # DAY_STOPPED resets at new session
        if self._day_stopped_date and new_date > self._day_stopped_date:
            self._state = StreakState.CLEAR
            self._day_stopped_date = None
        # Reset consecutive streaks for new day
        self._consecutive_losses = 0
        self._consecutive_wins   = 0
        self._session_date = new_date
        logger.info("[FORGE-15] New session %s — streak reset.", new_date)

    def get_status(self, as_of: Optional[datetime] = None) -> StreakStatus:
        now = as_of or datetime.now(timezone.utc)
        # Auto-check resume
        if self._state == StreakState.PAUSED and self._resume_at and now >= self._resume_at:
            self._state = StreakState.RESUMED

        mins_remaining = None
        if self._state == StreakState.PAUSED and self._resume_at:
            remaining = (self._resume_at - now).total_seconds()
            mins_remaining = max(0.0, remaining / 60.0)

        permitted = self._state in (StreakState.CLEAR, StreakState.RESUMED)

        if self._state == StreakState.CLEAR:
            reason = f"Clear. Losses: {self._consecutive_losses}."
        elif self._state == StreakState.PAUSED:
            reason = (
                f"⏸ PAUSED: {self._consecutive_losses} consecutive losses. "
                f"{mins_remaining:.0f} min remaining."
            )
        elif self._state == StreakState.DAY_STOPPED:
            reason = f"🛑 DAY STOPPED: {self._consecutive_losses} losses. No trading today."
        else:
            reason = f"▶ Resumed after {self._consecutive_losses} losses."

        return StreakStatus(
            state=self._state,
            consecutive_losses=self._consecutive_losses,
            consecutive_wins=self._consecutive_wins,
            resume_at=self._resume_at,
            session_date=self._session_date or date.today(),
            trading_permitted=permitted,
            reason=reason,
            minutes_remaining=mins_remaining,
        )

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    @property
    def is_trading_permitted(self) -> bool:
        return self._state in (StreakState.CLEAR, StreakState.RESUMED)
