"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   safety_margin.py — FORGE-03 — Layer 1                     ║
║                                                                              ║
║  DYNAMIC SAFETY MARGIN                                                       ║
║  Position size reduces automatically as drawdown is consumed.                ║
║  TITAN FORGE never operates at the edge of firm limits. Ever.                ║
║                                                                              ║
║  Three reduction tiers (FORGE-03):                                           ║
║    • 30% drawdown used → 75% of normal size (Tier 1 — early warning)        ║
║    • 50% drawdown used → 50% of normal size (Tier 2 — Yellow alert)         ║
║    • 70% drawdown used → minimum size only  (Tier 3 — Orange alert)         ║
║    • 85% drawdown used → ZERO new entries   (Red   — close all)              ║
║                                                                              ║
║  Integrates with:                                                            ║
║    • C-06 Kelly cap (clash_rules.py) — both apply, most conservative wins   ║
║    • C-08 Loss response floor (clash_rules.py) — stacks on top              ║
║    • EvaluationSnapshot (evaluation_state.py) — reads live drawdown pct     ║
║    • FirmRules (firm_rules.py) — minimum position size per firm              ║
║                                                                              ║
║  Principle: The firm's limit is NOT the target. TITAN FORGE always           ║
║  maintains visible daylight between position sizing and the firm floor.      ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

from firm_rules import FirmID, FirmRules, MultiFirmRuleEngine

logger = logging.getLogger("titan_forge.safety_margin")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — SAFETY MARGIN TIERS
# Hard-coded thresholds. These never change without Jorge approval + full re-test.
# ─────────────────────────────────────────────────────────────────────────────

class MarginTier(Enum):
    """
    Which safety tier is currently active.
    Determined entirely by drawdown percentage consumed.
    """
    CLEAR    = auto()   # 0–29% used. Full position size permitted.
    TIER_1   = auto()   # 30–49% used. 75% of normal size.
    TIER_2   = auto()   # 50–69% used. 50% of normal size. Yellow alert.
    TIER_3   = auto()   # 70–84% used. Minimum size only. Orange alert.
    RED      = auto()   # 85%+ used. No new entries. Close all. Red alert.


# Tier boundary thresholds (inclusive lower, exclusive upper)
TIER_BOUNDARIES: dict[MarginTier, tuple[float, float]] = {
    MarginTier.CLEAR:  (0.00, 0.30),
    MarginTier.TIER_1: (0.30, 0.50),
    MarginTier.TIER_2: (0.50, 0.70),
    MarginTier.TIER_3: (0.70, 0.85),
    MarginTier.RED:    (0.85, 1.01),
}

# Size multipliers per tier
TIER_MULTIPLIERS: dict[MarginTier, float] = {
    MarginTier.CLEAR:  1.00,   # Full size
    MarginTier.TIER_1: 0.75,   # 25% reduction
    MarginTier.TIER_2: 0.50,   # 50% reduction — Yellow alert
    MarginTier.TIER_3: 0.00,   # Minimum size only (handled separately)
    MarginTier.RED:    0.00,   # No new entries at all
}

# Daily drawdown budget proportions per tier
DAILY_BUDGET_MULTIPLIERS: dict[MarginTier, float] = {
    MarginTier.CLEAR:  1.00,
    MarginTier.TIER_1: 0.80,   # 80% of daily budget
    MarginTier.TIER_2: 0.60,   # 60% of daily budget
    MarginTier.TIER_3: 0.30,   # 30% of daily budget — survival mode
    MarginTier.RED:    0.00,   # No budget — no new entries
}

# Tier labels for logging
TIER_LABELS: dict[MarginTier, str] = {
    MarginTier.CLEAR:  "✅ CLEAR",
    MarginTier.TIER_1: "🔵 TIER-1 (30%)",
    MarginTier.TIER_2: "🟡 TIER-2 (50%) YELLOW",
    MarginTier.TIER_3: "🟠 TIER-3 (70%) ORANGE",
    MarginTier.RED:    "🔴 RED (85%) CLOSE ALL",
}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — MARGIN RESULT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarginResult:
    """
    Output of the Dynamic Safety Margin calculation.

    Always check .new_entries_permitted before executing any trade.
    Use .size_multiplier to scale the base position size.
    """
    tier:                   MarginTier
    drawdown_pct_used:      float          # Current drawdown consumption (0.0–1.0)
    daily_pct_used:         float          # Daily drawdown consumption (0.0–1.0)
    size_multiplier:        float          # Apply to base position size (0.0–1.0)
    daily_budget_multiplier: float         # Apply to daily budget allocation
    minimum_size:           float          # Firm minimum position size (lot/contract)
    permitted_size:         float          # Calculated final size (may be minimum)
    new_entries_permitted:  bool           # False = RED tier — no new positions
    firm_id:                str
    reason:                 str
    # Distance metrics (how far to next tier)
    pct_to_next_tier:       Optional[float]  # None if at RED
    dollars_to_next_tier:   Optional[float]
    dollars_to_floor:       float          # Critical: distance to firm failure

    @property
    def is_clear(self) -> bool:
        return self.tier == MarginTier.CLEAR

    @property
    def is_red(self) -> bool:
        return self.tier == MarginTier.RED

    @property
    def alert_color(self) -> str:
        return TIER_LABELS[self.tier]


@dataclass
class DailyBudgetAllocation:
    """
    FORGE-12: Daily drawdown budget for a single session.
    Calculated at session open. Unspent budget returns to reserve pool.
    """
    session_date:           object         # date
    firm_id:                str
    total_daily_limit:      float          # Firm's absolute daily drawdown limit ($)
    session_budget:         float          # Today's actual budget (may be < limit)
    reserve_pool:           float          # Budget available for high-conviction setups
    standard_budget:        float          # Budget for standard setups
    high_conviction_budget: float          # Reserve allocated to 4-stack+ setups
    budget_consumed:        float = 0.0    # Running tally of drawdown consumed today
    reserve_consumed:       float = 0.0    # Reserve budget consumed today

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self.standard_budget - self.budget_consumed)

    @property
    def reserve_remaining(self) -> float:
        return max(0.0, self.reserve_pool - self.reserve_consumed)

    @property
    def total_consumed(self) -> float:
        return self.budget_consumed + self.reserve_consumed

    def consume(self, amount: float, is_high_conviction: bool = False) -> bool:
        """
        Record consumption of drawdown budget.
        Standard trades draw from standard_budget (80% of session).
        High-conviction trades draw from reserve_pool (20% + carryover).
        Returns True if budget was available, False if it would be exceeded.
        """
        if is_high_conviction:
            if amount > self.reserve_remaining:
                return False
            self.reserve_consumed += amount
        else:
            if amount > self.budget_remaining:   # checks against standard_budget
                return False
            self.budget_consumed += amount
        return True

    def return_unspent(self) -> float:
        """
        At end of session, unspent STANDARD budget returns to reserve pool.
        Returns the unspent standard amount added to the reserve pool.
        """
        unspent = self.budget_remaining   # standard_budget - budget_consumed
        self.reserve_pool += unspent
        self.reserve_pool = min(self.reserve_pool, self.total_daily_limit * 1.5)
        return unspent


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — THE DYNAMIC SAFETY MARGIN ENGINE
# FORGE-03. Reads live drawdown metrics. Outputs the permitted position size.
# ─────────────────────────────────────────────────────────────────────────────

class DynamicSafetyMargin:
    """
    FORGE-03: Dynamic Safety Margin.

    Position size reduces automatically as drawdown budget is consumed.
    Never operates at the edge of firm limits. Daylight always maintained.

    The margin is calculated independently of Kelly (C-06) and Loss Response (C-08).
    All three constraints accumulate — the most conservative output wins.

    Usage:
        margin = DynamicSafetyMargin(rule_engine)

        result = margin.calculate(
            firm_id=FirmID.FTMO,
            drawdown_pct_used=0.45,    # 45% of drawdown budget consumed
            daily_pct_used=0.20,
            base_size=1.0,             # 1 lot, before any reduction
            drawdown_remaining_dollars=5_500.0,
            total_drawdown_dollars=10_000.0,
        )

        if not result.new_entries_permitted:
            # RED tier — close all, no new trades
            return

        final_size = result.permitted_size
    """

    def __init__(self, rule_engine: MultiFirmRuleEngine):
        self._rule_engine = rule_engine
        # Track per-firm margin history for logging
        self._last_tier: dict[str, MarginTier] = {}

    def get_tier(self, drawdown_pct_used: float) -> MarginTier:
        """
        Determine which safety tier is active based on drawdown consumption.

        Args:
            drawdown_pct_used: Fraction of total drawdown budget consumed (0.0–1.0).

        Returns:
            The active MarginTier.
        """
        # Check tiers from highest to lowest (RED first)
        for tier in [MarginTier.RED, MarginTier.TIER_3,
                     MarginTier.TIER_2, MarginTier.TIER_1, MarginTier.CLEAR]:
            lo, hi = TIER_BOUNDARIES[tier]
            if lo <= drawdown_pct_used < hi:
                return tier
        # Any value >= 1.0 is RED
        return MarginTier.RED

    def calculate(
        self,
        firm_id:                    str,
        drawdown_pct_used:          float,    # 0.0–1.0 total drawdown consumed
        daily_pct_used:             float,    # 0.0–1.0 today's daily budget consumed
        base_size:                  float,    # Base position size (lots or contracts)
        drawdown_remaining_dollars: float,    # Dollars until firm floor
        total_drawdown_dollars:     float,    # Total drawdown budget in dollars
    ) -> MarginResult:
        """
        Calculate the dynamic safety margin for a proposed position.

        This is called BEFORE every position is sized. The result feeds into
        the master sizing stack (along with Kelly cap and Loss Response floor).

        Args:
            firm_id:                    The firm being evaluated.
            drawdown_pct_used:          Fraction of total drawdown budget consumed.
            daily_pct_used:             Fraction of today's daily drawdown budget consumed.
            base_size:                  Base position size before any margin reduction.
            drawdown_remaining_dollars: Dollars left before the firm floor.
            total_drawdown_dollars:     Total drawdown budget at start of evaluation.

        Returns:
            MarginResult with the permitted size and all related metrics.
        """
        rules = self._rule_engine.get_firm_rules(firm_id)
        minimum_size = rules.minimum_position_size

        # ── Determine active tier ────────────────────────────────────────────
        tier = self.get_tier(drawdown_pct_used)

        # Log tier changes
        prev_tier = self._last_tier.get(firm_id)
        if prev_tier and prev_tier != tier:
            logger.warning(
                "[FORGE-03][%s] ⚡ Tier change: %s → %s | "
                "DD used: %.1f%% | Remaining: $%.2f",
                firm_id, TIER_LABELS[prev_tier], TIER_LABELS[tier],
                drawdown_pct_used * 100, drawdown_remaining_dollars
            )
        elif not prev_tier and tier != MarginTier.CLEAR:
            logger.warning(
                "[FORGE-03][%s] Active tier: %s | DD used: %.1f%%",
                firm_id, TIER_LABELS[tier], drawdown_pct_used * 100
            )
        self._last_tier[firm_id] = tier

        # ── Calculate permitted size ──────────────────────────────────────────
        size_multiplier = TIER_MULTIPLIERS[tier]

        if tier == MarginTier.RED:
            # RED: No new entries permitted
            permitted_size = 0.0
            new_entries = False
            reason = (
                f"🔴 RED ALERT: {drawdown_pct_used:.1%} of drawdown used. "
                f"NO new entries. Close all open positions. "
                f"${drawdown_remaining_dollars:,.2f} remaining to firm floor."
            )
            logger.critical(
                "[FORGE-03][%s] 🔴 RED — %.1f%% drawdown used. "
                "No new entries. $%.2f to floor.",
                firm_id, drawdown_pct_used * 100, drawdown_remaining_dollars
            )

        elif tier == MarginTier.TIER_3:
            # TIER 3 (Orange): Minimum size only
            permitted_size = minimum_size
            new_entries = True
            reason = (
                f"🟠 ORANGE: {drawdown_pct_used:.1%} drawdown used. "
                f"Minimum position size only: {minimum_size}. "
                f"${drawdown_remaining_dollars:,.2f} remaining."
            )
            logger.error(
                "[FORGE-03][%s] 🟠 ORANGE — %.1f%% used. "
                "Min size: %s. $%.2f to floor.",
                firm_id, drawdown_pct_used * 100,
                minimum_size, drawdown_remaining_dollars
            )

        elif tier == MarginTier.TIER_2:
            # TIER 2 (Yellow): 50% of base size
            calculated = base_size * size_multiplier
            permitted_size = max(minimum_size, calculated)
            new_entries = True
            reason = (
                f"🟡 YELLOW: {drawdown_pct_used:.1%} drawdown used. "
                f"Size reduced to 50%: {base_size:.4f} → {permitted_size:.4f}. "
                f"${drawdown_remaining_dollars:,.2f} remaining."
            )
            logger.warning(
                "[FORGE-03][%s] 🟡 YELLOW — %.1f%% used. "
                "Size: %.4f → %.4f. $%.2f to floor.",
                firm_id, drawdown_pct_used * 100,
                base_size, permitted_size, drawdown_remaining_dollars
            )

        elif tier == MarginTier.TIER_1:
            # TIER 1: 75% of base size
            calculated = base_size * size_multiplier
            permitted_size = max(minimum_size, calculated)
            new_entries = True
            reason = (
                f"🔵 TIER-1: {drawdown_pct_used:.1%} drawdown used. "
                f"Size reduced to 75%: {base_size:.4f} → {permitted_size:.4f}. "
                f"${drawdown_remaining_dollars:,.2f} remaining."
            )
            logger.info(
                "[FORGE-03][%s] 🔵 TIER-1 — %.1f%% used. "
                "Size: %.4f → %.4f. $%.2f to floor.",
                firm_id, drawdown_pct_used * 100,
                base_size, permitted_size, drawdown_remaining_dollars
            )

        else:
            # CLEAR: Full size
            permitted_size = base_size
            new_entries = True
            reason = (
                f"✅ CLEAR: {drawdown_pct_used:.1%} drawdown used. "
                f"Full position size: {base_size:.4f}. "
                f"${drawdown_remaining_dollars:,.2f} remaining."
            )

        # ── Distance to next tier ────────────────────────────────────────────
        tier_order = [MarginTier.CLEAR, MarginTier.TIER_1,
                      MarginTier.TIER_2, MarginTier.TIER_3, MarginTier.RED]
        current_idx = tier_order.index(tier)
        if current_idx < len(tier_order) - 1:
            next_tier = tier_order[current_idx + 1]
            next_boundary = TIER_BOUNDARIES[next_tier][0]
            pct_to_next = max(0.0, next_boundary - drawdown_pct_used)
            dollars_to_next = pct_to_next * total_drawdown_dollars
        else:
            pct_to_next = None
            dollars_to_next = None

        return MarginResult(
            tier=tier,
            drawdown_pct_used=drawdown_pct_used,
            daily_pct_used=daily_pct_used,
            size_multiplier=size_multiplier,
            daily_budget_multiplier=DAILY_BUDGET_MULTIPLIERS[tier],
            minimum_size=minimum_size,
            permitted_size=permitted_size,
            new_entries_permitted=new_entries,
            firm_id=firm_id,
            reason=reason,
            pct_to_next_tier=pct_to_next,
            dollars_to_next_tier=dollars_to_next,
            dollars_to_floor=drawdown_remaining_dollars,
        )

    # ── COMPOSITE SIZING (all three constraints together) ────────────────────

    def apply_full_stack(
        self,
        firm_id:                    str,
        drawdown_pct_used:          float,
        daily_pct_used:             float,
        base_size:                  float,
        drawdown_remaining_dollars: float,
        total_drawdown_dollars:     float,
        kelly_size:                 float,          # From C-06 Kelly cap
        loss_response_modifier:     float,          # From C-08 Loss response floor
    ) -> dict:
        """
        Apply the full three-constraint sizing stack:
            1. Dynamic Safety Margin (FORGE-03)
            2. Kelly Cap (C-06)
            3. Loss Response Floor (C-08)

        The minimum of all three wins. Always.

        Args:
            firm_id:                    Active firm.
            drawdown_pct_used:          Fraction of total DD budget consumed.
            daily_pct_used:             Fraction of daily DD budget consumed.
            base_size:                  Base size before any constraints.
            drawdown_remaining_dollars: Dollars to firm floor.
            total_drawdown_dollars:     Total DD budget.
            kelly_size:                 Size from Kelly Criterion (C-06 output).
            loss_response_modifier:     Modifier from Loss Response Floor (C-08 output).

        Returns:
            dict with final_size, binding_constraint, and all intermediate values.
        """
        # Step 1: Safety Margin
        margin_result = self.calculate(
            firm_id=firm_id,
            drawdown_pct_used=drawdown_pct_used,
            daily_pct_used=daily_pct_used,
            base_size=base_size,
            drawdown_remaining_dollars=drawdown_remaining_dollars,
            total_drawdown_dollars=total_drawdown_dollars,
        )

        if not margin_result.new_entries_permitted:
            return {
                "final_size": 0.0,
                "binding_constraint": "SAFETY_MARGIN_RED",
                "new_entries_permitted": False,
                "margin_result": margin_result,
                "kelly_size": kelly_size,
                "loss_response_modifier": loss_response_modifier,
                "reason": margin_result.reason,
            }

        # Step 2: Kelly cap (C-06) — expressed as fraction of account
        # Kelly size and margin size may be in different units depending on setup.
        # Here we compare the margin-reduced size against Kelly.
        margin_size = margin_result.permitted_size
        loss_adjusted = margin_size * loss_response_modifier
        # Apply kelly_size as a separate ceiling
        final_size = min(loss_adjusted, kelly_size) if kelly_size > 0 else loss_adjusted

        # Determine binding constraint
        if final_size == kelly_size and kelly_size < loss_adjusted:
            binding = "KELLY_CAP_C06"
        elif final_size == loss_adjusted and loss_response_modifier < 1.0:
            binding = "LOSS_RESPONSE_C08"
        elif margin_result.tier != MarginTier.CLEAR:
            binding = f"SAFETY_MARGIN_{margin_result.tier.name}"
        else:
            binding = "NONE"

        logger.info(
            "[FORGE-03][%s] Full stack: Margin=%.4f | Loss×Mod=%.4f | Kelly=%.4f → Final=%.4f | Binding: %s",
            firm_id, margin_size, loss_adjusted, kelly_size, final_size, binding
        )

        return {
            "final_size": final_size,
            "binding_constraint": binding,
            "new_entries_permitted": True,
            "margin_result": margin_result,
            "kelly_size": kelly_size,
            "loss_response_modifier": loss_response_modifier,
            "margin_permitted_size": margin_size,
            "loss_adjusted_size": loss_adjusted,
            "reason": (
                f"Final size {final_size:.4f} | Binding: {binding} | "
                f"Margin tier: {TIER_LABELS[margin_result.tier]}"
            ),
        }

    # ── DAILY BUDGET ALLOCATION (FORGE-12) ───────────────────────────────────

    def allocate_daily_budget(
        self,
        firm_id:              str,
        daily_limit_dollars:  float,         # Firm's daily drawdown limit in $
        margin_tier:          MarginTier,     # Current safety tier
        session_date:         object,         # date
        reserve_carryover:    float = 0.0,    # Unspent from prior sessions
    ) -> DailyBudgetAllocation:
        """
        FORGE-12: Allocate the daily drawdown budget for a new session.

        Budget is scaled down by the active safety tier.
        20% of the session budget is reserved for highest-conviction setups (4-stack+).
        Unspent budget at session close returns to the reserve pool.

        Args:
            firm_id:             Active firm.
            daily_limit_dollars: Firm's hard daily drawdown limit.
            margin_tier:         Current safety tier — scales the budget.
            session_date:        The trading day being opened.
            reserve_carryover:   Unspent budget from prior sessions.

        Returns:
            DailyBudgetAllocation for this session.
        """
        # Scale budget by tier
        tier_scale = DAILY_BUDGET_MULTIPLIERS[margin_tier]
        session_budget = daily_limit_dollars * tier_scale

        # Reserve pool: prior carryover + 20% of today's budget for high-conviction
        high_conviction_reserve = session_budget * 0.20
        standard_budget = session_budget * 0.80
        reserve_pool = reserve_carryover + high_conviction_reserve

        alloc = DailyBudgetAllocation(
            session_date=session_date,
            firm_id=firm_id,
            total_daily_limit=daily_limit_dollars,
            session_budget=session_budget,
            reserve_pool=reserve_pool,
            standard_budget=standard_budget,
            high_conviction_budget=high_conviction_reserve,
        )

        logger.info(
            "[FORGE-12][%s] Daily budget allocated: $%.2f (%.0f%% of $%.2f limit) | "
            "Standard: $%.2f | Reserve: $%.2f | Tier: %s",
            firm_id, session_budget, tier_scale * 100, daily_limit_dollars,
            standard_budget, reserve_pool, TIER_LABELS[margin_tier]
        )

        return alloc

    # ── 5%ERS SPECIAL CASE ───────────────────────────────────────────────────

    def apply_fivepercenters_extra_buffer(
        self,
        base_multiplier: float,
        drawdown_pct_used: float,
    ) -> float:
        """
        The 5%ers have a 4% total drawdown — the tightest of all 5 firms.
        One bad day can fail the entire evaluation.

        Apply an additional 20% size reduction on top of the standard tiers
        to ensure we never approach the 4% limit aggressively.

        Args:
            base_multiplier:   The standard tier multiplier.
            drawdown_pct_used: Current drawdown consumption.

        Returns:
            Adjusted multiplier with 5%ers extra buffer applied.
        """
        # Additional 20% reduction for the tightest drawdown firm
        extra_buffer = 0.80

        # At 20%+ used, add further caution (4% total means 20% of budget = 0.8%)
        if drawdown_pct_used >= 0.20:
            extra_buffer = 0.70  # 30% additional reduction when 20%+ used

        adjusted = base_multiplier * extra_buffer
        logger.debug(
            "[FORGE-03][5PERCENTERS] Extra buffer applied: %.2f × %.2f = %.2f",
            base_multiplier, extra_buffer, adjusted
        )
        return adjusted

    def calculate_with_firm_adjustment(
        self,
        firm_id:                    str,
        drawdown_pct_used:          float,
        daily_pct_used:             float,
        base_size:                  float,
        drawdown_remaining_dollars: float,
        total_drawdown_dollars:     float,
    ) -> MarginResult:
        """
        Calculate margin with firm-specific adjustments applied.
        The 5%ers get an additional buffer. All other firms use standard tiers.
        """
        result = self.calculate(
            firm_id=firm_id,
            drawdown_pct_used=drawdown_pct_used,
            daily_pct_used=daily_pct_used,
            base_size=base_size,
            drawdown_remaining_dollars=drawdown_remaining_dollars,
            total_drawdown_dollars=total_drawdown_dollars,
        )

        if firm_id == FirmID.FIVEPERCENTERS and result.new_entries_permitted:
            adjusted_multiplier = self.apply_fivepercenters_extra_buffer(
                result.size_multiplier if result.tier != MarginTier.TIER_3 else 1.0,
                drawdown_pct_used
            )
            rules = self._rule_engine.get_firm_rules(firm_id)
            new_size = max(rules.minimum_position_size, base_size * adjusted_multiplier)
            # Return a modified result
            return MarginResult(
                tier=result.tier,
                drawdown_pct_used=result.drawdown_pct_used,
                daily_pct_used=result.daily_pct_used,
                size_multiplier=adjusted_multiplier,
                daily_budget_multiplier=result.daily_budget_multiplier * 0.80,
                minimum_size=result.minimum_size,
                permitted_size=new_size,
                new_entries_permitted=result.new_entries_permitted,
                firm_id=firm_id,
                reason=result.reason + f" [5%ers extra buffer: ×{adjusted_multiplier:.2f}]",
                pct_to_next_tier=result.pct_to_next_tier,
                dollars_to_next_tier=result.dollars_to_next_tier,
                dollars_to_floor=result.dollars_to_floor,
            )

        return result

    # ── SUMMARY ──────────────────────────────────────────────────────────────

    def status_summary(
        self,
        firm_id: str,
        drawdown_pct_used: float,
        drawdown_remaining_dollars: float,
        total_drawdown_dollars: float,
    ) -> dict:
        """
        Quick status summary for ARCHITECT and Dashboard.
        No size calculation — just the current state.
        """
        tier = self.get_tier(drawdown_pct_used)
        tier_order = [MarginTier.CLEAR, MarginTier.TIER_1,
                      MarginTier.TIER_2, MarginTier.TIER_3, MarginTier.RED]
        current_idx = tier_order.index(tier)

        thresholds = []
        for t in tier_order:
            lo, hi = TIER_BOUNDARIES[t]
            budget_at_threshold = lo * total_drawdown_dollars
            thresholds.append({
                "tier": t.name,
                "label": TIER_LABELS[t],
                "at_pct": lo,
                "budget_at_threshold": budget_at_threshold,
                "multiplier": TIER_MULTIPLIERS[t],
            })

        return {
            "firm_id": firm_id,
            "active_tier": tier.name,
            "active_label": TIER_LABELS[tier],
            "drawdown_pct_used": drawdown_pct_used,
            "drawdown_remaining": drawdown_remaining_dollars,
            "new_entries_permitted": tier != MarginTier.RED,
            "size_multiplier": TIER_MULTIPLIERS[tier],
            "tiers": thresholds,
        }
