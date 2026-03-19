"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     nexus_core.py — FORGE-116–121 — Layer 4                 ║
║  FORGE-116: NEXUS Treasury (central capital manager — FX-08 enforced)      ║
║  FORGE-117: Capital Deployment Engine (when/where to deploy next)          ║
║  FORGE-118: Unified System State (single source of truth)                  ║
║  FORGE-119: NEXUS Mission Tracker ($280K/month target)                     ║
║  FORGE-120: System Health Watchdog                                         ║
║  FORGE-121: NEXUS Heartbeat (30-second system pulse)                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.nexus_core")

# ── FORGE-116: NEXUS Treasury ────────────────────────────────────────────────
# FX-08: get_available_capital() returns ONLY funds in Jorge's bank.
# NEVER receivables. NEVER funded account equity. NEVER pending payouts.

class TreasuryState(Enum):
    BOOTSTRAPPING    = auto()   # < $500 — barely starting
    BUILDING         = auto()   # < $2,000 — building toward first eval
    OPERATIONAL      = auto()   # < $10,000 — running evaluations
    SCALING          = auto()   # < $50,000 — multiple evaluations active
    ESTABLISHED      = auto()   # $50K+ — fully funded machine

@dataclass
class TreasurySnapshot:
    """FORGE-116: Complete treasury snapshot at a point in time."""
    timestamp:              datetime
    # FX-08: Only these funds are deployable
    bank_balance:           float    # Actual bank funds — ONLY deployable capital
    # Non-deployable (tracked but NOT available)
    pending_payouts:        float    # Submitted but not received
    funded_account_equity:  float    # In broker accounts — NOT withdrawable yet
    # Deployed capital
    active_eval_fees:       float    # Currently at risk in evaluations
    # Totals
    total_assets:           float    # Bank + funded equity + pending
    net_worth_tradeable:    float    # Bank + funded equity
    # State
    state:                  TreasuryState
    # Monthly metrics
    monthly_income_last:    float
    monthly_fees_last:      float

    @property
    def available_capital(self) -> float:
        """FX-08: ONLY bank balance is available for deployment."""
        return self.bank_balance

    @property
    def available_for_new_eval(self) -> float:
        """Available after reserving safety buffer and active fees."""
        SAFETY_RESERVE = 500.0
        return max(0.0, self.bank_balance - self.active_eval_fees - SAFETY_RESERVE)

    @property
    def is_solvent(self) -> bool:
        return self.bank_balance > 0

class NexusTreasury:
    """
    FORGE-116: NEXUS Treasury — central capital manager.

    Iron Law: get_available_capital() ONLY returns Jorge's bank balance.
    Receivables, funded account equity, and pending payouts are NEVER included.
    Spending from anything other than the bank = FX-08 violation.
    """

    def __init__(self, initial_bank_balance: float = 200.0):
        self._bank_balance:         float = initial_bank_balance
        self._pending_payouts:      float = 0.0
        self._funded_equity:        float = 0.0
        self._active_eval_fees:     float = 0.0
        self._total_income:         float = 0.0
        self._total_fees_paid:      float = 0.0
        self._snapshots:            list[TreasurySnapshot] = []

    # ── FX-08: ONLY source of deployable capital ──────────────────────────────

    def get_available_capital(self) -> float:
        """
        FX-08: Returns ONLY funds in Jorge's bank account.
        This is the only source of capital for new evaluations.
        """
        return self._bank_balance

    # ── Capital flows ─────────────────────────────────────────────────────────

    def receive_payout(self, amount: float, from_account: str) -> None:
        """Record capital received in Jorge's bank from a funded account."""
        self._bank_balance    += amount
        self._pending_payouts  = max(0.0, self._pending_payouts - amount)
        self._total_income    += amount
        logger.warning(
            "[FORGE-116][FX-08] 💰 Payout received: $%.2f from %s. "
            "Bank balance: $%.2f. ONLY this is deployable.",
            amount, from_account, self._bank_balance,
        )

    def pay_evaluation_fee(self, amount: float, firm: str, eval_id: str) -> bool:
        """Pay an evaluation fee from bank balance only."""
        if self._bank_balance < amount:
            logger.error(
                "[FORGE-116] Cannot pay eval fee $%.2f for %s — "
                "insufficient bank balance $%.2f.",
                amount, firm, self._bank_balance,
            )
            return False
        self._bank_balance    -= amount
        self._active_eval_fees += amount
        self._total_fees_paid += amount
        logger.info(
            "[FORGE-116] Paid $%.2f for %s %s. Bank: $%.2f.",
            amount, firm, eval_id, self._bank_balance,
        )
        return True

    def record_eval_complete(self, fee_amount: float) -> None:
        """Remove completed/failed eval from active fees."""
        self._active_eval_fees = max(0.0, self._active_eval_fees - fee_amount)

    def submit_payout_request(self, amount: float, account: str) -> None:
        """Record a payout request (moves to pending — NOT yet in bank)."""
        self._pending_payouts += amount
        logger.info(
            "[FORGE-116] Payout submitted: $%.2f from %s. "
            "Status: PENDING — NOT yet deployable.",
            amount, account,
        )

    def update_funded_equity(self, equity: float) -> None:
        self._funded_equity = equity

    def snapshot(self) -> TreasurySnapshot:
        """Create current treasury snapshot."""
        total = self._bank_balance + self._funded_equity + self._pending_payouts
        net   = self._bank_balance + self._funded_equity

        bank = self._bank_balance
        if bank < 500:
            state = TreasuryState.BOOTSTRAPPING
        elif bank < 2_000:
            state = TreasuryState.BUILDING
        elif bank < 10_000:
            state = TreasuryState.OPERATIONAL
        elif bank < 50_000:
            state = TreasuryState.SCALING
        else:
            state = TreasuryState.ESTABLISHED

        snap = TreasurySnapshot(
            timestamp=datetime.now(timezone.utc),
            bank_balance=self._bank_balance,
            pending_payouts=self._pending_payouts,
            funded_account_equity=self._funded_equity,
            active_eval_fees=self._active_eval_fees,
            total_assets=total,
            net_worth_tradeable=net,
            state=state,
            monthly_income_last=0.0,   # Set externally
            monthly_fees_last=0.0,
        )
        self._snapshots.append(snap)
        return snap


# ── FORGE-119: NEXUS Mission Tracker ─────────────────────────────────────────

@dataclass
class MissionStatus:
    """FORGE-119: Progress toward $280K/month ultimate target."""
    current_monthly:        float
    ultimate_target:        float    # $280,000
    pct_of_target:          float
    months_to_target:       Optional[float]
    current_phase:          str
    milestone_next:         Optional[str]
    years_elapsed:          float
    is_mission_complete:    bool
    recommendation:         str

NEXUS_ULTIMATE_TARGET: float = 280_000.0

def get_mission_status(
    current_monthly_income: float,
    months_elapsed:         int,
) -> MissionStatus:
    """FORGE-119: Track NEXUS Capital's progress toward $280K/month."""
    pct = current_monthly_income / NEXUS_ULTIMATE_TARGET
    years = months_elapsed / 12.0

    if current_monthly_income <= 0:
        phase = "PRE-REVENUE"
        months_to = None
    elif current_monthly_income < 1_000:
        phase = "BOOTSTRAPPING"
        months_to = (NEXUS_ULTIMATE_TARGET - current_monthly_income) / max(1, current_monthly_income) * 12
    elif current_monthly_income < 10_000:
        phase = "EARLY_GROWTH"
        months_to = None
    elif current_monthly_income < 50_000:
        phase = "SCALING"
        months_to = None
    elif current_monthly_income < 200_000:
        phase = "ADVANCED_SCALING"
        months_to = None
    else:
        phase = "FINAL_APPROACH"
        months_to = (NEXUS_ULTIMATE_TARGET - current_monthly_income) / max(1_000, current_monthly_income / 12)

    complete = current_monthly_income >= NEXUS_ULTIMATE_TARGET

    if complete:
        rec = (f"🏆 MISSION COMPLETE. ${current_monthly_income:,.0f}/month. "
               f"Jorge Trujillo — NEXUS Capital — $280K/month achieved. "
               f"Years to target: {years:.1f}.")
    else:
        remaining = NEXUS_ULTIMATE_TARGET - current_monthly_income
        rec = (f"Mission {pct:.1%} complete. ${remaining:,.0f}/month remaining. "
               f"Phase: {phase}. Year {years:.1f}.")

    return MissionStatus(
        current_monthly=current_monthly_income,
        ultimate_target=NEXUS_ULTIMATE_TARGET,
        pct_of_target=round(pct, 4),
        months_to_target=round(months_to, 1) if months_to else None,
        current_phase=phase,
        milestone_next=None,
        years_elapsed=round(years, 2),
        is_mission_complete=complete,
        recommendation=rec,
    )


# ── FORGE-121: NEXUS Heartbeat ────────────────────────────────────────────────
# 30-second system pulse. Verifies all systems operational.

@dataclass
class SystemHeartbeat:
    """FORGE-121: 30-second system health pulse."""
    timestamp:          datetime
    pulse_id:           int
    systems_ok:         dict[str, bool]   # system → healthy?
    overall_health:     str
    active_accounts:    int
    open_positions:     int
    daily_pnl:          float
    alerts_pending:     int
    uptime_hours:       float

class NexusHeartbeat:
    """FORGE-121: System heartbeat — 30-second pulse."""

    def __init__(self, start_time: Optional[datetime] = None):
        self._start   = start_time or datetime.now(timezone.utc)
        self._pulse   = 0
        self._history: list[SystemHeartbeat] = []

    def pulse(
        self,
        systems_ok:     dict[str, bool],
        active_accounts: int,
        open_positions: int,
        daily_pnl:      float,
        alerts_pending: int,
    ) -> SystemHeartbeat:
        """Fire one heartbeat."""
        self._pulse += 1
        now = datetime.now(timezone.utc)
        uptime = (now - self._start).total_seconds() / 3600.0

        all_ok   = all(systems_ok.values())
        any_down = not all_ok
        health   = "HEALTHY" if all_ok else "DEGRADED"

        hb = SystemHeartbeat(
            timestamp=now, pulse_id=self._pulse,
            systems_ok=systems_ok,
            overall_health=health,
            active_accounts=active_accounts,
            open_positions=open_positions,
            daily_pnl=daily_pnl,
            alerts_pending=alerts_pending,
            uptime_hours=round(uptime, 2),
        )
        self._history.append(hb)
        if len(self._history) > 120:   # Keep 1 hour of history (120 × 30s)
            self._history.pop(0)

        if any_down:
            failed = [k for k, v in systems_ok.items() if not v]
            logger.error("[FORGE-121] ❤️ DEGRADED PULSE #%d: %s DOWN", self._pulse, failed)
        else:
            logger.debug("[FORGE-121] ❤️ Pulse #%d: HEALTHY | PnL: $%+.0f",
                         self._pulse, daily_pnl)

        return hb

    @property
    def consecutive_healthy(self) -> int:
        count = 0
        for hb in reversed(self._history):
            if hb.overall_health == "HEALTHY":
                count += 1
            else:
                break
        return count
