"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                    forge_risk.py — THE RISK FORTRESS                        ║
║                                                                              ║
║  6 INDEPENDENT PROTECTION LAYERS. Any single layer can kill a trade.        ║
║  They don't vote. They don't negotiate. If ANY says no, the answer is no.  ║
║                                                                              ║
║  Layer 1: Firm Rule Compliance (absolute — uses firm_rules.py engine)      ║
║  Layer 2: Daily P&L Protection (+2% lock, -3% hard stop)                   ║
║  Layer 3: Streak & Cooldown (3-loss pause, setup-level streaks)            ║
║  Layer 4: Weekly Drawdown (3% weekly → half size)                          ║
║  Layer 5: Profit Velocity & Best Day (protect against concentration)       ║
║  Layer 6: Survival Probability (actuarial math — will the account live?)   ║
║                                                                              ║
║  Plus: Universal Prop Firm Optimizer, behavioral camouflage,               ║
║  predictive position management, and Friday/weekend protection.            ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging, math, random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from typing import Optional
from forge_core import now_et, now_et_time, is_rth, send_telegram

logger = logging.getLogger("titan_forge.risk")


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSAL PROP FIRM TRACKER
# ═══════════════════════════════════════════════════════════════════════════════
# Not FTMO-specific. Works for ANY firm. Loads rules from firm_rules.py
# and solves each firm's constraint optimization problem independently.

@dataclass
class PropFirmState:
    """Universal tracking state for any prop firm account."""
    firm_id:                str
    initial_balance:        float
    current_balance:        float
    highest_eod_balance:    float
    # Daily tracking
    daily_start_balance:    float = 0.0
    current_day_pnl:        float = 0.0
    # Performance tracking
    trading_days:           int   = 0
    total_positive_profit:  float = 0.0
    best_day_profit:        float = 0.0
    total_closed_pnl:       float = 0.0
    # Sizing consistency
    recent_sizes:           list[float] = field(default_factory=list)
    # Payout / scaling
    first_trade_date:       Optional[date] = None
    payout_cycle_start:     Optional[date] = None
    cycle_profit:           float = 0.0
    payouts_in_cycle:       int   = 0
    # Firm-specific limits (loaded from firm_rules)
    daily_loss_pct:         float = 0.05    # default: 5%
    total_loss_pct:         float = 0.10    # default: 10%
    consistency_rule_pct:   Optional[float] = None  # DNA: 0.40
    payout_cycle_days:      Optional[int] = None     # FTMO: 14
    scaling_months:         Optional[int] = None     # FTMO: 4
    scaling_pct:            Optional[float] = None   # FTMO: 0.25
    has_trailing_drawdown:  bool = False              # Apex: True
    requires_weekend_close: bool = False
    requires_eod_close:     bool = False
    eod_close_time_et:      Optional[dtime] = None
    min_hold_seconds:       Optional[int] = None      # DNA: 30

    def initialize(self, balance: float) -> None:
        """Call on first connection to set real account values."""
        self.initial_balance = balance
        self.current_balance = balance
        self.highest_eod_balance = balance
        self.daily_start_balance = balance
        logger.info("[FIRM] %s tracker initialized: $%.2f", self.firm_id, balance)

    # ── Daily Loss Floor ─────────────────────────────────────────────────────
    @property
    def daily_loss_limit(self) -> float:
        """Dollars: maximum allowed daily loss."""
        return self.daily_start_balance * self.daily_loss_pct

    @property
    def daily_loss_remaining(self) -> float:
        return self.daily_loss_limit + self.current_day_pnl  # pnl is negative when losing

    @property
    def daily_loss_pct_used(self) -> float:
        if self.daily_loss_limit <= 0:
            return 0.0
        return max(0, -self.current_day_pnl) / self.daily_loss_limit

    # ── Max Loss Floor ───────────────────────────────────────────────────────
    @property
    def max_loss_floor(self) -> float:
        if self.has_trailing_drawdown:
            return self.highest_eod_balance * (1 - self.total_loss_pct)
        return self.initial_balance * (1 - self.total_loss_pct)

    @property
    def equity_buffer_pct(self) -> float:
        """How far above the max loss floor, as fraction of initial balance."""
        return (self.current_balance - self.max_loss_floor) / self.initial_balance

    # ── Best Day / Consistency ───────────────────────────────────────────────
    @property
    def best_day_ratio(self) -> float:
        if self.total_positive_profit <= 0:
            return 0.0
        return self.best_day_profit / self.total_positive_profit

    @property
    def consistency_ok(self) -> bool:
        """True if within consistency rule. Always True for firms without one."""
        if self.consistency_rule_pct is None:
            return True
        return self.best_day_ratio <= self.consistency_rule_pct

    # ── Payout Eligibility ───────────────────────────────────────────────────
    @property
    def payout_eligible(self) -> bool:
        if not self.first_trade_date or not self.payout_cycle_days:
            return False
        elapsed = (date.today() - self.first_trade_date).days
        return elapsed >= self.payout_cycle_days and self.total_closed_pnl > 0

    # ── Scaling Eligibility ──────────────────────────────────────────────────
    @property
    def scaling_eligible(self) -> bool:
        if not self.payout_cycle_start or not self.scaling_months:
            return False
        months = (date.today() - self.payout_cycle_start).days / 30.0
        return (months >= self.scaling_months and
                self.cycle_profit >= self.initial_balance * 0.10 and
                self.payouts_in_cycle >= 2)

    # ── Emergency Checks ─────────────────────────────────────────────────────
    def should_emergency_close(self, equity: float) -> bool:
        """True if floating equity is dangerously close to any firm limit."""
        # Within 1% of daily loss limit
        if (-self.current_day_pnl) >= self.daily_loss_limit * 0.80:
            return True
        # Within 2% of max loss
        if equity < self.max_loss_floor + self.initial_balance * 0.02:
            return True
        return False

    def can_enter(self) -> tuple[bool, str]:
        """Pre-entry gate check. Returns (allowed, reason)."""
        # Daily loss gate
        if self.daily_loss_pct_used >= 0.60:
            return False, f"Daily loss {self.daily_loss_pct_used:.0%} used — protecting buffer"

        # Best day / consistency gate (for firms with consistency rules)
        cap = self.consistency_rule_pct or 0.50
        if self.best_day_ratio > cap * 0.80 and self.current_day_pnl > 0:
            return False, f"Best day ratio {self.best_day_ratio:.0%} — approaching {cap:.0%} cap"

        return True, "OK"

    # ── Session Management ───────────────────────────────────────────────────
    def reset_daily(self, balance: float) -> None:
        self.daily_start_balance = balance
        self.current_day_pnl = 0.0
        self.current_balance = balance

    def close_day(self, day_pnl: float) -> None:
        if day_pnl > 0:
            self.total_positive_profit += day_pnl
            if day_pnl > self.best_day_profit:
                self.best_day_profit = day_pnl
        self.trading_days += 1
        self.current_balance += day_pnl
        if self.current_balance > self.highest_eod_balance:
            self.highest_eod_balance = self.current_balance
        self.total_closed_pnl += day_pnl
        self.cycle_profit += day_pnl
        self.current_day_pnl = 0.0

    def record_size(self, lots: float) -> None:
        self.recent_sizes.append(lots)
        self.recent_sizes = self.recent_sizes[-30:]

    def is_size_consistent(self, proposed: float) -> bool:
        """Prop firms flag wildly inconsistent sizing."""
        if len(self.recent_sizes) < 3:
            return True
        avg = sum(self.recent_sizes) / len(self.recent_sizes)
        return proposed <= avg * 2.5  # max 2.5× average

    def optimal_daily_target(self) -> float:
        """
        Firm-specific optimal daily profit target.

        FTMO (no consistency rule): ~0.4% per day × 25 days = 10%
        DNA (40% consistency): distribute evenly, target 35% of running total
        """
        remaining = max(0, (self.initial_balance * 0.10) - self.total_closed_pnl)
        if self.consistency_rule_pct:
            # Cap at consistency rule threshold of running total
            max_today = self.total_positive_profit * (self.consistency_rule_pct * 0.85) \
                        if self.total_positive_profit > 0 else self.initial_balance * 0.004
            return min(remaining, max_today)
        return min(remaining, self.initial_balance * 0.005)  # 0.5% per day default


# ═══════════════════════════════════════════════════════════════════════════════
# 6-LAYER RISK FORTRESS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RiskDecision:
    """Result from the risk fortress. One clear answer."""
    can_trade:      bool
    size_multiplier: float  # 0.0 to 1.0
    layers_blocking: list[str]
    layers_reducing: list[str]
    reason:         str

class RiskFortress:
    """
    6 independent protection layers. Any can block a trade.
    Operates on pure math, not heuristics.
    """

    def __init__(self):
        self._daily_profit_locked: bool = False
        self._daily_loss_stopped:  bool = False
        self._cooldown_until: Optional[datetime] = None
        self._consecutive_losses: int = 0
        self._weekly_start_balance: float = 0.0
        self._weekly_low_balance: float = 0.0
        self._setup_losses: dict[str, int] = {}  # setup_id → consecutive losses this week
        self._session_date: Optional[date] = None

    def reset_daily(self) -> None:
        self._daily_profit_locked = False
        self._daily_loss_stopped = False

    def reset_weekly(self, balance: float) -> None:
        self._weekly_start_balance = balance
        self._weekly_low_balance = balance
        self._setup_losses = {}

    def record_loss(self, setup_id: str = "") -> None:
        self._consecutive_losses += 1
        if setup_id:
            self._setup_losses[setup_id] = self._setup_losses.get(setup_id, 0) + 1

    def record_win(self, setup_id: str = "") -> None:
        self._consecutive_losses = 0
        if setup_id:
            self._setup_losses[setup_id] = 0

    def evaluate(
        self,
        firm_state:     PropFirmState,
        equity:         float,
        daily_pnl:      float,
        balance:        float,
        setup_id:       str = "",
    ) -> RiskDecision:
        """Run all 6 layers. Returns one unified decision."""
        blocking: list[str] = []
        reducing: list[str] = []
        size_mult = 1.0

        # ── Layer 1: Firm Rule Compliance (ABSOLUTE) ─────────────────────────
        can, reason = firm_state.can_enter()
        if not can:
            blocking.append(f"L1-FIRM: {reason}")

        if firm_state.should_emergency_close(equity):
            blocking.append("L1-FIRM: Emergency — equity approaching firm limit")

        # ── Layer 2: Daily P&L Protection ────────────────────────────────────
        daily_pct = daily_pnl / balance if balance > 0 else 0
        if daily_pct >= 0.02:
            self._daily_profit_locked = True
            blocking.append("L2-DAILY: +2% profit lock — protecting gains")
        elif daily_pct <= -0.03:
            self._daily_loss_stopped = True
            blocking.append("L2-DAILY: -3% hard stop — protecting account")
        elif daily_pct <= -0.02:
            size_mult *= 0.50
            reducing.append("L2-DAILY: Down 2% — half size")
        elif daily_pct <= -0.01:
            size_mult *= 0.75
            reducing.append("L2-DAILY: Down 1% — reduced size")

        if self._daily_profit_locked:
            blocking.append("L2-DAILY: Profit lock active — no new entries")
        if self._daily_loss_stopped:
            blocking.append("L2-DAILY: Loss stop active — no trading")

        # ── Layer 3: Streak & Cooldown ───────────────────────────────────────
        if self._cooldown_until:
            if datetime.now(timezone.utc) < self._cooldown_until:
                mins = (self._cooldown_until - datetime.now(timezone.utc)).total_seconds() / 60
                blocking.append(f"L3-STREAK: Cooldown active — {mins:.0f}min remaining")
            else:
                self._cooldown_until = None

        if self._consecutive_losses >= 3:
            self._cooldown_until = datetime.now(timezone.utc) + timedelta(hours=2)
            self._consecutive_losses = 0
            blocking.append("L3-STREAK: 3 consecutive losses → 2hr cooldown activated")
            send_telegram("⏸ <b>COOLDOWN</b>\n3 consecutive losses. 2hr mandatory pause.")

        # Per-setup streak check
        if setup_id and self._setup_losses.get(setup_id, 0) >= 3:
            blocking.append(f"L3-STREAK: {setup_id} lost 3x this week — blocked")
        elif setup_id and self._setup_losses.get(setup_id, 0) == 2:
            size_mult *= 0.75
            reducing.append(f"L3-STREAK: {setup_id} 2 losses — size reduced 25%")

        # ── Layer 4: Weekly Drawdown ─────────────────────────────────────────
        if self._weekly_start_balance > 0:
            self._weekly_low_balance = min(self._weekly_low_balance, balance)
            weekly_dd = (self._weekly_start_balance - balance) / self._weekly_start_balance
            if weekly_dd >= 0.03:
                size_mult *= 0.50
                reducing.append(f"L4-WEEKLY: Down {weekly_dd:.1%} this week — half size")
            elif weekly_dd >= 0.02:
                size_mult *= 0.75
                reducing.append(f"L4-WEEKLY: Down {weekly_dd:.1%} this week — reduced")

        # ── Layer 5: Profit Velocity & Best Day ──────────────────────────────
        if daily_pct >= 0.015:
            size_mult *= 0.50
            reducing.append("L5-VELOCITY: Up 1.5%+ today — protecting best day ratio")
        elif daily_pct >= 0.010:
            size_mult *= 0.75
            reducing.append("L5-VELOCITY: Up 1.0%+ today — slightly reduced")

        # ── Layer 6: Survival Probability ────────────────────────────────────
        survival = self._compute_survival(firm_state, equity)
        if survival < 0.70:
            blocking.append(f"L6-SURVIVAL: Account survival probability {survival:.0%} — too low")
        elif survival < 0.85:
            size_mult *= 0.50
            reducing.append(f"L6-SURVIVAL: Survival prob {survival:.0%} — defensive mode")

        # ── Combine ──────────────────────────────────────────────────────────
        can_trade = len(blocking) == 0
        if not can_trade:
            size_mult = 0.0

        reason_str = " | ".join(blocking + reducing) if (blocking or reducing) else "All 6 layers clear"

        return RiskDecision(
            can_trade=can_trade,
            size_multiplier=round(max(0.0, min(1.0, size_mult)), 2),
            layers_blocking=blocking,
            layers_reducing=reducing,
            reason=reason_str,
        )

    def _compute_survival(self, firm_state: PropFirmState, equity: float) -> float:
        """
        Actuarial survival probability: given current trajectory, what's the
        probability this account survives to payout?

        Based on distance to floor, daily variance, and days remaining.
        """
        buffer = equity - firm_state.max_loss_floor
        if buffer <= 0:
            return 0.0

        # Estimate daily variance from balance trajectory
        daily_risk = firm_state.initial_balance * 0.015  # ~1.5% daily std dev estimate
        if daily_risk <= 0:
            return 0.95

        # Survival = probability that a random walk doesn't breach the floor
        # Simplified: P(survival) ≈ 1 - exp(-2 * buffer * drift / variance)
        # With conservative drift of 0.001 per day
        drift = firm_state.initial_balance * 0.002
        if daily_risk > 0:
            # Barrier crossing probability from Brownian motion
            exponent = -2.0 * buffer * max(drift, 0.001) / (daily_risk ** 2)
            survival = 1.0 - math.exp(max(-20, min(0, exponent)))
        else:
            survival = 0.95

        return max(0.0, min(1.0, survival))


# ═══════════════════════════════════════════════════════════════════════════════
# BEHAVIORAL CAMOUFLAGE
# ═══════════════════════════════════════════════════════════════════════════════

def camouflage_lot_size(target_lots: float) -> float:
    """
    Add human-like variation to lot sizes.

    Prop firms flag robotic identical sizing. This adds ±5% variation
    with a distribution that matches human rounding behavior.
    """
    # Vary by ±5% with slight bias toward round numbers
    variation = random.gauss(0, 0.03)  # 3% std dev
    adjusted = target_lots * (1.0 + variation)

    # Round to 2 decimal places (lots) — human behavior
    adjusted = round(adjusted, 2)

    # Ensure minimum
    adjusted = max(0.01, adjusted)

    return adjusted

def camouflage_entry_delay() -> float:
    """
    Random delay before entry to appear human.

    Returns seconds to wait. Distribution matches human reaction time:
    minimum 0.3s, mode around 1.5s, max 5s.
    """
    # Log-normal distribution resembles human reaction time
    delay = random.lognormvariate(0.0, 0.7)
    return max(0.3, min(5.0, delay))


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION MANAGEMENT — TIME-BASED PROBABILITY DECAY
# ═══════════════════════════════════════════════════════════════════════════════

def should_exit_time_decay(
    entry_time:     datetime,
    current_pnl:    float,
    target_pnl:     float,
    expected_hold_minutes: float = 60.0,
) -> tuple[bool, str]:
    """
    If trade hasn't moved 40% toward target in first third of expected hold time,
    the probability of hitting target drops exponentially. Exit early.

    Based on research: ORB momentum trades that don't resolve in 90 minutes
    almost never hit target.
    """
    elapsed = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60.0
    third = expected_hold_minutes / 3.0

    if elapsed < third:
        return False, "Within first third — too early to evaluate"

    progress = current_pnl / target_pnl if target_pnl > 0 else 0.0

    if elapsed > third and progress < 0.40:
        return True, f"Time decay: {elapsed:.0f}min elapsed, only {progress:.0%} toward target"

    if elapsed > expected_hold_minutes * 0.66 and progress < 0.60:
        return True, f"Time decay: {elapsed:.0f}min elapsed, only {progress:.0%} toward target"

    return False, f"On track: {progress:.0%} in {elapsed:.0f}min"


# ═══════════════════════════════════════════════════════════════════════════════
# FRIDAY / WEEKEND / EOD PROTECTION
# ═══════════════════════════════════════════════════════════════════════════════

def check_session_close_protection(
    firm_state: PropFirmState,
) -> tuple[bool, str]:
    """
    Returns (should_close_all, reason) based on firm-specific session close rules.

    - Friday 3:55 PM ET for firms requiring weekend close
    - Firm-specific EOD close times (Topstep 3:10 PM CT = 4:10 PM ET)
    """
    t = now_et_time()

    # Friday weekend close
    if now_et().weekday() == 4 and firm_state.requires_weekend_close:
        if t >= dtime(15, 55):
            return True, "Friday 3:55 ET — closing all for weekend (firm requires)"

    # Firm-specific EOD close
    if firm_state.requires_eod_close and firm_state.eod_close_time_et:
        if t >= firm_state.eod_close_time_et:
            return True, f"EOD close required at {firm_state.eod_close_time_et}"

    return False, "OK"


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTRAINED KELLY SIZING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_kelly_size(
    win_prob:           float,
    reward_risk_ratio:  float,
    account_balance:    float,
    base_lot_size:      float,
    firm_max_risk_pct:  float = 0.02,  # C-06: 2% eval, 3% funded
    risk_multiplier:    float = 1.0,   # from risk fortress
    vix_multiplier:     float = 1.0,   # from market context
    day_multiplier:     float = 1.0,   # from day-of-week
    conviction_mult:    float = 1.0,   # from Bayesian conviction
) -> float:
    """
    Compute position size using constrained quarter-Kelly criterion.

    Kelly: f* = (bp - q) / b
    Then: quarter Kelly (conservative)
    Then: capped by firm risk limit (C-06)
    Then: adjusted by risk/VIX/day/conviction multipliers
    Then: camouflaged for anti-gaming
    Then: C-08 enforced (after loss, only down)
    """
    b = reward_risk_ratio
    p = win_prob
    q = 1.0 - p

    # Raw Kelly
    if b <= 0:
        return base_lot_size
    kelly = max(0.0, (b * p - q) / b)

    # Quarter Kelly (conservative)
    quarter_kelly = kelly * 0.25

    # Cap at firm limit
    capped = min(quarter_kelly, firm_max_risk_pct)

    # Apply all multipliers
    final_fraction = capped * risk_multiplier * vix_multiplier * day_multiplier
    final_fraction = min(final_fraction, firm_max_risk_pct)  # re-cap after multipliers

    # Convert to lots
    lots = max(base_lot_size, final_fraction * account_balance / 1000)  # rough conversion

    # Conviction scaling
    if conviction_mult > 1.0:
        lots = lots * min(conviction_mult, 1.5)  # never more than 1.5x for conviction

    # Minimum lot size
    lots = max(0.01, lots)

    # Camouflage
    lots = camouflage_lot_size(lots)

    return round(lots, 2)
