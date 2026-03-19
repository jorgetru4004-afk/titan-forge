"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║             rithmic_adapter.py — FORGE-39 (FX-01) — Execution Layer         ║
║                                                                              ║
║  RITHMIC VPS BRIDGE ADAPTER — APEX / TOPSTEP                                 ║
║  Railway cannot connect to Rithmic directly.                                ║
║  Rithmic requires a Windows VPS running NinjaTrader or Tradovate.           ║
║                                                                              ║
║  Architecture:                                                               ║
║    Railway → HTTP Bridge → Windows VPS → Rithmic → Apex / Topstep           ║
║                                                                              ║
║  The VPS runs a lightweight HTTP bridge server (bridge_server.py) that      ║
║  accepts Railway's REST calls and forwards them to Rithmic via the          ║
║  NinjaTrader/Tradovate local API.                                            ║
║                                                                              ║
║  FX-01: "Rithmic and Tradovate need a Windows VPS bridge ($30-50/month).   ║
║  Build DXTrade and TradeLocker connections first. Add VPS later for          ║
║  Apex and Topstep."                                                          ║
║                                                                              ║
║  STATUS: STAGE 2 — Build after FTMO/DNA are proven.                         ║
║  VPS provisioning guide included below.                                     ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from execution_base import (
    ExecutionAdapter, OrderRequest, OrderResult, OrderStatus,
    AccountState, OpenPosition, PlatformHealth, PlatformStatus, OrderDirection,
)
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.rithmic")

# ─────────────────────────────────────────────────────────────────────────────
# VPS BRIDGE SETUP GUIDE
# ─────────────────────────────────────────────────────────────────────────────
#
# Step 1: Provision Windows VPS ($30-50/month)
#   Recommended: Vultr or Linode Windows Server 2019
#   Minimum specs: 4GB RAM, 2 CPU, SSD
#   Static IP: Required for FORGE-44 IP consistency
#
# Step 2: Install on VPS
#   - NinjaTrader 8 (free) OR Tradovate desktop app
#   - Python 3.11 for Windows
#   - pip install fastapi uvicorn
#
# Step 3: Deploy bridge_server.py on VPS
#   - Runs on port 8765 (internal, not public)
#   - Accepts REST calls from Railway
#   - Forwards to NinjaTrader via local API
#   - Returns JSON responses
#
# Step 4: Configure Railway → VPS connection
#   - APEX_VPS_HOST = "your.vps.ip.address"
#   - APEX_VPS_PORT = "8765"
#   - APEX_VPS_KEY  = "shared secret for auth"
#
# ─────────────────────────────────────────────────────────────────────────────


class RithmicVPSAdapter(ExecutionAdapter):
    """
    FORGE-39 / FX-01: Rithmic bridge adapter for Apex / Topstep.

    Connects to a Windows VPS running the Rithmic bridge server.
    The VPS translates REST calls into Rithmic API commands.

    THIS IS STAGE 2 — build after FTMO is live and profitable.
    Do not provision VPS until Month 6+ when Apex evaluation begins.
    """

    def __init__(
        self,
        account_id:     str,
        firm_id:        str,      # FirmID.APEX or FirmID.TOPSTEP
        vps_host:       str,      # Windows VPS IP address
        vps_port:       int = 8765,
        vps_auth_key:   str = "",
        is_demo:        bool = True,
    ):
        super().__init__(firm_id, account_id, is_demo)
        self._vps_host    = vps_host
        self._vps_port    = vps_port
        self._vps_auth_key = vps_auth_key
        self._bridge_url  = f"http://{vps_host}:{vps_port}"
        self._session     = None

    # ── CONNECTION ────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Connect to the Windows VPS bridge server.
        The bridge server handles the actual Rithmic authentication.
        """
        try:
            import aiohttp
            self._session = aiohttp.ClientSession()

            # Ping the bridge server
            async with self._session.get(
                f"{self._bridge_url}/health",
                headers={"X-Auth-Key": self._vps_auth_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    self._connected = True
                    logger.info(
                        "[FORGE-39][%s] ✅ VPS bridge connected at %s:%d. Mode: %s.",
                        self.firm_id, self._vps_host, self._vps_port,
                        "DEMO" if self.is_demo else "LIVE",
                    )
                    return True
                else:
                    logger.error(
                        "[FORGE-39][%s] ❌ VPS bridge unreachable. "
                        "Is bridge_server.py running on the VPS?",
                        self.firm_id,
                    )
                    return False

        except Exception as e:
            logger.error(
                "[FORGE-39][%s] VPS bridge connection error: %s. "
                "Have you provisioned the Windows VPS? See rithmic_adapter.py setup guide.",
                self.firm_id, e,
            )
            return False

    async def disconnect(self) -> None:
        self._connected = False
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[FORGE-39][%s] VPS bridge disconnected.", self.firm_id)

    async def health_check(self) -> PlatformHealth:
        start = time.time()
        try:
            async with self._session.get(
                f"{self._bridge_url}/health",
                headers={"X-Auth-Key": self._vps_auth_key},
            ) as resp:
                latency = (time.time() - start) * 1000
                return PlatformHealth(
                    platform=f"Rithmic-VPS-{self.firm_id}",
                    status=PlatformStatus.CONNECTED if resp.status == 200 else PlatformStatus.ERROR,
                    latency_ms=round(latency, 2),
                    last_checked=datetime.now(timezone.utc),
                    is_demo=self.is_demo,
                )
        except Exception as e:
            return PlatformHealth(
                platform=f"Rithmic-VPS-{self.firm_id}",
                status=PlatformStatus.ERROR,
                latency_ms=0.0,
                last_checked=datetime.now(timezone.utc),
                error=str(e),
            )

    async def get_account_state(self) -> AccountState:
        data = await self._bridge_get("/account") or {}
        positions = await self.get_open_positions()
        return AccountState(
            account_id=self.account_id,
            platform="Rithmic",
            firm_id=self.firm_id,
            balance=float(data.get("balance", 0.0)),
            equity=float(data.get("equity", 0.0)),
            margin_used=float(data.get("marginUsed", 0.0)),
            margin_free=float(data.get("marginFree", 0.0)),
            open_positions=positions,
            daily_pnl=float(data.get("dailyPnl", 0.0)),
            timestamp=datetime.now(timezone.utc),
        )

    async def place_order(self, request: OrderRequest) -> OrderResult:
        if request.stop_loss is None:
            return self._rejected("FORGE-11: No stop loss.", request)

        payload = {
            "instrument": request.instrument,
            "side":       "Buy" if request.direction == OrderDirection.LONG else "Sell",
            "qty":        request.size,
            "type":       request.order_type.value,
            "stopLoss":   request.stop_loss,
            "comment":    request.comment,
        }
        if request.take_profit:
            payload["takeProfit"] = request.take_profit

        response = await self._bridge_post("/order", payload)

        if response and response.get("orderId"):
            result = OrderResult(
                success=True,
                order_id=str(response["orderId"]),
                status=OrderStatus.FILLED,
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
            result = self._rejected(
                str(response.get("error", "Bridge error")), request
            )

        self._log_order(request, result)
        return result

    async def close_position(
        self, position_id: str, size: Optional[float] = None
    ) -> OrderResult:
        payload = {"positionId": position_id}
        if size: payload["qty"] = size

        response = await self._bridge_post("/close_position", payload)
        success = bool(response and not response.get("error"))

        return OrderResult(
            success=success, order_id=position_id,
            status=OrderStatus.CLOSED if success else OrderStatus.REJECTED,
            instrument="", direction="", size=size or 0.0,
            fill_price=float(response.get("closePrice", 0.0)) if response else None,
            stop_loss=None, take_profit=None,
            timestamp=datetime.now(timezone.utc),
            raw_response=response,
        )

    async def close_all_positions(self) -> list[OrderResult]:
        response = await self._bridge_post("/close_all", {})
        positions = await self.get_open_positions()
        if not positions:
            return []
        # Return individual close results
        return [
            OrderResult(
                success=True, order_id=p.position_id,
                status=OrderStatus.CLOSED,
                instrument=p.instrument, direction=p.direction.value,
                size=p.size, fill_price=None,
                stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
            )
            for p in positions
        ]

    async def modify_position(
        self,
        position_id:     str,
        new_stop_loss:   Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> OrderResult:
        payload = {"positionId": position_id}
        if new_stop_loss   is not None: payload["stopLoss"]   = new_stop_loss
        if new_take_profit is not None: payload["takeProfit"] = new_take_profit

        response = await self._bridge_post("/modify_position", payload)
        success = bool(response and not response.get("error"))

        return OrderResult(
            success=success, order_id=position_id,
            status=OrderStatus.OPEN if success else OrderStatus.REJECTED,
            instrument="", direction="", size=0.0,
            fill_price=None, stop_loss=new_stop_loss, take_profit=new_take_profit,
            timestamp=datetime.now(timezone.utc), raw_response=response,
        )

    async def get_current_price(self, instrument: str) -> tuple[float, float]:
        data = await self._bridge_get(f"/quote/{instrument}")
        if data:
            return float(data.get("bid", 0.0)), float(data.get("ask", 0.0))
        return 0.0, 0.0

    async def get_open_positions(self) -> list[OpenPosition]:
        data = await self._bridge_get("/positions") or []
        return [self._parse_position(p) for p in data]

    # ── BRIDGE HELPERS ────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {"X-Auth-Key": self._vps_auth_key, "Content-Type": "application/json"}

    async def _bridge_get(self, path: str) -> Optional[dict]:
        if not self._session: return None
        try:
            async with self._session.get(
                f"{self._bridge_url}{path}", headers=self._headers()
            ) as resp:
                return await resp.json() if resp.status == 200 else None
        except Exception as e:
            logger.error("[FORGE-39][%s] Bridge GET %s: %s", self.firm_id, path, e)
            return None

    async def _bridge_post(self, path: str, payload: dict) -> Optional[dict]:
        if not self._session: return None
        try:
            async with self._session.post(
                f"{self._bridge_url}{path}", json=payload, headers=self._headers()
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("[FORGE-39][%s] Bridge POST %s: %s", self.firm_id, path, e)
            return None

    def _rejected(self, reason: str, request: OrderRequest) -> OrderResult:
        return OrderResult(
            success=False, order_id=None, status=OrderStatus.REJECTED,
            instrument=request.instrument, direction=request.direction.value,
            size=request.size, fill_price=None, stop_loss=None, take_profit=None,
            timestamp=datetime.now(timezone.utc), error_message=reason,
        )

    def _parse_position(self, data: dict) -> OpenPosition:
        side = data.get("side", "Buy").lower()
        return OpenPosition(
            position_id   = str(data.get("positionId", "")),
            instrument    = data.get("instrument", ""),
            direction     = OrderDirection.LONG if side == "buy" else OrderDirection.SHORT,
            size          = float(data.get("qty", 0.0)),
            entry_price   = float(data.get("entryPrice", 0.0)),
            current_price = float(data.get("currentPrice", 0.0)),
            stop_loss     = data.get("stopLoss"),
            take_profit   = data.get("takeProfit"),
            unrealized_pnl= float(data.get("pnl", 0.0)),
            open_time     = datetime.now(timezone.utc),
        )
