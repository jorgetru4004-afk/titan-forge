"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                multi_firm_orchestrator.py — FORGE-96–103 — Layer 4          ║
║  FORGE-96: Multi-Firm Account Registry                                      ║
║  FORGE-97: Cross-Firm P&L Aggregation                                       ║
║  FORGE-98: Multi-Firm Risk Coordination                                     ║
║  FORGE-99: Session Priority Queue (which account trades first)              ║
║  FORGE-100: Cross-Firm Drawdown Monitor                                     ║
║  FORGE-101: Firm Conflict Resolver (prevent same move on all accounts)      ║
║  FORGE-102: Multi-Firm Reporting                                            ║
║  FORGE-103: Account Health Monitor                                          ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.multi_firm_orchestrator")

class AccountStatus(Enum):
    EVALUATION      = auto()
    FUNDED_ACTIVE   = auto()
    FUNDED_PAUSED   = auto()   # Drawdown concern — paused
    RETIRING        = auto()   # Near lifecycle end
    RETIRED         = auto()
    FAILED          = auto()


@dataclass
class LiveAccount:
    """FORGE-96: Live account in the NEXUS registry."""
    account_id:         str
    firm_id:            str
    account_size:       float
    status:             AccountStatus
    is_funded:          bool
    current_equity:     float
    unrealized_pnl:     float
    daily_pnl:          float
    total_pnl:          float       # Lifetime P&L on this account
    drawdown_pct_used:  float
    safety_net_met:     bool
    payouts_taken:      int
    platform:           str          # "DXTrade", "Rithmic", "TradeLocker", "MT5"
    requires_vps:       bool         # Rithmic/Tradovate need Windows VPS
    last_updated:       date

    @property
    def is_at_risk(self) -> bool:
        return self.drawdown_pct_used >= 0.70

    @property
    def is_healthy(self) -> bool:
        return self.drawdown_pct_used < 0.50 and self.status == AccountStatus.FUNDED_ACTIVE


# ── FORGE-96: Multi-Firm Account Registry ────────────────────────────────────

class MultiFirmRegistry:
    """FORGE-96: Central registry of all live accounts across all firms."""

    def __init__(self):
        self._accounts: dict[str, LiveAccount] = {}

    def register(self, account: LiveAccount) -> None:
        self._accounts[account.account_id] = account
        logger.info("[FORGE-96] Registered: %s | %s | $%s | %s",
                    account.account_id, account.firm_id,
                    f"{account.account_size:,.0f}", account.status.name)

    def update_equity(
        self, account_id: str, equity: float, unrealized: float,
        daily_pnl: float, drawdown_pct: float,
    ) -> None:
        if account_id in self._accounts:
            acc = self._accounts[account_id]
            acc.current_equity  = equity
            acc.unrealized_pnl  = unrealized
            acc.daily_pnl       = daily_pnl
            acc.drawdown_pct_used = drawdown_pct
            acc.last_updated    = date.today()

    def get(self, account_id: str) -> Optional[LiveAccount]:
        return self._accounts.get(account_id)

    def all_funded(self) -> list[LiveAccount]:
        return [a for a in self._accounts.values() if a.is_funded]

    def all_active(self) -> list[LiveAccount]:
        return [a for a in self._accounts.values()
                if a.status not in (AccountStatus.RETIRED, AccountStatus.FAILED)]

    def at_risk(self) -> list[LiveAccount]:
        return [a for a in self._accounts.values() if a.is_at_risk]


# ── FORGE-97: Cross-Firm P&L Aggregation ─────────────────────────────────────

@dataclass
class AggregatedPnL:
    """FORGE-97: Total P&L across all accounts."""
    total_equity:           float
    total_unrealized:       float
    total_daily_pnl:        float
    total_lifetime_pnl:     float
    funded_account_count:   int
    by_firm:                dict[str, float]   # firm_id → daily P&L
    best_account:           Optional[str]
    worst_account:          Optional[str]
    overall_health:         str   # "EXCELLENT" / "GOOD" / "CAUTION" / "ALERT"

def aggregate_pnl(accounts: list[LiveAccount]) -> AggregatedPnL:
    """FORGE-97: Aggregate P&L across all live accounts."""
    if not accounts:
        return AggregatedPnL(0, 0, 0, 0, 0, {}, None, None, "NO_ACCOUNTS")

    total_eq   = sum(a.current_equity for a in accounts)
    total_unr  = sum(a.unrealized_pnl for a in accounts)
    total_day  = sum(a.daily_pnl for a in accounts)
    total_life = sum(a.total_pnl for a in accounts)
    funded     = sum(1 for a in accounts if a.is_funded)

    by_firm: dict[str, float] = {}
    for a in accounts:
        by_firm[a.firm_id] = by_firm.get(a.firm_id, 0.0) + a.daily_pnl

    best  = max(accounts, key=lambda a: a.daily_pnl).account_id if accounts else None
    worst = min(accounts, key=lambda a: a.daily_pnl).account_id if accounts else None

    max_dd = max((a.drawdown_pct_used for a in accounts), default=0.0)
    if max_dd >= 0.85:
        health = "ALERT"
    elif max_dd >= 0.60 or total_day < 0:
        health = "CAUTION"
    elif total_day > 0:
        health = "EXCELLENT" if total_day > 1_000 else "GOOD"
    else:
        health = "GOOD"

    return AggregatedPnL(
        total_equity=total_eq, total_unrealized=total_unr,
        total_daily_pnl=total_day, total_lifetime_pnl=total_life,
        funded_account_count=funded, by_firm=by_firm,
        best_account=best, worst_account=worst, overall_health=health,
    )


# ── FORGE-99: Session Priority Queue ─────────────────────────────────────────
# When multiple accounts can trade — prioritize by health and opportunity.

def prioritize_accounts(accounts: list[LiveAccount]) -> list[tuple[str, float, str]]:
    """
    FORGE-99: Return accounts sorted by trading priority.
    Returns list of (account_id, priority_score, reason).
    Healthiest accounts trade first. At-risk accounts trade last (reduced size).
    """
    scored = []
    for acc in accounts:
        if not acc.is_funded or acc.status != AccountStatus.FUNDED_ACTIVE:
            continue

        score = 10.0

        # Drawdown health
        if acc.drawdown_pct_used >= 0.85:
            score -= 7.0
        elif acc.drawdown_pct_used >= 0.70:
            score -= 4.0
        elif acc.drawdown_pct_used >= 0.60:
            score -= 2.0

        # Safety net status
        if not acc.safety_net_met:
            score -= 1.5

        # Daily P&L momentum
        if acc.daily_pnl > 0:
            score += 0.5

        reason = (f"DD: {acc.drawdown_pct_used:.0%} | "
                  f"Safety: {'✓' if acc.safety_net_met else '✗'} | "
                  f"Daily: ${acc.daily_pnl:+,.0f}")

        scored.append((acc.account_id, round(score, 2), reason))

    return sorted(scored, key=lambda x: -x[1])


# ── FORGE-101: Firm Conflict Resolver ────────────────────────────────────────
# Prevent taking the same trade across all accounts (correlation check at system level).

@dataclass
class ConflictResolution:
    """FORGE-101: Cross-firm conflict resolution result."""
    account_id:         str
    can_trade:          bool
    conflicts:          list[str]   # Account IDs with conflicting positions
    reason:             str

def resolve_firm_conflicts(
    proposed_account_id: str,
    proposed_instrument: str,
    open_positions_by_account: dict[str, list[str]],   # account_id → [instruments]
) -> ConflictResolution:
    """FORGE-101: Prevent same trade on multiple accounts simultaneously."""
    conflicts = []
    for acc_id, instruments in open_positions_by_account.items():
        if acc_id == proposed_account_id:
            continue
        if proposed_instrument.upper() in [i.upper() for i in instruments]:
            conflicts.append(acc_id)

    can_trade = len(conflicts) == 0
    reason = (
        f"No conflict. {proposed_instrument} not held elsewhere."
        if can_trade else
        f"CONFLICT: {proposed_instrument} already open on {conflicts}. "
        f"FORGE-70 prevents identical positions across simultaneous evaluations."
    )

    return ConflictResolution(
        account_id=proposed_account_id,
        can_trade=can_trade,
        conflicts=conflicts,
        reason=reason,
    )


# ── FORGE-103: Account Health Monitor ────────────────────────────────────────

@dataclass
class HealthReport:
    """FORGE-103: Account health summary for ARCHITECT dashboard."""
    account_id:         str
    firm_id:            str
    health_grade:       str     # "A" / "B" / "C" / "D" / "F"
    health_score:       float   # 0–100
    concerns:           list[str]
    action_required:    bool
    action:             Optional[str]

def generate_health_report(account: LiveAccount) -> HealthReport:
    """FORGE-103: Grade account health."""
    score = 100.0
    concerns = []

    if account.drawdown_pct_used >= 0.85:
        score -= 50.0; concerns.append("⚠ CRITICAL drawdown (85%+)")
    elif account.drawdown_pct_used >= 0.70:
        score -= 30.0; concerns.append("🟠 High drawdown (70%+)")
    elif account.drawdown_pct_used >= 0.60:
        score -= 15.0; concerns.append("🟡 Elevated drawdown (60%+)")

    if not account.safety_net_met:
        score -= 10.0; concerns.append("Safety net not yet reached")

    if account.daily_pnl < 0:
        score -= 5.0; concerns.append(f"Negative today: ${account.daily_pnl:,.0f}")

    if score >= 85:
        grade = "A"
    elif score >= 70:
        grade = "B"
    elif score >= 55:
        grade = "C"
    elif score >= 35:
        grade = "D"
    else:
        grade = "F"

    action_required = grade in ("D", "F")
    action = "Reduce position sizes immediately. Review risk parameters." if action_required else None

    return HealthReport(
        account_id=account.account_id, firm_id=account.firm_id,
        health_grade=grade, health_score=round(score, 1),
        concerns=concerns, action_required=action_required, action=action,
    )
