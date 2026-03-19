"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              execution_base.py — FORGE-39 (FX-01) — Execution Layer         ║
║                                                                              ║
║  ABSTRACT EXECUTION INTERFACE                                                ║
║  Every platform adapter (DXTrade, TradeLocker, Rithmic) implements          ║
║  this exact interface. TITAN FORGE never talks to a broker directly —       ║
║  always through this layer.                                                  ║
║                                                                              ║
║  FX-01: "START HERE. TradeLocker (DNA Funded) and DXTrade (FTMO) connect   ║
║  direct REST API from Railway. Rithmic and Tradovate need a Windows VPS     ║
║  bridge. Build DXTrade and TradeLocker connections first."                  ║
║                                                                              ║
║  Architecture:                                                               ║
║    Railway (Linux) → DXTrade REST API     → FTMO                            ║
║    Railway (Linux) → TradeLocker REST API → DNA Funded                      ║
║    Railway (Linux) → VPS Bridge           → Rithmic → Apex / Topstep        ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.execution")


# ─────────────────────────────────────────────────────────────────────────────
# CORE DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

class OrderType(Enum):
    MARKET       = "market"
    LIMIT        = "limit"
    STOP         = "stop"
    STOP_LIMIT   = "stop_limit"

class OrderStatus(Enum):
    PENDING      = "pending"
    OPEN         = "open"
    FILLED       = "filled"
    PARTIAL      = "partial"
    CANCELLED    = "cancelled"
    REJECTED     = "rejected"
    CLOSED       = "closed"

class OrderDirection(Enum):
    LONG         = "long"
    SHORT        = "short"

class PlatformStatus(Enum):
    CONNECTED     = "connected"
    DISCONNECTED  = "disconnected"
    ERROR         = "error"
    DEMO          = "demo"        # Connected to demo account


@dataclass
class OrderRequest:
    """
    Everything needed to place an order.
    Built by TITAN FORGE brain → sent to platform adapter.
    """
    instrument:     str           # e.g. "EURUSD", "US30", "NQ"
    direction:      OrderDirection
    size:           float         # Lots or contracts
    order_type:     OrderType     = OrderType.MARKET
    limit_price:    Optional[float] = None
    stop_loss:      Optional[float] = None   # REQUIRED per Layer 1 FORGE-11
    take_profit:    Optional[float] = None
    comment:        str           = ""       # Setup ID + signal info
    magic_number:   int           = 1000     # TITAN FORGE identifier


@dataclass
class OrderResult:
    """
    What the platform returns after an order is placed/modified/closed.
    """
    success:        bool
    order_id:       Optional[str]
    status:         OrderStatus
    instrument:     str
    direction:      str
    size:           float
    fill_price:     Optional[float]
    stop_loss:      Optional[float]
    take_profit:    Optional[float]
    timestamp:      datetime
    error_message:  Optional[str]   = None
    raw_response:   Optional[dict]  = None

    @property
    def is_live(self) -> bool:
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIAL)


@dataclass
class OpenPosition:
    """A currently open position on the platform."""
    position_id:    str
    instrument:     str
    direction:      OrderDirection
    size:           float
    entry_price:    float
    current_price:  float
    stop_loss:      Optional[float]
    take_profit:    Optional[float]
    unrealized_pnl: float
    open_time:      datetime
    comment:        str = ""


@dataclass
class AccountState:
    """
    Current account snapshot from the platform.
    Pulled every 30 seconds per FORGE-67/121.
    """
    account_id:         str
    platform:           str
    firm_id:            str
    balance:            float       # Realized balance
    equity:             float       # Balance + open P&L
    margin_used:        float
    margin_free:        float
    open_positions:     list[OpenPosition] = field(default_factory=list)
    daily_pnl:          float       = 0.0
    timestamp:          datetime    = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def unrealized_pnl(self) -> float:
        return self.equity - self.balance

    @property
    def open_position_count(self) -> int:
        return len(self.open_positions)


@dataclass
class PlatformHealth:
    """Health check result from a platform connection."""
    platform:       str
    status:         PlatformStatus
    latency_ms:     float
    last_checked:   datetime
    error:          Optional[str] = None
    is_demo:        bool = False

    @property
    def is_healthy(self) -> bool:
        return self.status in (PlatformStatus.CONNECTED, PlatformStatus.DEMO)


# ─────────────────────────────────────────────────────────────────────────────
# ABSTRACT BASE — EVERY ADAPTER IMPLEMENTS THIS
# ─────────────────────────────────────────────────────────────────────────────

class ExecutionAdapter(ABC):
    """
    Abstract base for all platform adapters.
    DXTrade, TradeLocker, Rithmic all extend this class.

    TITAN FORGE only ever calls these methods — never platform-specific code.
    This is what makes swapping firms a config change, not a code change.
    """

    def __init__(self, firm_id: str, account_id: str, is_demo: bool = True):
        self.firm_id    = firm_id
        self.account_id = account_id
        self.is_demo    = is_demo
        self._connected = False
        self._session_token: Optional[str] = None
        logger.info(
            "[FORGE-39][%s] Adapter initialized. Account: %s. Mode: %s.",
            firm_id, account_id, "DEMO" if is_demo else "LIVE"
        )

    # ── CONNECTION ────────────────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> bool:
        """Authenticate and establish session. Returns True on success."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Cleanly close the session."""

    @abstractmethod
    async def health_check(self) -> PlatformHealth:
        """Ping the platform and return health status. Called every 30 seconds."""

    # ── ACCOUNT ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_account_state(self) -> AccountState:
        """Pull current balance, equity, positions. The 30-second snapshot."""

    # ── ORDER EXECUTION ───────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult:
        """
        Place an order on the platform.
        FORGE-11 Layer 1 enforces: stop_loss MUST be set before this is called.
        """

    @abstractmethod
    async def close_position(self, position_id: str, size: Optional[float] = None) -> OrderResult:
        """
        Close a position fully or partially.
        size=None means close everything.
        size=0.30 means close 30% (FORGE-64 Stage 2 profit lock).
        """

    @abstractmethod
    async def close_all_positions(self) -> list[OrderResult]:
        """
        Close ALL open positions immediately.
        Called by FORGE-11 Layer 3 RED (85% drawdown) and news blackouts.
        """

    @abstractmethod
    async def modify_position(
        self,
        position_id:    str,
        new_stop_loss:  Optional[float] = None,
        new_take_profit: Optional[float] = None,
    ) -> OrderResult:
        """
        Modify stop/target on an open position.
        Called by FORGE-64 profit lock (0.5R → breakeven, 3R → trailing).
        """

    # ── MARKET DATA ───────────────────────────────────────────────────────────

    @abstractmethod
    async def get_current_price(self, instrument: str) -> tuple[float, float]:
        """Return (bid, ask) for an instrument. Used by signal generators."""

    @abstractmethod
    async def get_open_positions(self) -> list[OpenPosition]:
        """Return all currently open positions."""

    # ── UTILITY ───────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _log_order(self, request: OrderRequest, result: OrderResult) -> None:
        """Log every order for audit trail (FORGE-87 Corporate Audit)."""
        status = "✅" if result.success else "❌"
        logger.info(
            "[FORGE-39][%s] %s Order: %s %s %.4f @ %.5f | Stop: %s | "
            "ID: %s | Comment: %s",
            self.firm_id, status,
            request.direction.value, request.instrument, request.size,
            result.fill_price or 0.0,
            result.stop_loss or "NONE",
            result.order_id or "N/A",
            request.comment,
        )
        if not result.success:
            logger.error(
                "[FORGE-39][%s] Order FAILED: %s", self.firm_id, result.error_message
            )
