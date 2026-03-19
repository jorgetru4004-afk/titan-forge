"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   capital_extractor.py — FORGE-89–95 — Layer 4              ║
║  FORGE-89: Payout Optimization (when to extract, how much)                  ║
║  FORGE-90: Safety Net Management (always keep enough to re-run evals)       ║
║  FORGE-91: Compound Growth Engine (reinvest extracted capital)              ║
║  FORGE-92: Extraction Scheduling (optimal payout timing per firm)           ║
║  FORGE-93: Funded Rule Set Switcher (post-pass state machine)               ║
║  FORGE-94: Capital Staging (tier capital for optimal deployment)            ║
║  FORGE-95: Apex Payout Max Tracker (6-payout lifecycle limit)               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.capital_extractor")

# ── FORGE-89: Payout Optimization ────────────────────────────────────────────
# When to pull capital and how much. Firm-specific rules.

# Payout rules per firm
PAYOUT_RULES: dict[str, dict] = {
    FirmID.FTMO: {
        "min_wait_days":      14,
        "min_profit_to_pull": 0.10,   # 10% of account before first pull
        "max_extraction_pct": 0.80,   # Never pull more than 80% — keep trading capital
        "cycle_days":         14,
        "notes": "FTMO: bi-weekly. Min $100 withdrawal. Consistency rule on Phase 2.",
    },
    FirmID.APEX: {
        "min_wait_days":      30,
        "min_profit_to_pull": 0.0625, # $3,125 on $50K = safety net first
        "max_extraction_pct": 0.90,
        "cycle_days":         30,
        "notes": "Apex: monthly. Build to $52,600 safety net first (C-19).",
    },
    FirmID.DNA_FUNDED: {
        "min_wait_days":      14,
        "min_profit_to_pull": 0.05,
        "max_extraction_pct": 0.80,
        "cycle_days":         14,
        "notes": "DNA: bi-weekly. Consistency rule: no day > 40% of total P&L.",
    },
    FirmID.FIVEPERCENTERS: {
        "min_wait_days":      30,
        "min_profit_to_pull": 0.05,
        "max_extraction_pct": 1.00,   # 5%ers: can pull 100% profit
        "cycle_days":         30,
        "notes": "5%ers: monthly. Path to $4M account. Optimize for scaling.",
    },
    FirmID.TOPSTEP: {
        "min_wait_days":      30,
        "min_profit_to_pull": 0.05,
        "max_extraction_pct": 0.80,
        "cycle_days":         30,
        "notes": "Topstep: monthly. $100 minimum.",
    },
}

@dataclass
class PayoutDecision:
    """FORGE-89: Payout optimization decision."""
    firm_id:            str
    account_id:         str
    should_request:     bool
    requested_amount:   float
    available_to_pull:  float
    reason:             str
    next_payout_date:   Optional[date]
    is_safety_net_met:  bool

def optimize_payout(
    firm_id:            str,
    account_id:         str,
    account_size:       float,
    current_profit:     float,
    safety_net_met:     bool,
    last_payout_date:   Optional[date] = None,
    as_of:              Optional[date] = None,
) -> PayoutDecision:
    """FORGE-89: Optimize payout timing and amount."""
    today = as_of or date.today()
    rules = PAYOUT_RULES.get(firm_id, PAYOUT_RULES[FirmID.FTMO])

    # Days since last payout
    if last_payout_date:
        days_since = (today - last_payout_date).days
    else:
        days_since = rules["min_wait_days"]   # First payout

    can_request = days_since >= rules["min_wait_days"]

    # Available to pull (respect max extraction)
    max_pull = current_profit * rules["max_extraction_pct"]

    # Safety net must be met first (C-19)
    if not safety_net_met:
        return PayoutDecision(
            firm_id=firm_id, account_id=account_id,
            should_request=False, requested_amount=0.0,
            available_to_pull=0.0,
            reason=(
                f"Safety net not yet reached. Build buffer first. "
                f"Current profit: ${current_profit:,.2f}"
            ),
            next_payout_date=None, is_safety_net_met=False,
        )

    # Minimum profit threshold
    min_profit = account_size * rules["min_profit_to_pull"]
    if current_profit < min_profit:
        return PayoutDecision(
            firm_id=firm_id, account_id=account_id,
            should_request=False, requested_amount=0.0,
            available_to_pull=current_profit,
            reason=f"Profit ${current_profit:,.2f} below minimum threshold ${min_profit:,.2f}.",
            next_payout_date=last_payout_date + timedelta(days=rules["cycle_days"])
                             if last_payout_date else today,
            is_safety_net_met=True,
        )

    if not can_request:
        next_payout = (last_payout_date + timedelta(days=rules["min_wait_days"])
                       if last_payout_date else today)
        return PayoutDecision(
            firm_id=firm_id, account_id=account_id,
            should_request=False, requested_amount=0.0,
            available_to_pull=max_pull,
            reason=f"Must wait {rules['min_wait_days'] - days_since} more days.",
            next_payout_date=next_payout, is_safety_net_met=True,
        )

    # Ready to request payout
    amount = round(max_pull, 2)
    next_d = today + timedelta(days=rules["cycle_days"])
    logger.info(
        "[FORGE-89][%s] 💰 Payout authorized: $%.2f from %s. Next: %s.",
        account_id, amount, firm_id, next_d,
    )
    return PayoutDecision(
        firm_id=firm_id, account_id=account_id,
        should_request=True, requested_amount=amount,
        available_to_pull=max_pull,
        reason=f"Payout ready. ${amount:,.2f} ({rules['max_extraction_pct']:.0%} of profit).",
        next_payout_date=next_d, is_safety_net_met=True,
    )


# ── FORGE-90: Safety Net Management ──────────────────────────────────────────
# Always keep enough capital to re-run any failed evaluation.
# Apex: $52,600 safety net (C-19)

SAFETY_NET_TARGETS: dict[str, float] = {
    FirmID.APEX:           52_600.0,  # C-19: $52,600 (100% unrealized floor + 5% buffer)
    FirmID.FTMO:            5_000.0,  # Enough for 2 re-evaluations + buffer
    FirmID.DNA_FUNDED:      3_000.0,
    FirmID.FIVEPERCENTERS:  5_000.0,
    FirmID.TOPSTEP:         3_000.0,
}

EVAL_RE_RUN_RESERVE: float = 2_000.0  # Always keep $2K for worst-case re-run

@dataclass
class SafetyNetStatus:
    firm_id:            str
    target_amount:      float
    current_amount:     float
    is_met:             bool
    shortfall:          float
    pct_complete:       float
    recommendation:     str

def check_safety_net(
    firm_id:    str,
    balance:    float,    # Current funded account balance (realized)
    start_bal:  float,    # Starting balance
) -> SafetyNetStatus:
    """FORGE-90: Check safety net status."""
    target = SAFETY_NET_TARGETS.get(firm_id, 3_000.0)
    current = max(0.0, balance - start_bal)  # Net profit
    met = current >= target
    shortfall = max(0.0, target - current)
    pct = min(1.0, current / target) if target > 0 else 1.0

    if met:
        rec = f"✅ Safety net met ({current:,.0f} ≥ {target:,.0f}). Payout extraction active."
    else:
        rec = (f"Build safety net first. Need ${shortfall:,.0f} more "
               f"({pct:.0%} complete). No payouts until target reached.")

    return SafetyNetStatus(
        firm_id=firm_id, target_amount=target, current_amount=current,
        is_met=met, shortfall=shortfall, pct_complete=round(pct, 4),
        recommendation=rec,
    )


# ── FORGE-91: Compound Growth Engine ─────────────────────────────────────────
# Month-by-month capital projection with compound reinvestment.

@dataclass
class GrowthProjection:
    """FORGE-91: Capital growth projection."""
    month:              int
    funded_accounts:    int
    monthly_income:     float
    cumulative_income:  float
    reinvested:         float     # Portion reinvested in new evaluations
    extracted:          float     # Portion taken as personal income
    bank_balance:       float

def project_compound_growth(
    months:             int = 48,
    starting_capital:   float = 200.0,
    ftmo_monthly_yield: float = 0.08,   # 8% monthly on $100K funded
    reinvestment_rate:  float = 0.30,   # 30% reinvested, 70% extracted
) -> list[GrowthProjection]:
    """FORGE-91: Project compound growth month by month."""
    projections = []
    bank = starting_capital
    funded = 0
    monthly_income = 0.0
    cumulative = 0.0

    # Growth stages from the document
    stage_schedule = {
        3: {"accounts": 1, "size": 100_000.0, "cost": 540.0},    # FTMO $100K
        6: {"accounts": 2, "size": 50_000.0,  "cost": 147.0},    # + Apex
        12: {"accounts": 3, "size": 100_000.0, "cost": 200.0},   # + DNA
        18: {"accounts": 4, "size": 100_000.0, "cost": 295.0},   # + 5%ers
    }

    for m in range(1, months + 1):
        # Check for new account purchases
        if m in stage_schedule and bank >= stage_schedule[m]["cost"]:
            bank -= stage_schedule[m]["cost"]
            funded += 1

        # Monthly income scales with funded accounts
        monthly_income = funded * 100_000.0 * ftmo_monthly_yield
        # Real-world: grows more slowly early, faster at scale
        if m <= 6:
            monthly_income *= 0.30   # Still ramping up
        elif m <= 18:
            monthly_income *= 0.60
        elif m <= 36:
            monthly_income *= 0.85

        reinvested = monthly_income * reinvestment_rate
        extracted  = monthly_income * (1.0 - reinvestment_rate)
        bank      += extracted
        cumulative += monthly_income

        projections.append(GrowthProjection(
            month=m, funded_accounts=funded,
            monthly_income=round(monthly_income, 2),
            cumulative_income=round(cumulative, 2),
            reinvested=round(reinvested, 2),
            extracted=round(extracted, 2),
            bank_balance=round(bank, 2),
        ))

    return projections


# ── FORGE-95: Apex Payout Lifecycle Tracker ──────────────────────────────────
# Apex has a hard cap: 6 payouts maximum per account.

APEX_MAX_PAYOUTS: int = 6

@dataclass
class ApexLifecycleStatus:
    """FORGE-95: Apex account lifecycle — 6 payout maximum."""
    account_id:         str
    payouts_taken:      int
    payouts_remaining:  int
    should_retire:      bool
    total_extracted:    float
    recommendation:     str

def check_apex_lifecycle(
    account_id:     str,
    payouts_taken:  int,
    total_extracted: float,
) -> ApexLifecycleStatus:
    """FORGE-95: Track Apex account toward 6-payout retirement."""
    remaining = max(0, APEX_MAX_PAYOUTS - payouts_taken)
    should_retire = payouts_taken >= APEX_MAX_PAYOUTS

    if should_retire:
        rec = (f"🏁 Apex account {account_id} at max payouts ({APEX_MAX_PAYOUTS}). "
               f"Total extracted: ${total_extracted:,.0f}. "
               f"Start replacement evaluation NOW before gap in capital flow.")
    elif remaining == 1:
        rec = (f"⚠ Final Apex payout remaining. Start replacement eval pipeline. "
               f"Remaining: {remaining}/{APEX_MAX_PAYOUTS}.")
    else:
        rec = f"Apex lifecycle: {payouts_taken}/{APEX_MAX_PAYOUTS} payouts. {remaining} remaining."

    return ApexLifecycleStatus(
        account_id=account_id, payouts_taken=payouts_taken,
        payouts_remaining=remaining, should_retire=should_retire,
        total_extracted=total_extracted, recommendation=rec,
    )
