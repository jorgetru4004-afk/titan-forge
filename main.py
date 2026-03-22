"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     main.py — CONTINUOUS LIVE TRADING LOOP                  ║
║                                                                              ║
║  VERSION 2 — All constructor and import errors fixed.                        ║
║                                                                              ║
║  FIXES APPLIED:                                                              ║
║    v1 ERROR 1: MT5Adapter called with wrong params (firm_id, api_url,       ║
║                username). Fixed to: account_id, server, password, is_demo.  ║
║    v1 ERROR 2: SessionQualityClassifier does not exist.                      ║
║                Fixed to: SessionQualityFilter (correct class name).          ║
║    v1 ERROR 3: BehavioralArchitecture class does not exist.                  ║
║                behavioral_arch.py has standalone functions only.             ║
║                Fixed: call check_behavioral_consistency() directly.          ║
║    v1 ERROR 4: import time unused. Removed.                                  ║
║                                                                              ║
║  FLOW:                                                                       ║
║    1. Run simulation training until cleared for live                         ║
║    2. Connect to FTMO via MT5Adapter                                         ║
║    3. Loop: health check → account state → behavioral check →               ║
║             session quality → execute → sleep 60s → repeat                  ║
║    4. Never exit — Railway status: RUNNING permanently                       ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 2026                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
from datetime import date, datetime, timezone, timedelta
from typing import Optional

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("titan_forge.main")

# ── IMPORTS ───────────────────────────────────────────────────────────────────
from sim.training_runner import TrainingRunner
from mt5_adapter import MT5Adapter
from session_quality import (
    SessionQualityFilter,        # ← FIXED: was SessionQualityClassifier
    SessionQualityScore,
    SessionDecision,
    build_pre_session_data,
    GEXRegime,
    EventImpact,
)
from behavioral_arch import (
    check_behavioral_consistency, # ← FIXED: standalone function, no class
    TiltLevel,
    assess_tilt,
)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — SIMULATION TRAINING
# Identical behavior to original main.py — just extracted to a function.
# ─────────────────────────────────────────────────────────────────────────────

def run_simulation_training() -> bool:
    """
    Run simulation cycles until FORGE is cleared for live trading.
    Returns True when cleared. Returns False if 200 cycles exhausted.
    """
    logger.info("TITAN FORGE — Starting simulation training...")
    logger.info("Running multiple protocol cycles to mature all capabilities...")

    r = TrainingRunner()

    for i in range(200):
        report = r.run_full_protocol()
        if report.cleared_for_live:
            logger.info(f"CLEARED after {i + 1} cycles!")
            logger.info(report.summary)
            return True
        if (i + 1) % 20 == 0:
            logger.info(
                f"Cycle {i + 1}/200: "
                f"{len(report.blocking_reasons)} blocking issue(s) remain."
            )

    logger.error("Simulation training exhausted 200 cycles without clearing.")
    logger.error(report.summary)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — SESSION TIMING UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def is_market_session_active() -> bool:
    """Returns True if any tradeable session is currently active."""
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:  # Weekend
        return False
    hour = now_utc.hour
    # Asian: 00-08 UTC | London: 07-16 UTC | New York: 12-21 UTC
    return 0 <= hour < 21


def seconds_until_next_session() -> int:
    """Seconds until the next session opens."""
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        days_until_monday = 7 - now_utc.weekday()
        next_open = (now_utc + timedelta(days=days_until_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(60, int((next_open - now_utc).total_seconds()))
    if now_utc.hour >= 21:
        next_open = (now_utc + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(60, int((next_open - now_utc).total_seconds()))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — THE LIVE TRADING LOOP
# ─────────────────────────────────────────────────────────────────────────────

async def live_trading_loop(adapter: MT5Adapter) -> None:
    """
    Continuous live trading loop. Never returns.
    Each 60-second cycle: health → account → behavioral check →
    session quality → execute → sleep.
    """
    # ── FORGE: Session quality filter (FORGE-08 + FORGE-61) ─────────────────
    sqf = SessionQualityFilter()   # ← FIXED: correct class name

    # Track recent trade history for behavioral checks (FORGE-56)
    recent_position_sizes: list[float] = []
    recent_entry_hours:    list[int]   = []
    session_wins:          int         = 0
    session_losses:        int         = 0
    consecutive_losses:    int         = 0

    logger.info("TITAN FORGE — Live trading loop started. Running indefinitely.")
    cycle = 0

    while True:
        cycle += 1

        try:
            # ── WAIT FOR MARKET SESSION ───────────────────────────────────────
            if not is_market_session_active():
                wait_seconds = seconds_until_next_session()
                logger.info(
                    f"[Cycle {cycle}] Market closed. "
                    f"Sleeping {wait_seconds // 60} minutes until next session."
                )
                await asyncio.sleep(wait_seconds)
                continue

            # ── HEALTH CHECK ──────────────────────────────────────────────────
            health = await adapter.health_check()
            if not health.is_healthy:
                logger.warning(
                    f"[Cycle {cycle}] Platform unhealthy: {health.error}. "
                    f"Reconnecting..."
                )
                await adapter.connect()
                await asyncio.sleep(30)
                continue

            # ── ACCOUNT STATE ─────────────────────────────────────────────────
            account = await adapter.get_account_state()
            logger.info(
                f"[Cycle {cycle}] Account: "
                f"Balance={account.balance:.2f} | "
                f"Equity={account.equity:.2f} | "
                f"Daily P&L={account.daily_pnl:.2f} | "
                f"Positions={account.open_position_count}"
            )

            # ── BEHAVIORAL CONSISTENCY CHECK — Bug 6 fix ──────────────────────
            # FORGE-56: Auto-runs at session start (wired per Bug 6 fix).
            # check_behavioral_consistency is a standalone function in
            # behavioral_arch.py — not a class method.
            now_hour = datetime.now(timezone.utc).hour
            recent_entry_hours.append(now_hour)
            recent_entry_hours = recent_entry_hours[-20:]  # Keep last 20

            total_trades = session_wins + session_losses
            baseline_wr  = 0.60   # FORGE training baseline
            recent_wr    = (
                session_wins / total_trades if total_trades > 0 else baseline_wr
            )

            behavioral = check_behavioral_consistency(  # ← FIXED: standalone fn
                position_sizes=recent_position_sizes or [0.01],
                entry_hours=recent_entry_hours or [9],
                baseline_win_rate=baseline_wr,
                recent_win_rate=recent_wr,
            )

            if behavioral.severity == "FLAGGED":
                logger.warning(
                    f"[Cycle {cycle}][FORGE-56] Behavioral FLAGGED: "
                    f"{' | '.join(behavioral.flags)}"
                )
            elif behavioral.severity == "CAUTION":
                logger.info(
                    f"[Cycle {cycle}][FORGE-56] Behavioral CAUTION: "
                    f"{' | '.join(behavioral.flags)}"
                )
            else:
                logger.info(f"[Cycle {cycle}][FORGE-56] Behavioral: CLEAN.")

            # ── SESSION QUALITY CHECK — FORGE-08 ─────────────────────────────
            # build_pre_session_data assembles the PreSessionData object.
            # Until the full data pipeline (Alpaca, news APIs) is integrated,
            # neutral defaults are used. The filter will return TRADE_STANDARD
            # under neutral conditions — FORGE will trade with normal caution.
            pre_session = build_pre_session_data(
                session_date=date.today(),
                firm_id=os.environ.get("FTMO_ACCOUNT_ID", "FTMO"),
                is_evaluation=True,
                overnight_pct=0.001,        # Mild directional — neutral default
                futures_direction="bullish",
                vix_level=18.0,             # Normal VIX
                gex_regime=GEXRegime.NEUTRAL,
                consecutive_losses=consecutive_losses,
            )

            sq_score: SessionQualityScore = sqf.score_session(
                data=pre_session,
                pacing_threshold=6.0,
                position_sizes=recent_position_sizes or [0.01],
                entry_hours=recent_entry_hours or [9],
                baseline_win_rate=baseline_wr,
                recent_win_rate=recent_wr,
            )

            if sq_score.hard_blocked or not sq_score.is_tradeable:
                logger.info(
                    f"[Cycle {cycle}][FORGE-08] Session not tradeable. "
                    f"Score={sq_score.composite_score:.1f} | "
                    f"Decision={sq_score.decision.name} | "
                    f"Reason={sq_score.reason}"
                )
                await asyncio.sleep(300)  # 5-min recheck
                continue

            logger.info(
                f"[Cycle {cycle}][FORGE-08] Session tradeable. "
                f"Score={sq_score.composite_score:.1f} | "
                f"Decision={sq_score.decision.name} | "
                f"Best setups={sq_score.best_setups_for_today}"
            )

            # ── TRADING LOGIC ─────────────────────────────────────────────────
            # NOTE: The full strategy execution engine (setup scoring,
            # conviction engine, order sizing, clash rules, profit lock)
            # is implemented across the 54 files in the titan_forge codebase.
            # main.py coordinates the session loop — it does not implement
            # strategy logic directly.
            #
            # NEXT STEP: Wire the execution engine here.
            # The adapter, account, and sq_score are all available.
            # Example call once the execution engine entry point is identified:
            #
            #   from execution_engine import run_session
            #   await run_session(adapter=adapter, account=account, sq=sq_score)
            #
            logger.info(
                f"[Cycle {cycle}] Session loop active. "
                f"Adapter connected. Account healthy. Quality gate passed. "
                f"Awaiting execution engine wire-up."
            )

            # ── SLEEP UNTIL NEXT CYCLE ────────────────────────────────────────
            await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info("TITAN FORGE — Shutdown signal. Closing gracefully.")
            await adapter.disconnect()
            return

        except Exception as e:
            logger.error(
                f"[Cycle {cycle}] Unexpected error: {type(e).__name__}: {e}. "
                f"Retrying in 60 seconds."
            )
            await asyncio.sleep(60)
            continue


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    """
    1. Simulation training until cleared.
    2. Connect to FTMO via MT5Adapter (correct params: account_id, server,
       password, is_demo).
    3. Enter live trading loop — never exits.
    """

    # ── STEP 1: SIMULATION ────────────────────────────────────────────────────
    cleared = run_simulation_training()
    if not cleared:
        logger.error(
            "FORGE failed to clear simulation training. "
            "Review strategy configuration before redeploying."
        )
        await asyncio.sleep(600)  # Wait before Railway restart
        return

    # ── STEP 2: CONNECT — FIXED CONSTRUCTOR ───────────────────────────────────
    # ORIGINAL ERROR: MT5Adapter(firm_id=..., api_url=..., username=..., ...)
    # FIXED:          MT5Adapter(account_id=..., server=..., password=..., ...)
    logger.info("TITAN FORGE — Simulation cleared. Connecting to FTMO MT5...")

    adapter = MT5Adapter(
        account_id = os.environ.get("FTMO_ACCOUNT_ID", ""),
        server     = os.environ.get("FTMO_API_URL", ""),      # server, not api_url
        password   = os.environ.get("FTMO_PASSWORD", ""),
        is_demo    = os.environ.get("FTMO_IS_DEMO", "true").lower() == "true",
        # NOTE: no firm_id param — handled internally via FirmID.FTMO
        # NOTE: no username param — MT5 uses account_id as login number
    )

    connected = await adapter.connect()
    if not connected:
        logger.error(
            "FORGE failed to connect to FTMO MT5. "
            "Check Railway env vars: FTMO_ACCOUNT_ID, FTMO_API_URL, FTMO_PASSWORD."
        )
        await asyncio.sleep(300)  # Wait before Railway restart
        return

    logger.info(
        f"TITAN FORGE — Connected. "
        f"Mode: {'DEMO' if adapter.is_demo else 'LIVE'}. "
        f"Account: {adapter.account_id}."
    )

    # ── STEP 3: LIVE LOOP — NEVER EXITS ──────────────────────────────────────
    await live_trading_loop(adapter)


if __name__ == "__main__":
    asyncio.run(main())
