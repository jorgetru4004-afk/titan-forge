"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║         mt5_adapter.py — FORGE-39 (FX-06) — MetaAPI Edition                 ║
║                                                                              ║
║  REPLACES: Direct MetaTrader5 Python library (Windows-only)                 ║
║  WITH: MetaAPI REST/WebSocket cloud service (Linux compatible)               ║
║                                                                              ║
║  MetaAPI runs a cloud MT5 terminal on Windows servers.                       ║
║  Railway calls MetaAPI's REST API → MetaAPI forwards to FTMO MT5.           ║
║  Works on Linux. Works on Railway. Works permanently.                        ║
║                                                                              ║
║  Required Railway environment variables:                                     ║
║    METAAPI_TOKEN      — Auth token from MetaAPI API Access page              ║
║    METAAPI_ACCOUNT_ID — Account ID from MetaAPI accounts page                ║
║    FTMO_IS_DEMO       — "true" for demo/free trial, "false" for live         ║
║                                                                              ║
║  All other FTMO_ variables no longer needed for connection.                  ║
║  MetaAPI handles authentication to MT5 server directly.                      ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 2026                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import aiohttp

from execution_base import (
    ExecutionAdapter, OrderRequest, OrderResult, OrderStatus,
    AccountState, OpenPosition, PlatformHealth, PlatformStatus, OrderDirection,
)
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.mt5")

# MetaAPI REST API base URL
METAAPI_BASE = "https://mt-client-api-v1.london.agiliumtrade.ai"

# MT5 order type constants (mirrors MetaTrader5 values)
MT5_ORDER_TYPE_BUY  = 0
MT5_ORDER_TYPE_SELL = 1

# Trade actions
MT5_TRADE_ACTION_DEAL = 1
MT5_TRADE_ACTION_SLTP = 6

# TITAN FORGE magic number
TITAN_FORGE_MAGIC = 20260320


class MT5Adapter(ExecutionAdapter):
    """
    FORGE-39 / FX-06: MetaTrader 5 adapter using MetaAPI cloud service.

    Connects Railway (Linux) → MetaAPI cloud → FTMO US MT5 server.
    MetaAPI runs the Windows MT5 terminal on their infrastructure.
    This adapter calls MetaAPI's REST API — no Windows dependency.

    Works for any MT5 prop firm: FTMO, The 5%ers, etc.
    Just change METAAPI_ACCOUNT_ID per firm.

    Usage:
        adapter = MT5Adapter(
            account_id=os.environ["METAAPI_ACCOUNT_ID"],
            server="OANDA-Demo-1",      # kept for compatibility
            password="",                # not needed — MetaAPI handles auth
            is_demo=True,
        )
        await adapter.connect()
        state = await adapter.get_account_state()
    """

    def __init__(
        self,
        account_id:  str,
        server:      str = "",
        password:    str = "",
        is_demo:     bool = True,
    ):
        # Always use METAAPI_ACCOUNT_ID from env — overrides whatever caller passes
        # (caller may pass FTMO_ACCOUNT_ID which is the MT5 login number, not MetaAPI UUID)
        metaapi_account_id = os.environ.get("METAAPI_ACCOUNT_ID", account_id)
        super().__init__(FirmID.FTMO, metaapi_account_id, is_demo)
        self._token      = os.environ.get("METAAPI_TOKEN", "")
        self._session:   Optional[aiohttp.ClientSession] = None
        self._headers    = {
            "auth-token":   self._token,
            "Content-Type": "application/json",
        }

    # ── CONNECTION ────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Initialize aiohttp session and verify MetaAPI account is deployed.
        MetaAPI handles the actual MT5 authentication — we just verify
        the account is reachable.
        """
        if not self._token:
            logger.error(
                "[FORGE-39][MT5] METAAPI_TOKEN not set in Railway env vars. "
                "Go to MetaAPI → API Access → Generate token."
            )
            return False

        if not self.account_id:
            logger.error(
                "[FORGE-39][MT5] METAAPI_ACCOUNT_ID not set in Railway env vars. "
                "Copy Account ID from MetaAPI accounts page."
            )
            return False

        try:
            self._session = aiohttp.ClientSession(headers=self._headers)

            # Verify account exists and is deployed
            url = f"{METAAPI_BASE}/users/current/accounts/{self.account_id}"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data  = await resp.json()
                    state = data.get("state", "unknown")
                    conn  = data.get("connectionStatus", "unknown")

                    self._connected = True
                    logger.info(
                        "[FORGE-39][MT5] ✅ MetaAPI connected. "
                        "Account: %s | State: %s | Connection: %s | Mode: %s.",
                        self.account_id, state, conn,
                        "DEMO" if self.is_demo else "LIVE",
                    )
                    return True
                else:
                    text = await resp.text()
                    logger.error(
                        "[FORGE-39][MT5] ❌ MetaAPI account check failed. "
                        "Status: %s | Response: %s", resp.status, text
                    )
                    return False

        except Exception as e:
            logger.error("[FORGE-39][MT5] Connection error: %s", e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Close aiohttp session cleanly."""
        self._connected = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[FORGE-39][MT5] MetaAPI session closed.")

    async def health_check(self) -> PlatformHealth:
        """P-07: Health check. Pings MetaAPI account status."""
        start = time.time()
        try:
            if not self._connected or not self._session:
                return PlatformHealth(
                    platform="MT5-MetaAPI",
                    status=PlatformStatus.DISCONNECTED,
                    latency_ms=0.0,
                    last_checked=datetime.now(timezone.utc),
                    error="Not connected",
                )

            url = f"{METAAPI_BASE}/users/current/accounts/{self.account_id}"
            async with self._session.get(url) as resp:
                latency = (time.time() - start) * 1000
                if resp.status == 200:
                    return PlatformHealth(
                        platform="MT5-MetaAPI",
                        status=PlatformStatus.DEMO if self.is_demo else PlatformStatus.CONNECTED,
                        latency_ms=round(latency, 2),
                        last_checked=datetime.now(timezone.utc),
                        is_demo=self.is_demo,
                    )
                else:
                    return PlatformHealth(
                        platform="MT5-MetaAPI",
                        status=PlatformStatus.ERROR,
                        latency_ms=round(latency, 2),
                        last_checked=datetime.now(timezone.utc),
                        error=f"HTTP {resp.status}",
                    )
        except Exception as e:
            latency = (time.time() - start) * 1000
            return PlatformHealth(
                platform="MT5-MetaAPI",
                status=PlatformStatus.ERROR,
                latency_ms=round(latency, 2),
                last_checked=datetime.now(timezone.utc),
                error=str(e),
            )

    # ── ACCOUNT ───────────────────────────────────────────────────────────────

    async def get_account_state(self) -> AccountState:
        """Pull balance, equity, drawdown, open positions via MetaAPI."""
        try:
            url = f"{METAAPI_BASE}/users/current/accounts/{self.account_id}/accountInformation"
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    logger.error("[FORGE-39][MT5] get_account_state failed: HTTP %s", resp.status)
                    return self._empty_account_state()

                info      = await resp.json()
                balance   = float(info.get("balance", 0))
                equity    = float(info.get("equity", 0))
                margin_u  = float(info.get("margin", 0))
                margin_f  = float(info.get("freeMargin", 0))
                daily_pnl = equity - balance

                positions = await self.get_open_positions()

                return AccountState(
                    account_id=self.account_id,
                    platform="MT5-MetaAPI",
                    firm_id=FirmID.FTMO,
                    balance=balance,
                    equity=equity,
                    margin_used=margin_u,
                    margin_free=margin_f,
                    open_positions=positions,
                    daily_pnl=daily_pnl,
                    timestamp=datetime.now(timezone.utc),
                )
        except Exception as e:
            logger.error("[FORGE-39][MT5] get_account_state error: %s", e)
            return self._empty_account_state()

    def _empty_account_state(self) -> AccountState:
        return AccountState(
            account_id=self.account_id,
            platform="MT5-MetaAPI",
            firm_id=FirmID.FTMO,
            balance=0.0, equity=0.0,
            margin_used=0.0, margin_free=0.0,
            open_positions=[], daily_pnl=0.0,
            timestamp=datetime.now(timezone.utc),
        )

    # ── ORDER EXECUTION ───────────────────────────────────────────────────────

    async def place_order(self, request: OrderRequest) -> OrderResult:
        """Place market or limit order via MetaAPI REST."""
        # FORGE-11 Layer 1: No stop = no entry
        if request.stop_loss is None:
            logger.error(
                "[FORGE-39][MT5] ❌ ORDER REJECTED: No stop loss. "
                "FORGE-11 Layer 1 requires a stop on every trade."
            )
            return self._rejected_result(request, "FORGE-11: No stop loss defined.")

        is_buy    = request.direction == OrderDirection.LONG
        is_market = request.order_type.value == "market"

        action_type = "ORDER_TYPE_BUY" if is_buy else "ORDER_TYPE_SELL"
        if not is_market:
            action_type = "ORDER_TYPE_BUY_LIMIT" if is_buy else "ORDER_TYPE_SELL_LIMIT"

        payload: dict = {
            "symbol":      request.instrument,
            "actionType":  action_type,
            "volume":      float(request.size),
            "stopLoss":    float(request.stop_loss),
            "comment":     request.comment or f"TF-{TITAN_FORGE_MAGIC}",
            "magic":       TITAN_FORGE_MAGIC,
        }
        if request.take_profit:
            payload["takeProfit"] = float(request.take_profit)
        if not is_market and request.limit_price:
            payload["openPrice"] = float(request.limit_price)

        try:
            url = f"{METAAPI_BASE}/users/current/accounts/{self.account_id}/trade"
            async with self._session.post(url, json=payload) as resp:
                data = await resp.json()

                success    = resp.status in (200, 201) and data.get("numericCode") in (10009, 0)
                order_id   = str(data.get("orderId", "")) if success else None
                fill_price = float(data.get("price", 0)) if success else None
                error_msg  = None if success else (
                    data.get("message") or data.get("error") or f"HTTP {resp.status}"
                )

                result = OrderResult(
                    success=success,
                    order_id=order_id,
                    status=OrderStatus.FILLED if success else OrderStatus.REJECTED,
                    instrument=request.instrument,
                    direction=request.direction.value,
                    size=request.size,
                    fill_price=fill_price,
                    stop_loss=request.stop_loss,
                    take_profit=request.take_profit,
                    timestamp=datetime.now(timezone.utc),
                    error_message=error_msg,
                    raw_response=data,
                )
                self._log_order(request, result)
                return result

        except Exception as e:
            logger.error("[FORGE-39][MT5] place_order error: %s", e)
            return self._rejected_result(request, str(e))

    async def close_position(
        self, position_id: str, size: Optional[float] = None
    ) -> OrderResult:
        """Close position fully or partially via MetaAPI."""
        try:
            payload: dict = {"actionType": "POSITION_CLOSE_ID", "positionId": position_id}
            if size is not None:
                payload["volume"] = float(size)

            url = f"{METAAPI_BASE}/users/current/accounts/{self.account_id}/trade"
            async with self._session.post(url, json=payload) as resp:
                data    = await resp.json()
                success = resp.status in (200, 201) and data.get("numericCode") in (10009, 0)

                return OrderResult(
                    success=success,
                    order_id=position_id,
                    status=OrderStatus.CLOSED if success else OrderStatus.REJECTED,
                    instrument="",
                    direction="",
                    size=size or 0.0,
                    fill_price=float(data.get("price", 0)) if success else None,
                    stop_loss=None, take_profit=None,
                    timestamp=datetime.now(timezone.utc),
                    error_message=None if success else str(data),
                    raw_response=data,
                )
        except Exception as e:
            logger.error("[FORGE-39][MT5] close_position error: %s", e)
            return OrderResult(
                success=False, order_id=position_id,
                status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=str(e),
            )

    async def close_all_positions(self) -> list[OrderResult]:
        """Close ALL positions. Called by FORGE-11 RED and news blackouts."""
        positions = await self.get_open_positions()
        if not positions:
            logger.info("[FORGE-39][MT5] close_all: No open positions.")
            return []

        results = []
        for pos in positions:
            result = await self.close_position(pos.position_id)
            results.append(result)
            status = "✅" if result.success else "❌"
            logger.info(
                "[FORGE-39][MT5] %s Close position %s (%s %s)",
                status, pos.position_id, pos.direction.value, pos.instrument,
            )
        return results

    async def modify_position(
        self,
        position_id:     str,
        new_stop_loss:   Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Modify SL/TP on open position. Called by FORGE-64 profit lock."""
        payload: dict = {"actionType": "POSITION_MODIFY", "positionId": position_id}
        if new_stop_loss   is not None: payload["stopLoss"]   = float(new_stop_loss)
        if new_take_profit is not None: payload["takeProfit"] = float(new_take_profit)

        try:
            url = f"{METAAPI_BASE}/users/current/accounts/{self.account_id}/trade"
            async with self._session.post(url, json=payload) as resp:
                data    = await resp.json()
                success = resp.status in (200, 201) and data.get("numericCode") in (10009, 0)

                logger.info(
                    "[FORGE-39][MT5] %s Modify %s: SL=%s TP=%s",
                    "✅" if success else "❌", position_id,
                    new_stop_loss, new_take_profit,
                )
                return OrderResult(
                    success=success,
                    order_id=position_id,
                    status=OrderStatus.OPEN if success else OrderStatus.REJECTED,
                    instrument="", direction="", size=0.0,
                    fill_price=None,
                    stop_loss=new_stop_loss, take_profit=new_take_profit,
                    timestamp=datetime.now(timezone.utc),
                    error_message=None if success else str(data),
                )
        except Exception as e:
            logger.error("[FORGE-39][MT5] modify_position error: %s", e)
            return OrderResult(
                success=False, order_id=position_id,
                status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=str(e),
            )

    # ── MARKET DATA ───────────────────────────────────────────────────────────

    async def get_current_price(self, instrument: str) -> tuple[float, float]:
        """Return (bid, ask) from MetaAPI price endpoint."""
        try:
            url = f"{METAAPI_BASE}/users/current/accounts/{self.account_id}/symbols/{instrument}/current-price"
            async with self._session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get("bid", 0)), float(data.get("ask", 0))
                logger.warning("[FORGE-39][MT5] No price for %s: HTTP %s", instrument, resp.status)
                return 0.0, 0.0
        except Exception as e:
            logger.warning("[FORGE-39][MT5] get_current_price error: %s", e)
            return 0.0, 0.0

    async def get_open_positions(self) -> list[OpenPosition]:
        """Return all open positions from MetaAPI filtered by FORGE magic."""
        try:
            url = f"{METAAPI_BASE}/users/current/accounts/{self.account_id}/positions"
            async with self._session.get(url) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                positions = []
                for p in data:
                    if p.get("magic") != TITAN_FORGE_MAGIC:
                        continue
                    positions.append(self._parse_position(p))
                return positions
        except Exception as e:
            logger.error("[FORGE-39][MT5] get_open_positions error: %s", e)
            return []

    # ── PARSERS ───────────────────────────────────────────────────────────────

    def _parse_position(self, p: dict) -> OpenPosition:
        """Convert MetaAPI position dict to OpenPosition."""
        is_buy = p.get("type") == "POSITION_TYPE_BUY"
        return OpenPosition(
            position_id    = str(p.get("id", "")),
            instrument     = p.get("symbol", ""),
            direction      = OrderDirection.LONG if is_buy else OrderDirection.SHORT,
            size           = float(p.get("volume", 0)),
            entry_price    = float(p.get("openPrice", 0)),
            current_price  = float(p.get("currentPrice", 0)),
            stop_loss      = float(p["stopLoss"])   if p.get("stopLoss")   else None,
            take_profit    = float(p["takeProfit"]) if p.get("takeProfit") else None,
            unrealized_pnl = float(p.get("profit", 0)),
            open_time      = datetime.fromisoformat(
                p.get("time", datetime.now(timezone.utc).isoformat()).replace("Z", "+00:00")
            ),
            comment = p.get("comment", ""),
        )

    # ── HELPERS ───────────────────────────────────────────────────────────────

    def _rejected_result(self, request: OrderRequest, error: str) -> OrderResult:
        return OrderResult(
            success=False, order_id=None,
            status=OrderStatus.REJECTED,
            instrument=request.instrument,
            direction=request.direction.value,
            size=request.size,
            fill_price=None, stop_loss=None, take_profit=None,
            timestamp=datetime.now(timezone.utc),
            error_message=error,
        )
