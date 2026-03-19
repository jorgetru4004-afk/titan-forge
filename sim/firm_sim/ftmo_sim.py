"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  sim/firm_sim/ftmo_sim.py — Exact FTMO rule implementation for simulation   ║
║  Section 12: "firm_sim/ — exact rule implementations per firm"              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class FTMOSimAccount:
    """
    Exact FTMO rule simulation.
    Phase 1: 10% profit target, 5% daily loss, 10% total drawdown.
    Phase 2: 5% profit target, 5% daily loss, 10% total drawdown.
    No time limit. No consistency rule.
    """
    account_size:       float = 100_000.0
    phase:              int   = 1         # 1 = challenge, 2 = verification

    # State
    balance:            float = field(init=False)
    equity:             float = field(init=False)
    daily_start_balance: float = field(init=False)
    peak_balance:       float = field(init=False)
    trading_days:       int   = 0
    current_date:       date  = field(default_factory=date.today)

    # Rules (FTMO specific)
    PROFIT_TARGET_P1:   float = 0.10   # 10%
    PROFIT_TARGET_P2:   float = 0.05   # 5%
    DAILY_LOSS_LIMIT:   float = 0.05   # 5% of start-of-day balance
    TOTAL_DRAWDOWN:     float = 0.10   # 10% from initial

    def __post_init__(self):
        self.balance            = self.account_size
        self.equity             = self.account_size
        self.daily_start_balance = self.account_size
        self.peak_balance       = self.account_size

    @property
    def profit_target(self) -> float:
        pct = self.PROFIT_TARGET_P1 if self.phase == 1 else self.PROFIT_TARGET_P2
        return self.account_size * pct

    @property
    def profit_achieved(self) -> float:
        return self.balance - self.account_size

    @property
    def daily_loss_limit_dollars(self) -> float:
        return self.daily_start_balance * self.DAILY_LOSS_LIMIT

    @property
    def total_floor(self) -> float:
        return self.account_size * (1.0 - self.TOTAL_DRAWDOWN)  # $90,000 for $100K

    @property
    def drawdown_used_pct(self) -> float:
        budget = self.account_size * self.TOTAL_DRAWDOWN
        used   = self.account_size - self.equity
        return min(1.0, max(0.0, used / budget)) if budget > 0 else 0.0

    @property
    def daily_loss_today(self) -> float:
        return max(0.0, self.daily_start_balance - self.equity)

    @property
    def is_daily_limit_breached(self) -> bool:
        return self.daily_loss_today >= self.daily_loss_limit_dollars

    @property
    def is_total_drawdown_breached(self) -> bool:
        return self.equity < self.total_floor

    @property
    def is_target_met(self) -> bool:
        return self.profit_achieved >= self.profit_target

    @property
    def is_failed(self) -> bool:
        return self.is_daily_limit_breached or self.is_total_drawdown_breached

    def apply_trade(self, pnl: float) -> None:
        self.equity  += pnl
        self.balance += pnl
        if self.equity > self.peak_balance:
            self.peak_balance = self.equity

    def advance_day(self, new_date: date) -> None:
        self.current_date       = new_date
        self.daily_start_balance = self.balance
        self.trading_days      += 1

    def status(self) -> str:
        if self.is_target_met:
            return "PASSED"
        if self.is_failed:
            return "FAILED"
        return "IN_PROGRESS"


@dataclass
class APEXSimAccount:
    """
    Exact Apex Trader Funding rule simulation.
    MOST DANGEROUS RULE: trailing drawdown on UNREALIZED P&L.
    $50K account: 6% profit target, 30-day time limit.
    Safety net: $52,600 before full sizing.
    """
    account_size:       float = 50_000.0
    PROFIT_TARGET:      float = 0.06    # 6%
    DAILY_LIMIT:        float = 1_500.0 # Fixed dollar amount
    TIME_LIMIT_DAYS:    int   = 30
    TRAILING_DRAWDOWN:  float = 0.06    # 6% trailing on UNREALIZED

    balance:            float = field(init=False)
    equity:             float = field(init=False)
    trailing_high:      float = field(init=False)  # Highest equity ever
    trading_days:       int   = 0
    daily_loss_today:   float = 0.0
    safety_net_dollars: float = 52_600.0

    def __post_init__(self):
        self.balance        = self.account_size
        self.equity         = self.account_size
        self.trailing_high  = self.account_size

    @property
    def trailing_floor(self) -> float:
        """The floor moves UP as equity rises — most dangerous Apex rule."""
        return self.trailing_high - (self.account_size * self.TRAILING_DRAWDOWN)

    @property
    def safety_net_met(self) -> bool:
        return (self.balance - self.account_size) >= (self.safety_net_dollars - self.account_size)

    @property
    def is_total_drawdown_breached(self) -> bool:
        return self.equity < self.trailing_floor

    @property
    def is_daily_limit_breached(self) -> bool:
        return self.daily_loss_today >= self.DAILY_LIMIT

    @property
    def is_time_expired(self) -> bool:
        return self.trading_days >= self.TIME_LIMIT_DAYS

    @property
    def is_target_met(self) -> bool:
        return self.balance >= self.account_size * (1.0 + self.PROFIT_TARGET)

    @property
    def is_failed(self) -> bool:
        return (
            self.is_total_drawdown_breached or
            self.is_daily_limit_breached or
            self.is_time_expired
        )

    def apply_trade(self, pnl: float) -> None:
        self.equity += pnl
        self.balance += pnl
        # CRITICAL: Trailing high updates on UNREALIZED gains too
        if self.equity > self.trailing_high:
            self.trailing_high = self.equity

    def advance_day(self) -> None:
        self.daily_loss_today = 0.0
        self.trading_days += 1

    def status(self) -> str:
        if self.is_target_met:
            return "PASSED"
        if self.is_failed:
            return "FAILED"
        return "IN_PROGRESS"
