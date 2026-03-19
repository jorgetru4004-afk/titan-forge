"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║             dxtrade_adapter.py — FORGE-39 (FX-01) — Execution Layer         ║
║                                                                              ║
║  DXTRADE REST API ADAPTER — FTMO                                             ║
║  Direct REST API from Railway to FTMO's DXTrade platform.                   ║
║  No VPS needed. Railway calls DXTrade directly.                              ║
║                                                                              ║
║  DXTrade API endpoints (FTMO):                                               ║
║    Base URL:   https://ftmo.dx.trade/api/v1                                  ║
║    Auth:       POST /token → Bearer token                                    ║
║    Account:    GET  /account                                                 ║
║    Positions:  GET  /positions                                               ║
║    Place:      POST /orders                                                  ║
║    Close:      DELETE /positions/{id}                                        ║
║    Modify:     PATCH  /positions/{id}                                        ║
║    Price:      GET  /quotes/{instrument}                                     ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from execution_base import (
    ExecutionAdapter, OrderRequest, OrderResult, OrderStatus,
    AccountState, OpenPosition, PlatformHealth, PlatformStatus, OrderDirection,
)
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.dxtrade")

# DXTrade base URL for FTMO
DXTRADE_BASE_URL = "https://ftmo.dx.trade/api/v1"
DXTRADE_DEMO_URL = "https://demo.dx.trade/api/v1"

# Connection settings
CONNECT_TIMEOUT_SEC   = 10
REQUEST_TIMEOUT_SEC   = 8
HEALTH_CHECK_INTERVAL = 30   # seconds (FORGE-121 / P-07)
MAX_RETRY_ATTEMPTS    = 3


class DXTradeAdapter(ExecutionAdapter):
    """
    FORGE-39 / FX-01: DXTrade REST API adapter for FTMO.

    Direct Railway → FTMO connection. No VPS needed.
    Handles authentication, order placement, position management,
    and the 30-second health check cycle.

    Usage:
        adapter = DXTradeAdapter(
            account_id="FTMO-EVAL-001",
            username=os.environ["FTMO_USERNAME"],
            password=os.environ["FTMO_PASSWORD"],
            is_demo=True,   # Always demo first — FX-05
        )

        await adapter.connect()
        state = await adapter.get_account_state()
        result = await adapter.place_order(OrderRequest(...))
    """

    def __init__(
        self,
        account_id:     str,
        username:       str,
        password:       str,
        is_demo:        bool = True,
        server_url:     Optional[str] = None,
    ):
        super().__init__(FirmID.FTMO, account_id, is_demo)
        self._username    = username
        self._password    = password
        self._base_url    = server_url or (DXTRADE_DEMO_URL if is_demo else DXTRADE_BASE_URL)
        self._token:      Optional[str] = None
        self._token_expiry: float = 0.0
        self._session     = None   # aiohttp.ClientSession — created on connect

    # ── CONNECTION ────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Authenticate with DXTrade and establish session.
        Stores Bearer token for all subsequent requests.
        """
        try:
            import aiohttp
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=CONNECT_TIMEOUT_SEC)
            )

            payload = {
                "username": self._username,
                "password": self._password,
            }

            async with self._session.post(
                f"{self._base_url}/token",
                json=payload,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self._token       = data.get("token") or data.get("access_token")
                    # DXTrade tokens typically expire in 24 hours
                    self._token_expiry = time.time() + 86_000
                    self._connected   = True
                    logger.info(
                        "[FORGE-39][FTMO] ✅ DXTrade connected. Account: %s. Mode: %s.",
                        self.account_id, "DEMO" if self.is_demo else "LIVE",
                    )
                    return True
                else:
                    text = await resp.text()
                    logger.error(
                        "[FORGE-39][FTMO] ❌ Auth failed. Status: %d. Response: %s",
                        resp.status, text[:200],
                    )
                    return False

        except Exception as e:
            logger.error("[FORGE-39][FTMO] Connection error: %s", e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Close session cleanly."""
        self._connected = False
        self._token     = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[FORGE-39][FTMO] Disconnected.")

    async def health_check(self) -> PlatformHealth:
        """
        P-07: Health check every 30 seconds.
        0-30s: soft close attempt on failure.
        60-90s: backup close attempt.
        90-120s: CRITICAL alert to Jorge.
        """
        start = time.time()
        try:
            if not self._connected or not self._token:
                return PlatformHealth(
                    platform="DXTrade", status=PlatformStatus.DISCONNECTED,
                    latency_ms=0.0, last_checked=datetime.now(timezone.utc),
                    error="Not connected",
                )

            resp_data = await self._get("/account")
            latency = (time.time() - start) * 1000

            if resp_data:
                return PlatformHealth(
                    platform="DXTrade",
                    status=PlatformStatus.DEMO if self.is_demo else PlatformStatus.CONNECTED,
                    latency_ms=round(latency, 2),
                    last_checked=datetime.now(timezone.utc),
                    is_demo=self.is_demo,
                )
            else:
                return PlatformHealth(
                    platform="DXTrade", status=PlatformStatus.ERROR,
                    latency_ms=round(latency, 2),
                    last_checked=datetime.now(timezone.utc),
                    error="Empty response from /account",
                )

        except Exception as e:
            latency = (time.time() - start) * 1000
            logger.error("[FORGE-39][FTMO] Health check failed: %s", e)
            return PlatformHealth(
                platform="DXTrade", status=PlatformStatus.ERROR,
                latency_ms=round(latency, 2),
                last_checked=datetime.now(timezone.utc),
                error=str(e),
            )

    # ── ACCOUNT ───────────────────────────────────────────────────────────────

    async def get_account_state(self) -> AccountState:
        """Pull balance, equity, open positions. The 30-second snapshot."""
        account_data   = await self._get("/account") or {}
        positions_data = await self._get("/positions") or []

        balance  = float(account_data.get("balance",  0.0))
        equity   = float(account_data.get("equity",   balance))
        margin_u = float(account_data.get("marginUsed",  0.0))
        margin_f = float(account_data.get("marginFree",  equity))

        positions = [self._parse_position(p) for p in positions_data]

        # Calculate daily P&L from positions + realized
        daily_pnl = float(account_data.get("dailyPnl", 0.0))

        return AccountState(
            account_id=self.account_id,
            platform="DXTrade",
            firm_id=FirmID.FTMO,
            balance=balance,
            equity=equity,
            margin_used=margin_u,
            margin_free=margin_f,
            open_positions=positions,
            daily_pnl=daily_pnl,
            timestamp=datetime.now(timezone.utc),
        )

    # ── ORDER EXECUTION ───────────────────────────────────────────────────────

    async def place_order(self, request: OrderRequest) -> OrderResult:
        """
        Place a market or limit order via DXTrade REST API.

        DXTrade POST /orders payload:
        {
            "symbol":       "EURUSD",
            "side":         "buy" or "sell",
            "type":         "market" or "limit",
            "quantity":     1.0,
            "stopLoss":     1.0800,
            "takeProfit":   1.1000,
            "comment":      "GEX-01 long"
        }
        """
        # FORGE-11 Layer 1: No stop = no entry
        if request.stop_loss is None:
            logger.error(
                "[FORGE-39][FTMO] ❌ ORDER REJECTED: No stop loss defined. "
                "FORGE-11 Layer 1: every trade requires a stop."
            )
            return OrderResult(
                success=False, order_id=None,
                status=OrderStatus.REJECTED,
                instrument=request.instrument,
                direction=request.direction.value,
                size=request.size, fill_price=None,
                stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message="FORGE-11: No stop loss defined. Entry blocked.",
            )

        payload = {
            "symbol":     request.instrument,
            "side":       "buy" if request.direction == OrderDirection.LONG else "sell",
            "type":       request.order_type.value,
            "quantity":   request.size,
            "stopLoss":   request.stop_loss,
            "comment":    request.comment or f"TITAN-FORGE-{request.magic_number}",
        }
        if request.take_profit:
            payload["takeProfit"] = request.take_profit
        if request.limit_price and request.order_type.value in ("limit", "stop_limit"):
            payload["price"] = request.limit_price

        response = await self._post("/orders", payload)

        if response and response.get("orderId"):
            result = OrderResult(
                success=True,
                order_id=str(response["orderId"]),
                status=OrderStatus.FILLED if response.get("status") == "filled" else OrderStatus.OPEN,
                instrument=request.instrument,
                direction=request.direction.value,
                size=request.size,
                fill_price=float(response.get("fillPrice", 0.0)),
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                timestamp=datetime.now(timezone.utc),
                raw_response=response,
            )
        else:
            result = OrderResult(
                success=False, order_id=None,
                status=OrderStatus.REJECTED,
                instrument=request.instrument,
                direction=request.direction.value,
                size=request.size, fill_price=None,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                timestamp=datetime.now(timezone.utc),
                error_message=str(response.get("error", "Unknown error")),
                raw_response=response,
            )

        self._log_order(request, result)
        return result

    async def close_position(
        self, position_id: str, size: Optional[float] = None
    ) -> OrderResult:
        """Close a position fully or partially."""
        payload = {}
        if size is not None:
            payload["quantity"] = size   # Partial close

        response = await self._delete(f"/positions/{position_id}", payload or None)

        success = response is not None and not response.get("error")
        return OrderResult(
            success=success,
            order_id=position_id,
            status=OrderStatus.CLOSED if success else OrderStatus.REJECTED,
            instrument=response.get("symbol", "") if response else "",
            direction="",
            size=size or 0.0,
            fill_price=float(response.get("closePrice", 0.0)) if response else None,
            stop_loss=None, take_profit=None,
            timestamp=datetime.now(timezone.utc),
            error_message=response.get("error") if response else "No response",
            raw_response=response,
        )

    async def close_all_positions(self) -> list[OrderResult]:
        """
        Close ALL positions immediately.
        Called by FORGE-11 RED trigger and news blackouts.
        """
        positions = await self.get_open_positions()
        if not positions:
            logger.info("[FORGE-39][FTMO] close_all: No open positions.")
            return []

        results = []
        for pos in positions:
            result = await self.close_position(pos.position_id)
            results.append(result)
            if result.success:
                logger.info(
                    "[FORGE-39][FTMO] Closed position %s (%s %s).",
                    pos.position_id, pos.direction.value, pos.instrument,
                )
            else:
                logger.error(
                    "[FORGE-39][FTMO] ❌ Failed to close %s: %s",
                    pos.position_id, result.error_message,
                )

        return results

    async def modify_position(
        self,
        position_id:     str,
        new_stop_loss:   Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Modify stop/target. Called by FORGE-64 profit lock stages."""
        payload = {}
        if new_stop_loss   is not None: payload["stopLoss"]   = new_stop_loss
        if new_take_profit is not None: payload["takeProfit"] = new_take_profit

        if not payload:
            return OrderResult(
                success=False, order_id=position_id,
                status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message="Nothing to modify.",
            )

        response = await self._patch(f"/positions/{position_id}", payload)
        success = response is not None and not response.get("error")

        logger.info(
            "[FORGE-39][FTMO] %s Modified %s: SL=%s TP=%s",
            "✅" if success else "❌", position_id,
            new_stop_loss, new_take_profit,
        )

        return OrderResult(
            success=success,
            order_id=position_id,
            status=OrderStatus.OPEN if success else OrderStatus.REJECTED,
            instrument="", direction="", size=0.0,
            fill_price=None,
            stop_loss=new_stop_loss,
            take_profit=new_take_profit,
            timestamp=datetime.now(timezone.utc),
            error_message=response.get("error") if response and not success else None,
            raw_response=response,
        )

    # ── MARKET DATA ───────────────────────────────────────────────────────────

    async def get_current_price(self, instrument: str) -> tuple[float, float]:
        """Return (bid, ask) for an instrument."""
        data = await self._get(f"/quotes/{instrument}")
        if data:
            bid = float(data.get("bid", 0.0))
            ask = float(data.get("ask", 0.0))
            return bid, ask
        return 0.0, 0.0

    async def get_open_positions(self) -> list[OpenPosition]:
        """Return all currently open positions."""
        data = await self._get("/positions") or []
        return [self._parse_position(p) for p in data]

    # ── HTTP HELPERS ─────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        """Build auth headers for every request."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _get(self, path: str) -> Optional[dict]:
        if not self._session or not self._token:
            return None
        try:
            async with self._session.get(
                f"{self._base_url}{path}", headers=self._headers()
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning("[FORGE-39][FTMO] GET %s → %d", path, resp.status)
                return None
        except Exception as e:
            logger.error("[FORGE-39][FTMO] GET %s error: %s", path, e)
            return None

    async def _post(self, path: str, payload: dict) -> Optional[dict]:
        if not self._session or not self._token:
            return None
        try:
            async with self._session.post(
                f"{self._base_url}{path}", json=payload, headers=self._headers()
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("[FORGE-39][FTMO] POST %s error: %s", path, e)
            return None

    async def _patch(self, path: str, payload: dict) -> Optional[dict]:
        if not self._session or not self._token:
            return None
        try:
            async with self._session.patch(
                f"{self._base_url}{path}", json=payload, headers=self._headers()
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("[FORGE-39][FTMO] PATCH %s error: %s", path, e)
            return None

    async def _delete(self, path: str, payload: Optional[dict] = None) -> Optional[dict]:
        if not self._session or not self._token:
            return None
        try:
            async with self._session.delete(
                f"{self._base_url}{path}",
                json=payload,
                headers=self._headers(),
            ) as resp:
                if resp.status in (200, 204):
                    try:
                        return await resp.json()
                    except Exception:
                        return {"success": True}
                return {"error": f"HTTP {resp.status}"}
        except Exception as e:
            logger.error("[FORGE-39][FTMO] DELETE %s error: %s", path, e)
            return None

    # ── PARSERS ──────────────────────────────────────────────────────────────

    def _parse_position(self, data: dict) -> OpenPosition:
        side = data.get("side", "buy").lower()
        return OpenPosition(
            position_id  = str(data.get("positionId", data.get("id", ""))),
            instrument   = data.get("symbol", ""),
            direction    = OrderDirection.LONG if side == "buy" else OrderDirection.SHORT,
            size         = float(data.get("quantity", 0.0)),
            entry_price  = float(data.get("openPrice", 0.0)),
            current_price= float(data.get("currentPrice", 0.0)),
            stop_loss    = data.get("stopLoss"),
            take_profit  = data.get("takeProfit"),
            unrealized_pnl = float(data.get("pnl", data.get("profit", 0.0))),
            open_time    = datetime.now(timezone.utc),
            comment      = data.get("comment", ""),
        )
