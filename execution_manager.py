"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              execution_manager.py — FORGE-39 (FX-01) — Execution Layer      ║
║                                                                              ║
║  EXECUTION MANAGER                                                           ║
║  Single entry point for ALL order execution across all platforms.            ║
║  Selects the right adapter. Monitors platform health.                        ║
║  Implements P-07 API Outage Protocol.                                        ║
║                                                                              ║
║  P-07 API Outage Protocol:                                                   ║
║    0-30s: soft close attempt                                                 ║
║    60-90s: backup close attempt                                              ║
║    90-120s: CRITICAL alert to Jorge via Telegram                            ║
║    Max 2 simultaneous open positions to bound emergency exposure            ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from execution_base import (
    ExecutionAdapter, OrderRequest, OrderResult, OrderStatus,
    AccountState, PlatformHealth, PlatformStatus,
)
from dxtrade_adapter     import DXTradeAdapter
from tradelocker_adapter import TradeLockerAdapter
from rithmic_adapter     import RithmicVPSAdapter
from firm_rules          import FirmID

logger = logging.getLogger("titan_forge.execution_manager")

# P-07: Maximum simultaneous open positions (bounds emergency exposure)
MAX_SIMULTANEOUS_POSITIONS = 2

# P-07: Outage escalation timings (seconds)
SOFT_CLOSE_TIMEOUT_S  =  30
BACKUP_CLOSE_TIMEOUT_S = 90
CRITICAL_ALERT_S      = 120


class ExecutionManager:
    """
    FORGE-39: Central execution manager.

    Every order TITAN FORGE wants to place flows through here.
    This layer:
        1. Selects the right platform adapter for the account
        2. Runs the 30-second health check loop (P-07)
        3. Escalates on API outage: soft close → backup → CRITICAL alert
        4. Enforces max 2 simultaneous positions (P-07 emergency bound)

    Usage:
        manager = ExecutionManager()
        manager.register_adapter("FTMO-EVAL-001", DXTradeAdapter(...))
        manager.register_adapter("DNA-001", TradeLockerAdapter(...))

        result = await manager.place_order("FTMO-EVAL-001", OrderRequest(...))
        state  = await manager.get_account_state("FTMO-EVAL-001")
    """

    def __init__(self):
        self._adapters:         dict[str, ExecutionAdapter] = {}
        self._health_failures:  dict[str, float] = {}   # account_id → first failure time
        self._health_task:      Optional[asyncio.Task] = None

    # ── ADAPTER REGISTRATION ─────────────────────────────────────────────────

    def register_adapter(self, account_id: str, adapter: ExecutionAdapter) -> None:
        """Register a platform adapter for an account."""
        self._adapters[account_id] = adapter
        logger.info(
            "[FORGE-39] Registered adapter: %s → %s (%s)",
            account_id, adapter.firm_id,
            "DEMO" if adapter.is_demo else "LIVE",
        )

    def get_adapter(self, account_id: str) -> Optional[ExecutionAdapter]:
        return self._adapters.get(account_id)

    # ── FACTORY: Build adapters from environment variables ───────────────────

    @classmethod
    def from_environment(cls) -> "ExecutionManager":
        """
        Build the execution manager from environment variables.
        All credentials come from Railway environment variables.
        """
        manager = cls()

        # ── FTMO — DXTrade ────────────────────────────────────────────────────
        ftmo_user = os.environ.get("FTMO_USERNAME")
        ftmo_pass = os.environ.get("FTMO_PASSWORD")
        ftmo_acct = os.environ.get("FTMO_ACCOUNT_ID")
        ftmo_demo = os.environ.get("FTMO_IS_DEMO", "true").lower() == "true"

        if ftmo_user and ftmo_pass and ftmo_acct:
            adapter = DXTradeAdapter(
                account_id=ftmo_acct,
                username=ftmo_user,
                password=ftmo_pass,
                is_demo=ftmo_demo,
            )
            manager.register_adapter(ftmo_acct, adapter)
            logger.info("[FORGE-39] DXTrade/FTMO adapter configured.")
        else:
            logger.warning(
                "[FORGE-39] ⚠ FTMO env vars not set. "
                "Set FTMO_USERNAME, FTMO_PASSWORD, FTMO_ACCOUNT_ID."
            )

        # ── DNA Funded — TradeLocker ──────────────────────────────────────────
        dna_email  = os.environ.get("DNA_EMAIL")
        dna_pass   = os.environ.get("DNA_PASSWORD")
        dna_server = os.environ.get("DNA_SERVER", "TradingFirmServer")
        dna_acct   = os.environ.get("DNA_ACCOUNT_ID")
        dna_demo   = os.environ.get("DNA_IS_DEMO", "true").lower() == "true"

        if dna_email and dna_pass and dna_acct:
            adapter = TradeLockerAdapter(
                account_id=dna_acct,
                email=dna_email,
                password=dna_pass,
                server=dna_server,
                is_demo=dna_demo,
            )
            manager.register_adapter(dna_acct, adapter)
            logger.info("[FORGE-39] TradeLocker/DNA adapter configured.")
        else:
            logger.info("[FORGE-39] DNA Funded not configured (Stage 4 — expected).")

        # ── Apex — Rithmic VPS ────────────────────────────────────────────────
        apex_vps_host = os.environ.get("APEX_VPS_HOST")
        apex_acct     = os.environ.get("APEX_ACCOUNT_ID")
        apex_vps_key  = os.environ.get("APEX_VPS_KEY", "")
        apex_demo     = os.environ.get("APEX_IS_DEMO", "true").lower() == "true"

        if apex_vps_host and apex_acct:
            adapter = RithmicVPSAdapter(
                account_id=apex_acct,
                firm_id=FirmID.APEX,
                vps_host=apex_vps_host,
                vps_auth_key=apex_vps_key,
                is_demo=apex_demo,
            )
            manager.register_adapter(apex_acct, adapter)
            logger.info("[FORGE-39] Rithmic/Apex VPS adapter configured.")
        else:
            logger.info("[FORGE-39] Apex not configured (Stage 2 — add VPS later).")

        return manager

    # ── CONNECTION ────────────────────────────────────────────────────────────

    async def connect_all(self) -> dict[str, bool]:
        """Connect all registered adapters. Returns {account_id: connected}."""
        results = {}
        for account_id, adapter in self._adapters.items():
            results[account_id] = await adapter.connect()
        return results

    async def connect(self, account_id: str) -> bool:
        adapter = self._adapters.get(account_id)
        if not adapter:
            logger.error("[FORGE-39] No adapter for %s", account_id)
            return False
        return await adapter.connect()

    # ── ORDER EXECUTION ───────────────────────────────────────────────────────

    async def place_order(
        self, account_id: str, request: OrderRequest
    ) -> OrderResult:
        """
        Place an order through the correct platform adapter.
        Enforces P-07 max 2 simultaneous positions.
        """
        adapter = self._adapters.get(account_id)
        if not adapter:
            return OrderResult(
                success=False, order_id=None, status=OrderStatus.REJECTED,
                instrument=request.instrument, direction=request.direction.value,
                size=request.size, fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=f"No adapter registered for account {account_id}",
            )

        if not adapter.is_connected:
            return OrderResult(
                success=False, order_id=None, status=OrderStatus.REJECTED,
                instrument=request.instrument, direction=request.direction.value,
                size=request.size, fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=f"Adapter not connected for {account_id}",
            )

        # P-07: Max 2 simultaneous positions
        positions = await adapter.get_open_positions()
        if len(positions) >= MAX_SIMULTANEOUS_POSITIONS:
            logger.warning(
                "[FORGE-39][P-07] %s already has %d positions (max %d). "
                "Close a position before opening new one.",
                account_id, len(positions), MAX_SIMULTANEOUS_POSITIONS,
            )
            return OrderResult(
                success=False, order_id=None, status=OrderStatus.REJECTED,
                instrument=request.instrument, direction=request.direction.value,
                size=request.size, fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=(
                    f"P-07: Max {MAX_SIMULTANEOUS_POSITIONS} simultaneous positions. "
                    f"Currently: {len(positions)}."
                ),
            )

        return await adapter.place_order(request)

    async def close_position(
        self, account_id: str, position_id: str, size: Optional[float] = None
    ) -> OrderResult:
        adapter = self._adapters.get(account_id)
        if not adapter:
            return OrderResult(
                success=False, order_id=position_id,
                status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=f"No adapter for {account_id}",
            )
        return await adapter.close_position(position_id, size)

    async def close_all_positions(self, account_id: str) -> list[OrderResult]:
        adapter = self._adapters.get(account_id)
        if not adapter:
            return []
        return await adapter.close_all_positions()

    async def emergency_close_all(self) -> dict[str, list[OrderResult]]:
        """
        Emergency close ALL positions on ALL accounts.
        Called by FORGE-11 RED (85% drawdown) or P-07 critical outage.
        """
        logger.critical("[FORGE-39] 🚨 EMERGENCY CLOSE ALL — all accounts.")
        results = {}
        for account_id, adapter in self._adapters.items():
            if adapter.is_connected:
                results[account_id] = await adapter.close_all_positions()
        return results

    async def modify_position(
        self,
        account_id:      str,
        position_id:     str,
        new_stop_loss:   Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> OrderResult:
        adapter = self._adapters.get(account_id)
        if not adapter:
            return OrderResult(
                success=False, order_id=position_id, status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=f"No adapter for {account_id}",
            )
        return await adapter.modify_position(position_id, new_stop_loss, new_take_profit)

    # ── ACCOUNT / PRICE ───────────────────────────────────────────────────────

    async def get_account_state(self, account_id: str) -> Optional[AccountState]:
        adapter = self._adapters.get(account_id)
        if not adapter:
            return None
        return await adapter.get_account_state()

    async def get_price(self, account_id: str, instrument: str) -> tuple[float, float]:
        adapter = self._adapters.get(account_id)
        if not adapter:
            return 0.0, 0.0
        return await adapter.get_current_price(instrument)

    # ── P-07: HEALTH MONITORING ───────────────────────────────────────────────

    async def run_health_monitor(self) -> None:
        """
        P-07: Health check every 30 seconds.
        Runs as a background async task on Railway.

        Escalation:
            0-30s:   Soft close attempt
            60-90s:  Backup close attempt
            90-120s: CRITICAL Telegram alert to Jorge
        """
        logger.info("[FORGE-39][P-07] Health monitor started. Interval: 30s.")

        while True:
            await asyncio.sleep(30)

            for account_id, adapter in self._adapters.items():
                if not adapter.is_connected:
                    continue

                health = await adapter.health_check()

                if health.is_healthy:
                    # Clear any outage timer
                    self._health_failures.pop(account_id, None)
                else:
                    first_failure = self._health_failures.get(account_id)
                    now = time.time()

                    if first_failure is None:
                        self._health_failures[account_id] = now
                        logger.error(
                            "[FORGE-39][P-07] %s health check FAILED. "
                            "Starting outage timer. Error: %s",
                            account_id, health.error,
                        )
                    else:
                        elapsed = now - first_failure

                        if elapsed >= CRITICAL_ALERT_S:
                            logger.critical(
                                "[FORGE-39][P-07] 🚨 CRITICAL: %s unreachable "
                                "for %.0f seconds. Sending Telegram alert. "
                                "Manual intervention required.",
                                account_id, elapsed,
                            )
                            await self._send_critical_alert(account_id, elapsed, health)

                        elif elapsed >= BACKUP_CLOSE_TIMEOUT_S:
                            logger.error(
                                "[FORGE-39][P-07] %s unreachable %.0fs. "
                                "Backup close attempt.",
                                account_id, elapsed,
                            )
                            await adapter.close_all_positions()

                        elif elapsed >= SOFT_CLOSE_TIMEOUT_S:
                            logger.warning(
                                "[FORGE-39][P-07] %s unreachable %.0fs. "
                                "Soft close attempt.",
                                account_id, elapsed,
                            )
                            await adapter.close_all_positions()

    async def _send_critical_alert(
        self, account_id: str, elapsed: float, health: PlatformHealth
    ) -> None:
        """
        Send Telegram alert to Jorge on critical outage.
        Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
        """
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id   = os.environ.get("TELEGRAM_CHAT_ID")

        if not bot_token or not chat_id:
            logger.error(
                "[FORGE-39][P-07] Telegram not configured. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
            )
            return

        message = (
            f"🚨 TITAN FORGE CRITICAL ALERT\n\n"
            f"Account {account_id} unreachable for {elapsed:.0f} seconds.\n"
            f"Platform: {health.platform}\n"
            f"Error: {health.error or 'Unknown'}\n\n"
            f"⚠️ MANUAL INTERVENTION REQUIRED.\n"
            f"Check Railway logs and broker platform immediately."
        )

        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": chat_id, "text": message},
                ) as resp:
                    if resp.status == 200:
                        logger.info("[FORGE-39][P-07] Telegram alert sent to Jorge.")
                    else:
                        logger.error(
                            "[FORGE-39][P-07] Telegram send failed: %d", resp.status
                        )
        except Exception as e:
            logger.error("[FORGE-39][P-07] Telegram error: %s", e)

    def start_health_monitor(self) -> asyncio.Task:
        """Start the health monitor as a background task."""
        self._health_task = asyncio.create_task(self.run_health_monitor())
        return self._health_task

    @property
    def registered_accounts(self) -> list[str]:
        return list(self._adapters.keys())

    @property
    def connected_accounts(self) -> list[str]:
        return [aid for aid, a in self._adapters.items() if a.is_connected]
