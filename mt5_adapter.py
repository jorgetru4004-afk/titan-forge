"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              mt5_adapter.py — FORGE-39 (FX-06) — Execution Layer            ║
║                                                                              ║
║  METATRADER 5 ADAPTER — FTMO US (trader.ftmo.oanda.com)                     ║
║  Connects Railway directly to FTMO US MT5 server.                           ║
║  FTMO US is powered by OANDA — MT5 only (DXTrade not available to US).      ║
║                                                                              ║
║  MT5 Python library wraps the MT5 terminal protocol.                        ║
║  All blocking MT5 calls run in a thread executor for async compatibility.   ║
║                                                                              ║
║  Capabilities:                                                               ║
║    connect()              — Authenticate with MT5 server                    ║
║    disconnect()           — Clean shutdown                                  ║
║    health_check()         — P-07 30-second health check                     ║
║    get_account_state()    — Balance, equity, drawdown, P&L, positions       ║
║    place_order()          — Market/limit orders with FORGE-11 stop guard    ║
║    close_position()       — Full or partial close                           ║
║    close_all_positions()  — Emergency close (FORGE-11 RED)                  ║
║    modify_position()      — Move SL/TP (FORGE-64 profit lock)               ║
║    get_current_price()    — Bid/ask from MT5 symbol tick                    ║
║    get_open_positions()   — All open trades                                 ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 20, 2026            ║
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

logger = logging.getLogger("titan_forge.mt5")

# MT5 connection settings
CONNECT_TIMEOUT_SEC   = 15
HEALTH_CHECK_INTERVAL = 30    # seconds (FORGE-121 / P-07)
MAX_RETRY_ATTEMPTS    = 3

# MT5 order type constants (mirrors MetaTrader5 library values)
MT5_ORDER_TYPE_BUY        = 0
MT5_ORDER_TYPE_SELL       = 1
MT5_ORDER_TYPE_BUY_LIMIT  = 2
MT5_ORDER_TYPE_SELL_LIMIT = 3
MT5_ORDER_TYPE_BUY_STOP   = 4
MT5_ORDER_TYPE_SELL_STOP  = 5

# MT5 trade action constants
MT5_TRADE_ACTION_DEAL    = 1   # Market order
MT5_TRADE_ACTION_PENDING = 5   # Pending order
MT5_TRADE_ACTION_SLTP    = 6   # Modify SL/TP
MT5_TRADE_ACTION_REMOVE  = 8   # Cancel pending
MT5_TRADE_ACTION_CLOSE   = 9   # Close position by opposite

# MT5 filling modes
MT5_ORDER_FILLING_IOC = 1   # Immediate or Cancel (FTMO US standard)
MT5_ORDER_FILLING_FOK = 0   # Fill or Kill

# Magic number — identifies TITAN FORGE trades in MT5 terminal
TITAN_FORGE_MAGIC = 20260320


class MT5Adapter(ExecutionAdapter):
    """
    FORGE-39 / FX-06: MetaTrader 5 adapter for FTMO US.

    Connects Railway → FTMO US MT5 server (trader.ftmo.oanda.com).
    Uses the MetaTrader5 Python library wrapped in asyncio thread executor
    so all blocking MT5 calls remain non-blocking in the async event loop.

    Usage:
        adapter = MT5Adapter(
            account_id=os.environ["FTMO_ACCOUNT_ID"],    # MT5 login number
            server=os.environ["FTMO_API_URL"],           # MT5 server address
            password=os.environ["FTMO_PASSWORD"],
            is_demo=True,   # Always demo first — FX-05
        )

        await adapter.connect()
        state = await adapter.get_account_state()
        result = await adapter.place_order(OrderRequest(...))
    """

    def __init__(
        self,
        account_id:  str,
        server:      str,
        password:    str,
        is_demo:     bool = True,
    ):
        super().__init__(FirmID.FTMO, account_id, is_demo)
        self._server       = server
        self._password     = password
        self._mt5          = None    # MetaTrader5 module — imported on connect
        self._loop         = None    # Event loop reference for thread executor

    # ── CONNECTION ────────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """
        Initialize MT5 and authenticate with FTMO US server.
        MT5 login uses integer account_id, server string, and password.
        """
        self._loop = asyncio.get_event_loop()
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
        except ImportError:
            logger.error(
                "[FORGE-39][MT5] MetaTrader5 package not installed. "
                "Run: pip install MetaTrader5"
            )
            return False

        try:
            success = await self._run(
                self._mt5.initialize,
                login=int(self.account_id),
                server=self._server,
                password=self._password,
                timeout=CONNECT_TIMEOUT_SEC * 1000,
            )

            if success:
                account_info = await self._run(self._mt5.account_info)
                if account_info:
                    self._connected = True
                    logger.info(
                        "[FORGE-39][MT5] ✅ Connected to FTMO US. "
                        "Account: %s | Server: %s | Balance: %.2f | Mode: %s.",
                        self.account_id, self._server,
                        account_info.balance,
                        "DEMO" if self.is_demo else "LIVE",
                    )
                    return True
                else:
                    error = await self._run(self._mt5.last_error)
                    logger.error(
                        "[FORGE-39][MT5] ❌ Connected but account_info failed. "
                        "MT5 error: %s", error,
                    )
                    return False
            else:
                error = await self._run(self._mt5.last_error)
                logger.error(
                    "[FORGE-39][MT5] ❌ MT5 initialize failed. "
                    "Server: %s | Account: %s | Error: %s",
                    self._server, self.account_id, error,
                )
                return False

        except Exception as e:
            logger.error("[FORGE-39][MT5] Connection error: %s", e)
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Shut down MT5 connection cleanly."""
        self._connected = False
        if self._mt5:
            await self._run(self._mt5.shutdown)
        logger.info("[FORGE-39][MT5] Disconnected.")

    async def health_check(self) -> PlatformHealth:
        """
        P-07: Health check every 30 seconds.
        Pings MT5 terminal connection status.
        """
        start = time.time()
        try:
            if not self._connected or not self._mt5:
                return PlatformHealth(
                    platform="MT5", status=PlatformStatus.DISCONNECTED,
                    latency_ms=0.0, last_checked=datetime.now(timezone.utc),
                    error="Not connected",
                )

            account_info = await self._run(self._mt5.account_info)
            latency = (time.time() - start) * 1000

            if account_info:
                return PlatformHealth(
                    platform="MT5",
                    status=PlatformStatus.DEMO if self.is_demo else PlatformStatus.CONNECTED,
                    latency_ms=round(latency, 2),
                    last_checked=datetime.now(timezone.utc),
                    is_demo=self.is_demo,
                )
            else:
                error = await self._run(self._mt5.last_error)
                return PlatformHealth(
                    platform="MT5", status=PlatformStatus.ERROR,
                    latency_ms=round(latency, 2),
                    last_checked=datetime.now(timezone.utc),
                    error=f"account_info returned None. MT5 error: {error}",
                )

        except Exception as e:
            latency = (time.time() - start) * 1000
            logger.error("[FORGE-39][MT5] Health check failed: %s", e)
            return PlatformHealth(
                platform="MT5", status=PlatformStatus.ERROR,
                latency_ms=round(latency, 2),
                last_checked=datetime.now(timezone.utc),
                error=str(e),
            )

    # ── ACCOUNT ───────────────────────────────────────────────────────────────

    async def get_account_state(self) -> AccountState:
        """Pull balance, equity, drawdown, open positions. The 30-second snapshot."""
        account_info = await self._run(self._mt5.account_info)
        positions    = await self.get_open_positions()

        if not account_info:
            logger.error("[FORGE-39][MT5] get_account_state: account_info returned None.")
            return AccountState(
                account_id=self.account_id,
                platform="MT5",
                firm_id=FirmID.FTMO,
                balance=0.0, equity=0.0,
                margin_used=0.0, margin_free=0.0,
                open_positions=[],
                daily_pnl=0.0,
                timestamp=datetime.now(timezone.utc),
            )

        balance  = float(account_info.balance)
        equity   = float(account_info.equity)
        margin_u = float(account_info.margin)
        margin_f = float(account_info.margin_free)

        # Daily P&L = sum of all open position unrealized P&L
        # Realized daily P&L requires history — use equity - balance as proxy
        daily_pnl = equity - balance

        return AccountState(
            account_id=self.account_id,
            platform="MT5",
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
        Place a market or limit order via MT5.

        MT5 order_send() request structure:
        {
            "action":       TRADE_ACTION_DEAL (market) or TRADE_ACTION_PENDING,
            "symbol":       "EURUSD",
            "volume":       1.0,
            "type":         ORDER_TYPE_BUY or ORDER_TYPE_SELL,
            "price":        ask/bid for market, limit price for pending,
            "sl":           stop loss price,
            "tp":           take profit price (optional),
            "magic":        TITAN_FORGE_MAGIC,
            "comment":      "GEX-01 long",
            "type_filling": ORDER_FILLING_IOC,
        }
        """
        # FORGE-11 Layer 1: No stop = no entry
        if request.stop_loss is None:
            logger.error(
                "[FORGE-39][MT5] ❌ ORDER REJECTED: No stop loss defined. "
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

        is_buy     = request.direction == OrderDirection.LONG
        is_market  = request.order_type.value == "market"

        # Get current price for market orders
        if is_market:
            bid, ask = await self.get_current_price(request.instrument)
            price = ask if is_buy else bid
        else:
            price = request.limit_price or 0.0

        order_type = (
            MT5_ORDER_TYPE_BUY        if is_buy and is_market else
            MT5_ORDER_TYPE_SELL       if not is_buy and is_market else
            MT5_ORDER_TYPE_BUY_LIMIT  if is_buy else
            MT5_ORDER_TYPE_SELL_LIMIT
        )

        action = MT5_TRADE_ACTION_DEAL if is_market else MT5_TRADE_ACTION_PENDING

        mt5_request = {
            "action":        action,
            "symbol":        request.instrument,
            "volume":        float(request.size),
            "type":          order_type,
            "price":         float(price),
            "sl":            float(request.stop_loss),
            "magic":         TITAN_FORGE_MAGIC,
            "comment":       request.comment or f"TF-{request.magic_number}",
            "type_filling":  MT5_ORDER_FILLING_IOC,
        }
        if request.take_profit:
            mt5_request["tp"] = float(request.take_profit)

        result_raw = await self._run(self._mt5.order_send, mt5_request)

        # MT5 retcode 10009 = TRADE_RETCODE_DONE (success)
        success    = result_raw is not None and result_raw.retcode == 10009
        order_id   = str(result_raw.order) if result_raw and success else None
        fill_price = float(result_raw.price) if result_raw and success else None
        error_msg  = (
            None if success else
            f"MT5 retcode {result_raw.retcode}: {result_raw.comment}"
            if result_raw else "No response from MT5"
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
            raw_response=result_raw._asdict() if result_raw else None,
        )

        self._log_order(request, result)
        return result

    async def close_position(
        self, position_id: str, size: Optional[float] = None
    ) -> OrderResult:
        """Close a position fully or partially by ticket number."""
        # Fetch position details to build the close request
        positions = await self._run(
            self._mt5.positions_get, ticket=int(position_id)
        )

        if not positions:
            return OrderResult(
                success=False, order_id=position_id,
                status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=f"Position {position_id} not found.",
            )

        pos       = positions[0]
        is_buy    = pos.type == MT5_ORDER_TYPE_BUY
        close_vol = size if size is not None else pos.volume

        # Close direction is opposite of open direction
        close_type = MT5_ORDER_TYPE_SELL if is_buy else MT5_ORDER_TYPE_BUY
        bid, ask   = await self.get_current_price(pos.symbol)
        price      = bid if is_buy else ask

        mt5_request = {
            "action":        MT5_TRADE_ACTION_DEAL,
            "symbol":        pos.symbol,
            "volume":        float(close_vol),
            "type":          close_type,
            "position":      int(position_id),
            "price":         float(price),
            "magic":         TITAN_FORGE_MAGIC,
            "comment":       "TF-CLOSE",
            "type_filling":  MT5_ORDER_FILLING_IOC,
        }

        result_raw = await self._run(self._mt5.order_send, mt5_request)
        success    = result_raw is not None and result_raw.retcode == 10009

        return OrderResult(
            success=success,
            order_id=position_id,
            status=OrderStatus.CLOSED if success else OrderStatus.REJECTED,
            instrument=pos.symbol,
            direction="long" if is_buy else "short",
            size=close_vol,
            fill_price=float(result_raw.price) if result_raw and success else None,
            stop_loss=None, take_profit=None,
            timestamp=datetime.now(timezone.utc),
            error_message=(
                f"MT5 retcode {result_raw.retcode}: {result_raw.comment}"
                if result_raw and not success else None
            ),
            raw_response=result_raw._asdict() if result_raw else None,
        )

    async def close_all_positions(self) -> list[OrderResult]:
        """
        Close ALL positions immediately.
        Called by FORGE-11 RED trigger and news blackouts.
        """
        positions = await self.get_open_positions()
        if not positions:
            logger.info("[FORGE-39][MT5] close_all: No open positions.")
            return []

        results = []
        for pos in positions:
            result = await self.close_position(pos.position_id)
            results.append(result)
            if result.success:
                logger.info(
                    "[FORGE-39][MT5] ✅ Closed position %s (%s %s).",
                    pos.position_id, pos.direction.value, pos.instrument,
                )
            else:
                logger.error(
                    "[FORGE-39][MT5] ❌ Failed to close %s: %s",
                    pos.position_id, result.error_message,
                )

        return results

    async def modify_position(
        self,
        position_id:     str,
        new_stop_loss:   Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Modify SL/TP on an open position. Called by FORGE-64 profit lock."""
        if new_stop_loss is None and new_take_profit is None:
            return OrderResult(
                success=False, order_id=position_id,
                status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message="Nothing to modify.",
            )

        # Fetch current position to preserve unmodified SL/TP
        positions = await self._run(
            self._mt5.positions_get, ticket=int(position_id)
        )

        if not positions:
            return OrderResult(
                success=False, order_id=position_id,
                status=OrderStatus.REJECTED,
                instrument="", direction="", size=0.0,
                fill_price=None, stop_loss=None, take_profit=None,
                timestamp=datetime.now(timezone.utc),
                error_message=f"Position {position_id} not found for modify.",
            )

        pos = positions[0]

        mt5_request = {
            "action":   MT5_TRADE_ACTION_SLTP,
            "symbol":   pos.symbol,
            "position": int(position_id),
            "sl":       float(new_stop_loss)   if new_stop_loss   is not None else float(pos.sl),
            "tp":       float(new_take_profit) if new_take_profit is not None else float(pos.tp),
        }

        result_raw = await self._run(self._mt5.order_send, mt5_request)
        success    = result_raw is not None and result_raw.retcode == 10009

        logger.info(
            "[FORGE-39][MT5] %s Modified %s: SL=%s TP=%s",
            "✅" if success else "❌", position_id,
            new_stop_loss, new_take_profit,
        )

        return OrderResult(
            success=success,
            order_id=position_id,
            status=OrderStatus.OPEN if success else OrderStatus.REJECTED,
            instrument=pos.symbol,
            direction="",
            size=0.0,
            fill_price=None,
            stop_loss=new_stop_loss,
            take_profit=new_take_profit,
            timestamp=datetime.now(timezone.utc),
            error_message=(
                f"MT5 retcode {result_raw.retcode}: {result_raw.comment}"
                if result_raw and not success else None
            ),
            raw_response=result_raw._asdict() if result_raw else None,
        )

    # ── MARKET DATA ───────────────────────────────────────────────────────────

    async def get_current_price(self, instrument: str) -> tuple[float, float]:
        """Return (bid, ask) from MT5 symbol tick."""
        tick = await self._run(self._mt5.symbol_info_tick, instrument)
        if tick:
            return float(tick.bid), float(tick.ask)
        logger.warning("[FORGE-39][MT5] No tick data for %s.", instrument)
        return 0.0, 0.0

    async def get_open_positions(self) -> list[OpenPosition]:
        """Return all open positions filtered by TITAN FORGE magic number."""
        raw = await self._run(self._mt5.positions_get) or []
        return [
            self._parse_position(p) for p in raw
            if p.magic == TITAN_FORGE_MAGIC
        ]

    # ── PARSERS ──────────────────────────────────────────────────────────────

    def _parse_position(self, pos) -> OpenPosition:
        """Convert MT5 TradePosition namedtuple to OpenPosition."""
        is_buy = pos.type == MT5_ORDER_TYPE_BUY
        return OpenPosition(
            position_id   = str(pos.ticket),
            instrument    = pos.symbol,
            direction     = OrderDirection.LONG if is_buy else OrderDirection.SHORT,
            size          = float(pos.volume),
            entry_price   = float(pos.price_open),
            current_price = float(pos.price_current),
            stop_loss     = float(pos.sl)     if pos.sl else None,
            take_profit   = float(pos.tp)     if pos.tp else None,
            unrealized_pnl= float(pos.profit),
            open_time     = datetime.fromtimestamp(pos.time, tz=timezone.utc),
            comment       = pos.comment or "",
        )

    # ── ASYNC THREAD EXECUTOR ─────────────────────────────────────────────────

    async def _run(self, func, *args, **kwargs):
        """
        Run a blocking MT5 call in a thread executor.
        Keeps the async event loop non-blocking.
        """
        loop = self._loop or asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: func(*args, **kwargs)
        )
