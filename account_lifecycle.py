"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  account_lifecycle.py — FORGE-104–110 — Layer 4             ║
║  FORGE-104: Account Scaling Milestones                                      ║
║  FORGE-105: Replacement Evaluation Pipeline                                 ║
║  FORGE-106: Month-by-Month Capital Projection                               ║
║  FORGE-107: Retirement Trigger Detection                                    ║
║  FORGE-108: Cross-Firm Scaling Coordination                                ║
║  FORGE-109: The 5%ers $4M Path ($280K/month target)                        ║
║  FORGE-110: Capital Velocity Tracker                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.account_lifecycle")

# ── FORGE-106: Month-by-Month Capital Projection ──────────────────────────────
# From the document: projected income at each milestone.

NEXUS_CAPITAL_PROJECTION: list[dict] = [
    # Month: {description, funded_accounts, monthly_income_low, monthly_income_high}
    {"month": 1,   "desc": "Warmup + Paper Gate",      "funded": 0, "low": 0,         "high": 0},
    {"month": 3,   "desc": "FTMO $100K Funded",         "funded": 1, "low": 800,       "high": 1_200},
    {"month": 6,   "desc": "FTMO + Apex",               "funded": 2, "low": 2_000,     "high": 3_000},
    {"month": 12,  "desc": "3 Firms Active",            "funded": 3, "low": 3_500,     "high": 4_200},
    {"month": 18,  "desc": "5%ers Added",               "funded": 4, "low": 6_000,     "high": 8_000},
    {"month": 24,  "desc": "Scaling Accelerating",      "funded": 5, "low": 18_000,    "high": 22_000},
    {"month": 36,  "desc": "Full Scale",                "funded": 8, "low": 165_000,   "high": 184_000},
    {"month": 48,  "desc": "5%ers $4M",                 "funded": 10,"low": 220_000,   "high": 250_000},
    {"month": 60,  "desc": "Ultimate Target",           "funded": 12,"low": 280_000,   "high": 280_000},
]

def get_projection_for_month(month: int) -> dict:
    """FORGE-106: Get the capital projection for a given month."""
    best = None
    for p in NEXUS_CAPITAL_PROJECTION:
        if p["month"] <= month:
            best = p
        else:
            break
    return best or NEXUS_CAPITAL_PROJECTION[0]


# ── FORGE-104: Account Scaling Milestones ────────────────────────────────────
# Firm-specific scaling plans.

SCALING_PLANS: dict[str, list[dict]] = {
    FirmID.FIVEPERCENTERS: [
        {"size": 100_000,  "month_eligible": 18, "profit_split": 0.80},
        {"size": 200_000,  "month_eligible": 24, "profit_split": 0.80},
        {"size": 500_000,  "month_eligible": 30, "profit_split": 0.80},
        {"size": 1_000_000,"month_eligible": 36, "profit_split": 0.90},
        {"size": 2_000_000,"month_eligible": 42, "profit_split": 1.00},
        {"size": 4_000_000,"month_eligible": 48, "profit_split": 1.00},  # ULTIMATE TARGET
    ],
    FirmID.FTMO: [
        {"size": 10_000,   "month_eligible": 0,  "profit_split": 0.80},
        {"size": 25_000,   "month_eligible": 1,  "profit_split": 0.80},
        {"size": 50_000,   "month_eligible": 2,  "profit_split": 0.80},
        {"size": 100_000,  "month_eligible": 3,  "profit_split": 0.80},
        {"size": 200_000,  "month_eligible": 6,  "profit_split": 0.90},
    ],
    FirmID.APEX: [
        {"size": 25_000,   "month_eligible": 0, "profit_split": 0.90},
        {"size": 50_000,   "month_eligible": 1, "profit_split": 0.90},
        {"size": 100_000,  "month_eligible": 3, "profit_split": 0.90},
        {"size": 150_000,  "month_eligible": 6, "profit_split": 0.90},
    ],
}

@dataclass
class ScalingMilestone:
    firm_id:        str
    current_size:   float
    next_size:      Optional[float]
    months_to_eligible: Optional[int]
    profit_split_next:  float
    monthly_income_at_next: float
    recommendation: str

def get_scaling_milestone(
    firm_id:            str,
    current_size:       float,
    months_live:        int,
    avg_monthly_return: float = 0.04,  # 4% monthly return
) -> ScalingMilestone:
    """FORGE-104: Determine next scaling milestone."""
    plan = SCALING_PLANS.get(firm_id, [])

    # Find current tier
    current_tier  = None
    next_tier     = None
    for i, tier in enumerate(plan):
        if tier["size"] <= current_size:
            current_tier = tier
        elif next_tier is None:
            next_tier = tier

    if not next_tier:
        return ScalingMilestone(
            firm_id=firm_id, current_size=current_size,
            next_size=None, months_to_eligible=None,
            profit_split_next=current_tier["profit_split"] if current_tier else 0.80,
            monthly_income_at_next=0.0,
            recommendation=f"At maximum scaling tier for {firm_id}.",
        )

    months_to = max(0, next_tier["month_eligible"] - months_live)
    income    = next_tier["size"] * avg_monthly_return * next_tier["profit_split"]

    return ScalingMilestone(
        firm_id=firm_id, current_size=current_size,
        next_size=next_tier["size"],
        months_to_eligible=months_to,
        profit_split_next=next_tier["profit_split"],
        monthly_income_at_next=round(income, 2),
        recommendation=(
            f"Next: ${next_tier['size']:,} at month {next_tier['month_eligible']}. "
            f"{'Ready now!' if months_to == 0 else f'{months_to} months to go.'} "
            f"Monthly income at next tier: ${income:,.0f}."
        )
    )


# ── FORGE-109: The 5%ers $4M Path ────────────────────────────────────────────
# The ultimate target: $4M account at 100% profit split = $280K/month.

@dataclass
class FivePercenterPath:
    """FORGE-109: Progress toward $4M 5%ers account."""
    current_size:       float
    target_size:        float   # $4,000,000
    current_split:      float   # Current profit split
    monthly_income:     float   # Current monthly income
    target_monthly:     float   # $280,000 at $4M 100% split
    pct_to_ultimate:    float
    tiers_remaining:    int
    years_to_target:    float
    recommendation:     str
    is_mission_complete: bool = False

FIVE_PERCENTERS_ULTIMATE_TARGET = 4_000_000.0
FIVE_PERCENTERS_MONTHLY_TARGET  = 280_000.0

def get_five_percenters_path(
    current_size:   float,
    months_live:    int = 0,
) -> FivePercenterPath:
    """FORGE-109: Track progress to $4M 5%ers account."""
    milestone = get_scaling_milestone(FirmID.FIVEPERCENTERS, current_size, months_live)
    plan      = SCALING_PLANS[FirmID.FIVEPERCENTERS]

    # Count remaining tiers
    tiers_remaining = sum(1 for t in plan if t["size"] > current_size)

    # Current income
    current_split   = next((t["profit_split"] for t in plan if t["size"] == current_size), 0.80)
    monthly_income  = current_size * 0.07 * current_split  # 7% monthly return assumption

    pct_to_ultimate = current_size / FIVE_PERCENTERS_ULTIMATE_TARGET
    years_remaining = (FIVE_PERCENTERS_ULTIMATE_TARGET - current_size) / (current_size * 12 * 0.07) \
                      if current_size > 0 else 10.0

    if current_size >= FIVE_PERCENTERS_ULTIMATE_TARGET:
        rec = (f"🏆 ULTIMATE TARGET REACHED: $4M at 100% split. "
               f"Monthly income: ${FIVE_PERCENTERS_MONTHLY_TARGET:,.0f}/month. "
               f"NEXUS Capital mission complete.")
    else:
        rec = (f"5%ers path: ${current_size:,.0f}/{FIVE_PERCENTERS_ULTIMATE_TARGET:,.0f} "
               f"({pct_to_ultimate:.1%}). "
               f"{tiers_remaining} tiers remaining. "
               f"Est. {years_remaining:.1f} years at current pace.")

    complete = current_size >= FIVE_PERCENTERS_ULTIMATE_TARGET

    return FivePercenterPath(
        current_size=current_size,
        target_size=FIVE_PERCENTERS_ULTIMATE_TARGET,
        current_split=current_split,
        monthly_income=round(monthly_income, 2),
        target_monthly=FIVE_PERCENTERS_MONTHLY_TARGET,
        pct_to_ultimate=round(pct_to_ultimate, 4),
        tiers_remaining=tiers_remaining,
        years_to_target=round(max(0.0, years_remaining), 2),
        recommendation=rec,
        is_mission_complete=complete,
    )


# ── FORGE-110: Capital Velocity Tracker ──────────────────────────────────────
# Tracks how fast capital is being generated and deployed.

@dataclass
class CapitalVelocity:
    """FORGE-110: How fast capital flows through the system."""
    monthly_inflow:     float   # From funded account payouts
    monthly_outflow:    float   # Evaluation fees + costs
    net_velocity:       float   # Inflow - outflow
    roi_on_fees:        float   # Return on evaluation fee investment
    months_to_payback:  float   # Months until fees are recovered
    is_accelerating:    bool    # Getting faster?
    recommendation:     str

def calculate_capital_velocity(
    monthly_income:     float,
    monthly_fees:       float,
    total_fees_paid:    float,
    total_extracted:    float,
) -> CapitalVelocity:
    """FORGE-110: Calculate capital velocity metrics."""
    net = monthly_income - monthly_fees
    roi = (monthly_income * 12) / total_fees_paid * 100 if total_fees_paid > 0 else 0.0
    payback = total_fees_paid / monthly_income if monthly_income > 0 else float("inf")
    accelerating = net > 0

    rec = (
        f"Velocity: ${net:+,.0f}/month net. "
        f"ROI on fees: {roi:.0f}%. "
        f"Payback: {payback:.1f} months."
        if payback != float("inf") else
        "No income yet — still building."
    )

    return CapitalVelocity(
        monthly_inflow=monthly_income, monthly_outflow=monthly_fees,
        net_velocity=net, roi_on_fees=round(roi, 2),
        months_to_payback=round(payback, 1),
        is_accelerating=accelerating, recommendation=rec,
    )
