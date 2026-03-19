"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   capital_management.py — Layer 3                           ║
║  FORGE-29: Capital Pipeline Management                                      ║
║  FORGE-30: Evaluation Scaling Strategy                                      ║
║  FORGE-52: Multi-Firm Capital Coordination                                  ║
║  FORGE-53: Evaluation Scaling (account size progression)                   ║
║  FORGE-62: Drawdown Budget Allocation                                       ║
║  FORGE-69: Opportunity Cost Calculator                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.capital_management")


# ── FORGE-29: Capital Pipeline Management ────────────────────────────────────

@dataclass
class CapitalPipeline:
    """FORGE-29: Track the full capital pipeline across all evaluations."""
    # Capital states (FX-08: only received capital is deployable)
    bank_capital:           float   # Actual money in Jorge's bank — ONLY this is deployable
    receivables:            float   # Pending payouts — NOT deployable
    evaluation_costs:       float   # Total spent on eval fees
    # Active evaluations
    active_evaluations:     int
    active_eval_cost:       float   # Total capital at risk in active evals
    # Funded accounts
    funded_accounts:        int
    funded_account_value:   float   # Total equity in funded accounts
    # Projections
    monthly_projection:     float   # Expected monthly income
    roi_pct:                float   # ROI on evaluation fees

    @property
    def total_deployed(self) -> float:
        return self.active_eval_cost

    @property
    def available_for_new_eval(self) -> float:
        """FX-08: Only bank capital, after reserving safety buffer."""
        safety_buffer = 500.0   # Always keep $500 in reserve
        return max(0.0, self.bank_capital - self.total_deployed - safety_buffer)

    @property
    def pipeline_health(self) -> str:
        if self.bank_capital < 200:
            return "CRITICAL"
        elif self.bank_capital < 500:
            return "LOW"
        elif self.funded_accounts > 0:
            return "HEALTHY"
        return "BUILDING"


def build_pipeline_snapshot(
    bank_capital:       float,
    receivables:        float,
    active_evals:       int,
    active_eval_cost:   float,
    funded_accounts:    int,
    funded_equity:      float,
    monthly_payouts:    float,
    total_fees_paid:    float,
) -> CapitalPipeline:
    """FORGE-29: Build capital pipeline snapshot."""
    roi = (monthly_payouts * 12) / total_fees_paid * 100 if total_fees_paid > 0 else 0.0
    return CapitalPipeline(
        bank_capital=bank_capital,
        receivables=receivables,
        evaluation_costs=total_fees_paid,
        active_evaluations=active_evals,
        active_eval_cost=active_eval_cost,
        funded_accounts=funded_accounts,
        funded_account_value=funded_equity,
        monthly_projection=monthly_payouts,
        roi_pct=round(roi, 2),
    )


# ── FORGE-30: Evaluation Scaling Strategy ────────────────────────────────────
# Stage progression: $10K warmup → $100K FTMO → Apex → DNA → 5%ers

SCALING_STAGES: list[dict] = [
    {"stage": 1, "firm": FirmID.FTMO,          "account_size": 10_000,   "cost": 84,
     "notes": "Warmup — cheapest. Prove consistency. Pass rate target 80%+."},
    {"stage": 2, "firm": FirmID.FTMO,          "account_size": 100_000,  "cost": 540,
     "notes": "Main FTMO. No time limit. No consistency rule. Best evaluation."},
    {"stage": 3, "firm": FirmID.APEX,          "account_size": 50_000,   "cost": 147,
     "notes": "Add Apex. Futures focus. Beware trailing drawdown."},
    {"stage": 4, "firm": FirmID.DNA_FUNDED,    "account_size": 100_000,  "cost": 200,
     "notes": "DNA Funded Stage 4 only. Forex. Verify reputation before funding."},
    {"stage": 5, "firm": FirmID.FIVEPERCENTERS,"account_size": 100_000,  "cost": 295,
     "notes": "5%ers at Month 18+. 4-stack catalyst required. Path to $4M."},
]

def get_next_scaling_stage(
    current_stage:  int,
    bank_capital:   float,
    consecutive_passes: int,
) -> Optional[dict]:
    """FORGE-30: Get the next recommended scaling stage."""
    if current_stage >= len(SCALING_STAGES):
        return None   # Already at max scale

    next_stage = SCALING_STAGES[current_stage]   # 0-indexed

    if bank_capital < next_stage["cost"]:
        logger.info(
            "[FORGE-30] Next stage blocked: need $%d for %s %s. "
            "Have: $%.0f.",
            next_stage["cost"], next_stage["firm"], next_stage["account_size"],
            bank_capital,
        )
        return None

    if consecutive_passes < 2 and current_stage > 0:
        logger.info("[FORGE-30] Need 2+ consecutive passes before scaling to stage %d.", current_stage + 1)
        return None

    return next_stage


# ── FORGE-62: Drawdown Budget Allocation ─────────────────────────────────────
# Allocate the drawdown budget across sessions, setups, and positions.

@dataclass
class DrawdownBudgetAllocation:
    """FORGE-62: How the total drawdown budget is divided."""
    total_budget:           float   # Total allowed drawdown in $
    session_allocation:     float   # Max per session (daily limit applies)
    per_trade_allocation:   float   # Max risk per trade
    reserve:                float   # Safety buffer — never touch
    remaining_budget:       float   # Available for new trades today
    allocation_pct:         dict    # Distribution: session/per_trade/reserve

def allocate_drawdown_budget(
    total_budget:       float,   # e.g. $10,000 drawdown budget
    drawdown_used:      float,   # Already consumed
    daily_limit:        float,   # Firm's daily limit
    session_pct:        float = 0.20,   # 20% of budget for today's session
    per_trade_pct:      float = 0.02,   # 2% per trade max
    reserve_pct:        float = 0.15,   # 15% reserve — untouchable
) -> DrawdownBudgetAllocation:
    """FORGE-62: Allocate drawdown budget for safety."""
    remaining = max(0.0, total_budget - drawdown_used)
    session_alloc = min(daily_limit, remaining * session_pct)
    per_trade_alloc = total_budget * per_trade_pct
    reserve = total_budget * reserve_pct

    return DrawdownBudgetAllocation(
        total_budget=total_budget,
        session_allocation=round(session_alloc, 2),
        per_trade_allocation=round(per_trade_alloc, 2),
        reserve=round(reserve, 2),
        remaining_budget=round(remaining, 2),
        allocation_pct={
            "session": f"{session_pct:.0%}",
            "per_trade": f"{per_trade_pct:.0%}",
            "reserve": f"{reserve_pct:.0%}",
        }
    )


# ── FORGE-69: Opportunity Cost Calculator ────────────────────────────────────
# Compare value of trading this setup vs waiting for a better one.

@dataclass
class OpportunityCostResult:
    """FORGE-69: Opportunity cost analysis for a proposed trade."""
    current_ev:         float   # Expected value of current setup
    alternative_ev:     float   # EV of a typical better setup (if waited)
    opportunity_cost:   float   # Foregone value by not waiting
    should_wait:        bool    # True if opportunity cost justifies waiting
    reason:             str

def calculate_opportunity_cost(
    current_win_rate:   float,
    current_avg_win:    float,
    current_avg_loss:   float,
    typical_win_rate:   float = 0.72,   # Avg of 30 strategies
    typical_avg_win:    float = 200.0,
    typical_avg_loss:   float = 80.0,
    wait_probability:   float = 0.60,   # Prob of finding a better setup in next session
) -> OpportunityCostResult:
    """FORGE-69: Should we take this trade or wait for a better opportunity?"""
    current_ev = (current_win_rate * current_avg_win) - ((1 - current_win_rate) * current_avg_loss)
    typical_ev = (typical_win_rate * typical_avg_win) - ((1 - typical_win_rate) * typical_avg_loss)

    # Expected value of waiting = (prob of finding better) × (better EV) - cost of inaction
    wait_ev = wait_probability * typical_ev

    # Opportunity cost = value you give up by choosing current over waiting
    opp_cost = wait_ev - current_ev

    should_wait = opp_cost > (current_ev * 0.30)   # Wait if >30% better EV available

    if should_wait:
        reason = (f"Wait. Better opportunity likely: current EV ${current_ev:.2f} vs "
                  f"typical ${typical_ev:.2f} ({wait_probability:.0%} probability).")
    else:
        reason = (f"Take trade. Current EV ${current_ev:.2f} acceptable. "
                  f"Opportunity cost ${opp_cost:.2f} doesn't justify waiting.")

    return OpportunityCostResult(
        current_ev=round(current_ev, 2),
        alternative_ev=round(typical_ev, 2),
        opportunity_cost=round(opp_cost, 2),
        should_wait=should_wait,
        reason=reason,
    )
