"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║               architect_integration.py — FORGE-111–115 — Layer 4            ║
║  FORGE-111: ARCHITECT Real-Time Feed                                        ║
║  FORGE-112: META BRAIN Learning Loop                                        ║
║  FORGE-113: Evaluation Dashboard (FORGE-31 implementation)                 ║
║  FORGE-114: Environment Variable Registry (ARCHITECT + META BRAIN)         ║
║  FORGE-115: Alert Distribution System                                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("titan_forge.architect_integration")

# ── FORGE-114: Environment Variable Registry ──────────────────────────────────
# From the document: ARCHITECT + META BRAIN need these env vars.

REQUIRED_ENV_VARS: dict[str, str] = {
    # Core system
    "RAILWAY_IP":               "Static IP of Railway server (FORGE-44)",
    "RAILWAY_REGION":           "Railway deployment region",
    # FTMO — Direct REST API
    "FTMO_API_URL":             "https://api.ftmo.com/v1",
    "FTMO_API_KEY":             "FTMO API key",
    "FTMO_ACCOUNT_ID":          "FTMO funded account ID",
    "FTMO_EVAL_ACCOUNT_ID":     "FTMO evaluation account ID",
    # Apex — Requires Windows VPS
    "APEX_RITHMIC_USERNAME":    "Apex Rithmic login",
    "APEX_RITHMIC_PASSWORD":    "Apex Rithmic password (never log)",
    "APEX_ACCOUNT_ID":          "Apex account ID",
    "APEX_VPS_HOST":            "Windows VPS host for Rithmic",
    # DNA Funded — TradeLocker REST API
    "DNA_TRADELOCKER_URL":      "https://api.tradelocker.com/v1",
    "DNA_TRADELOCKER_KEY":      "DNA TradeLocker API key",
    "DNA_ACCOUNT_ID":           "DNA Funded account ID",
    # 5%ers — MT4/MT5
    "FIVEPCTERS_MT5_LOGIN":     "5%ers MT5 login",
    "FIVEPCTERS_MT5_SERVER":    "5%ers MT5 server",
    "FIVEPCTERS_MT5_PASSWORD":  "5%ers MT5 password (never log)",
    # Market data
    "UNUSUAL_WHALES_API_KEY":   "Unusual Whales API ($25/month) — INS-01",
    "POLYGON_API_KEY":          "Polygon.io market data",
    # Notification
    "TELEGRAM_BOT_TOKEN":       "FORGE-33 payout alerts + tilt notifications",
    "TELEGRAM_CHAT_ID":         "Jorge's Telegram chat ID",
    # NEXUS
    "NEXUS_SECRET_KEY":         "System-wide signing key",
    "META_BRAIN_DB_URL":        "META BRAIN learning database URL",
    "ARCHITECT_DB_URL":         "ARCHITECT state database URL",
}


class EnvironmentRegistry:
    """FORGE-114: Environment variable registry and validation."""

    def __init__(self):
        self._values: dict[str, str] = {}
        self._loaded: set[str] = set()

    def load_from_env(self) -> dict[str, bool]:
        """Load all required env vars. Returns {var_name: is_set}."""
        import os
        status = {}
        for key in REQUIRED_ENV_VARS:
            val = os.environ.get(key)
            if val:
                self._values[key] = val
                self._loaded.add(key)
                status[key] = True
            else:
                status[key] = False
        return status

    def validate(self) -> tuple[list[str], list[str]]:
        """Validate all required vars are present. Returns (missing, present)."""
        missing = [k for k in REQUIRED_ENV_VARS if k not in self._loaded]
        present = [k for k in REQUIRED_ENV_VARS if k in self._loaded]
        return missing, present

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def is_set(self, key: str) -> bool:
        return key in self._loaded

    @property
    def missing_critical(self) -> list[str]:
        """Critical vars that will prevent trading."""
        critical = ["RAILWAY_IP", "FTMO_API_KEY", "NEXUS_SECRET_KEY"]
        return [k for k in critical if k not in self._loaded]


# ── FORGE-111: ARCHITECT Real-Time Feed ──────────────────────────────────────

@dataclass
class ArchitectFeedItem:
    """Single item in the ARCHITECT real-time feed."""
    timestamp:      datetime
    category:       str     # "TRADE" / "PNLUPDATE" / "ALERT" / "SYSTEM"
    account_id:     str
    firm_id:        str
    payload:        dict
    priority:       str     # "INFO" / "WARNING" / "CRITICAL"

class ArchitectFeed:
    """FORGE-111: Real-time feed from TITAN FORGE to ARCHITECT."""

    def __init__(self):
        self._items:     list[ArchitectFeedItem] = []
        self._max_items: int = 1000   # Rolling buffer

    def push(
        self,
        category:   str,
        account_id: str,
        firm_id:    str,
        payload:    dict,
        priority:   str = "INFO",
    ) -> None:
        now = datetime.now(timezone.utc)
        item = ArchitectFeedItem(
            timestamp=now, category=category, account_id=account_id,
            firm_id=firm_id, payload=payload, priority=priority,
        )
        self._items.append(item)
        if len(self._items) > self._max_items:
            self._items.pop(0)

        if priority == "CRITICAL":
            logger.critical("[FORGE-111][ARCHITECT] %s | %s | %s",
                            account_id, category, payload)
        elif priority == "WARNING":
            logger.warning("[FORGE-111][ARCHITECT] %s | %s", account_id, category)

    def get_recent(self, n: int = 20) -> list[ArchitectFeedItem]:
        return self._items[-n:]

    def get_critical(self) -> list[ArchitectFeedItem]:
        return [i for i in self._items if i.priority == "CRITICAL"]

    def to_dashboard(self) -> dict:
        """FORGE-113: Dashboard-ready summary."""
        recent = self.get_recent(50)
        by_cat: dict[str, int] = {}
        for item in recent:
            by_cat[item.category] = by_cat.get(item.category, 0) + 1
        return {
            "total_events": len(self._items),
            "recent_50_by_category": by_cat,
            "critical_count": len(self.get_critical()),
            "last_event": self._items[-1].timestamp.isoformat() if self._items else None,
        }


# ── FORGE-112: META BRAIN Learning Loop ──────────────────────────────────────
# The system learns from every evaluation. Closes the feedback loop.

@dataclass
class LearningUpdate:
    """FORGE-112: META BRAIN learning update from completed evaluation."""
    evaluation_id:      str
    outcome:            str     # "PASS" / "FAIL"
    lessons_extracted:  list[str]
    parameters_updated: dict[str, float]   # What changed in the system
    confidence_delta:   float  # How much confidence in the system changed
    version_bump:       int

class MetaBrainLearningLoop:
    """FORGE-112: Closes the learning loop after each evaluation."""

    def __init__(self):
        self._updates: list[LearningUpdate] = []
        self._version: int = 1

    def process_evaluation(
        self,
        eval_id:    str,
        outcome:    str,
        metrics:    dict,   # WR, avg_RR, drawdown used, etc.
    ) -> LearningUpdate:
        """Extract lessons from completed evaluation and update parameters."""
        lessons = []
        params  = {}

        if outcome == "PASS":
            lessons.append(f"Evaluation {eval_id}: PASSED. Strategy confirmed effective.")
            if metrics.get("drawdown_used", 1.0) < 0.50:
                lessons.append("Drawdown management was excellent. Safety margin is well calibrated.")
                params["safety_margin_confidence"] = min(1.0,
                    metrics.get("safety_margin_confidence", 0.8) + 0.05)
            conf_delta = 0.05
        else:
            lessons.append(f"Evaluation {eval_id}: FAILED. Root cause required.")
            if metrics.get("failure_reason"):
                lessons.append(f"Primary cause: {metrics['failure_reason']}")
            conf_delta = -0.10

        self._version += 1
        update = LearningUpdate(
            evaluation_id=eval_id, outcome=outcome,
            lessons_extracted=lessons, parameters_updated=params,
            confidence_delta=conf_delta, version_bump=self._version,
        )
        self._updates.append(update)
        logger.info(
            "[FORGE-112][META BRAIN] v%d: %s. Lessons: %d. "
            "Confidence delta: %+.2f.",
            self._version, outcome, len(lessons), conf_delta,
        )
        return update

    @property
    def system_version(self) -> int:
        return self._version

    @property
    def total_evaluations_learned(self) -> int:
        return len(self._updates)

    @property
    def lifetime_confidence(self) -> float:
        base = 0.60
        return min(1.0, max(0.0, base + sum(u.confidence_delta for u in self._updates)))
