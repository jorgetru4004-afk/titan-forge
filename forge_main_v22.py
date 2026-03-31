"""
FORGE v22 — Main Loop Integration
====================================
This file shows the v22 scan loop that replaces the v21 signal scanning.
Wire this into your existing main.py, replacing the old signal generation.

The existing main.py structure (asyncio loop, MetaAPI connection, 
Telegram bot, session management) stays the same.
Only the signal generation and trade management change.

Philosophy: "ALL GAS FIRST THEN BRAKES"
- Conviction threshold: 0.20
- Cooldown: 120s (EVAL) / 120s (FUNDED)
- Enter on any decent signal, manage the trade
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

# v22 new modules
from forge_signals_v22 import SignalEngine, MarketSnapshot, Signal
from forge_runner import TradeManager, ManagedTrade, RunnerContext, RunnerDetector, TradeType
from forge_limit import LimitOrderManager, check_slippage
from forge_correlation import CorrelationGuard
from forge_instruments_v22 import (
    SETUP_CONFIG, OrderType, get_all_symbols, get_ftmo_symbol,
    INSTRUMENT_META,
)

# Existing v21 modules (KEEP UNCHANGED)
# from forge_risk import RiskManager        # All 13 risk gates
# from forge_evidence import EvidenceLogger  # Evidence logging
# from forge_brain import BayesianBrain     # Bayesian conviction engine
# from forge_mode import ModeManager        # EVAL/FUNDED switching
# from forge_market import MarketDataEngine  # Indicator computation
# from mt5_adapter import MT5Adapter        # MetaAPI connection
# from forge_heartbeat import Heartbeat     # Dead man switch
# from forge_readiness import ReadinessCheck # Pre-flight checks

logger = logging.getLogger("FORGE.main")


# ─── v22 Configuration ──────────────────────────────────────────────────────

V22_CONFIG = {
    # Trade flow — ALL GAS FIRST
    "conviction_threshold": 0.20,    # v21 was 0.35 — killed flow
    "cooldown_seconds": 120,         # v21 was 180 in EVAL
    "max_daily_trades": 12,          # Same as v21
    "min_daily_trades_target": 2,    # NEW: activity target
    "max_open_positions": 5,         # Same as v21
    
    # Scan interval
    "scan_interval_seconds": 30,     # Check for signals every 30s
    
    # Breakeven
    "breakeven_r": 0.5,              # Move SL to BE at +0.5R
    
    # Max slippage for MARKET orders
    "max_slippage_atr": 0.3,
}


# ─── Main Scan Loop (replaces v21 signal scanning) ──────────────────────────

class ForgeV22Engine:
    """
    FORGE v22 Main Engine.
    
    Orchestrates:
    - Signal generation (forge_signals_v22)
    - Trade management (forge_runner)
    - Limit order tracking (forge_limit)
    - Correlation checks (forge_correlation)
    - Integration with existing risk gates, evidence, MetaAPI
    """

    def __init__(
        self,
        # These are your existing v21 components — pass them in
        risk_manager=None,       # forge_risk.RiskManager
        evidence_logger=None,    # forge_evidence.EvidenceLogger
        brain=None,              # forge_brain.BayesianBrain
        mode_manager=None,       # forge_mode.ModeManager
        market_engine=None,      # forge_market.MarketDataEngine
        mt5=None,                # mt5_adapter.MT5Adapter
        telegram=None,           # Telegram bot instance
    ):
        # Existing components
        self.risk = risk_manager
        self.evidence = evidence_logger
        self.brain = brain
        self.mode = mode_manager
        self.market = market_engine
        self.mt5 = mt5
        self.telegram = telegram

        # v22 new components
        self.signal_engine = SignalEngine()
        self.trade_manager = TradeManager()
        self.limit_manager = LimitOrderManager()
        self.correlation_guard = CorrelationGuard()

        # State
        self._last_trade_time: Dict[str, datetime] = {}  # Per-symbol cooldown
        self._daily_trade_count = 0
        self._daily_reset_date = None
        self._active_symbols: Set[str] = set()

    async def scan_loop(self):
        """
        Main scanning loop. Runs every scan_interval_seconds.
        
        This replaces the v21 signal scanning in main.py.
        """
        interval = V22_CONFIG["scan_interval_seconds"]
        logger.info(f"FORGE v22 scan loop starting | interval={interval}s | "
                   f"threshold={V22_CONFIG['conviction_threshold']}")

        while True:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"Scan cycle error: {e}", exc_info=True)
            
            await asyncio.sleep(interval)

    async def _scan_cycle(self):
        """Single scan cycle — generate signals, manage trades, execute."""
        now = datetime.now(timezone.utc)
        
        # Reset daily counter at midnight UTC
        today = now.date()
        if self._daily_reset_date != today:
            self._daily_reset_date = today
            self._daily_trade_count = 0
            logger.info(f"Daily reset | trades today: 0")

        # ─── Step 1: Update market data for all instruments ──────────
        snapshots = await self._build_snapshots()
        if not snapshots:
            return

        # ─── Step 2: Manage existing trades (breakeven, partials, runners)
        await self._manage_active_trades(snapshots)

        # ─── Step 3: Update pending limit orders ─────────────────────
        await self._update_limits(snapshots)

        # ─── Step 4: Check risk gates (from existing forge_risk.py) ──
        # if self.risk and not self.risk.can_trade():
        #     logger.info("Risk gates blocking new trades")
        #     return

        # ─── Step 5: Generate new signals ────────────────────────────
        if self._daily_trade_count >= V22_CONFIG["max_daily_trades"]:
            return  # Daily limit reached

        signals = self.signal_engine.generate_signals(snapshots, current_time=now)
        
        if not signals:
            return

        # ─── Step 6: Filter and execute signals ──────────────────────
        for signal in signals:
            if not await self._can_take_signal(signal, now):
                continue

            await self._execute_signal(signal, now)

    async def _build_snapshots(self) -> Dict[str, MarketSnapshot]:
        """
        Build MarketSnapshot for each instrument using forge_market.py.
        
        This is a bridge to your existing market data engine.
        Replace this with your actual market data pipeline.
        """
        snapshots = {}
        
        for symbol in get_all_symbols():
            try:
                # --- YOUR EXISTING CODE HERE ---
                # Example integration with forge_market.py:
                #
                # ftmo_sym = get_ftmo_symbol(symbol)
                # candles = await self.market.get_candles(ftmo_sym, timeframe="5m", count=200)
                # indicators = self.market.compute_indicators(candles)
                # tick = await self.mt5.get_tick(ftmo_sym)
                #
                # snap = MarketSnapshot(
                #     symbol=symbol,
                #     opens=candles.opens, highs=candles.highs,
                #     lows=candles.lows, closes=candles.closes,
                #     volumes=candles.volumes,
                #     bid=tick.bid, ask=tick.ask,
                #     atr=indicators.atr, rsi=indicators.rsi,
                #     stoch_k=indicators.stoch_k, stoch_d=indicators.stoch_d,
                #     stoch_k_prev=indicators.stoch_k_prev,
                #     stoch_d_prev=indicators.stoch_d_prev,
                #     ema_50=indicators.ema_50, ema_200=indicators.ema_200,
                #     bb_upper=indicators.bb_upper, bb_lower=indicators.bb_lower,
                #     bb_middle=indicators.bb_middle,
                #     vwap=indicators.vwap, vwap_std=indicators.vwap_std,
                #     adx=indicators.adx, adx_prev=indicators.adx_prev,
                #     plus_di=indicators.plus_di, minus_di=indicators.minus_di,
                #     prev_day_high=indicators.prev_day_high,
                #     prev_day_low=indicators.prev_day_low,
                #     prev_day_close=indicators.prev_day_close,
                #     session_open=indicators.session_open,
                #     session_high=indicators.session_high,
                #     session_low=indicators.session_low,
                #     orb_high=indicators.orb_high,
                #     orb_low=indicators.orb_low,
                #     orb_complete=indicators.orb_complete,
                #     asian_high=indicators.asian_high,
                #     asian_low=indicators.asian_low,
                #     asian_complete=indicators.asian_complete,
                #     keltner_upper=indicators.keltner_upper,
                #     keltner_lower=indicators.keltner_lower,
                #     bars_since_open=indicators.bars_since_open,
                #     current_hour_utc=datetime.now(timezone.utc).hour,
                # )
                # snapshots[symbol] = snap
                pass
            except Exception as e:
                logger.error(f"Snapshot error {symbol}: {e}")

        return snapshots

    async def _manage_active_trades(self, snapshots: Dict[str, MarketSnapshot]):
        """Update all active trades with current prices for BE/partial/trailing."""
        for trade_id, trade in list(self.trade_manager.active_trades.items()):
            snap = snapshots.get(trade.symbol)
            if snap is None:
                continue

            current_price = snap.bid if trade.direction == "LONG" else snap.ask

            # Build runner context (for RUNNER trades)
            ctx = None
            if trade.trade_type == TradeType.RUNNER:
                # Calculate ATR consumed today
                day_range = snap.session_high - snap.session_low
                atr_consumed = day_range / snap.atr if snap.atr > 0 else 0

                # Check for reversal candle against our direction
                against_dir = "LONG" if trade.direction == "SHORT" else "SHORT"
                has_reversal = False
                has_vol_spike = False
                if len(snap.closes) >= 2:
                    from forge_signals_v22 import _is_reversal_candle, _volume_spike
                    has_reversal = _is_reversal_candle(
                        snap.opens, snap.highs, snap.lows, snap.closes, against_dir
                    )
                    has_vol_spike = _volume_spike(snap.volumes)

                ctx = RunnerContext(
                    current_price=current_price,
                    adx=snap.adx,
                    adx_5bars_ago=snap.adx_prev,
                    vwap=snap.vwap,
                    atr_consumed_pct=atr_consumed,
                    has_reversal_candle=has_reversal,
                    has_volume_spike=has_vol_spike,
                    bars_held=trade.bars_held,
                )

            actions = self.trade_manager.update_trade(trade_id, current_price, ctx)

            for action in actions:
                if action["action"] == "MOVE_SL":
                    # Move SL via MetaAPI
                    # await self.mt5.modify_position(trade_id, sl=action["new_sl"])
                    logger.info(f"SL MOVED: {trade.symbol} -> {action['new_sl']:.5f} ({action.get('reason', '')})")

                elif action["action"] == "PARTIAL_CLOSE":
                    # Close partial via MetaAPI
                    # await self.mt5.close_partial(trade_id, pct=action["pct"])
                    logger.info(f"PARTIAL: {trade.symbol} closing {action['pct']:.0%} ({action.get('reason', '')})")

                elif action["action"] == "CLOSE_ALL":
                    # Close position via MetaAPI
                    # await self.mt5.close_position(trade_id)
                    self._active_symbols.discard(trade.symbol)
                    logger.info(f"CLOSED: {trade.symbol} reason={action.get('reason', '')}")
                    
                    # Send Telegram alert
                    # await self.telegram.send(f"🔴 CLOSED {trade.symbol} | {action.get('reason', '')}")

    async def _update_limits(self, snapshots: Dict[str, MarketSnapshot]):
        """Update pending limit orders — check fills, timeouts, fallbacks."""
        for order_id in list(self.limit_manager.pending_orders.keys()):
            pending = self.limit_manager.pending_orders[order_id]
            snap = snapshots.get(pending.signal.symbol)
            if snap is None:
                continue

            current_price = snap.closes[-1] if len(snap.closes) > 0 else 0

            result = self.limit_manager.update_tick(
                order_id=order_id,
                current_price=current_price,
                current_rsi=snap.rsi,
                current_stoch_k=snap.stoch_k,
                atr=snap.atr,
            )

            if result["action"] == "MARKET_ENTRY":
                # Limit expired, falling back to market
                signal = result["signal"]
                logger.info(f"LIMIT->MARKET: {signal.symbol} | {result.get('reason', '')}")
                signal.order_type = OrderType.MARKET
                signal.entry_price = current_price
                await self._execute_trade(signal, current_price)

            elif result["action"] == "CANCEL":
                # Cancel the pending order via MetaAPI
                # await self.mt5.cancel_order(order_id)
                logger.info(f"LIMIT CANCEL: {order_id} | {result.get('reason', '')}")

    async def _can_take_signal(self, signal: Signal, now: datetime) -> bool:
        """Pre-flight checks before executing a signal."""
        
        # Check max open positions
        total_open = self.trade_manager.get_active_count() + self.limit_manager.get_pending_count()
        if total_open >= V22_CONFIG["max_open_positions"]:
            logger.debug(f"SKIP {signal.symbol}: max positions ({total_open})")
            return False

        # Check cooldown
        last_trade = self._last_trade_time.get(signal.symbol)
        if last_trade:
            elapsed = (now - last_trade).total_seconds()
            if elapsed < V22_CONFIG["cooldown_seconds"]:
                logger.debug(f"SKIP {signal.symbol}: cooldown ({elapsed:.0f}s < {V22_CONFIG['cooldown_seconds']}s)")
                return False

        # Check correlation
        allowed, reason = self.correlation_guard.can_trade(signal.symbol, self._active_symbols)
        if not allowed:
            logger.info(f"SKIP {signal.symbol}: {reason}")
            return False

        # Check if already have position on this symbol
        if signal.symbol in self._active_symbols:
            logger.debug(f"SKIP {signal.symbol}: already have position")
            return False

        # Check if already have pending limit on this symbol
        if signal.symbol in self.limit_manager.get_pending_symbols():
            logger.debug(f"SKIP {signal.symbol}: pending limit exists")
            return False

        return True

    async def _execute_signal(self, signal: Signal, now: datetime):
        """Execute a signal — either place limit or fire market order."""
        
        if signal.order_type == OrderType.LIMIT:
            await self._execute_limit(signal, now)
        else:
            await self._execute_trade(signal, signal.entry_price)

    async def _execute_limit(self, signal: Signal, now: datetime):
        """Place a limit order and register it for tracking."""
        order_id = f"FORGE-{signal.symbol}-{uuid.uuid4().hex[:8]}"
        
        # Place limit via MetaAPI
        # result = await self.mt5.place_limit_order(
        #     symbol=get_ftmo_symbol(signal.symbol),
        #     direction=signal.direction,
        #     price=signal.entry_price,
        #     sl=signal.sl_price,
        #     tp=signal.tp_price if signal.trade_type == TradeType.SCALP else None,
        #     volume=calculated_lots,
        # )
        
        # Register for tracking
        self.limit_manager.add_limit(
            order_id=order_id,
            signal=signal,
            rsi=signal.context.get("rsi", 50),
            stoch_k=signal.context.get("stoch_k", 50),
        )

        self._last_trade_time[signal.symbol] = now
        self._daily_trade_count += 1

        logger.info(
            f"🔵 LIMIT PLACED: {signal.symbol} {signal.direction} {signal.strategy.value} "
            f"at {signal.entry_price:.5f} | SL={signal.sl_price:.5f} TP={signal.tp_price:.5f} "
            f"| conf={signal.final_confidence:.3f} | type={signal.trade_type.value}"
        )

        # Telegram alert
        # await self.telegram.send(
        #     f"🔵 LIMIT: {signal.symbol} {signal.direction}\n"
        #     f"Strategy: {signal.strategy.value}\n"
        #     f"Entry: {signal.entry_price:.5f}\n"
        #     f"SL: {signal.sl_price:.5f} | TP: {signal.tp_price:.5f}\n"
        #     f"Confidence: {signal.final_confidence:.3f}\n"
        #     f"Type: {signal.trade_type.value}"
        # )

    async def _execute_trade(self, signal: Signal, fill_price: float):
        """Execute a market order and register for trade management."""
        trade_id = f"FORGE-{signal.symbol}-{uuid.uuid4().hex[:8]}"

        # Calculate position size via forge_risk.py
        # lots = self.risk.calculate_lots(
        #     symbol=signal.symbol,
        #     sl_distance=abs(fill_price - signal.sl_price),
        #     risk_pct=signal.risk_pct,
        # )
        lots = 0.10  # Placeholder — replace with actual calculation

        # Place market order via MetaAPI
        # result = await self.mt5.place_market_order(
        #     symbol=get_ftmo_symbol(signal.symbol),
        #     direction=signal.direction,
        #     volume=lots,
        #     sl=signal.sl_price,
        #     tp=signal.tp_price if signal.trade_type == TradeType.SCALP else None,
        # )

        # Check slippage
        # actual_fill = result.fill_price
        # acceptable, slippage_atr = check_slippage(
        #     expected_price=fill_price, actual_price=actual_fill,
        #     atr=signal.atr_value, max_slippage_atr=V22_CONFIG["max_slippage_atr"],
        # )
        # if not acceptable:
        #     await self.mt5.close_position(trade_id)
        #     return

        # Register for trade management
        managed = ManagedTrade(
            trade_id=trade_id,
            symbol=signal.symbol,
            direction=signal.direction,
            trade_type=signal.trade_type,
            entry_price=fill_price,
            sl_price=signal.sl_price,
            tp_price=signal.tp_price,
            current_sl=signal.sl_price,
            risk_amount=abs(fill_price - signal.sl_price) * lots,
            position_size=lots,
            remaining_size=lots,
        )
        self.trade_manager.add_trade(managed)
        self._active_symbols.add(signal.symbol)
        self._last_trade_time[signal.symbol] = datetime.now(timezone.utc)
        self._daily_trade_count += 1

        logger.info(
            f"🟢 TRADE OPEN: {signal.symbol} {signal.direction} {signal.strategy.value} "
            f"at {fill_price:.5f} | SL={signal.sl_price:.5f} TP={signal.tp_price:.5f} "
            f"| lots={lots} | conf={signal.final_confidence:.3f} | type={signal.trade_type.value}"
        )

        # Log evidence
        # if self.evidence:
        #     self.evidence.log_entry(signal, managed)

        # Telegram alert
        # await self.telegram.send(
        #     f"🟢 TRADE: {signal.symbol} {signal.direction}\n"
        #     f"Strategy: {signal.strategy.value}\n"
        #     f"Entry: {fill_price:.5f} | Lots: {lots}\n"
        #     f"SL: {signal.sl_price:.5f} | TP: {signal.tp_price:.5f}\n"
        #     f"Confidence: {signal.final_confidence:.3f}\n"
        #     f"Type: {signal.trade_type.value}\n"
        #     f"Trades today: {self._daily_trade_count}"
        # )

    def get_status(self) -> Dict:
        """Status summary for Telegram /status command."""
        return {
            "version": "v22",
            "active_trades": self.trade_manager.get_active_count(),
            "pending_limits": self.limit_manager.get_pending_count(),
            "daily_trades": self._daily_trade_count,
            "total_signals": self.signal_engine.total_signals,
            "active_symbols": list(self._active_symbols),
            "trade_summary": self.trade_manager.get_trade_summary(),
            "limit_stats": self.limit_manager.get_stats(),
            "config": V22_CONFIG,
        }


# ─── Entry Point Integration ────────────────────────────────────────────────
# 
# In your existing main.py, replace the v21 signal loop with:
#
#   engine = ForgeV22Engine(
#       risk_manager=risk_mgr,
#       evidence_logger=evidence,
#       brain=brain,
#       mode_manager=mode,
#       market_engine=market,
#       mt5=adapter,
#       telegram=tg_bot,
#   )
#   
#   # In your asyncio event loop:
#   await engine.scan_loop()
#
# The engine handles everything: signals, limits, runners, correlation.
# Your existing risk gates (forge_risk.py) still fire independently.
# Your existing evidence logger still records everything.
# Your existing Telegram commands still work.
