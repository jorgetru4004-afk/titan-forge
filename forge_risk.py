"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE V20                      ║
║                    forge_risk.py — THE RISK FORTRESS                        ║
║                                                                              ║
║  6 INDEPENDENT PROTECTION LAYERS. Plus:                                     ║
║  V20: 5-gate pre-trade checklist. Session memory (intraday learning).     ║
║  Prop firm profiles (FTMO 2-step, 1-step). Dynamic daily targets.         ║
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
from forge_core import now_et, now_et_time, is_rth, send_telegram, MarketContext

logger = logging.getLogger("titan_forge.risk")


# ═══════════════════════════════════════════════════════════════════════════════
# V20: PROP FIRM PROFILES — Not just FTMO. Universal.
# ═══════════════════════════════════════════════════════════════════════════════

FIRM_PROFILES = {
    "FTMO_2STEP": {
        "daily_loss_pct": 0.05,
        "total_loss_pct": 0.10,
        "profit_target_pct": 0.10,
        "profit_target_p2_pct": 0.05,
        "news_restricted": True,
        "best_day_rule": False,
        "trailing_drawdown": False,
        "consistency_rule_pct": None,
        "requires_weekend_close": False,
        "requires_eod_close": False,
    },
    "FTMO_1STEP": {
        "daily_loss_pct": 0.03,
        "total_loss_pct": 0.10,
        "profit_target_pct": 0.10,
        "news_restricted": False,
        "best_day_rule": True,
        "best_day_cap": 0.50,
        "trailing_drawdown": True,
        "consistency_rule_pct": 0.50,
        "requires_weekend_close": False,
        "requires_eod_close": False,
    },
    "FTMO": {  # alias for 2-step
        "daily_loss_pct": 0.05,
        "total_loss_pct": 0.10,
        "profit_target_pct": 0.10,
        "news_restricted": True,
        "best_day_rule": False,
        "trailing_drawdown": False,
        "consistency_rule_pct": None,
        "requires_weekend_close": False,
        "requires_eod_close": False,
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# UNIVERSAL PROP FIRM TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PropFirmState:
    firm_id:                str
    initial_balance:        float
    current_balance:        float
    highest_eod_balance:    float
    daily_start_balance:    float = 0.0
    current_day_pnl:        float = 0.0
    trading_days:           int   = 0
    total_positive_profit:  float = 0.0
    best_day_profit:        float = 0.0
    total_closed_pnl:       float = 0.0
    recent_sizes:           list[float] = field(default_factory=list)
    first_trade_date:       Optional[date] = None
    payout_cycle_start:     Optional[date] = None
    cycle_profit:           float = 0.0
    payouts_in_cycle:       int   = 0
    # Firm-specific limits
    daily_loss_pct:         float = 0.05
    total_loss_pct:         float = 0.10
    consistency_rule_pct:   Optional[float] = None
    payout_cycle_days:      Optional[int] = None
    scaling_months:         Optional[int] = None
    scaling_pct:            Optional[float] = None
    has_trailing_drawdown:  bool = False
    requires_weekend_close: bool = False
    requires_eod_close:     bool = False
    eod_close_time_et:      Optional[dtime] = None
    min_hold_seconds:       Optional[int] = None
    best_day_rule:          bool = False

    def __post_init__(self):
        """Load firm profile if it exists."""
        profile = FIRM_PROFILES.get(self.firm_id, {})
        if profile:
            self.daily_loss_pct = profile.get("daily_loss_pct", self.daily_loss_pct)
            self.total_loss_pct = profile.get("total_loss_pct", self.total_loss_pct)
            self.has_trailing_drawdown = profile.get("trailing_drawdown", False)
            self.consistency_rule_pct = profile.get("consistency_rule_pct")
            self.requires_weekend_close = profile.get("requires_weekend_close", False)
            self.requires_eod_close = profile.get("requires_eod_close", False)
            self.best_day_rule = profile.get("best_day_rule", False)

    def initialize(self, balance: float) -> None:
        self.initial_balance = balance
        self.current_balance = balance
        self.highest_eod_balance = balance
        self.daily_start_balance = balance
        logger.info("[FIRM] %s tracker initialized: $%.2f", self.firm_id, balance)

    @property
    def daily_loss_limit(self) -> float:
        return self.daily_start_balance * self.daily_loss_pct

    @property
    def daily_loss_remaining(self) -> float:
        return self.daily_loss_limit + self.current_day_pnl

    @property
    def daily_loss_pct_used(self) -> float:
        if self.daily_loss_limit <= 0:
            return 0.0
        return max(0, -self.current_day_pnl) / self.daily_loss_limit

    @property
    def max_loss_floor(self) -> float:
        if self.has_trailing_drawdown:
            return self.highest_eod_balance * (1 - self.total_loss_pct)
        return self.initial_balance * (1 - self.total_loss_pct)

    @property
    def equity_buffer_pct(self) -> float:
        return (self.current_balance - self.max_loss_floor) / self.initial_balance

    @property
    def best_day_ratio(self) -> float:
        if self.total_positive_profit <= 0:
            return 0.0
        return self.best_day_profit / self.total_positive_profit

    @property
    def consistency_ok(self) -> bool:
        if self.consistency_rule_pct is None:
            return True
        return self.best_day_ratio <= self.consistency_rule_pct

    def should_emergency_close(self, equity: float) -> bool:
        if self.daily_start_balance <= 0:
            return False
        if self.daily_loss_limit > 0 and (-self.current_day_pnl) >= self.daily_loss_limit * 0.80:
            return True
        if equity < self.max_loss_floor + self.initial_balance * 0.02:
            return True
        return False

    def can_enter(self) -> tuple[bool, str]:
        if self.daily_loss_pct_used >= 0.60:
            return False, f"Daily loss {self.daily_loss_pct_used:.0%} used"
        cap = self.consistency_rule_pct or 0.50
        if self.best_day_rule and self.best_day_ratio > cap * 0.80 and self.current_day_pnl > 0:
            return False, f"Best day ratio {self.best_day_ratio:.0%} near {cap:.0%} cap"
        return True, "OK"

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
        if len(self.recent_sizes) < 3:
            return True
        avg = sum(self.recent_sizes) / len(self.recent_sizes)
        return proposed <= avg * 2.5

    def optimal_daily_target(self) -> float:
        remaining = max(0, (self.initial_balance * 0.10) - self.total_closed_pnl)
        if self.consistency_rule_pct:
            max_today = self.total_positive_profit * (self.consistency_rule_pct * 0.85) \
                        if self.total_positive_profit > 0 else self.initial_balance * 0.004
            return min(remaining, max_today)
        return min(remaining, self.initial_balance * 0.005)


# ═══════════════════════════════════════════════════════════════════════════════
# V20: SESSION MEMORY — INTRADAY LEARNING
# ═══════════════════════════════════════════════════════════════════════════════

class SessionMemory:
    """
    FORGE learns from its own trades WITHIN the session.
    Adjusts size and behavior based on intraday performance.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self._trades: list[dict] = []  # {setup_id, direction, pnl, outcome}
        self._consecutive_losses: int = 0
        self._scalp_only_until: Optional[datetime] = None
        self._session_size_mult: float = 1.0
        self._setup_performance: dict[str, dict] = {}  # setup_id → {wins, losses}

    def record_trade(self, setup_id: str, direction: str, pnl: float, outcome: str) -> None:
        self._trades.append({
            "setup_id": setup_id, "direction": direction,
            "pnl": pnl, "outcome": outcome,
        })

        if setup_id not in self._setup_performance:
            self._setup_performance[setup_id] = {"wins": 0, "losses": 0}

        if outcome == "WIN":
            self._consecutive_losses = 0
            self._setup_performance[setup_id]["wins"] += 1
        elif outcome == "LOSS":
            self._consecutive_losses += 1
            self._setup_performance[setup_id]["losses"] += 1

            # After first loss: reduce size 10%
            if len(self._trades) == 1:
                self._session_size_mult = 0.90

            # 3+ consecutive losses: scalp-only mode for 30 minutes
            if self._consecutive_losses >= 3:
                self._scalp_only_until = datetime.now(timezone.utc) + timedelta(minutes=30)
                self._session_size_mult = max(0.50, self._session_size_mult - 0.10)
                logger.warning("[SESSION] 3 losses → scalp-only for 30min, size=%.0f%%",
                              self._session_size_mult * 100)
                send_telegram(
                    f"⏸ <b>SESSION MEMORY</b>\n"
                    f"3 consecutive losses → scalp-only 30min\n"
                    f"Size reduced to {self._session_size_mult:.0%}"
                )

    @property
    def is_scalp_only(self) -> bool:
        if self._scalp_only_until is None:
            return False
        if datetime.now(timezone.utc) >= self._scalp_only_until:
            self._scalp_only_until = None
            return False
        return True

    @property
    def size_multiplier(self) -> float:
        return self._session_size_mult

    def get_setup_conviction_adj(self, setup_id: str) -> float:
        """
        Adjust conviction based on today's performance of this setup.
        Winners get a small boost, losers get a small penalty.
        """
        perf = self._setup_performance.get(setup_id)
        if not perf:
            return 0.0
        wins, losses = perf["wins"], perf["losses"]
        total = wins + losses
        if total < 2:
            return 0.0
        # Small adjustment: ±3% max
        win_rate_today = wins / total
        if win_rate_today > 0.60:
            return 0.03  # slight boost for today's winners
        elif win_rate_today < 0.40:
            return -0.03  # slight penalty for today's losers
        return 0.0

    @property
    def session_pnl(self) -> float:
        return sum(t.get("pnl", 0) for t in self._trades)

    @property
    def trade_count(self) -> int:
        return len(self._trades)


# ═══════════════════════════════════════════════════════════════════════════════
# V20: 5-GATE PRE-TRADE CHECKLIST
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GateResult:
    """Result of the 5-gate pre-trade checklist."""
    all_pass:       bool
    failed_gate:    Optional[str]   # which gate failed, if any
    details:        list[str]       # gate-by-gate details


def pre_trade_checklist(
    setup_id:       str,
    direction:      str,
    regime:         str,
    ctx:            MarketContext,
    risk_dollars:   float,
    account_equity: float,
    open_risk:      float,
    minutes_remaining: float,
    expected_hold_min: float,
    session_memory: SessionMemory,
    regime_mult:    float,
) -> GateResult:
    """
    5-gate pre-trade checklist. ALL must pass.
    Gate 1: Regime alignment
    Gate 2: Multi-timeframe agreement
    Gate 3: No conflict (checked elsewhere — always pass here)
    Gate 4: Risk budget
    Gate 5: Time value
    """
    details = []

    # Gate 1: Regime Alignment
    if regime_mult <= 0.0:
        return GateResult(False, "GATE_1_REGIME",
                         [f"G1 FAIL: {setup_id} SUPPRESSED in {regime} regime"])
    details.append(f"G1 OK: {setup_id} regime_mult={regime_mult:.2f} in {regime}")

    # Gate 2: Multi-Timeframe Agreement
    mtf_ok = True
    if ctx.mtf_trend_m15 != "neutral" and ctx.mtf_trend_m15 != direction:
        # Fighting M15 trend — only pass if conviction is HIGH+
        mtf_ok = False
    if not mtf_ok:
        details.append(f"G2 FAIL: {direction} fights M15={ctx.mtf_trend_m15}")
        return GateResult(False, "GATE_2_MTF", details)
    details.append(f"G2 OK: M15={ctx.mtf_trend_m15} H1={ctx.mtf_trend_h1}")

    # Gate 3: No conflict (handled by anti-conflict filter in main loop)
    details.append("G3 OK: conflict check in main loop")

    # Gate 4: Risk Budget
    total_risk = open_risk + risk_dollars
    max_risk = account_equity * 0.03  # 3% max total open risk
    if total_risk > max_risk:
        details.append(f"G4 FAIL: total risk ${total_risk:.0f} > 3% (${max_risk:.0f})")
        return GateResult(False, "GATE_4_RISK", details)
    details.append(f"G4 OK: total risk ${total_risk:.0f} / ${max_risk:.0f}")

    # Gate 5: Time Value
    if minutes_remaining < expected_hold_min * 0.5:
        details.append(f"G5 FAIL: {minutes_remaining:.0f}min left < {expected_hold_min*0.5:.0f}min needed")
        return GateResult(False, "GATE_5_TIME", details)
    details.append(f"G5 OK: {minutes_remaining:.0f}min remaining, need {expected_hold_min:.0f}min")

    # V20: Session memory gate — if scalp-only and this isn't a scalp, block
    if session_memory.is_scalp_only:
        details.append("G-SESSION: scalp-only mode active (cooldown)")
        # Don't fail the gate — let conviction tiers handle this
        # But flag it for the caller

    return GateResult(True, None, details)


# ═══════════════════════════════════════════════════════════════════════════════
# 6-LAYER RISK FORTRESS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class RiskDecision:
    can_trade:      bool
    size_multiplier: float
    layers_blocking: list[str]
    layers_reducing: list[str]
    reason:         str


class RiskFortress:
    def __init__(self):
        self._daily_profit_locked: bool = False
        self._daily_loss_stopped:  bool = False
        self._cooldown_until: Optional[datetime] = None
        self._consecutive_losses: int = 0
        self._weekly_start_balance: float = 0.0
        self._weekly_low_balance: float = 0.0
        self._setup_losses: dict[str, int] = {}
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
        self, firm_state: PropFirmState, equity: float,
        daily_pnl: float, balance: float, setup_id: str = "",
    ) -> RiskDecision:
        blocking: list[str] = []
        reducing: list[str] = []
        size_mult = 1.0

        # Layer 1: Firm Rule Compliance
        can, reason = firm_state.can_enter()
        if not can:
            blocking.append(f"L1-FIRM: {reason}")
        if firm_state.should_emergency_close(equity):
            blocking.append("L1-FIRM: Emergency — near limit")

        # Layer 2: Daily P&L Protection
        daily_pct = daily_pnl / balance if balance > 0 else 0
        if daily_pct >= 0.02:
            self._daily_profit_locked = True
            blocking.append("L2-DAILY: +2% profit lock")
        elif daily_pct <= -0.03:
            self._daily_loss_stopped = True
            blocking.append("L2-DAILY: -3% hard stop")
        elif daily_pct <= -0.02:
            size_mult *= 0.50
            reducing.append("L2-DAILY: Down 2% — half size")
        elif daily_pct <= -0.01:
            size_mult *= 0.75
            reducing.append("L2-DAILY: Down 1% — reduced")
        if self._daily_profit_locked:
            blocking.append("L2-DAILY: Profit lock active")
        if self._daily_loss_stopped:
            blocking.append("L2-DAILY: Loss stop active")

        # Layer 3: Streak & Cooldown
        if self._cooldown_until:
            if datetime.now(timezone.utc) < self._cooldown_until:
                mins = (self._cooldown_until - datetime.now(timezone.utc)).total_seconds() / 60
                blocking.append(f"L3-STREAK: Cooldown {mins:.0f}min remaining")
            else:
                self._cooldown_until = None
        if self._consecutive_losses >= 3:
            self._cooldown_until = datetime.now(timezone.utc) + timedelta(hours=2)
            self._consecutive_losses = 0
            blocking.append("L3-STREAK: 3 losses → 2hr cooldown")
            send_telegram("⏸ <b>COOLDOWN</b>\n3 consecutive losses. 2hr pause.")
        if setup_id and self._setup_losses.get(setup_id, 0) >= 3:
            blocking.append(f"L3-STREAK: {setup_id} lost 3x this week")
        elif setup_id and self._setup_losses.get(setup_id, 0) == 2:
            size_mult *= 0.75
            reducing.append(f"L3-STREAK: {setup_id} 2 losses — size -25%")

        # Layer 4: Weekly Drawdown
        if self._weekly_start_balance > 0:
            self._weekly_low_balance = min(self._weekly_low_balance, balance)
            weekly_dd = (self._weekly_start_balance - balance) / self._weekly_start_balance
            if weekly_dd >= 0.03:
                size_mult *= 0.50
                reducing.append(f"L4-WEEKLY: Down {weekly_dd:.1%} — half size")
            elif weekly_dd >= 0.02:
                size_mult *= 0.75
                reducing.append(f"L4-WEEKLY: Down {weekly_dd:.1%} — reduced")

        # Layer 5: Profit Velocity
        if daily_pct >= 0.015:
            size_mult *= 0.50
            reducing.append("L5-VELOCITY: Up 1.5%+ — protect best day")
        elif daily_pct >= 0.010:
            size_mult *= 0.75
            reducing.append("L5-VELOCITY: Up 1.0%+ — slightly reduced")

        # Layer 6: Survival Probability
        survival = self._compute_survival(firm_state, equity)
        if survival < 0.70:
            blocking.append(f"L6-SURVIVAL: {survival:.0%} — too low")
        elif survival < 0.85:
            size_mult *= 0.50
            reducing.append(f"L6-SURVIVAL: {survival:.0%} — defensive")

        can_trade = len(blocking) == 0
        if not can_trade:
            size_mult = 0.0
        reason_str = " | ".join(blocking + reducing) if (blocking or reducing) else "All 6 layers clear"

        return RiskDecision(
            can_trade=can_trade,
            size_multiplier=round(max(0.0, min(1.0, size_mult)), 2),
            layers_blocking=blocking, layers_reducing=reducing,
            reason=reason_str,
        )

    def _compute_survival(self, firm_state: PropFirmState, equity: float) -> float:
        buffer = equity - firm_state.max_loss_floor
        if buffer <= 0:
            return 0.0
        daily_risk = firm_state.initial_balance * 0.015
        if daily_risk <= 0:
            return 0.95
        drift = firm_state.initial_balance * 0.002
        if daily_risk > 0:
            exponent = -2.0 * buffer * max(drift, 0.001) / (daily_risk ** 2)
            survival = 1.0 - math.exp(max(-20, min(0, exponent)))
        else:
            survival = 0.95
        return max(0.0, min(1.0, survival))


# ═══════════════════════════════════════════════════════════════════════════════
# BEHAVIORAL CAMOUFLAGE
# ═══════════════════════════════════════════════════════════════════════════════

def camouflage_lot_size(target_lots: float) -> float:
    variation = random.gauss(0, 0.03)
    adjusted = target_lots * (1.0 + variation)
    adjusted = round(adjusted, 2)
    return max(0.01, adjusted)


def camouflage_entry_delay() -> float:
    delay = random.lognormvariate(0.0, 0.7)
    return max(0.3, min(5.0, delay))


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION MANAGEMENT — TIME DECAY
# ═══════════════════════════════════════════════════════════════════════════════

def should_exit_time_decay(
    entry_time: datetime, current_pnl: float,
    target_pnl: float, expected_hold_minutes: float = 60.0,
) -> tuple[bool, str]:
    elapsed = (datetime.now(timezone.utc) - entry_time).total_seconds() / 60.0
    third = expected_hold_minutes / 3.0
    if elapsed < third:
        return False, "Within first third"
    progress = current_pnl / target_pnl if target_pnl > 0 else 0.0
    if elapsed > third and progress < 0.40:
        return True, f"Time decay: {elapsed:.0f}min, {progress:.0%} progress"
    if elapsed > expected_hold_minutes * 0.66 and progress < 0.60:
        return True, f"Time decay: {elapsed:.0f}min, {progress:.0%} progress"
    return False, f"On track: {progress:.0%} in {elapsed:.0f}min"


# ═══════════════════════════════════════════════════════════════════════════════
# FRIDAY / WEEKEND / EOD PROTECTION
# ═══════════════════════════════════════════════════════════════════════════════

def check_session_close_protection(firm_state: PropFirmState) -> tuple[bool, str]:
    t = now_et_time()
    if now_et().weekday() == 4 and firm_state.requires_weekend_close:
        if t >= dtime(15, 55):
            return True, "Friday 3:55 ET — weekend close"
    if firm_state.requires_eod_close and firm_state.eod_close_time_et:
        if t >= firm_state.eod_close_time_et:
            return True, f"EOD close required at {firm_state.eod_close_time_et}"
    return False, "OK"


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTRAINED KELLY SIZING
# ═══════════════════════════════════════════════════════════════════════════════

def compute_kelly_size(
    win_prob: float, reward_risk_ratio: float, account_balance: float,
    base_lot_size: float, firm_max_risk_pct: float = 0.02,
    risk_multiplier: float = 1.0, vix_multiplier: float = 1.0,
    day_multiplier: float = 1.0, conviction_mult: float = 1.0,
) -> float:
    b, p, q = reward_risk_ratio, win_prob, 1.0 - win_prob
    if b <= 0:
        return base_lot_size
    kelly = max(0.0, (b * p - q) / b)
    quarter_kelly = kelly * 0.25
    capped = min(quarter_kelly, firm_max_risk_pct)
    final_fraction = capped * risk_multiplier * vix_multiplier * day_multiplier
    final_fraction = min(final_fraction, firm_max_risk_pct)
    lots = max(base_lot_size, final_fraction * account_balance / 1000)
    if conviction_mult > 1.0:
        lots = lots * min(conviction_mult, 1.5)
    lots = max(0.01, lots)
    lots = camouflage_lot_size(lots)
    return round(lots, 2)
