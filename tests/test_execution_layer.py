"""
╔══════════════════════════════════════════════════════════════════════════════╗
║               test_execution_layer.py — FORGE-39 — FX-06 Compliance         ║
║  Tests for all execution layer modules — no live broker connection needed    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys, os, traceback, asyncio
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from execution_base import (
    ExecutionAdapter, OrderRequest, OrderResult, OrderStatus,
    AccountState, OpenPosition, PlatformHealth, PlatformStatus,
    OrderDirection, OrderType,
)
from execution_manager import ExecutionManager, MAX_SIMULTANEOUS_POSITIONS
from dxtrade_adapter    import DXTradeAdapter
from tradelocker_adapter import TradeLockerAdapter, DNA_MIN_HOLD_SECONDS
from rithmic_adapter    import RithmicVPSAdapter
from firm_rules         import FirmID


def run_async(coro):
    """Run async coroutine in test context."""
    return asyncio.get_event_loop().run_until_complete(coro)


def make_order_request(
    instrument="EURUSD", direction=OrderDirection.LONG,
    size=1.0, stop_loss=1.0800, take_profit=1.1000,
    comment="GEX-01 test",
) -> OrderRequest:
    return OrderRequest(
        instrument=instrument, direction=direction, size=size,
        order_type=OrderType.MARKET, stop_loss=stop_loss,
        take_profit=take_profit, comment=comment,
    )


def make_open_position(pos_id="POS-001", instrument="EURUSD") -> OpenPosition:
    return OpenPosition(
        position_id=pos_id, instrument=instrument,
        direction=OrderDirection.LONG, size=1.0,
        entry_price=1.0900, current_price=1.0920,
        stop_loss=1.0800, take_profit=1.1000,
        unrealized_pnl=200.0, open_time=datetime.now(timezone.utc),
    )


def make_success_result(instrument="EURUSD") -> OrderResult:
    return OrderResult(
        success=True, order_id="ORD-001",
        status=OrderStatus.FILLED,
        instrument=instrument, direction="long",
        size=1.0, fill_price=1.0905,
        stop_loss=1.0800, take_profit=1.1000,
        timestamp=datetime.now(timezone.utc),
    )


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION BASE — ABSTRACT INTERFACE
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionBase:

    def test_normal_order_request_has_required_fields(self):
        """Normal: OrderRequest contains all fields needed for execution."""
        req = make_order_request()
        assert req.instrument == "EURUSD"
        assert req.direction == OrderDirection.LONG
        assert req.stop_loss == 1.0800
        assert req.take_profit == 1.1000

    def test_edge_order_result_is_live_when_open(self):
        """Edge: is_live returns True for OPEN/PARTIAL, False for FILLED/CLOSED."""
        open_result = OrderResult(
            success=True, order_id="X", status=OrderStatus.OPEN,
            instrument="ES", direction="long", size=1.0,
            fill_price=4800.0, stop_loss=4790.0, take_profit=4830.0,
            timestamp=datetime.now(timezone.utc),
        )
        closed_result = OrderResult(
            success=True, order_id="X", status=OrderStatus.CLOSED,
            instrument="ES", direction="long", size=1.0,
            fill_price=4800.0, stop_loss=None, take_profit=None,
            timestamp=datetime.now(timezone.utc),
        )
        assert open_result.is_live is True
        assert closed_result.is_live is False

    def test_conflict_account_state_unrealized_pnl_calculated(self):
        """Conflict: unrealized_pnl = equity - balance, not stored separately."""
        state = AccountState(
            account_id="TEST", platform="DXTrade", firm_id=FirmID.FTMO,
            balance=100_000.0, equity=101_500.0,
            margin_used=0.0, margin_free=101_500.0,
        )
        assert abs(state.unrealized_pnl - 1_500.0) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# DXTRADE ADAPTER — FTMO
# ─────────────────────────────────────────────────────────────────────────────

class TestDXTradeAdapter:

    def test_normal_adapter_initializes_with_demo_url(self):
        """Normal: Demo=True uses DEMO URL, not live URL."""
        from dxtrade_adapter import DXTRADE_DEMO_URL, DXTRADE_BASE_URL
        adapter = DXTradeAdapter("FTMO-001", "user", "pass", is_demo=True)
        assert adapter._base_url == DXTRADE_DEMO_URL
        assert adapter.is_demo is True

    def test_edge_live_adapter_uses_live_url(self):
        """Edge: Demo=False uses live production URL."""
        from dxtrade_adapter import DXTRADE_BASE_URL
        adapter = DXTradeAdapter("FTMO-001", "user", "pass", is_demo=False)
        assert adapter._base_url == DXTRADE_BASE_URL

    def test_conflict_forge11_blocks_order_without_stop(self):
        """Conflict: FORGE-11 — no stop loss → order rejected, not sent to DXTrade."""
        adapter = DXTradeAdapter("FTMO-001", "user", "pass", is_demo=True)
        adapter._connected = True
        adapter._token = "fake_token"

        request = OrderRequest(
            instrument="EURUSD", direction=OrderDirection.LONG,
            size=1.0, order_type=OrderType.MARKET,
            stop_loss=None,   # No stop — MUST be rejected
            comment="test",
        )

        result = run_async(adapter.place_order(request))
        assert result.success is False
        assert result.status == OrderStatus.REJECTED
        assert "FORGE-11" in result.error_message

    def test_normal_adapter_firm_id_is_ftmo(self):
        """Normal: DXTrade adapter is always configured for FTMO."""
        adapter = DXTradeAdapter("FTMO-001", "user", "pass")
        assert adapter.firm_id == FirmID.FTMO

    def test_normal_parse_position_extracts_fields(self):
        """Normal: _parse_position correctly maps DXTrade response to OpenPosition."""
        adapter = DXTradeAdapter("FTMO-001", "user", "pass")
        raw = {
            "positionId": "12345", "symbol": "EURUSD", "side": "buy",
            "quantity": 1.5, "openPrice": 1.0900, "currentPrice": 1.0920,
            "stopLoss": 1.0800, "takeProfit": 1.1000, "pnl": 300.0,
        }
        pos = adapter._parse_position(raw)
        assert pos.position_id == "12345"
        assert pos.instrument  == "EURUSD"
        assert pos.direction   == OrderDirection.LONG
        assert abs(pos.size    - 1.5) < 1e-9
        assert pos.stop_loss   == 1.0800


# ─────────────────────────────────────────────────────────────────────────────
# TRADELOCKER ADAPTER — DNA FUNDED
# ─────────────────────────────────────────────────────────────────────────────

class TestTradeLockerAdapter:

    def test_normal_adapter_initializes_for_dna(self):
        """Normal: TradeLocker adapter configured for DNA Funded."""
        adapter = TradeLockerAdapter("DNA-001", "email@test.com", "pass", "server", is_demo=True)
        assert adapter.firm_id == FirmID.DNA_FUNDED
        assert adapter.is_demo is True

    def test_edge_30_second_hold_enforced_on_close(self):
        """Edge: DNA Funded 30-second minimum hold — closing immediately is rejected."""
        import time
        adapter = TradeLockerAdapter("DNA-001", "e@t.com", "p", "s", is_demo=True)
        adapter._connected = True
        adapter._access_token = "fake"
        # Simulate a position opened 5 seconds ago
        adapter._position_open_times["POS-001"] = time.time() - 5

        result = run_async(adapter.close_position("POS-001"))
        assert result.success is False
        assert "30s" in result.error_message or "DNA" in result.error_message

    def test_conflict_forge11_stops_no_stop_orders(self):
        """Conflict: FORGE-11 — no stop → rejected before reaching TradeLocker."""
        adapter = TradeLockerAdapter("DNA-001", "e@t.com", "p", "s")
        adapter._connected = True
        adapter._access_token = "token"

        request = OrderRequest(
            instrument="EURUSD", direction=OrderDirection.LONG,
            size=0.5, order_type=OrderType.MARKET,
            stop_loss=None,
        )
        result = run_async(adapter.place_order(request))
        assert result.success is False
        assert "FORGE-11" in result.error_message

    def test_normal_min_hold_is_30_seconds(self):
        """Normal: DNA_MIN_HOLD_SECONDS constant is exactly 30."""
        assert DNA_MIN_HOLD_SECONDS == 30

    def test_normal_parse_position_handles_dna_format(self):
        """Normal: _parse_position handles TradeLocker DNA response format."""
        adapter = TradeLockerAdapter("DNA-001", "e@t.com", "p", "s")
        raw = {
            "positionId": "TL-99", "instrument": "GBPUSD", "side": "sell",
            "qty": 2.0, "openPrice": 1.2700, "currentPrice": 1.2680,
            "stopLoss": 1.2800, "pnl": 200.0,
        }
        pos = adapter._parse_position(raw)
        assert pos.position_id == "TL-99"
        assert pos.direction == OrderDirection.SHORT
        assert abs(pos.size - 2.0) < 1e-9


# ─────────────────────────────────────────────────────────────────────────────
# RITHMIC VPS ADAPTER — APEX / TOPSTEP
# ─────────────────────────────────────────────────────────────────────────────

class TestRithmicVPSAdapter:

    def test_normal_adapter_initializes_for_apex(self):
        """Normal: Rithmic VPS adapter configured for Apex."""
        adapter = RithmicVPSAdapter("APEX-001", FirmID.APEX, "10.0.0.1", 8765)
        assert adapter.firm_id == FirmID.APEX
        assert "10.0.0.1" in adapter._bridge_url

    def test_edge_forge11_stops_no_stop_orders(self):
        """Edge: Even through VPS bridge, FORGE-11 blocks orderless entries."""
        adapter = RithmicVPSAdapter("APEX-001", FirmID.APEX, "10.0.0.1")
        adapter._connected = True
        adapter._session = MagicMock()

        request = OrderRequest(
            instrument="NQ", direction=OrderDirection.LONG,
            size=1.0, stop_loss=None,
        )
        result = run_async(adapter.place_order(request))
        assert result.success is False
        assert "FORGE-11" in result.error_message

    def test_conflict_vps_url_built_from_host_and_port(self):
        """Conflict: Bridge URL must include host and port for Railway → VPS routing."""
        adapter = RithmicVPSAdapter("APEX-001", FirmID.APEX, "192.168.1.50", 9000)
        assert "192.168.1.50" in adapter._bridge_url
        assert "9000" in adapter._bridge_url


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTION MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutionManager:

    def test_normal_register_and_retrieve_adapter(self):
        """Normal: Register adapter, retrieve it by account ID."""
        manager = ExecutionManager()
        adapter = DXTradeAdapter("FTMO-001", "user", "pass")
        manager.register_adapter("FTMO-001", adapter)
        assert manager.get_adapter("FTMO-001") is adapter

    def test_edge_unknown_account_returns_none(self):
        """Edge: Requesting unknown account returns None (no crash)."""
        manager = ExecutionManager()
        assert manager.get_adapter("UNKNOWN-999") is None

    def test_normal_registered_accounts_listed(self):
        """Normal: registered_accounts returns all account IDs."""
        manager = ExecutionManager()
        manager.register_adapter("FTMO-001", DXTradeAdapter("FTMO-001", "u", "p"))
        manager.register_adapter("DNA-001",  TradeLockerAdapter("DNA-001", "e@t.com", "p", "s"))
        assert "FTMO-001" in manager.registered_accounts
        assert "DNA-001"  in manager.registered_accounts

    def test_conflict_p07_max_2_positions_enforced(self):
        """Conflict: P-07 — 2 open positions → third order rejected."""
        manager = ExecutionManager()

        # Mock adapter with 2 open positions
        mock_adapter = MagicMock(spec=ExecutionAdapter)
        mock_adapter.is_connected = True
        mock_adapter.firm_id = FirmID.FTMO
        mock_adapter.is_demo = True
        mock_adapter.get_open_positions = AsyncMock(return_value=[
            make_open_position("P1", "EURUSD"),
            make_open_position("P2", "ES"),
        ])

        manager.register_adapter("FTMO-001", mock_adapter)
        request = make_order_request(instrument="NQ")

        result = run_async(manager.place_order("FTMO-001", request))
        assert result.success is False
        assert "P-07" in result.error_message or "Max" in result.error_message

    def test_normal_place_order_calls_adapter(self):
        """Normal: place_order delegates to the adapter when position limit not hit."""
        manager = ExecutionManager()

        mock_adapter = MagicMock(spec=ExecutionAdapter)
        mock_adapter.is_connected = True
        mock_adapter.firm_id = FirmID.FTMO
        mock_adapter.is_demo = True
        mock_adapter.get_open_positions = AsyncMock(return_value=[])
        mock_adapter.place_order = AsyncMock(return_value=make_success_result())

        manager.register_adapter("FTMO-001", mock_adapter)
        request = make_order_request()

        result = run_async(manager.place_order("FTMO-001", request))
        assert result.success is True
        mock_adapter.place_order.assert_called_once_with(request)

    def test_conflict_not_connected_rejects_order(self):
        """Conflict: Adapter not connected → order rejected immediately."""
        manager = ExecutionManager()
        adapter = DXTradeAdapter("FTMO-001", "u", "p")
        # Not connected — _connected stays False
        manager.register_adapter("FTMO-001", adapter)

        request = make_order_request()
        result = run_async(manager.place_order("FTMO-001", request))
        assert result.success is False
        assert "not connected" in result.error_message.lower()

    def test_normal_max_simultaneous_positions_is_2(self):
        """Normal: P-07 constant is exactly 2."""
        assert MAX_SIMULTANEOUS_POSITIONS == 2

    def test_normal_from_environment_handles_missing_vars(self):
        """Normal: from_environment() works even when env vars are missing."""
        # No env vars set → should still return a manager (just no adapters)
        manager = ExecutionManager.from_environment()
        assert isinstance(manager, ExecutionManager)
        # FTMO not configured because env vars not set in test environment
        # That's correct — no crash is the key assertion


# ─────────────────────────────────────────────────────────────────────────────
# PLATFORM HEALTH
# ─────────────────────────────────────────────────────────────────────────────

class TestPlatformHealth:

    def test_normal_connected_is_healthy(self):
        """Normal: CONNECTED status → is_healthy = True."""
        h = PlatformHealth("DXTrade", PlatformStatus.CONNECTED, 45.0,
                           datetime.now(timezone.utc))
        assert h.is_healthy is True

    def test_edge_demo_is_also_healthy(self):
        """Edge: DEMO status → is_healthy = True (demo counts as working)."""
        h = PlatformHealth("DXTrade", PlatformStatus.DEMO, 30.0,
                           datetime.now(timezone.utc), is_demo=True)
        assert h.is_healthy is True

    def test_conflict_error_status_is_not_healthy(self):
        """Conflict: ERROR/DISCONNECTED status → is_healthy = False."""
        h_err  = PlatformHealth("DXTrade", PlatformStatus.ERROR, 0.0,
                                datetime.now(timezone.utc), error="timeout")
        h_disc = PlatformHealth("DXTrade", PlatformStatus.DISCONNECTED, 0.0,
                                datetime.now(timezone.utc))
        assert h_err.is_healthy  is False
        assert h_disc.is_healthy is False


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    passed = failed = 0
    failures = []
    for cls_name in sorted(dir()):
        cls = eval(cls_name)
        if not (isinstance(cls, type) and cls_name.startswith("Test")):
            continue
        inst = cls()
        for meth_name in sorted(dir(inst)):
            if not meth_name.startswith("test_"):
                continue
            try:
                if hasattr(inst, "setup_method"):
                    inst.setup_method()
                getattr(inst, meth_name)()
                print(f"  ✅ {cls_name}::{meth_name}")
                passed += 1
            except Exception as e:
                print(f"  ❌ {cls_name}::{meth_name}")
                failures.append((cls_name, meth_name, traceback.format_exc()))
                failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    if failures:
        for cn, mn, tb in failures:
            print(f"\nFAIL: {cn}::{mn}\n{tb}")
