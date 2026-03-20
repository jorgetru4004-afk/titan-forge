"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  evaluation_state.py — FORGE-02 — Layer 1                   ║
║                                                                              ║
║  EVALUATION STATE MACHINE                                                    ║
║  Real-time tracking of every metric that determines pass or fail.            ║
║                                                                              ║
║  Tracks — updated after every trade and every tick:                          ║
║    • Profit vs target (absolute + percentage)                                ║
║    • Drawdown used vs limit (absolute + percentage)                          ║
║    • Daily drawdown used vs daily limit                                      ║
║    • Trading days completed vs minimum required                              ║
║    • Calendar days remaining vs deadline                                     ║
║    • Distance to pass (profit needed)                                        ║
║    • Distance to fail (drawdown remaining)                                   ║
║    • Pass probability score (hourly — FORGE-32 feeds from this)              ║
║    • State transitions: IDLE→ACTIVE→PASSED/FAILED/EXPIRED/SUSPENDED         ║
║                                                                              ║
║  Supports: Multiple simultaneous evaluations (FORGE-28).                     ║
║  Integrates: MultiFirmRuleEngine (FORGE-01) for all thresholds.              ║
║  Feeds: ARCHITECT dashboard (FORGE-31), Pacing Engine (FORGE-04).            ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional

from firm_rules import FirmID, MultiFirmRuleEngine, DrawdownType

logger = logging.getLogger("titan_forge.evaluation_state")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — EVALUATION STATES
# ─────────────────────────────────────────────────────────────────────────────

class EvalState(Enum):
    """
    All possible states of a prop firm evaluation.
    Transitions are one-way (no going back from PASSED/FAILED/EXPIRED).
    """
    IDLE        = auto()   # Created, not yet started
    ACTIVE      = auto()   # Trading in progress
    PASSED      = auto()   # ✅ Profit target hit. All requirements met.
    FAILED      = auto()   # ❌ Drawdown limit breached or daily limit crossed.
    EXPIRED     = auto()   # ⏰ Time limit exceeded without passing.
    SUSPENDED   = auto()   # ⏸  Paused (streak detector, emergency, review)


class EvalPhase(Enum):
    """Which phase of a multi-phase evaluation."""
    PHASE_1 = 1
    PHASE_2 = 2
    SINGLE  = 0   # Single-phase firms (Apex)


class SuspendReason(Enum):
    """Why the evaluation is currently suspended."""
    STREAK_DETECTOR    = auto()   # FORGE-15: 3+ consecutive losses
    EMERGENCY          = auto()   # Level 2: Flash crash / correlation spike / liquidity
    MANUAL_REVIEW      = auto()   # Jorge requested a review
    CIRCUIT_BREAKER    = auto()   # Daily loss limit hit (Apex EOD — account not failed)
    RECOVERY_PROTOCOL  = auto()   # FORGE-43: 90-minute mandatory pause


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — TRADE RECORD
# Every trade appended to the evaluation ledger.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """Immutable record of a single trade within an evaluation."""
    trade_id:           str
    strategy_name:      str
    entry_time:         datetime
    exit_time:          datetime
    pnl:                float          # Realized P&L in dollars (gross)
    commission:         float          # Commission + spread cost for this trade
    unrealized_peak:    float          # Highest unrealized gain reached (Apex trailing)
    position_size:      float          # Position size at entry
    setup_type:         str            # e.g. "GEX-01", "ICT-02"
    is_win:             bool
    session_date:       date           # Trading day this trade belongs to

    @property
    def net_pnl(self) -> float:
        """Net P&L after commission. This is the real money earned."""
        return self.pnl - self.commission

    @property
    def hold_seconds(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — SESSION SNAPSHOT
# One per trading day. Tracks daily metrics for consistency rule enforcement.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SessionSnapshot:
    """Daily session record. Built up as trades come in throughout the day."""
    session_date:           date
    opening_balance:        float      # Balance at start of this session
    opening_equity:         float      # Equity at start of this session
    gross_pnl:              float = 0.0
    total_commission:       float = 0.0   # Cumulative commissions this session
    trade_count:            int   = 0
    win_count:              int   = 0
    loss_count:             int   = 0
    peak_unrealized:        float = 0.0   # Highest unrealized P&L (Apex trailing)
    max_adverse_excursion:  float = 0.0   # Worst intraday drawdown from opening equity
    is_qualifying_day:      bool  = False  # Met firm's qualifying day threshold
    circuit_breaker_hit:    bool  = False  # Daily limit hit (circuit breaker, not failure)
    session_closed:         bool  = False  # Session has been formally closed

    @property
    def win_rate(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return self.win_count / self.trade_count

    @property
    def net_pnl(self) -> float:
        """Real money earned — gross P&L minus all commissions.""'''
        return self.gross_pnl - self.total_commission

    def record_trade(self, trade: TradeRecord, qualifying_threshold: Optional[float]) -> None:
        """Update session metrics with a completed trade."""
        self.gross_pnl        += trade.pnl
        self.total_commission += trade.commission
        self.trade_count += 1
        if trade.is_win:
            self.win_count += 1
        else:
            self.loss_count += 1
        self.peak_unrealized = max(self.peak_unrealized, trade.unrealized_peak)
        # Qualifying day uses NET P&L — commissions reduce qualification
        if qualifying_threshold is not None and self.net_pnl >= qualifying_threshold:
            self.is_qualifying_day = True

    def close_session(self, closing_equity: float) -> None:
        self.session_closed = True
        intraday_dd = self.opening_equity - min(closing_equity, self.opening_equity)
        self.max_adverse_excursion = max(self.max_adverse_excursion, intraday_dd)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — EVALUATION SNAPSHOT
# The complete real-time picture at any moment. Rebuilt after every update.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvaluationSnapshot:
    """
    The complete real-time picture of a live evaluation.
    Every metric that ARCHITECT, Pacing Engine, and FORGE-31 Dashboard need.
    Rebuilt after every trade and every significant tick.
    """
    # Identity
    eval_id:                    str
    firm_id:                    str
    account_size:               float
    phase:                      EvalPhase
    state:                      EvalState

    # Balance / Equity
    starting_balance:           float
    current_balance:            float    # Realized balance (closed trades)
    current_equity:             float    # Balance + open P&L
    peak_unrealized_ever:       float    # Apex trailing: highest unrealized reached

    # Profit metrics
    profit_target_dollars:      float
    current_profit:             float    # current_balance - starting_balance
    profit_remaining:           float    # target - current (positive = need more)
    profit_pct_complete:        float    # 0.0–1.0, how close to target
    on_pace:                    bool     # True if daily pace is sufficient

    # Drawdown metrics
    total_drawdown_limit:       float    # Dollars — firm limit
    drawdown_used:              float    # Dollars consumed from the budget
    drawdown_remaining:         float    # Dollars left before failure
    drawdown_pct_used:          float    # 0.0–1.0
    firm_floor:                 float    # The actual equity floor
    daily_drawdown_limit:       float    # Today's daily limit in dollars
    daily_drawdown_used:        float    # Today's drawdown consumed
    daily_drawdown_remaining:   float
    daily_pct_used:             float    # 0.0–1.0

    # Time metrics
    start_date:                 date
    today:                      date
    calendar_days_elapsed:      int
    calendar_days_remaining:    Optional[int]   # None = no time limit
    trading_days_completed:     int
    min_trading_days:           Optional[int]   # None = no minimum
    trading_days_still_needed:  int             # Max(0, min - completed)
    deadline_met:               bool            # calendar_days_remaining > 0

    # Pass / Fail assessment
    can_pass_today:             bool     # All criteria met right now?
    profit_gate_met:            bool     # Profit target hit?
    time_gate_met:              bool     # Min trading days hit?
    drawdown_intact:            bool     # Not breached?
    daily_limit_intact:         bool     # Daily limit not crossed?
    pass_probability:           float    # 0.0–1.0 (FORGE-32 mathematical score)

    # Alert flags (from FORGE-67 thresholds)
    at_yellow:                  bool     # 50% drawdown used
    at_orange:                  bool     # 70% drawdown used
    at_red:                     bool     # 85% drawdown — close all

    # Daily loss tiered stops (Bug Fix — protects the 5% firm limit)
    daily_soft_stop:            bool     # 4% daily loss used — half size
    daily_hard_stop:            bool     # 4.5% daily loss used — no new trades today
    daily_loss_pct:             float    # Current daily loss as fraction of daily limit
    daily_size_multiplier:      float    # 1.0 / 0.75 / 0.5 / 0.25 / 0.0 based on level

    # Streak / behavioral
    consecutive_losses:         int
    consecutive_wins:           int
    consecutive_profitable_sessions: int
    total_trades:               int
    session_trade_count_today:  int

    # Suspension
    is_suspended:               bool
    suspend_reason:             Optional[SuspendReason]
    suspend_until:              Optional[datetime]

    # Timestamps
    last_updated:               datetime
    snapshot_sequence:          int      # Monotonically increasing per eval


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — EVALUATION STATE MACHINE
# FORGE-02. The core real-time tracker.
# ─────────────────────────────────────────────────────────────────────────────

class EvaluationStateMachine:
    """
    FORGE-02: Evaluation State Machine.

    Tracks every metric that determines pass or fail — in real time.
    Updated after every trade close and every significant tick.

    One instance per active evaluation.
    The EvaluationOrchestrator (FORGE-28) manages multiple instances.

    Usage:
        esm = EvaluationStateMachine(
            firm_id=FirmID.FTMO,
            account_size=100_000,
            phase=EvalPhase.PHASE_1,
            start_date=date.today(),
            rule_engine=rule_engine,
        )
        esm.start()

        # After each trade closes:
        esm.record_trade(trade)

        # On each tick (equity update from broker):
        esm.update_equity(current_equity, peak_unrealized)

        # Get the full picture:
        snap = esm.snapshot()
        if snap.at_red:
            # CLOSE ALL POSITIONS
    """

    def __init__(
        self,
        firm_id:        str,
        account_size:   float,
        phase:          EvalPhase,
        start_date:     date,
        rule_engine:    MultiFirmRuleEngine,
        eval_id:        Optional[str] = None,
    ):
        self.eval_id        = eval_id or str(uuid.uuid4())[:8].upper()
        self.firm_id        = firm_id
        self.account_size   = account_size
        self.phase          = phase
        self.start_date     = start_date
        self._rule_engine   = rule_engine
        self._rules         = rule_engine.get_firm_rules(firm_id)

        # State
        self._state: EvalState = EvalState.IDLE
        self._suspend_reason: Optional[SuspendReason] = None
        self._suspend_until:  Optional[datetime]       = None
        self._failure_reason: Optional[str]            = None
        self._passed_at:      Optional[datetime]       = None
        self._failed_at:      Optional[datetime]       = None

        # Balance / equity tracking
        self._starting_balance  = account_size
        self._current_balance   = account_size   # Realized (updated after each trade close)
        self._current_equity    = account_size   # Live (updated each tick)
        self._peak_unrealized   = 0.0            # Apex: all-time unrealized high

        # Daily tracking
        self._today             = start_date
        self._session_open_equity  = account_size
        self._session_open_balance = account_size

        # Trade / session ledger
        self._trades:   list[TradeRecord]      = []
        self._sessions: list[SessionSnapshot]  = []
        self._current_session: Optional[SessionSnapshot] = None

        # Streak counters
        self._consecutive_losses = 0
        self._consecutive_wins   = 0
        self._consecutive_profitable_sessions = 0

        # Drawdown tracking (for Apex trailing — updated per tick)
        self._apex_trailing_floor = account_size - (account_size * self._rules.total_drawdown_pct)

        # Snapshot counter
        self._snapshot_seq = 0

        # Profit targets by phase
        if phase == EvalPhase.PHASE_1:
            self._profit_target_pct = self._rules.profit_target_phase1_pct
        elif phase == EvalPhase.PHASE_2:
            self._profit_target_pct = self._rules.profit_target_phase2_pct or self._rules.profit_target_phase1_pct
        else:
            self._profit_target_pct = self._rules.profit_target_phase1_pct

        self._profit_target_dollars = account_size * self._profit_target_pct

        logger.info(
            "[FORGE-02][%s] State machine created. Firm: %s. Size: $%s. "
            "Phase: %s. Target: $%.2f (%.1f%%). Drawdown: $%.2f (%.1f%%).",
            self.eval_id, firm_id, f"{account_size:,.0f}",
            phase.name, self._profit_target_dollars, self._profit_target_pct * 100,
            account_size * self._rules.total_drawdown_pct,
            self._rules.total_drawdown_pct * 100,
        )

    # ── STATE TRANSITIONS ─────────────────────────────────────────────────────

    @property
    def state(self) -> EvalState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state == EvalState.ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self._state in (EvalState.PASSED, EvalState.FAILED, EvalState.EXPIRED)

    def start(self, as_of: Optional[datetime] = None) -> None:
        """
        Transition IDLE → ACTIVE. Open the first session.
        """
        if self._state != EvalState.IDLE:
            raise RuntimeError(
                f"[FORGE-02][{self.eval_id}] Cannot start — already in state {self._state.name}"
            )
        now = as_of or datetime.now(timezone.utc)
        self._state = EvalState.ACTIVE
        self._open_session(now.date(), self._current_balance, self._current_equity)
        logger.info(
            "[FORGE-02][%s] ▶ Evaluation STARTED. %s %s $%s.",
            self.eval_id, self.firm_id, self.phase.name, f"{self.account_size:,.0f}"
        )

    def suspend(self, reason: SuspendReason, resume_at: Optional[datetime] = None) -> None:
        """Pause trading. ACTIVE → SUSPENDED."""
        if self._state not in (EvalState.ACTIVE,):
            return
        self._state = EvalState.SUSPENDED
        self._suspend_reason = reason
        self._suspend_until  = resume_at
        logger.warning(
            "[FORGE-02][%s] ⏸ SUSPENDED: %s. Resume: %s.",
            self.eval_id, reason.name,
            resume_at.isoformat() if resume_at else "manual"
        )

    def resume(self) -> None:
        """Resume from SUSPENDED → ACTIVE."""
        if self._state != EvalState.SUSPENDED:
            return
        now = datetime.now(timezone.utc)
        if self._suspend_until and now < self._suspend_until:
            remaining = (self._suspend_until - now).total_seconds() / 60
            logger.warning(
                "[FORGE-02][%s] Resume blocked — %.1f minutes remaining in suspension.",
                self.eval_id, remaining
            )
            return
        self._state = EvalState.ACTIVE
        self._suspend_reason = None
        self._suspend_until  = None
        logger.info("[FORGE-02][%s] ▶ RESUMED.", self.eval_id)

    def _mark_passed(self, as_of: datetime) -> None:
        """Internal: mark the evaluation as passed."""
        self._state     = EvalState.PASSED
        self._passed_at = as_of
        logger.info(
            "[FORGE-02][%s] ✅ PASSED! Profit: $%.2f / $%.2f (%.1f%%). "
            "Trading days: %d. Calendar days: %d.",
            self.eval_id,
            self._current_balance - self._starting_balance,
            self._profit_target_dollars,
            ((self._current_balance - self._starting_balance) / self._profit_target_dollars) * 100,
            self._count_trading_days(),
            (as_of.date() - self.start_date).days,
        )

    def _mark_failed(self, reason: str, as_of: datetime) -> None:
        """Internal: mark the evaluation as failed."""
        self._state          = EvalState.FAILED
        self._failure_reason = reason
        self._failed_at      = as_of
        logger.error(
            "[FORGE-02][%s] ❌ FAILED: %s. Balance: $%.2f. Floor: $%.2f.",
            self.eval_id, reason, self._current_equity,
            self._get_current_floor()
        )

    def _mark_expired(self, as_of: datetime) -> None:
        """Internal: mark as expired (time limit exceeded)."""
        self._state = EvalState.EXPIRED
        logger.error(
            "[FORGE-02][%s] ⏰ EXPIRED. Calendar days: %d/%d. "
            "Profit: $%.2f / $%.2f.",
            self.eval_id,
            (as_of.date() - self.start_date).days,
            self._rules.max_calendar_days or 0,
            self._current_balance - self._starting_balance,
            self._profit_target_dollars,
        )

    # ── SESSION MANAGEMENT ────────────────────────────────────────────────────

    def _open_session(self, session_date: date, balance: float, equity: float) -> None:
        """Open a new trading day session."""
        if self._current_session and not self._current_session.session_closed:
            # Close the previous session first
            self._current_session.close_session(self._current_equity)
            self._sessions.append(self._current_session)
            self._update_profitable_session_streak(self._current_session)

        self._current_session = SessionSnapshot(
            session_date=session_date,
            opening_balance=balance,
            opening_equity=equity,
        )
        self._today = session_date
        self._session_open_equity  = equity
        self._session_open_balance = balance
        logger.debug(
            "[FORGE-02][%s] 📅 Session opened: %s. Balance: $%.2f.",
            self.eval_id, session_date.isoformat(), balance
        )

    def _update_profitable_session_streak(self, session: SessionSnapshot) -> None:
        """Update consecutive profitable session count after a session closes."""
        if session.gross_pnl > 0:
            self._consecutive_profitable_sessions += 1
        else:
            self._consecutive_profitable_sessions = 0

    def advance_session(self, new_date: date, closing_equity: float) -> None:
        """
        Call this at end-of-day or start of new trading day.
        Closes the current session, opens the next.
        Also checks time-based expiry (Apex 30-day limit).
        """
        if self.is_terminal:
            return
        self._open_session(new_date, self._current_balance, closing_equity)
        # Check calendar deadline
        self._check_time_expiry(new_date)

    def _check_time_expiry(self, as_of_date: date) -> None:
        """Check if the calendar deadline has passed (Apex 30-day hard limit)."""
        if not self._rules.max_calendar_days:
            return
        elapsed = (as_of_date - self.start_date).days
        if elapsed > self._rules.max_calendar_days:
            self._mark_expired(datetime.combine(as_of_date, datetime.min.time(),
                                                tzinfo=timezone.utc))

    # ── EQUITY UPDATES (per tick) ─────────────────────────────────────────────

    def update_equity(
        self,
        current_equity:  float,
        peak_unrealized: float = 0.0,
        as_of:           Optional[datetime] = None,
    ) -> Optional[str]:
        """
        Called on every significant equity tick from the broker.
        Updates the live picture and checks for immediate failure conditions.

        Args:
            current_equity:  Current account equity (balance + open P&L).
            peak_unrealized: Highest unrealized P&L reached on currently open position.
            as_of:           Timestamp of this tick.

        Returns:
            Alert string if a critical threshold is crossed, else None.
        """
        if self.is_terminal or self._state == EvalState.IDLE:
            return None

        now = as_of or datetime.now(timezone.utc)
        self._current_equity = current_equity
        self._peak_unrealized = max(self._peak_unrealized, peak_unrealized)

        # Update Apex trailing floor if peak unrealized has risen
        if self._rules.drawdown_type == DrawdownType.TRAILING_UNREALIZED:
            base_floor = self._starting_balance - (self._starting_balance * self._rules.total_drawdown_pct)
            self._apex_trailing_floor = base_floor + self._peak_unrealized

        alert = None

        # ── Check for FAILURE: equity at or below firm floor ─────────────────
        floor = self._get_current_floor()
        if current_equity <= floor:
            reason = (
                f"Equity ${current_equity:,.2f} breached firm floor ${floor:,.2f}. "
                f"Drawdown type: {self._rules.drawdown_type.value}."
            )
            self._mark_failed(reason, now)
            return f"[FAILED] {reason}"

        # ── Check daily limit ─────────────────────────────────────────────────
        if self._rules.daily_drawdown_pct:
            daily_limit_dollars = self._session_open_equity * self._rules.daily_drawdown_pct
            daily_used = self._session_open_equity - current_equity
            if daily_used >= daily_limit_dollars:
                if self.firm_id == FirmID.APEX:
                    # Apex: circuit breaker — session ends, account NOT failed
                    if self._current_session:
                        self._current_session.circuit_breaker_hit = True
                    self.suspend(SuspendReason.CIRCUIT_BREAKER)
                    alert = (
                        f"[CIRCUIT BREAKER][{self.eval_id}] Apex daily limit hit. "
                        f"Session ended. Account NOT failed. Resume tomorrow."
                    )
                    logger.warning(alert)
                else:
                    # Other firms: daily limit breach = account FAILED
                    reason = (
                        f"Daily drawdown limit breached. "
                        f"Used: ${daily_used:,.2f} / Limit: ${daily_limit_dollars:,.2f}."
                    )
                    self._mark_failed(reason, now)
                    return f"[FAILED] {reason}"

        # ── Threshold alerts (FORGE-67) ───────────────────────────────────────
        total_budget = self._starting_balance * self._rules.total_drawdown_pct
        pct_used = max(0.0, (self._starting_balance - current_equity + self._peak_unrealized
                              if self._rules.drawdown_type == DrawdownType.TRAILING_UNREALIZED
                              else self._starting_balance - current_equity) / total_budget)

        if pct_used >= 0.85 and not alert:
            alert = (
                f"[RED][{self.eval_id}] 85% drawdown used. "
                f"CLOSE ALL POSITIONS. Distance to floor: ${current_equity - floor:,.2f}."
            )
            logger.critical(alert)
        elif pct_used >= 0.70 and not alert:
            alert = (
                f"[ORANGE][{self.eval_id}] 70% drawdown used. "
                f"Minimum position size only. Distance to floor: ${current_equity - floor:,.2f}."
            )
            logger.error(alert)
        elif pct_used >= 0.50 and not alert:
            alert = (
                f"[YELLOW][{self.eval_id}] 50% drawdown used. "
                f"-25% position size. Distance to floor: ${current_equity - floor:,.2f}."
            )
            logger.warning(alert)

        self._snapshot_seq += 1
        return alert

    # ── TRADE RECORDING ───────────────────────────────────────────────────────

    def record_trade(
        self,
        trade: TradeRecord,
        as_of: Optional[datetime] = None,
    ) -> Optional[str]:
        """
        Record a completed trade. Updates balance, streaks, session metrics.
        Checks for pass condition after every winning trade.

        Returns alert string on critical events, None otherwise.
        """
        if self.is_terminal:
            return None
        if self._state not in (EvalState.ACTIVE, EvalState.SUSPENDED):
            return None

        now = as_of or datetime.now(timezone.utc)

        # Ensure we have an open session
        if self._current_session is None:
            self._open_session(trade.session_date, self._current_balance, self._current_equity)

        # If trade is on a different day than current session: advance
        if trade.session_date != self._current_session.session_date:
            self.advance_session(trade.session_date, self._current_equity)

        # Update realized balance
        self._current_balance += trade.pnl
        self._current_equity   = self._current_balance  # Will be updated by next tick

        # Update peak unrealized (Apex trailing)
        self._peak_unrealized = max(self._peak_unrealized, trade.unrealized_peak)

        # Update session
        self._current_session.record_trade(
            trade,
            qualifying_threshold=self._rules.qualifying_day_profit
        )

        # Update streak counters
        if trade.is_win:
            self._consecutive_wins   += 1
            self._consecutive_losses  = 0
        else:
            self._consecutive_losses += 1
            self._consecutive_wins    = 0

        # Append to ledger
        self._trades.append(trade)

        # Log the trade
        pnl_sign = "+" if trade.pnl >= 0 else ""
        logger.info(
            "[FORGE-02][%s] Trade recorded: %s | %s%s | Balance: $%.2f | "
            "Profit: $%.2f / $%.2f (%.1f%%)",
            self.eval_id, trade.setup_type, pnl_sign, f"{trade.pnl:,.2f}",
            self._current_balance,
            self._current_balance - self._starting_balance,
            self._profit_target_dollars,
            ((self._current_balance - self._starting_balance) / self._profit_target_dollars) * 100
        )

        # Check streak detector (FORGE-15 integration point)
        if self._consecutive_losses >= 3:
            logger.warning(
                "[FORGE-02][%s] ⚠ %d consecutive losses — Streak Detector threshold.",
                self.eval_id, self._consecutive_losses
            )

        # Check pass condition
        alert = self._check_pass_condition(now)
        if alert:
            return alert

        # Check floor breach (realized balance)
        floor = self._get_current_floor()
        if self._current_balance <= floor:
            reason = (
                f"Realized balance ${self._current_balance:,.2f} "
                f"breached firm floor ${floor:,.2f}."
            )
            self._mark_failed(reason, now)
            return f"[FAILED] {reason}"

        self._snapshot_seq += 1
        return None

    def _check_pass_condition(self, as_of: datetime) -> Optional[str]:
        """
        Check whether all pass conditions are simultaneously met.
        All of: profit target + min trading days + drawdown intact.
        """
        profit = self._current_balance - self._starting_balance
        profit_gate = profit >= self._profit_target_dollars
        time_gate   = self._trading_days_requirement_met()
        floor_ok    = self._current_balance > self._get_current_floor()

        if profit_gate and time_gate and floor_ok:
            self._mark_passed(as_of)
            return (
                f"[PASSED][{self.eval_id}] All gates cleared. "
                f"Profit: ${profit:,.2f} ✓ | "
                f"Trading days: {self._count_trading_days()} ✓ | "
                f"Drawdown intact ✓"
            )
        return None

    # ── HELPER CALCULATIONS ───────────────────────────────────────────────────

    def _get_current_floor(self) -> float:
        """Calculate the current firm floor based on drawdown type."""
        rules = self._rules
        if rules.drawdown_type == DrawdownType.STATIC:
            return self._starting_balance - (self._starting_balance * rules.total_drawdown_pct)
        elif rules.drawdown_type == DrawdownType.TRAILING_UNREALIZED:
            return self._apex_trailing_floor
        elif rules.drawdown_type == DrawdownType.TRAILING_EOD:
            base = self._session_open_equity - (self._starting_balance * rules.total_drawdown_pct)
            lock_trigger = self._starting_balance * 1.10
            return self._starting_balance if self._session_open_equity >= lock_trigger else base
        elif rules.drawdown_type == DrawdownType.STATIC_EOD_SNAPSHOT:
            return self._starting_balance - (self._starting_balance * rules.total_drawdown_pct)
        return self._starting_balance - (self._starting_balance * rules.total_drawdown_pct)

    def _count_trading_days(self) -> int:
        """Count days with at least one trade."""
        traded_dates = {t.session_date for t in self._trades}
        if self._current_session and self._current_session.trade_count > 0:
            traded_dates.add(self._current_session.session_date)
        return len(traded_dates)

    def _trading_days_requirement_met(self) -> bool:
        """True if minimum trading days requirement is satisfied."""
        if not self._rules.min_trading_days:
            return True
        return self._count_trading_days() >= self._rules.min_trading_days

    def _calculate_pass_probability(self) -> float:
        """
        FORGE-32: Hourly mathematical pass probability.
        Simplified scoring — full mathematical model in Phase 2.

        Components:
          - Profit completion %        (40% weight)
          - Time remaining adequacy    (25% weight)
          - Drawdown buffer health     (20% weight)
          - Pace (on track)            (15% weight)
        """
        if self.is_terminal:
            return 1.0 if self._state == EvalState.PASSED else 0.0
        if self._state == EvalState.IDLE:
            return 0.5  # No information yet

        profit        = self._current_balance - self._starting_balance
        profit_pct    = profit / self._profit_target_dollars if self._profit_target_dollars > 0 else 0.0
        profit_score  = min(1.0, max(0.0, profit_pct))

        floor         = self._get_current_floor()
        total_budget  = self._starting_balance - floor
        dd_remaining  = self._current_equity - floor
        dd_health     = min(1.0, max(0.0, dd_remaining / total_budget)) if total_budget > 0 else 0.0

        # Time adequacy: how much time is left vs how much profit is left
        if self._rules.max_calendar_days:
            elapsed  = (self._today - self.start_date).days
            remaining = self._rules.max_calendar_days - elapsed
            time_score = min(1.0, max(0.0, remaining / self._rules.max_calendar_days))
        else:
            time_score = 0.8  # No time limit — generous score

        # Pace score: are we on track?
        if self._rules.max_calendar_days and elapsed > 0:
            days_total = self._rules.max_calendar_days
            expected_profit_pct = elapsed / days_total
            pace_score = min(1.0, profit_pct / expected_profit_pct) if expected_profit_pct > 0 else 0.5
        else:
            pace_score = min(1.0, profit_pct + 0.3)  # No time limit — pace not critical

        probability = (
            profit_score * 0.40 +
            time_score   * 0.25 +
            dd_health    * 0.20 +
            pace_score   * 0.15
        )

        # Hard floor: if below 30% — survival mode
        if probability < 0.30:
            logger.warning(
                "[FORGE-02][%s] Pass probability below 30%%: %.1f%%. "
                "Entering survival mode.",
                self.eval_id, probability * 100
            )

        return round(min(1.0, max(0.0, probability)), 4)

    def _is_on_pace(self) -> bool:
        """Quick check: is daily profit pace sufficient to hit target in time?"""
        profit = self._current_balance - self._starting_balance
        if profit >= self._profit_target_dollars:
            return True
        if not self._rules.max_calendar_days:
            return True
        elapsed  = max(1, (self._today - self.start_date).days)
        required_daily = (self._profit_target_dollars - profit) / max(
            1, self._rules.max_calendar_days - elapsed
        )
        total_daily_avg = profit / elapsed if elapsed > 0 else 0.0
        return total_daily_avg >= required_daily * 0.75  # 75% of required is "on pace"

    # ── SNAPSHOT ──────────────────────────────────────────────────────────────

    def snapshot(self, as_of: Optional[date] = None) -> EvaluationSnapshot:
        """
        Build the complete real-time picture of this evaluation.
        Called by ARCHITECT, Dashboard (FORGE-31), Pacing Engine (FORGE-04).
        """
        today           = as_of or self._today
        profit          = self._current_balance - self._starting_balance
        profit_remaining = max(0.0, self._profit_target_dollars - profit)
        profit_pct      = profit / self._profit_target_dollars if self._profit_target_dollars > 0 else 0.0

        floor           = self._get_current_floor()
        total_dd_budget = self._starting_balance * self._rules.total_drawdown_pct
        dd_used         = max(0.0, self._starting_balance - self._current_equity +
                              (self._peak_unrealized if self._rules.drawdown_type ==
                               DrawdownType.TRAILING_UNREALIZED else 0.0))
        dd_remaining    = max(0.0, self._current_equity - floor)
        dd_pct_used     = min(1.0, dd_used / total_dd_budget) if total_dd_budget > 0 else 0.0

        # Daily drawdown
        if self._rules.daily_drawdown_pct:
            daily_limit = self._session_open_equity * self._rules.daily_drawdown_pct
            daily_used  = max(0.0, self._session_open_equity - self._current_equity)
            daily_remaining = max(0.0, daily_limit - daily_used)
            daily_pct_used  = min(1.0, daily_used / daily_limit) if daily_limit > 0 else 0.0
        else:
            daily_limit = float("inf")
            daily_used  = 0.0
            daily_remaining = float("inf")
            daily_pct_used  = 0.0

        # Time
        calendar_elapsed   = (today - self.start_date).days
        calendar_remaining = (
            max(0, self._rules.max_calendar_days - calendar_elapsed)
            if self._rules.max_calendar_days else None
        )
        trading_days = self._count_trading_days()
        min_days     = self._rules.min_trading_days or 0
        days_still_needed = max(0, min_days - trading_days)

        # Thresholds — total drawdown
        at_yellow = dd_pct_used >= 0.50
        at_orange = dd_pct_used >= 0.70
        at_red    = dd_pct_used >= 0.85

        # Daily loss tiered stops (Bug Fix GEN-BUG-04)
        # Protects the firm's 5% daily limit with a 2-tier buffer
        # Soft stop at 4% (80% of 5% limit): half size
        # Hard stop at 4.5% (90% of 5% limit): no new trades
        if daily_limit and daily_limit != float("inf") and daily_limit > 0:
            _daily_loss_pct = daily_used / daily_limit  # 0.0–1.0 fraction of daily limit
        else:
            _daily_loss_pct = 0.0

        daily_soft_stop   = _daily_loss_pct >= 0.80   # 4% of daily limit used
        daily_hard_stop   = _daily_loss_pct >= 0.90   # 4.5% of daily limit used

        # Size multiplier based on daily loss level
        if daily_hard_stop:
            _daily_size_mult = 0.0   # No new trades
        elif daily_soft_stop:
            _daily_size_mult = 0.5   # Half size
        elif _daily_loss_pct >= 0.60:
            _daily_size_mult = 0.75  # 3% used: 75% size
        else:
            _daily_size_mult = 1.0   # Full size

        # Gates
        profit_gate = profit >= self._profit_target_dollars
        time_gate   = self._trading_days_requirement_met()
        dd_intact   = self._current_equity > floor
        deadline_ok = calendar_remaining is None or calendar_remaining > 0
        daily_ok    = daily_used < daily_limit if daily_limit != float("inf") else True

        return EvaluationSnapshot(
            eval_id                     = self.eval_id,
            firm_id                     = self.firm_id,
            account_size                = self.account_size,
            phase                       = self.phase,
            state                       = self._state,
            starting_balance            = self._starting_balance,
            current_balance             = self._current_balance,
            current_equity              = self._current_equity,
            peak_unrealized_ever        = self._peak_unrealized,
            profit_target_dollars       = self._profit_target_dollars,
            current_profit              = profit,
            profit_remaining            = profit_remaining,
            profit_pct_complete         = max(0.0, min(1.0, profit_pct)),
            on_pace                     = self._is_on_pace(),
            total_drawdown_limit        = total_dd_budget,
            drawdown_used               = dd_used,
            drawdown_remaining          = dd_remaining,
            drawdown_pct_used           = dd_pct_used,
            firm_floor                  = floor,
            daily_drawdown_limit        = daily_limit,
            daily_drawdown_used         = daily_used,
            daily_drawdown_remaining    = daily_remaining,
            daily_pct_used              = daily_pct_used,
            start_date                  = self.start_date,
            today                       = today,
            calendar_days_elapsed       = calendar_elapsed,
            calendar_days_remaining     = calendar_remaining,
            trading_days_completed      = trading_days,
            min_trading_days            = self._rules.min_trading_days,
            trading_days_still_needed   = days_still_needed,
            deadline_met                = deadline_ok,
            can_pass_today              = profit_gate and time_gate and dd_intact and daily_ok,
            profit_gate_met             = profit_gate,
            time_gate_met               = time_gate,
            drawdown_intact             = dd_intact,
            daily_limit_intact          = daily_ok,
            pass_probability            = self._calculate_pass_probability(),
            at_yellow                   = at_yellow,
            at_orange                   = at_orange,
            at_red                      = at_red,
            daily_soft_stop             = daily_soft_stop,
            daily_hard_stop             = daily_hard_stop,
            daily_loss_pct              = round(_daily_loss_pct, 4),
            daily_size_multiplier       = _daily_size_mult,
            consecutive_losses          = self._consecutive_losses,
            consecutive_wins            = self._consecutive_wins,
            consecutive_profitable_sessions = self._consecutive_profitable_sessions,
            total_trades                = len(self._trades),
            session_trade_count_today   = (
                self._current_session.trade_count
                if self._current_session else 0
            ),
            is_suspended                = self._state == EvalState.SUSPENDED,
            suspend_reason              = self._suspend_reason,
            suspend_until               = self._suspend_until,
            last_updated                = datetime.now(timezone.utc),
            snapshot_sequence           = self._snapshot_seq,
        )

    # ── REPORTING HELPERS ─────────────────────────────────────────────────────

    def status_line(self) -> str:
        """Compact one-line status for logging and alerts."""
        snap = self.snapshot()
        return (
            f"[{self.eval_id}] {self.firm_id} {self.phase.name} | "
            f"State: {snap.state.name} | "
            f"Profit: ${snap.current_profit:+,.2f} / ${snap.profit_target_dollars:,.2f} "
            f"({snap.profit_pct_complete:.1%}) | "
            f"DD used: {snap.drawdown_pct_used:.1%} | "
            f"Days: {snap.trading_days_completed} traded | "
            f"P(pass): {snap.pass_probability:.1%}"
        )

    @property
    def failure_reason(self) -> Optional[str]:
        return self._failure_reason

    @property
    def sessions(self) -> list[SessionSnapshot]:
        closed = list(self._sessions)
        if self._current_session and not self._current_session.session_closed:
            closed.append(self._current_session)
        return closed

    @property
    def trades(self) -> list[TradeRecord]:
        return list(self._trades)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — EVALUATION ORCHESTRATOR
# FORGE-28: Multiple simultaneous evaluations tracked independently.
# Never confuses one firm's rules with another's.
# ─────────────────────────────────────────────────────────────────────────────

class EvaluationOrchestrator:
    """
    FORGE-28: Simultaneous Evaluation Orchestration.

    Manages multiple EvaluationStateMachine instances in parallel.
    Each evaluation runs with its own rule set — firm rules never bleed across.

    Usage:
        orch = EvaluationOrchestrator(rule_engine)
        eval_id = orch.start_evaluation(FirmID.FTMO, 100_000, EvalPhase.PHASE_1)
        eval_id2 = orch.start_evaluation(FirmID.APEX, 50_000, EvalPhase.SINGLE)

        # Record trade on FTMO only
        orch.record_trade(eval_id, trade)

        # Update equity on Apex only
        orch.update_equity(eval_id2, equity=51_200, peak_unrealized=300)

        # Full picture of all evaluations
        for snap in orch.all_snapshots():
            print(snap.status_line())
    """

    def __init__(self, rule_engine: MultiFirmRuleEngine):
        self._rule_engine  = rule_engine
        self._evaluations: dict[str, EvaluationStateMachine] = {}

    def start_evaluation(
        self,
        firm_id:      str,
        account_size: float,
        phase:        EvalPhase,
        start_date:   Optional[date] = None,
        eval_id:      Optional[str]  = None,
    ) -> str:
        """
        Create and start a new evaluation. Returns the eval_id.
        Each evaluation is independent — its firm's rules never affect others.
        """
        esm = EvaluationStateMachine(
            firm_id=firm_id,
            account_size=account_size,
            phase=phase,
            start_date=start_date or date.today(),
            rule_engine=self._rule_engine,
            eval_id=eval_id,
        )
        esm.start()
        self._evaluations[esm.eval_id] = esm
        logger.info(
            "[FORGE-28] New evaluation started: %s | %s | $%s | %s",
            esm.eval_id, firm_id, f"{account_size:,.0f}", phase.name
        )
        return esm.eval_id

    def get(self, eval_id: str) -> EvaluationStateMachine:
        """Retrieve a specific evaluation. Raises KeyError if not found."""
        if eval_id not in self._evaluations:
            raise KeyError(f"Evaluation '{eval_id}' not found.")
        return self._evaluations[eval_id]

    def record_trade(self, eval_id: str, trade: TradeRecord) -> Optional[str]:
        """Record a trade on a specific evaluation."""
        return self.get(eval_id).record_trade(trade)

    def update_equity(
        self, eval_id: str, current_equity: float, peak_unrealized: float = 0.0
    ) -> Optional[str]:
        """Update live equity on a specific evaluation."""
        return self.get(eval_id).update_equity(current_equity, peak_unrealized)

    def snapshot(self, eval_id: str) -> EvaluationSnapshot:
        """Get snapshot of a specific evaluation."""
        return self.get(eval_id).snapshot()

    def all_snapshots(self) -> list[EvaluationSnapshot]:
        """Get snapshots of ALL active (non-terminal) evaluations."""
        return [
            esm.snapshot()
            for esm in self._evaluations.values()
            if not esm.is_terminal
        ]

    def all_status_lines(self) -> list[str]:
        """One-line status for every tracked evaluation."""
        return [esm.status_line() for esm in self._evaluations.values()]

    @property
    def active_count(self) -> int:
        return sum(1 for esm in self._evaluations.values() if esm.is_active)

    @property
    def passed_count(self) -> int:
        return sum(1 for esm in self._evaluations.values()
                   if esm.state == EvalState.PASSED)

    @property
    def failed_count(self) -> int:
        return sum(1 for esm in self._evaluations.values()
                   if esm.state == EvalState.FAILED)
