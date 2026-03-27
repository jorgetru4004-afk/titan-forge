"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  forge_evidence.py — THE EVIDENCE ENGINE                    ║
║                                                                              ║
║  FORGE doesn't just trade. It LEARNS from every trade — and every          ║
║  trade it DIDN'T take.                                                      ║
║                                                                              ║
║  Every trade gets a full fingerprint.                                      ║
║  Every rejected signal becomes a phantom trade.                             ║
║  Parameters evolve from FORGE's own evidence — not from backtests.         ║
║                                                                              ║
║  MANDATORY: Every FORGE record gets PROP_FIRM tag per V2.3 architecture.   ║
║                                                                              ║
║  Bug #10 FIX: Persistent storage at /data/evidence/, not /tmp/.            ║
║  Bug #11 FIX: update_trade_outcome() called on position close.             ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("titan_forge.evidence")

# Bug #10: Persistent storage path — Railway volume mount at /data/
EVIDENCE_DIR = Path(os.environ.get("EVIDENCE_PATH", "/data/evidence"))


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE FINGERPRINT — Complete context of every decision
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeFingerprint:
    """Complete fingerprint of a trade decision — taken or phantom."""
    # Identity
    trade_id:           str
    timestamp:          str              # ISO format
    setup_id:           str
    instrument:         str
    direction:          str              # "long" / "short"

    # Prices
    entry_price:        float
    stop_loss:          float
    take_profit:        float
    exit_price:         Optional[float]  = None

    # Sizing
    lot_size:           float            = 0.0

    # Firm
    firm_id:            str              = "FTMO"

    # Market context at entry
    vix:                float            = 20.0
    vix_regime:         str              = "NORMAL"
    futures_bias:       str              = "neutral"
    futures_pct:        float            = 0.0
    session_state:      str              = ""
    day_of_week:        str              = ""
    atr:                float            = 100.0
    atr_pct_consumed:   float            = 0.0
    ib_direction:       Optional[str]    = None
    pdh:                float            = 0.0
    pdl:                float            = 0.0

    # Decision quality
    bayesian_posterior: float             = 0.0
    confluence_score:   int              = 0
    expected_value:     float            = 0.0
    conviction_level:   str              = ""

    # Results (updated on close — Bug #11 fix)
    pnl:                Optional[float]  = None
    outcome:            str              = "OPEN"  # OPEN / WIN / LOSS / BREAKEVEN / PHANTOM
    r_multiple:         Optional[float]  = None
    exit_time:          Optional[str]    = None

    # Architecture tag (V2.3 — MANDATORY)
    capital_vehicle:    str              = "PROP_FIRM"
    is_phantom:         bool             = False


# ═══════════════════════════════════════════════════════════════════════════════
# EVIDENCE LOGGER — PERSISTENT STORAGE
# ═══════════════════════════════════════════════════════════════════════════════

class EvidenceLogger:
    """
    Logs every trade (real and phantom) to persistent JSON storage.

    Files organized by date: evidence_2026-03-24.json
    Bug #10: Uses /data/ persistent volume on Railway.
    Bug #11: update_trade_outcome() updates records when trades close.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self._dir = base_dir or EVIDENCE_DIR
        self._ensure_dir()
        self._all_records: list[dict] = []
        self._load_history()

    def _ensure_dir(self) -> None:
        """Create evidence directory. Falls back to home if /data/ unavailable."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            self._dir = Path.home() / "forge_evidence"
            self._dir.mkdir(parents=True, exist_ok=True)
            logger.warning("[EVIDENCE] Using fallback dir: %s", self._dir)

    def _get_file(self, d: date) -> Path:
        return self._dir / f"evidence_{d.isoformat()}.json"

    def _load_history(self) -> None:
        """Load recent evidence files into memory for queries."""
        try:
            files = sorted(self._dir.glob("evidence_*.json"))
            for f in files[-30:]:  # Last 30 days
                try:
                    with open(f) as fh:
                        records = json.load(fh)
                        self._all_records.extend(records)
                except (json.JSONDecodeError, IOError):
                    continue
            logger.info("[EVIDENCE] Loaded %d records from %d files",
                       len(self._all_records), min(len(files), 30))
        except Exception as e:
            logger.warning("[EVIDENCE] Failed to load history: %s", e)

    def log_trade(self, fp: TradeFingerprint) -> None:
        """Log a trade fingerprint to persistent storage."""
        today = date.today()
        filepath = self._get_file(today)
        record = asdict(fp)
        self._all_records.append(record)

        try:
            existing = []
            if filepath.exists():
                try:
                    with open(filepath) as fh:
                        existing = json.load(fh)
                except (json.JSONDecodeError, IOError):
                    existing = []

            existing.append(record)
            with open(filepath, "w") as fh:
                json.dump(existing, fh, indent=2, default=str)

            log_type = "PHANTOM" if fp.is_phantom else fp.outcome
            logger.info("[EVIDENCE] Logged %s: %s %s %s | P(win)=%.1f%%",
                       log_type, fp.setup_id, fp.direction, fp.instrument,
                       fp.bayesian_posterior * 100)
        except Exception as e:
            logger.error("[EVIDENCE] Write failed: %s", e)

    def update_trade_outcome(
        self, trade_id: str, exit_price: float, pnl: float, exit_time: str
    ) -> None:
        """
        Bug #11 FIX: Update a trade's outcome when it closes.
        Called from position management when a trade exits.
        """
        outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

        # Update in-memory
        for record in reversed(self._all_records):
            if record.get("trade_id") == trade_id:
                record["exit_price"] = exit_price
                record["pnl"] = pnl
                record["exit_time"] = exit_time
                record["outcome"] = outcome
                if record.get("stop_loss") and record.get("entry_price"):
                    risk = abs(record["entry_price"] - record["stop_loss"])
                    record["r_multiple"] = round(pnl / risk, 2) if risk > 0 else 0
                break

        # Update on disk
        try:
            for filepath in sorted(self._dir.glob("evidence_*.json"), reverse=True):
                try:
                    with open(filepath) as fh:
                        records = json.load(fh)
                    updated = False
                    for rec in records:
                        if rec.get("trade_id") == trade_id:
                            rec["exit_price"] = exit_price
                            rec["pnl"] = pnl
                            rec["exit_time"] = exit_time
                            rec["outcome"] = outcome
                            if rec.get("stop_loss") and rec.get("entry_price"):
                                risk = abs(rec["entry_price"] - rec["stop_loss"])
                                rec["r_multiple"] = round(pnl / risk, 2) if risk > 0 else 0
                            updated = True
                            break
                    if updated:
                        with open(filepath, "w") as fh:
                            json.dump(records, fh, indent=2, default=str)
                        logger.info("[EVIDENCE] Updated %s → %s ($%.2f)", trade_id, outcome, pnl)
                        return
                except (json.JSONDecodeError, IOError):
                    continue
        except Exception as e:
            logger.error("[EVIDENCE] Update failed: %s", e)

    # ── QUERIES ──────────────────────────────────────────────────────────────

    def get_recent_trades(self, days: int = 30) -> list[dict]:
        """Get recent trade records for parameter evolution."""
        return self._all_records[-500:]  # Last 500 records max

    def get_setup_stats(self, setup_id: str, last_n: int = 50) -> dict:
        """Get performance stats for a specific setup."""
        trades = [r for r in self._all_records
                 if r.get("setup_id") == setup_id
                 and r.get("outcome") in ("WIN", "LOSS")
                 and not r.get("is_phantom")]

        trades = trades[-last_n:]
        if not trades:
            return {"trades": 0, "win_rate": 0.65, "avg_pnl": 0, "profit_factor": 1.0}

        wins = sum(1 for t in trades if t["outcome"] == "WIN")
        total_win_pnl = sum(t.get("pnl", 0) for t in trades if t["outcome"] == "WIN")
        total_loss_pnl = sum(abs(t.get("pnl", 0)) for t in trades if t["outcome"] == "LOSS")

        return {
            "trades": len(trades),
            "win_rate": wins / len(trades),
            "avg_pnl": sum(t.get("pnl", 0) for t in trades) / len(trades),
            "profit_factor": total_win_pnl / max(total_loss_pnl, 0.01),
            "wins": wins,
            "losses": len(trades) - wins,
        }

    def get_daily_summary(self) -> dict:
        """Get today's trading summary."""
        today_str = date.today().isoformat()
        today_trades = [r for r in self._all_records
                       if r.get("timestamp", "").startswith(today_str)
                       and not r.get("is_phantom")]

        completed = [t for t in today_trades if t.get("outcome") in ("WIN", "LOSS")]
        wins = sum(1 for t in completed if t["outcome"] == "WIN")
        total_pnl = sum(t.get("pnl", 0) for t in completed)

        return {
            "trades_today": len(today_trades),
            "completed": len(completed),
            "wins": wins,
            "losses": len(completed) - wins,
            "win_rate": wins / max(len(completed), 1) * 100,
            "total_pnl": total_pnl,
        }

    @property
    def total_records(self) -> int:
        return len(self._all_records)
