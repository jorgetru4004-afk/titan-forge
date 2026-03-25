"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                forge_firm.py — UNIVERSAL PROP FIRM OPTIMIZER                 ║
║                                                                              ║
║  Prop firms are not your partner. They're a counterparty in a game.        ║
║  Their rules are designed to protect THEIR capital.                         ║
║  FORGE models each firm's incentive structure and plays OPTIMALLY.          ║
║                                                                              ║
║  FTMO:  No consistency rule → concentrate profits on 3-4 best days         ║
║  APEX:  Trailing unrealized → lock profit aggressively, speed is weapon    ║
║  DNA:   40% consistency rule → spread evenly, constraint helps discipline  ║
║  5%ERS: Low leverage ceiling → patience game, compound slowly              ║
║                                                                              ║
║  Switch firms = one environment variable. The ENTIRE behavior changes.      ║
║                                                                              ║
║  INCLUDES:                                                                   ║
║    - Firm-specific sizing optimization                                      ║
║    - Payout timing optimization                                             ║
║    - Scaling milestone tracking                                             ║
║    - Best Day engineering per firm                                          ║
║    - Anti-gaming behavioral camouflage                                      ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import math
import random
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger("titan_forge.firm_optimizer")


# ═══════════════════════════════════════════════════════════════════════════════
# FIRM-SPECIFIC OPTIMAL STRATEGIES
#
# Each firm is a different math problem with a different optimal solution.
# These aren't rules. They're EXPLOIT STRATEGIES.
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class FirmStrategy:
    """Optimal strategy parameters for a specific prop firm."""
    firm_id:                str
    # Profit distribution strategy
    target_daily_pct:       float   # Optimal daily profit target
    max_daily_pct:          float   # Hard cap on daily profit (Best Day protection)
    concentrate_profits:    bool    # True = big days + small days. False = even distribution
    # Risk parameters
    daily_loss_hard_stop:   float   # Our internal stop (tighter than firm's)
    size_cap_pct:           float   # Max position size as % of drawdown buffer
    # Timing
    min_hold_seconds:       Optional[int]  # Minimum hold time (DNA requires 30s)
    close_before_weekend:   bool    # Must be flat before weekend?
    close_before_news:      bool    # Must close existing positions before news?
    # Payout optimization
    payout_cycle_days:      int     # How often payouts available
    target_payout_pct:      float   # What % of profit to extract per payout
    # Scaling
    has_scaling:            bool
    scaling_months:         Optional[int]
    scaling_increase_pct:   Optional[float]
    # Special mechanics
    trails_unrealized:      bool    # Apex-style trailing on unrealized P&L
    unrealized_lock_pct:    float   # Lock X% of peak unrealized (Apex)
    consistency_rule_pct:   Optional[float]  # Max single day as % of total (DNA)
    # Camouflage
    size_variation_pct:     float   # Random lot size variation (anti-detection)
    timing_variation_sec:   int     # Random entry timing variation (seconds)


# The strategy database — each firm's optimal play
FIRM_STRATEGIES: dict[str, FirmStrategy] = {
    "FTMO": FirmStrategy(
        firm_id="FTMO",
        target_daily_pct=0.004,        # 0.4% per day × 25 days = 10% target exactly
        max_daily_pct=0.025,           # Cap at 2.5% to avoid Best Day issues
        concentrate_profits=True,       # FTMO has NO consistency rule — use it
        daily_loss_hard_stop=0.03,     # Our stop at 3% (firm limit is 5%)
        size_cap_pct=0.02,             # C-06: 2% max during eval
        min_hold_seconds=None,         # No minimum hold
        close_before_weekend=True,     # Classic account — no weekend holds
        close_before_news=True,        # Close existing + block new during news
        payout_cycle_days=14,          # Bi-weekly payouts
        target_payout_pct=0.80,        # Extract 80% of profit each payout
        has_scaling=True,
        scaling_months=4,
        scaling_increase_pct=0.25,     # +25% every 4 months
        trails_unrealized=False,
        unrealized_lock_pct=0.0,
        consistency_rule_pct=None,     # NO CONSISTENCY RULE — biggest advantage
        size_variation_pct=0.05,       # ±5% lot size variation
        timing_variation_sec=8,        # 2-8 second entry delay
    ),
    "APEX": FirmStrategy(
        firm_id="APEX",
        target_daily_pct=0.003,        # Smaller daily targets — trailing is dangerous
        max_daily_pct=0.015,           # Tighter cap — protect trailing buffer
        concentrate_profits=False,      # Even distribution — trailing punishes big swings
        daily_loss_hard_stop=0.025,    # Tighter stop — trailing drawdown is ruthless
        size_cap_pct=0.015,            # More conservative — trailing tracks unrealized
        min_hold_seconds=None,
        close_before_weekend=False,    # Apex allows weekend holds
        close_before_news=True,
        payout_cycle_days=7,           # Weekly payouts
        target_payout_pct=1.0,         # Extract ALL profit — reset trailing
        has_scaling=False,
        scaling_months=None,
        scaling_increase_pct=None,
        trails_unrealized=True,        # THE KEY MECHANIC — must lock aggressively
        unrealized_lock_pct=0.60,      # Lock 60% of peak unrealized
        consistency_rule_pct=None,     # No consistency rule
        size_variation_pct=0.04,
        timing_variation_sec=6,
    ),
    "DNA_FUNDED": FirmStrategy(
        firm_id="DNA_FUNDED",
        target_daily_pct=0.003,
        max_daily_pct=0.012,           # Hard cap below 40% consistency rule
        concentrate_profits=False,      # Spread evenly — consistency rule enforced
        daily_loss_hard_stop=0.03,
        size_cap_pct=0.02,
        min_hold_seconds=30,           # DNA requires 30-second minimum hold
        close_before_weekend=True,
        close_before_news=True,
        payout_cycle_days=14,
        target_payout_pct=0.50,        # First 3 payouts capped at 50%
        has_scaling=True,
        scaling_months=3,
        scaling_increase_pct=0.25,
        trails_unrealized=False,
        unrealized_lock_pct=0.0,
        consistency_rule_pct=0.40,     # 40% rule — single day can't exceed 40% of total
        size_variation_pct=0.05,
        timing_variation_sec=10,
    ),
    "FIVEPERCENTERS": FirmStrategy(
        firm_id="FIVEPERCENTERS",
        target_daily_pct=0.002,        # Very conservative — low leverage ceiling
        max_daily_pct=0.008,
        concentrate_profits=False,
        daily_loss_hard_stop=0.025,
        size_cap_pct=0.015,
        min_hold_seconds=None,
        close_before_weekend=True,
        close_before_news=True,
        payout_cycle_days=30,          # Monthly payouts
        target_payout_pct=0.50,
        has_scaling=True,
        scaling_months=3,
        scaling_increase_pct=0.25,
        trails_unrealized=False,
        unrealized_lock_pct=0.0,
        consistency_rule_pct=None,
        size_variation_pct=0.06,
        timing_variation_sec=12,
    ),
}


def get_active_strategy() -> FirmStrategy:
    """Get the strategy for the currently active firm."""
    firm_id = os.environ.get("ACTIVE_FIRM", "FTMO")
    return FIRM_STRATEGIES.get(firm_id, FIRM_STRATEGIES["FTMO"])


# ═══════════════════════════════════════════════════════════════════════════════
# PROP FIRM ACCOUNT TRACKER
#
# Universal tracker that works with ANY firm.
# Tracks everything needed for optimal play.
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PropFirmTracker:
    """Universal prop firm account tracker."""
    firm_id:            str = "FTMO"
    strategy:           Optional[FirmStrategy] = None

    # Balance tracking
    initial_balance:    float = 0.0
    current_balance:    float = 0.0
    highest_eod:        float = 0.0
    peak_unrealized:    float = 0.0    # For Apex trailing

    # Daily tracking
    day_start_balance:  float = 0.0
    current_day_pnl:    float = 0.0
    trading_days:       int   = 0

    # Profit distribution
    total_positive_pnl: float = 0.0
    best_day_profit:    float = 0.0
    positive_days:      int   = 0
    daily_pnls:         list  = field(default_factory=list)

    # Payout tracking
    first_trade_date:   Optional[date] = None
    last_payout_date:   Optional[date] = None
    payouts_count:      int   = 0

    # Scaling tracking
    scaling_start:      Optional[date] = None
    scaling_profit:     float = 0.0

    # Trade size history (for consistency + camouflage)
    recent_sizes:       list  = field(default_factory=list)

    def __post_init__(self):
        if self.strategy is None:
            self.strategy = FIRM_STRATEGIES.get(self.firm_id, FIRM_STRATEGIES["FTMO"])

    def initialize(self, balance: float, firm_id: str = "") -> None:
        """Initialize from live account data."""
        if firm_id:
            self.firm_id = firm_id
            self.strategy = FIRM_STRATEGIES.get(firm_id, FIRM_STRATEGIES["FTMO"])
        self.initial_balance  = balance
        self.current_balance  = balance
        self.highest_eod      = balance
        self.day_start_balance = balance
        logger.info("[FIRM] Tracker initialized: %s | $%.2f", self.firm_id, balance)

    # ── DAILY OPERATIONS ─────────────────────────────────────────────────────

    def update(self, balance: float, equity: float, daily_pnl: float) -> None:
        """Call every cycle — updates all tracking."""
        self.current_balance = balance
        self.current_day_pnl = daily_pnl

        # Apex trailing — track peak unrealized
        if self.strategy and self.strategy.trails_unrealized:
            unrealized = equity - balance
            if unrealized > self.peak_unrealized:
                self.peak_unrealized = unrealized

    def reset_daily(self, balance: float) -> None:
        """Start of new trading day."""
        self.day_start_balance = balance
        self.current_day_pnl   = 0.0
        self.peak_unrealized   = 0.0
        self.trading_days     += 1
        if balance > self.highest_eod:
            self.highest_eod = balance
        if not self.first_trade_date:
            self.first_trade_date = date.today()

    def close_of_day(self, day_pnl: float) -> None:
        """End of trading day — finalize daily tracking."""
        self.daily_pnls.append(day_pnl)
        self.daily_pnls = self.daily_pnls[-90:]  # Keep 90 days
        if day_pnl > 0:
            self.positive_days += 1
            self.total_positive_pnl += day_pnl
            if day_pnl > self.best_day_profit:
                self.best_day_profit = day_pnl

    # ── FIRM-SPECIFIC INTELLIGENCE ───────────────────────────────────────────

    @property
    def best_day_ratio(self) -> float:
        """Best day as % of total positive profit."""
        if self.total_positive_pnl <= 0:
            return 0.0
        return self.best_day_profit / self.total_positive_pnl

    @property
    def consistency_ok(self) -> bool:
        """True if within consistency rule (if firm has one)."""
        if not self.strategy or not self.strategy.consistency_rule_pct:
            return True
        return self.best_day_ratio <= self.strategy.consistency_rule_pct

    @property
    def payout_eligible(self) -> bool:
        """True if payout can be requested."""
        if not self.strategy or not self.first_trade_date:
            return False
        days_active = (date.today() - self.first_trade_date).days
        return days_active >= self.strategy.payout_cycle_days and self.total_positive_pnl > 0

    @property
    def scaling_eligible(self) -> bool:
        """True if account qualifies for scaling increase."""
        if not self.strategy or not self.strategy.has_scaling:
            return False
        if not self.scaling_start:
            return False
        months = (date.today() - self.scaling_start).days / 30.0
        return (months >= (self.strategy.scaling_months or 999) and
                self.scaling_profit >= self.initial_balance * 0.10 and
                self.payouts_count >= 2)

    @property
    def max_loss_floor(self) -> float:
        """Absolute floor — account fails if equity touches this."""
        return self.initial_balance * 0.90  # 10% max drawdown (most firms)

    @property
    def daily_loss_floor(self) -> float:
        """Daily loss floor — can only go up as balance grows."""
        return max(self.initial_balance, self.highest_eod) * 0.95  # 5% daily

    def can_trade_today(self) -> tuple[bool, str]:
        """Gate check: can FORGE enter new trades today?"""
        strat = self.strategy
        if not strat:
            return True, "No strategy loaded"

        # Daily profit cap (firm-specific)
        daily_pct = self.current_day_pnl / self.day_start_balance if self.day_start_balance > 0 else 0
        if daily_pct >= strat.max_daily_pct:
            return False, f"Daily cap {daily_pct:.1%} ≥ {strat.max_daily_pct:.1%} — protecting gains"

        # Consistency rule cap
        if strat.consistency_rule_pct and self.total_positive_pnl > 0:
            projected_ratio = (self.best_day_profit + abs(self.current_day_pnl)) / \
                             (self.total_positive_pnl + abs(self.current_day_pnl))
            if projected_ratio > strat.consistency_rule_pct * 0.90:  # Cap at 90% of limit
                return False, f"Consistency rule: projected ratio {projected_ratio:.0%} near {strat.consistency_rule_pct:.0%} cap"

        # Daily loss check (our internal stop, tighter than firm's)
        if daily_pct <= -strat.daily_loss_hard_stop:
            return False, f"Internal daily stop: {daily_pct:.1%} ≤ -{strat.daily_loss_hard_stop:.1%}"

        return True, "OK"

    def should_emergency_close(self, current_equity: float) -> bool:
        """True if equity approaching firm limits — close everything."""
        daily_floor = self.daily_loss_floor
        buffer = current_equity - daily_floor
        return buffer < self.initial_balance * 0.005  # Less than 0.5% buffer

    # ── APEX-SPECIFIC: UNREALIZED P&L LOCK ───────────────────────────────────

    def apex_should_lock(self, current_unrealized: float) -> tuple[bool, str]:
        """For Apex: should we lock profit based on unrealized tracking?"""
        if not self.strategy or not self.strategy.trails_unrealized:
            return False, "Not an Apex-style firm"

        if self.peak_unrealized <= 0:
            return False, "No unrealized peak yet"

        pct_of_peak = current_unrealized / self.peak_unrealized if self.peak_unrealized > 0 else 1.0

        if pct_of_peak < self.strategy.unrealized_lock_pct:
            return True, (f"APEX LOCK: unrealized {pct_of_peak:.0%} of peak "
                         f"(${current_unrealized:.0f} / ${self.peak_unrealized:.0f}) — CLOSE NOW")

        return False, f"Apex: {pct_of_peak:.0%} of peak — monitoring"

    # ── ANTI-GAMING BEHAVIORAL CAMOUFLAGE ────────────────────────────────────

    def camouflage_lot_size(self, target_lots: float) -> float:
        """
        Add human-like variation to lot size.
        Prop firms flag robotic identical lot sizes.
        """
        if not self.strategy:
            return target_lots
        variation = self.strategy.size_variation_pct
        # Normal distribution centered on target, clipped to ±variation
        noise = random.gauss(0, variation / 2)
        noise = max(-variation, min(variation, noise))
        adjusted = target_lots * (1.0 + noise)
        # Round to nearest 0.01 (human-like rounding)
        adjusted = round(adjusted, 2)
        return max(0.01, adjusted)

    def camouflage_entry_delay(self) -> float:
        """
        Add human-like delay before entry.
        Instant execution = bot detection flag.
        Returns seconds to wait.
        """
        if not self.strategy:
            return 0.0
        max_delay = self.strategy.timing_variation_sec
        # Gamma distribution mimics human reaction time
        delay = random.gammavariate(2.0, max_delay / 4.0)
        return min(delay, max_delay)

    def get_session_summary(self) -> str:
        """Generate a complete session summary for Telegram."""
        strat = self.strategy
        if not strat:
            return "No strategy loaded"

        daily_pct = self.current_day_pnl / self.day_start_balance * 100 if self.day_start_balance > 0 else 0
        remaining_to_target = (self.initial_balance * 0.10) - (self.current_balance - self.initial_balance)

        lines = [
            f"🏢 <b>{self.firm_id} Session Summary</b>",
            f"━━━━━━━━━━━━━━━━━━━━━",
            f"💰 Balance: ${self.current_balance:,.2f}",
            f"📊 Day P&L: ${self.current_day_pnl:,.2f} ({daily_pct:+.2f}%)",
            f"📅 Trading days: {self.trading_days}",
            f"🎯 To target: ${remaining_to_target:,.0f}",
        ]

        if strat.consistency_rule_pct:
            lines.append(f"📏 Consistency: {self.best_day_ratio:.0%} / {strat.consistency_rule_pct:.0%} limit")

        if self.payout_eligible:
            lines.append("✅ PAYOUT ELIGIBLE")
        elif self.first_trade_date:
            days_left = max(0, strat.payout_cycle_days - (date.today() - self.first_trade_date).days)
            lines.append(f"⏳ Payout in: {days_left} days")

        if self.scaling_eligible:
            lines.append("🚀 SCALING ELIGIBLE")

        return "\n".join(lines)
