"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  payout_alert.py — FORGE-33 — Layer 1                       ║
║  PAYOUT ALERT SYSTEM                                                         ║
║  Alerts Jorge: evaluation pass, payout due, payout received.                 ║
║  Full capital pipeline tracked.                                              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.payout_alert")

class AlertType(Enum):
    EVAL_PASSED         = auto()
    FUNDED_CONFIRMED    = auto()
    PAYOUT_DUE          = auto()
    PAYOUT_SUBMITTED    = auto()
    PAYOUT_RECEIVED     = auto()
    PAYOUT_OVERDUE      = auto()
    SAFETY_NET_REACHED  = auto()
    SCALING_ELIGIBLE    = auto()
    ACCOUNT_RETIRING    = auto()

class AlertPriority(Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"

@dataclass
class PayoutAlert:
    alert_id:    str
    alert_type:  AlertType
    priority:    AlertPriority
    account_id:  str
    firm_id:     str
    amount:      Optional[float]
    message:     str
    created_at:  datetime
    acknowledged: bool = False

@dataclass
class PayoutRecord:
    payout_id:       str
    account_id:      str
    firm_id:         str
    requested_amount: float
    approved_amount:  Optional[float]
    requested_at:    datetime
    expected_by:     datetime
    received_at:     Optional[datetime]
    status:          str   # "PENDING", "APPROVED", "RECEIVED", "OVERDUE"
    is_receivable:   bool  # True = pending/approved but not in bank yet

    @property
    def is_overdue(self) -> bool:
        return (
            self.status in ("PENDING", "APPROVED")
            and datetime.now(timezone.utc) > self.expected_by
        )

class PayoutAlertSystem:
    """FORGE-33: Payout Alert System — full pipeline tracked."""

    def __init__(self):
        self._alerts:  list[PayoutAlert]  = []
        self._payouts: dict[str, PayoutRecord] = {}
        self._received_capital: float = 0.0   # FX-08: only funds in bank
        self._receivables:      float = 0.0   # NOT available for deployment

    # ── CAPITAL TRACKING (FX-08) ────────────────────────────────────────────

    @property
    def available_capital(self) -> float:
        """FX-08: ONLY funds in Jorge's bank. Never include receivables."""
        return self._received_capital

    @property
    def total_receivables(self) -> float:
        """Pending/approved-awaiting-transfer funds — NOT available for deployment."""
        return self._receivables

    def record_payout_received(self, payout_id: str, amount: float) -> PayoutAlert:
        """Record capital that has actually arrived in Jorge's bank."""
        if payout_id in self._payouts:
            self._payouts[payout_id].received_at = datetime.now(timezone.utc)
            self._payouts[payout_id].status = "RECEIVED"
            self._payouts[payout_id].is_receivable = False
            self._receivables = max(0.0, self._receivables - amount)
        self._received_capital += amount
        return self._raise_alert(
            AlertType.PAYOUT_RECEIVED, AlertPriority.INFO,
            payout_id.split("-")[0] if "-" in payout_id else "UNKNOWN",
            "UNKNOWN", amount,
            f"💰 PAYOUT RECEIVED: ${amount:,.2f}. "
            f"Available capital updated: ${self._received_capital:,.2f}."
        )

    # ── ALERT GENERATORS ─────────────────────────────────────────────────────

    def alert_eval_passed(self, account_id: str, firm_id: str, profit: float) -> PayoutAlert:
        return self._raise_alert(
            AlertType.EVAL_PASSED, AlertPriority.CRITICAL,
            account_id, firm_id, profit,
            f"✅ EVALUATION PASSED: {firm_id} | ${profit:,.2f} profit. "
            f"ACTION: Confirm funded status and initiate payout process."
        )

    def alert_funded_confirmed(self, account_id: str, firm_id: str, account_size: float) -> PayoutAlert:
        return self._raise_alert(
            AlertType.FUNDED_CONFIRMED, AlertPriority.CRITICAL,
            account_id, firm_id, account_size,
            f"⚡ FUNDED CONFIRMED: {firm_id} ${account_size:,.0f}. "
            f"Funded rules now active. Build Safety Net buffer FIRST."
        )

    def alert_payout_due(
        self, account_id: str, firm_id: str, amount: float,
        payout_cycle_days: int = 14,
    ) -> PayoutAlert:
        payout_id = f"{account_id}-PAY-{datetime.now().strftime('%Y%m%d')}"
        expected_by = datetime.now(timezone.utc) + timedelta(days=payout_cycle_days)
        record = PayoutRecord(
            payout_id=payout_id, account_id=account_id, firm_id=firm_id,
            requested_amount=amount, approved_amount=None,
            requested_at=datetime.now(timezone.utc), expected_by=expected_by,
            received_at=None, status="PENDING", is_receivable=True,
        )
        self._payouts[payout_id] = record
        self._receivables += amount
        return self._raise_alert(
            AlertType.PAYOUT_DUE, AlertPriority.WARNING,
            account_id, firm_id, amount,
            f"📤 PAYOUT SUBMITTED: ${amount:,.2f} from {firm_id}. "
            f"Expected in {payout_cycle_days} days. ID: {payout_id}. "
            f"Status: RECEIVABLE (not yet in bank — not available for deployment)."
        )

    def alert_safety_net_reached(self, account_id: str, firm_id: str, balance: float) -> PayoutAlert:
        return self._raise_alert(
            AlertType.SAFETY_NET_REACHED, AlertPriority.INFO,
            account_id, firm_id, balance,
            f"🛡 SAFETY NET REACHED: {firm_id} {account_id}. "
            f"Balance: ${balance:,.2f}. "
            f"Payout extraction mode now active. Begin optimized payout schedule."
        )

    def alert_scaling_eligible(self, account_id: str, firm_id: str, new_size: float) -> PayoutAlert:
        return self._raise_alert(
            AlertType.SCALING_ELIGIBLE, AlertPriority.INFO,
            account_id, firm_id, new_size,
            f"📈 SCALING ELIGIBLE: {firm_id} {account_id}. "
            f"New account size: ${new_size:,.0f}. Request scale-up."
        )

    def check_overdue_payouts(self) -> list[PayoutAlert]:
        """Generate overdue alerts for any payout past expected date."""
        alerts = []
        for pid, rec in self._payouts.items():
            if rec.is_overdue:
                alert = self._raise_alert(
                    AlertType.PAYOUT_OVERDUE, AlertPriority.CRITICAL,
                    rec.account_id, rec.firm_id, rec.requested_amount,
                    f"⚠ PAYOUT OVERDUE: {rec.firm_id} ${rec.requested_amount:,.2f}. "
                    f"Requested: {rec.requested_at.date()}. "
                    f"Expected by: {rec.expected_by.date()}. "
                    f"Contact firm support immediately."
                )
                alerts.append(alert)
                rec.status = "OVERDUE"
        return alerts

    def pipeline_summary(self) -> dict:
        """Full capital pipeline for ARCHITECT dashboard."""
        pending  = sum(r.requested_amount for r in self._payouts.values()
                       if r.status in ("PENDING", "APPROVED"))
        received = sum(r.requested_amount for r in self._payouts.values()
                       if r.status == "RECEIVED")
        return {
            "available_capital":  self._received_capital,
            "total_receivables":  self._receivables,
            "pending_payouts":    pending,
            "received_total":     received,
            "payout_count":       len(self._payouts),
            "unacknowledged_alerts": sum(1 for a in self._alerts if not a.acknowledged),
        }

    def acknowledge_alert(self, alert_id: str) -> None:
        for a in self._alerts:
            if a.alert_id == alert_id:
                a.acknowledged = True

    def pending_alerts(self) -> list[PayoutAlert]:
        return [a for a in self._alerts if not a.acknowledged]

    def _raise_alert(
        self,
        alert_type: AlertType, priority: AlertPriority,
        account_id: str, firm_id: str, amount: Optional[float], message: str,
    ) -> PayoutAlert:
        alert_id = f"ALT-{len(self._alerts)+1:04d}"
        alert = PayoutAlert(
            alert_id=alert_id, alert_type=alert_type, priority=priority,
            account_id=account_id, firm_id=firm_id, amount=amount,
            message=message, created_at=datetime.now(timezone.utc),
        )
        self._alerts.append(alert)
        log_fn = {
            AlertPriority.CRITICAL: logger.critical,
            AlertPriority.WARNING:  logger.warning,
            AlertPriority.INFO:     logger.info,
        }[priority]
        log_fn("[FORGE-33] %s", message)
        return alert
