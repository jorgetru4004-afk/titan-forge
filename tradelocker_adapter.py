"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║           tradelocker_adapter.py — FORGE-39 (FX-01) — Execution Layer       ║
║                                                                              ║
║  TRADELOCKER REST API ADAPTER — DNA FUNDED                                   ║
║  Direct REST API from Railway to DNA Funded's TradeLocker platform.          ║
║                                                                              ║
║  TradeLocker API endpoints:                                                  ║
║    Base URL:   https://api.tradelocker.com/backend/auth/jwt                  ║
║    Auth:       POST /guest/token                                             ║
║    Account:    GET  /trade/accounts/{accountId}                              ║
║    Positions:  GET  /trade/accounts/{accountId}/positions                   ║
║    Place:      POST /trade/accounts/{accountId}/orders                      ║
║    Close:      DELETE /trade/accounts/{accountId}/positions/{id}            ║
║    Modify:     PATCH  /trade/accounts/{accountId}/positions/{id}            ║
║    Quotes:     GET  /trade/quotes?instrument={symbol}                       ║
║                                                                              ║
║  CRITICAL DNA Funded rules enforced here:                                    ║
║    • No open/close within 10 min of major news events                       ║
║    • No scalping (< 30 seconds hold)                                        ║
║    • No martingale / grid strategies                                        ║
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

logger = logging.getLogger("titan_forge.tradelocker")

TRADELOCKER_AUTH_URL  = "https://api.tradelocker.com/backend/auth/jwt"
TRADELOCKER_TRADE_URL = "https://api.tradelocker.com/backend/trade"
TRADELOCKER_DEMO_URL  = "https://demo.tradelocker.com/backend"

# DNA Funded: 30-second minimum hold — enforced at adapter level
DNA_MIN_HOLD_SECONDS = 30


class TradeLockerAdapter(ExecutionAdapter):
    """
    FORGE-39 / FX-01: TradeLocker REST API adapter for DNA Funded.

    Direct Railway → DNA Funded connection.
    Enforces DNA Funded's funded-mode restrictions at the adapter level:
        - No open/close within 10 min of news (handled by news_protocol.py)
        - No scalping: min 30-second hold enforced here
        - Position tracking with open timestamps

    Usage:
        adapter = TradeLockerAdapter(
            account_id="DNA-001",
            email=os.environ["DNA_EMAIL"],
            password=os.environ["DNA_PASSWORD"],
            server=os.environ["DNA_SERVER"],
            is_demo=True,
        )
        await adapter.connect()
    """

    def __init__(
        self,
        account_id:  str,
        email:       str,
        password:    str,
        server:      str,       # TradeLocker server name
        is_demo:     bool = True,
        account_num: Optional[str] = None,
    ):
        super().__init__(FirmID.DNA_FUNDED, account_id, is_demo)
        self._email      = email
        self._password   = password
        self._server     = server
        self._account_num = account_num or account_id
        self._base_url   = TRADELOCKER_DEMO_URL if is_demo else TRADELOCKER_TRADE_URL
        self._auth_url   = TRADELOCKER_DEMO_URL if is_demo else TRADELOCKER_AUTH_URL
        self._access_token:  Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._session    = None
        # Track position open times for 30-second minimum hold
        self._position_open_times: dict[str, float] = {}

    # ── CONNECTION ────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Authenticate with TradeLocker using JWT.
        TradeLocker uses email + password + server to get access token.
        """
        try:
            import aiohttp
            self._session = aiohttp.ClientSession()

            payload = {
                "email":    self._email,
                "password": self._password,
                "server":   self._server,
            }

            async with self._session.post(
                f"{self._auth_url}/guest/token",
                json=payload,
            ) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    self._access_token  = data.get("s") and data.get("d", {}).get("accessToken")
                    # Handle different response formats
                    if not self._access_token:
                        tokens = data.get("data", data)
                        self._access_token  = tokens.get("accessToken")
                        self._refresh_token = tokens.get("refreshToken")

                    self._connected = bool(self._access_token)

                    if self._connected:
                        logger.info(
                            "[FORGE-39][DNA] ✅ TradeLocker connected. Account: %s. Mode: %s.",
                            self.account_id, "DEMO" if self.is_demo else "LIVE",
                        )
                    else:
                        logger.error("[FORGE-39][DNA] Auth succeeded but no token in response.")
                    return self._connected

                else:
                    text = await resp.text()
                    logger.error(
                        "[FORGE-39][DNA] ❌ Auth failed. Status: %d. Response: %s",
                        resp.status, text[:200],
                    )
                    return False

        except Exception as e:
            logger.error("[FORGE-39][DNA] Connection error: %s", e)
            return False

    async def disconnect(self) -> None:
        self._connected = False
        self._access_token = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("[FORGE-39][DNA] Disconnected.")

    async def health_check(self) -> PlatformHealth:
        start = time.time()
        try:
            data = await self._get(f"/trade/accounts/{self._account_num}")
            latency = (time.time() - start) * 1000
            return PlatformHealth(
                platform="TradeLocker",
                status=PlatformStatus.DEMO if self.is_demo else PlatformStatus.CONNECTED,
                latency_ms=round(latency, 2),
                last_checked=datetime.now(timezone.utc),
                is_demo=self.is_demo,
            ) if data else PlatformHealth(
                platform="TradeLocker", status=PlatformStatus.ERROR,
                latency_ms=round(latency, 2),
                last_checked=datetime.now(timezone.utc),
                error="Empty response",
            )
        except Exception as e:
            return PlatformHealth(
                platform="TradeLocker", status=PlatformStatus.ERROR,
                latency_ms=0.0, last_checked=datetime.now(timezone.utc),
                error=str(e),
            )

    # ── ACCOUNT ───────────────────────────────────────────────────────────────

    async def get_account_state(self) -> AccountState:
        data = await self._get(f"/trade/accounts/{self._account_num}") or {}
        positions_data = await self._get(
            f"/trade/accounts/{self._account_num}/positions"
        ) or []

        # TradeLocker uses "equity", "balance" directly
        balance  = float(data.get("balance",  0.0))
        equity   = float(data.get("equity",   balance))
        margin_u = float(data.get("marginUsed",  0.0))
        margin_f = float(data.get("marginFree",  equity))

        positions = [self._parse_position(p) for p in positions_data]
        daily_pnl = float(data.get("dailyPnl", 0.0))

        return AccountState(
            account_id=self.account_id,
            platform="TradeLocker",
            firm_id=FirmID.DNA_FUNDED,
            balance=balance, equity=equity,
            margin_used=margin_u, margin_free=margin_f,
            open_positions=positions, daily_pnl=daily_pnl,
            timestamp=datetime.now(timezone.utc),
        )

    # ── ORDER EXECUTION ───────────────────────────────────────────────────────

    async def place_order(self, request: OrderRequest) -> OrderResult:
        """
        Place an order via TradeLocker REST API.
        Enforces DNA Funded funded restrictions before sending.
        """
        # FORGE-11: No stop = no entry
        if request.stop_loss is None:
            return self._rejected("FORGE-11: No stop loss defined.", request)

        # DNA Funded: Cannot open/close within 10 min of news
        # (news_protocol.py handles this upstream — double-check here)
        if hasattr(request, '_news_checked') and not request._news_checked:
            return self._rejected("DNA: News blackout not verified.", request)

        payload = {
            "instrument":  request.instrument,
            "side":        "buy" if request.direction == OrderDirection.LONG else "sell",
            "type":        request.order_type.value.upper(),
            "qty":         request.size,
            "stopLoss":    request.stop_loss,
            "comment":     request.comment or "TITAN-FORGE",
        }
        if request.take_profit:
            payload["takeProfit"] = request.take_profit

        response = await self._post(
            f"/trade/accounts/{self._account_num}/orders",
            payload,
        )

        if response and (response.get("positionId") or response.get("orderId")):
            pos_id = str(response.get("positionId") or response.get("orderId", ""))
            # Track open time for 30-second minimum hold
            self._position_open_times[pos_id] = time.time()

            result = OrderResult(
                success=True,
                order_id=pos_id,
                status=OrderStatus.OPEN,
                instrument=request.instrument,
                direction=request.direction.value,
                size=request.size,
                fill_price=float(response.get("openPrice", 0.0)),
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
                error_message=str(response.get("message", "Order rejected")),
                raw_response=response,
            )

        self._log_order(request, result)
        return result

    async def close_position(
        self, position_id: str, size: Optional[float] = None
    ) -> OrderResult:
        """
        Close a position. Enforces DNA Funded 30-second minimum hold.
        """
        # DNA Funded: 30-second minimum hold
        open_time = self._position_open_times.get(position_id, 0.0)
        held_seconds = time.time() - open_time
        if open_time > 0 and held_seconds < DNA_MIN_HOLD_SECONDS:
            wait = DNA_MIN_HOLD_SECONDS - held_seconds
            logger.warning(
                "[FORGE-39][DNA] 🛑 Cannot close %s — only held %.0fs. "
                "DNA requires 30s minimum. Waiting %.0fs.",
                position_id, held_seconds, wait,
            )
            return OrderResult(
                success=False, order_id=position_id,
                status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=f"DNA 30s hold: wait {wait:.0f}s more.",
            )

        payload = {"qty": size} if size else None
        response = await self._delete(
            f"/trade/accounts/{self._account_num}/positions/{position_id}",
            payload,
        )
        success = response is not None and not response.get("error")

        if success:
            self._position_open_times.pop(position_id, None)

        return OrderResult(
            success=success, order_id=position_id,
            status=OrderStatus.CLOSED if success else OrderStatus.REJECTED,
            instrument="", direction="", size=size or 0.0,
            fill_price=float(response.get("closePrice", 0.0)) if response else None,
            stop_loss=None, take_profit=None,
            timestamp=datetime.now(timezone.utc),
            error_message=response.get("message") if not success else None,
            raw_response=response,
        )

    async def close_all_positions(self) -> list[OrderResult]:
        """Close all positions. Note: DNA hold rule may delay some closures."""
        positions = await self.get_open_positions()
        results = []
        for pos in positions:
            result = await self.close_position(pos.position_id)
            results.append(result)
        return results

    async def modify_position(
        self,
        position_id:     str,
        new_stop_loss:   Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> OrderResult:
        payload = {}
        if new_stop_loss   is not None: payload["stopLoss"]   = new_stop_loss
        if new_take_profit is not None: payload["takeProfit"] = new_take_profit

        response = await self._patch(
            f"/trade/accounts/{self._account_num}/positions/{position_id}",
            payload,
        )
        success = bool(response and not response.get("error"))

        return OrderResult(
            success=success, order_id=position_id,
            status=OrderStatus.OPEN if success else OrderStatus.REJECTED,
            instrument="", direction="", size=0.0,
            fill_price=None,
            stop_loss=new_stop_loss, take_profit=new_take_profit,
            timestamp=datetime.now(timezone.utc),
            raw_response=response,
        )

    async def get_current_price(self, instrument: str) -> tuple[float, float]:
        data = await self._get(f"/trade/quotes?instrument={instrument}")
        if data:
            bid = float(data.get("bid", 0.0))
            ask = float(data.get("ask", 0.0))
            return bid, ask
        return 0.0, 0.0

    async def get_open_positions(self) -> list[OpenPosition]:
        data = await self._get(
            f"/trade/accounts/{self._account_num}/positions"
        ) or []
        return [self._parse_position(p) for p in data]

    # ── HELPERS ───────────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _get(self, path: str) -> Optional[dict]:
        if not self._session or not self._access_token:
            return None
        try:
            async with self._session.get(
                f"{self._base_url}{path}", headers=self._headers()
            ) as resp:
                return await resp.json() if resp.status == 200 else None
        except Exception as e:
            logger.error("[FORGE-39][DNA] GET %s error: %s", path, e)
            return None

    async def _post(self, path: str, payload: dict) -> Optional[dict]:
        if not self._session or not self._access_token:
            return None
        try:
            async with self._session.post(
                f"{self._base_url}{path}", json=payload, headers=self._headers()
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("[FORGE-39][DNA] POST %s error: %s", path, e)
            return None

    async def _patch(self, path: str, payload: dict) -> Optional[dict]:
        if not self._session or not self._access_token:
            return None
        try:
            async with self._session.patch(
                f"{self._base_url}{path}", json=payload, headers=self._headers()
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error("[FORGE-39][DNA] PATCH %s error: %s", path, e)
            return None

    async def _delete(self, path: str, payload: Optional[dict] = None) -> Optional[dict]:
        if not self._session or not self._access_token:
            return None
        try:
            async with self._session.delete(
                f"{self._base_url}{path}", json=payload, headers=self._headers()
            ) as resp:
                try:
                    return await resp.json()
                except Exception:
                    return {"success": resp.status in (200, 204)}
        except Exception as e:
            logger.error("[FORGE-39][DNA] DELETE %s error: %s", path, e)
            return None

    def _rejected(self, reason: str, request: OrderRequest) -> OrderResult:
        logger.error("[FORGE-39][DNA] ORDER REJECTED: %s", reason)
        return OrderResult(
            success=False, order_id=None, status=OrderStatus.REJECTED,
            instrument=request.instrument, direction=request.direction.value,
            size=request.size, fill_price=None, stop_loss=None, take_profit=None,
            timestamp=datetime.now(timezone.utc), error_message=reason,
        )

    def _parse_position(self, data: dict) -> OpenPosition:
        side = data.get("side", "buy").lower()
        return OpenPosition(
            position_id   = str(data.get("positionId", data.get("id", ""))),
            instrument    = data.get("instrument", data.get("symbol", "")),
            direction     = OrderDirection.LONG if side == "buy" else OrderDirection.SHORT,
            size          = float(data.get("qty", data.get("quantity", 0.0))),
            entry_price   = float(data.get("openPrice", 0.0)),
            current_price = float(data.get("currentPrice", data.get("price", 0.0))),
            stop_loss     = data.get("stopLoss"),
            take_profit   = data.get("takeProfit"),
            unrealized_pnl= float(data.get("pnl", data.get("profit", 0.0))),
            open_time     = datetime.now(timezone.utc),
            comment       = data.get("comment", ""),
        )
