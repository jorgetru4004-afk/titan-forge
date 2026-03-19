"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  ip_consistency.py — FORGE-44 — Layer 1                     ║
║  IP CONSISTENCY MANAGER                                                      ║
║  Ensures all connections from consistent Railway server IP.                  ║
║  Firms with IP requirements stay compliant.                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("titan_forge.ip_consistency")

# Firms that explicitly track/flag IP changes
IP_SENSITIVE_FIRMS = frozenset({"FTMO", "DNA_FUNDED"})

@dataclass
class IPCheckResult:
    is_consistent:    bool
    registered_ip:    Optional[str]
    current_ip:       str
    firm_id:          str
    is_ip_sensitive:  bool
    reason:           str

@dataclass
class IPRecord:
    ip_address:   str
    first_seen:   datetime
    last_seen:    datetime
    connection_count: int = 1

class IPConsistencyManager:
    """
    FORGE-44: IP Consistency Manager.

    Ensures all broker connections originate from the same Railway server IP.
    Firms like FTMO and DNA Funded flag IP changes as suspicious activity.
    """

    def __init__(self, railway_ip: Optional[str] = None):
        """
        Args:
            railway_ip: The static IP of the Railway server.
                        Set on initialization and never changes.
                        If None: first connection IP becomes the registered IP.
        """
        self._registered_ip:    Optional[str] = railway_ip
        self._ip_history:       list[IPRecord] = []
        self._violations:       list[dict] = []

        if railway_ip:
            logger.info("[FORGE-44] Registered Railway IP: %s", railway_ip)
        else:
            logger.warning(
                "[FORGE-44] No Railway IP registered. "
                "First connection will set the IP. "
                "Configure RAILWAY_IP environment variable before going live."
            )

    def register_ip(self, ip: str) -> None:
        """Explicitly register the authoritative Railway server IP."""
        old = self._registered_ip
        self._registered_ip = ip
        logger.info(
            "[FORGE-44] Railway IP %s: %s",
            "set" if not old else f"updated from {old} to",
            ip,
        )

    def check_connection(
        self, current_ip: str, firm_id: str, as_of: Optional[datetime] = None
    ) -> IPCheckResult:
        """
        Check whether the current connection IP matches the registered Railway IP.
        Call before EVERY broker API request.
        """
        now = as_of or datetime.now(timezone.utc)
        is_sensitive = firm_id in IP_SENSITIVE_FIRMS

        # First connection — register this IP as the authority
        if self._registered_ip is None:
            self._registered_ip = current_ip
            self._record_ip(current_ip, now)
            logger.info(
                "[FORGE-44] Railway IP auto-registered from first connection: %s",
                current_ip,
            )
            return IPCheckResult(
                is_consistent=True, registered_ip=current_ip,
                current_ip=current_ip, firm_id=firm_id,
                is_ip_sensitive=is_sensitive,
                reason=f"IP registered: {current_ip}. All future connections must match."
            )

        self._record_ip(current_ip, now)

        if current_ip == self._registered_ip:
            return IPCheckResult(
                is_consistent=True, registered_ip=self._registered_ip,
                current_ip=current_ip, firm_id=firm_id,
                is_ip_sensitive=is_sensitive,
                reason=f"IP consistent: {current_ip} ✓"
            )

        # IP mismatch — potential compliance violation
        violation = {
            "timestamp":     now.isoformat(),
            "firm_id":       firm_id,
            "registered_ip": self._registered_ip,
            "detected_ip":   current_ip,
        }
        self._violations.append(violation)

        severity = "CRITICAL" if is_sensitive else "WARNING"
        reason = (
            f"⚠ IP MISMATCH: Registered {self._registered_ip}, "
            f"detected {current_ip}. "
            + (f"{firm_id} is IP-sensitive — this may flag the account. "
               f"Verify Railway server hasn't changed IP."
               if is_sensitive else
               f"Non-sensitive firm but connection inconsistency detected.")
        )

        if is_sensitive:
            logger.critical("[FORGE-44] %s", reason)
        else:
            logger.warning("[FORGE-44] %s", reason)

        return IPCheckResult(
            is_consistent=False, registered_ip=self._registered_ip,
            current_ip=current_ip, firm_id=firm_id,
            is_ip_sensitive=is_sensitive, reason=reason,
        )

    def _record_ip(self, ip: str, now: datetime) -> None:
        existing = next((r for r in self._ip_history if r.ip_address == ip), None)
        if existing:
            existing.last_seen = now
            existing.connection_count += 1
        else:
            self._ip_history.append(IPRecord(
                ip_address=ip, first_seen=now, last_seen=now
            ))

    @property
    def registered_ip(self) -> Optional[str]:
        return self._registered_ip

    @property
    def violation_count(self) -> int:
        return len(self._violations)

    @property
    def violations(self) -> list[dict]:
        return list(self._violations)

    def is_compliant(self, current_ip: str) -> bool:
        """Quick check — True if IP matches registered."""
        return self._registered_ip is None or current_ip == self._registered_ip

    def status_summary(self) -> dict:
        return {
            "registered_ip":   self._registered_ip,
            "violation_count": self.violation_count,
            "ip_history":      [r.ip_address for r in self._ip_history],
            "is_configured":   self._registered_ip is not None,
        }
