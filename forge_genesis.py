"""
FORGE v21 — GENESIS CALIBRATION + AUTO-EVOLUTION
==================================================
genesis_nightly.py: Runs at 00:05 ET. Reads Ghost data.
  Builds probability tables. Forward Monte Carlo. Calibration file.
auto_evolve(): FORGE reads calibration at daily reset.
  Never moves prior > 15%/day. Never disables > 3 setups/day.
AccountOrchestrator: multi-account structure (activate later).

NEXUS Capital — Jorge Trujillo | Claude — AI Architect | March 2026
"""
from __future__ import annotations
import json
import logging
import os
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("FORGE.genesis")

GHOST_DIR = Path(os.environ.get("GHOST_DATA_PATH", "/data/ghost_intel/trades"))
CALIBRATION_DIR = Path(os.environ.get("GENESIS_CAL_PATH", "/data/genesis/calibration"))
FORWARD_DIR = Path(os.environ.get("GENESIS_FWD_PATH", "/data/genesis/forward"))


# ─────────────────────────────────────────────────────────────────
# GENESIS NIGHTLY PIPELINE
# ─────────────────────────────────────────────────────────────────

class GenesisNightly:
    """
    Runs at 00:05 ET nightly.
    1. Read Ghost trade files
    2. Build conditional probability tables
    3. Forward Monte Carlo simulation
    4. Weekly historical backtest (Sundays)
    5. Write calibration file for FORGE to read at daily reset
    """

    def __init__(self):
        self.ghost_trades: List[dict] = []
        self.calibration: dict = {}

    def load_ghost_data(self, days: int = 30) -> int:
        """Load ghost trade files from /data/ghost_intel/trades/."""
        self.ghost_trades = []
        try:
            GHOST_DIR.mkdir(parents=True, exist_ok=True)
            files = sorted(GHOST_DIR.glob("ghost_*.json"))[-days:]
            for f in files:
                try:
                    with open(f) as fh:
                        trades = json.load(fh)
                        if isinstance(trades, list):
                            self.ghost_trades.extend(trades)
                except (json.JSONDecodeError, IOError):
                    continue
        except Exception as e:
            logger.error("[GENESIS] Failed to load ghost data: %s", e)
        logger.info("[GENESIS] Loaded %d ghost trades from %d files",
                     len(self.ghost_trades), min(len(list(GHOST_DIR.glob("ghost_*.json"))), days))
        return len(self.ghost_trades)

    def build_probability_tables(self) -> dict:
        """Build conditional probability tables from ghost data."""
        tables = {
            "priors": {},
            "regime_multipliers": {},
            "session_multipliers": {},
            "time_multipliers": {},
            "conviction_calibration": {},
        }

        # Group by setup_id
        by_setup: Dict[str, List[dict]] = {}
        for t in self.ghost_trades:
            sid = t.get("setup_id", "")
            outcome = t.get("outcome", "")
            if not sid or outcome not in ("WIN", "LOSS"):
                continue
            if sid not in by_setup:
                by_setup[sid] = []
            by_setup[sid].append(t)

        # P(win | setup_id) — minimum 50 trades
        for sid, trades in by_setup.items():
            if len(trades) >= 50:
                wins = sum(1 for t in trades if t["outcome"] == "WIN")
                tables["priors"][sid] = {
                    "base_wr": round(wins / len(trades), 4),
                    "n_trades": len(trades),
                }

            # P(win | setup_id, regime) — minimum 20 trades per regime
            regime_groups: Dict[str, List[dict]] = {}
            for t in trades:
                r = t.get("regime", "NORMAL")
                if r not in regime_groups:
                    regime_groups[r] = []
                regime_groups[r].append(t)

            for regime, rtrades in regime_groups.items():
                if len(rtrades) >= 20:
                    wins = sum(1 for t in rtrades if t["outcome"] == "WIN")
                    base_wr = tables["priors"].get(sid, {}).get("base_wr", 0.50)
                    regime_wr = wins / len(rtrades)
                    mult = regime_wr / base_wr if base_wr > 0 else 1.0
                    if sid not in tables["regime_multipliers"]:
                        tables["regime_multipliers"][sid] = {}
                    tables["regime_multipliers"][sid][regime] = round(mult, 3)

            # P(win | setup_id, session) — minimum 15 trades per session
            session_groups: Dict[str, List[dict]] = {}
            for t in trades:
                s = t.get("session", "RTH")
                if s not in session_groups:
                    session_groups[s] = []
                session_groups[s].append(t)

            for session, strades in session_groups.items():
                if len(strades) >= 15:
                    wins = sum(1 for t in strades if t["outcome"] == "WIN")
                    base_wr = tables["priors"].get(sid, {}).get("base_wr", 0.50)
                    session_wr = wins / len(strades)
                    mult = session_wr / base_wr if base_wr > 0 else 1.0
                    if sid not in tables["session_multipliers"]:
                        tables["session_multipliers"][sid] = {}
                    tables["session_multipliers"][sid][session] = round(mult, 3)

            # P(win | setup_id, hour) — minimum 10 trades per hour
            hour_groups: Dict[str, List[dict]] = {}
            for t in trades:
                h = str(t.get("hour_et", ""))
                if h and h not in hour_groups:
                    hour_groups[h] = []
                if h:
                    hour_groups[h].append(t)

            for hour, htrades in hour_groups.items():
                if len(htrades) >= 10:
                    wins = sum(1 for t in htrades if t["outcome"] == "WIN")
                    base_wr = tables["priors"].get(sid, {}).get("base_wr", 0.50)
                    hour_wr = wins / len(htrades)
                    mult = hour_wr / base_wr if base_wr > 0 else 1.0
                    if sid not in tables["time_multipliers"]:
                        tables["time_multipliers"][sid] = {}
                    tables["time_multipliers"][sid][hour] = round(mult, 3)

        # Conviction calibration curve
        # Groups: 40-50%, 50-60%, 60-70%, 70-80%, 80+%
        conv_groups = {
            "40-50": [], "50-60": [], "60-70": [], "70-80": [], "80+": []
        }
        for t in self.ghost_trades:
            post = t.get("conviction_posterior", 0) * 100
            outcome = t.get("outcome", "")
            if outcome not in ("WIN", "LOSS"):
                continue
            if 40 <= post < 50:
                conv_groups["40-50"].append(t)
            elif 50 <= post < 60:
                conv_groups["50-60"].append(t)
            elif 60 <= post < 70:
                conv_groups["60-70"].append(t)
            elif 70 <= post < 80:
                conv_groups["70-80"].append(t)
            elif post >= 80:
                conv_groups["80+"].append(t)

        for group, trades in conv_groups.items():
            if len(trades) >= 30:
                wins = sum(1 for t in trades if t["outcome"] == "WIN")
                tables["conviction_calibration"][group] = {
                    "actual_wr": round(wins / len(trades), 4),
                    "n_trades": len(trades),
                }

        return tables

    def forward_monte_carlo(self, n_sims: int = 1000) -> dict:
        """Forward simulation of tomorrow using current parameters."""
        if not self.ghost_trades:
            return {"error": "No ghost data"}

        # Calculate base stats
        daily_results = []
        for _ in range(n_sims):
            daily_pnl = 0.0
            n_trades = random.randint(15, 30)
            for _ in range(n_trades):
                # Sample from recent ghost trade outcomes
                trade = random.choice(self.ghost_trades)
                r_mult = trade.get("r_multiple", 0)
                daily_pnl += r_mult * 20  # approximate $ per R
            daily_results.append(daily_pnl)

        daily_results.sort()
        return {
            "median_pnl": round(daily_results[len(daily_results) // 2], 2),
            "p5_pnl": round(daily_results[int(len(daily_results) * 0.05)], 2),
            "p95_pnl": round(daily_results[int(len(daily_results) * 0.95)], 2),
            "prob_profitable": round(sum(1 for d in daily_results if d > 0) / len(daily_results), 4),
            "deployment_recommendation": "DEPLOY FULL SIZE" if daily_results[len(daily_results) // 2] > 0 else "REDUCE SIZE",
        }

    def identify_disabled_setups(self, tables: dict) -> List[str]:
        """Identify setups that should be disabled (max 3 per day)."""
        disabled = []
        for sid, data in tables.get("priors", {}).items():
            if data["n_trades"] >= 50 and data["base_wr"] < 0.35:
                disabled.append(sid)
        return disabled[:3]  # max 3 per day

    def generate_calibration(self) -> dict:
        """Full calibration pipeline."""
        tables = self.build_probability_tables()
        forward = self.forward_monte_carlo()
        disabled = self.identify_disabled_setups(tables)

        self.calibration = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ghost_trades_processed": len(self.ghost_trades),
            "priors": tables["priors"],
            "regime_multipliers": tables["regime_multipliers"],
            "session_multipliers": tables["session_multipliers"],
            "time_multipliers": tables["time_multipliers"],
            "conviction_calibration": tables["conviction_calibration"],
            "forward_sim": forward,
            "disabled_setups": disabled,
            "deployment_recommendation": forward.get("deployment_recommendation", "HOLD"),
        }
        return self.calibration

    def write_calibration(self) -> Path:
        """Write calibration file for FORGE to read."""
        CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
        filepath = CALIBRATION_DIR / "forge_calibration.json"
        with open(filepath, "w") as f:
            json.dump(self.calibration, f, indent=2, default=str)
        logger.info("[GENESIS] Calibration written: %s (%d setups)",
                     filepath, len(self.calibration.get("priors", {})))
        return filepath

    def run_nightly(self) -> dict:
        """Full nightly pipeline."""
        n = self.load_ghost_data(30)
        if n < 100:
            logger.warning("[GENESIS] Only %d ghost trades — insufficient for calibration", n)
            return {"status": "skipped", "reason": f"Only {n} trades"}

        cal = self.generate_calibration()
        self.write_calibration()

        logger.info("[GENESIS] Nightly complete: %d trades → %d setups calibrated",
                     n, len(cal.get("priors", {})))
        return {"status": "completed", "trades_processed": n, "calibration": cal}


# ─────────────────────────────────────────────────────────────────
# AUTO-EVOLUTION — FORGE reads calibration at daily reset
# ─────────────────────────────────────────────────────────────────

def auto_evolve(setup_config: dict, send_telegram_fn=None) -> dict:
    """
    Read GENESIS calibration file and update FORGE parameters.
    Safety rails:
    - Never move prior more than 15% per day
    - Never disable more than 3 setups per day
    - Stale calibration (not today) = skip
    - Less than 50 ghost trades per setup = skip
    - Log every change to Telegram
    """
    cal_file = CALIBRATION_DIR / "forge_calibration.json"
    if not cal_file.exists():
        logger.info("[EVOLVE] No calibration file found — skipping")
        return {"status": "no_file"}

    try:
        with open(cal_file) as f:
            cal = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error("[EVOLVE] Failed to read calibration: %s", e)
        return {"status": "read_error"}

    # Only apply if generated today
    gen_date = cal.get("generated_at", "")[:10]
    if gen_date != date.today().isoformat():
        logger.info("[EVOLVE] Stale calibration (%s) — skipping", gen_date)
        return {"status": "stale"}

    changes = []

    # Update base win rates (cap at 15% move per day)
    for setup_id, data in cal.get("priors", {}).items():
        if setup_id in setup_config and data.get("n_trades", 0) >= 50:
            old = setup_config[setup_id].get("base_win_rate", 0.50)
            new = data["base_wr"]
            capped = max(old - 0.15, min(old + 0.15, new))
            if abs(capped - old) > 0.005:
                setup_config[setup_id]["base_win_rate"] = round(capped, 3)
                changes.append(f"{setup_id}: WR {old:.1%} → {capped:.1%}")

    # Disable flagged setups (max 3)
    disabled = cal.get("disabled_setups", [])[:3]
    for sid in disabled:
        if sid in setup_config:
            setup_config[sid]["signal_fn"] = "disabled"
            setup_config[sid]["name"] = setup_config[sid].get("name", "") + " [DISABLED by GENESIS]"
            changes.append(f"{sid}: DISABLED")

    msg = f"GENESIS EVOLUTION: {len(changes)} changes"
    if changes:
        msg += "\n" + "\n".join(changes)

    logger.info("[EVOLVE] %s", msg)
    if send_telegram_fn:
        send_telegram_fn(f"🧬 <b>{msg}</b>")

    return {"status": "applied", "changes": changes, "calibration": cal}


# ─────────────────────────────────────────────────────────────────
# CONVICTION CALIBRATION LOOKUP
# ─────────────────────────────────────────────────────────────────

def get_calibrated_wr(raw_posterior: float) -> Optional[float]:
    """
    Bayesian says 70% but actual outcomes might show 55%.
    Ghost data builds the truth. Kelly sizing uses CALIBRATED probability.

    Returns calibrated WR if available, None otherwise.
    """
    cal_file = CALIBRATION_DIR / "forge_calibration.json"
    if not cal_file.exists():
        return None

    try:
        with open(cal_file) as f:
            cal = json.load(f)
    except Exception:
        return None

    conv_cal = cal.get("conviction_calibration", {})
    post_pct = raw_posterior * 100

    if 40 <= post_pct < 50 and "40-50" in conv_cal:
        return conv_cal["40-50"]["actual_wr"]
    elif 50 <= post_pct < 60 and "50-60" in conv_cal:
        return conv_cal["50-60"]["actual_wr"]
    elif 60 <= post_pct < 70 and "60-70" in conv_cal:
        return conv_cal["60-70"]["actual_wr"]
    elif 70 <= post_pct < 80 and "70-80" in conv_cal:
        return conv_cal["70-80"]["actual_wr"]
    elif post_pct >= 80 and "80+" in conv_cal:
        return conv_cal["80+"]["actual_wr"]

    return None


# ─────────────────────────────────────────────────────────────────
# MULTI-ACCOUNT ARCHITECTURE (structure only, activate later)
# ─────────────────────────────────────────────────────────────────

class AccountOrchestrator:
    """
    Build structure now. Activate when FORGE passes evaluation.
    Zero code changes needed to add second account.
    """

    def __init__(self):
        self.accounts: List[dict] = []

    def add_account(self, account_id: str, adapter, firm_id: str,
                    risk_budget: float = 5000.0):
        self.accounts.append({
            "id": account_id,
            "adapter": adapter,
            "firm_id": firm_id,
            "risk_budget": risk_budget,
            "daily_pnl": 0.0,
            "active": True,
        })
        logger.info("[ORCHESTRATOR] Added account: %s (%s)", account_id[:8], firm_id)

    async def execute_signal(self, signal, conviction, lot_size: float):
        """
        Execute signal across all accounts with anti-copy-trade measures.
        - Stagger entry by 30-90 seconds
        - Vary lot size ±10%
        - Check per-account risk limits
        """
        import asyncio

        results = []
        for i, account in enumerate(self.accounts):
            if not account["active"]:
                continue

            # Anti-copy-trade: stagger and vary
            delay = 30 + random.randint(0, 60)
            size_variation = lot_size * (0.90 + random.random() * 0.20)
            size_variation = round(round(size_variation / 0.10) * 0.10, 2)
            size_variation = max(0.10, size_variation)

            # Per-account risk check
            if account["daily_pnl"] <= -account["risk_budget"]:
                logger.warning("[ORCHESTRATOR] %s risk budget blown", account["id"][:8])
                continue

            if i > 0:
                await asyncio.sleep(delay)

            try:
                from execution_base import OrderRequest, OrderDirection, OrderType
                order = OrderRequest(
                    instrument=signal.entry_price,  # resolved already
                    direction=OrderDirection.LONG if signal.direction == "long" else OrderDirection.SHORT,
                    size=size_variation,
                    order_type=OrderType.MARKET,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    comment=f"{signal.setup_id}|{conviction.conviction_level}",
                )
                result = await account["adapter"].place_order(order)
                results.append(result)
            except Exception as e:
                logger.error("[ORCHESTRATOR] %s order failed: %s", account["id"][:8], e)

        return results

    def record_pnl(self, account_id: str, pnl: float):
        for account in self.accounts:
            if account["id"] == account_id:
                account["daily_pnl"] += pnl
                break

    def reset_daily(self):
        for account in self.accounts:
            account["daily_pnl"] = 0.0


# ─────────────────────────────────────────────────────────────────
# STATISTICAL SIGNIFICANCE TESTING
# ─────────────────────────────────────────────────────────────────

def is_edge_significant(wins: int, total: int, null_wr: float = 0.50) -> Tuple[bool, float, float]:
    """
    Test if observed win rate is statistically significant.
    Only deploy setups where p < 0.05.
    Ghost generates 200+ trades/day — significance within 5 days.
    """
    if total < 30:
        return False, 0.0, 1.0

    observed_wr = wins / total
    z = (observed_wr - null_wr) / math.sqrt(null_wr * (1 - null_wr) / total)
    # Approximate p-value using error function
    p_value = 0.5 * (1 - math.erf(z / math.sqrt(2)))
    return p_value < 0.05, observed_wr, p_value
