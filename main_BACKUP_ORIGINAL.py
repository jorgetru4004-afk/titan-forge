"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                      NEXUS CAPITAL — TITAN FORGE V21                        ║
║                          main.py — THE CONDUCTOR                            ║
║                                                                              ║
║  40 SETUPS. 5 INSTRUMENTS. 24-HOUR COVERAGE. 11 BAYESIAN DIMENSIONS.      ║
║  7 MULTI-FRAMEWORK LENSES. ADAPTIVE TRADE MIX. PARTIAL EXITS.            ║
║  DYNAMIC R:R. FAST MODE. CROSS-MARKET EXPLOIT. GENESIS EVOLUTION.         ║
║                                                                              ║
║  "Take risk but don't fail." — Jorge Trujillo                              ║
║                                                                              ║
║  The intelligence is the ACCELERATOR, not the brake.                       ║
║  FORGE calculates the PATH to $2,000 every morning and executes it.        ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from datetime import time as dtime
from typing import Optional

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("titan_forge.main")

# ═══════════════════════════════════════════════════════════════════════════════
# IMPORTS — V20 KEPT + V21 NEW
# ═══════════════════════════════════════════════════════════════════════════════

# V20 kept (unchanged)
from forge_core import (
    utc_to_et, now_et, now_et_time, is_rth, is_dst,
    get_session_state as get_rth_session_state,
    get_state_weight, session_minutes_remaining,
    SessionState, send_telegram, PriceCache, _price_cache,
    InstrumentTracker, MarketContext, fetch_market_context,
    EvidenceLogger, TradeFingerprint, _evidence,
    is_news_blackout as _v20_is_news_blackout, minutes_to_next_news,
    Signal as _V20Signal, SignalVerdict as _V20SignalVerdict,
    get_candle_store, detect_candlestick_pattern, get_m15_trend, get_h1_trend, m5_confirms_m1,
)
from forge_risk import (
    PropFirmState, RiskFortress, RiskDecision,
    camouflage_lot_size, camouflage_entry_delay,
    should_exit_time_decay, check_session_close_protection,
    compute_kelly_size, SessionMemory, pre_trade_checklist, GateResult,
)
from forge_market import (
    fetch_polygon_candles, get_correlation_engine, get_anomaly_detector,
    detect_gap,
)
from mt5_adapter import MT5Adapter
from execution_base import OrderRequest, OrderDirection, OrderType

# V21 NEW modules
from forge_brain_v21 import (
    compute_bayesian_conviction, BayesianConviction,
    compute_expected_value, ExpectedValueResult,
    monte_carlo_stress_test, StressTestResult,
    compute_price_entropy, compute_move_energy,
    detect_non_reaction, predict_regime_transition,
    ParameterEvolver, get_evolver, get_regime_mult,
)
from forge_signals_v21 import (
    generate_signal, SETUP_CONFIG, Signal, SignalVerdict,
)
from forge_sessions import (
    get_current_session, is_market_open, is_in_daily_break,
    should_force_close_nq, can_open_new_position, minutes_until_break,
    SESSION_PARAMS, SessionRiskTracker, is_news_blackout,
    get_session_state as get_24h_session_state, now_et as sessions_now_et,
    FTMO_ACCOUNT_TYPE,
)
from forge_instruments import (
    TrackerManager, SymbolResolver, InstrumentTracker as V21Tracker,
    ATR_DEFAULTS, POINT_VALUE, MIN_LOT, POLYGON_TICKERS,
    INSTRUMENT_SESSIONS, LIQUIDITY_TIER,
)
from forge_target import (
    DailyTargetEngine, SessionAdapter, PartialExitManager,
    PerformanceMonitor, CrossMarketExploit,
    dynamic_target, dynamic_stop,
    get_cycle_speed, collect_key_levels,
    SETUP_TRADE_TYPE, STRATEGY_TYPES,
)
from forge_genesis import auto_evolve, get_calibrated_wr
from forge_router import SmartOrderRouter, get_order_type

FORGE_VERSION = "v21"


# ═══════════════════════════════════════════════════════════════════════════════
# INSTRUMENT RESOLUTION — V21: expanded for CL + multi-instrument
# ═══════════════════════════════════════════════════════════════════════════════

_resolved_instruments: dict[str, Optional[str]] = {}


async def resolve_instrument(adapter: MT5Adapter, logical: str) -> Optional[str]:
    if logical in _resolved_instruments:
        return _resolved_instruments[logical]
    aliases = {
        "NAS100": ["US100.sim", "USTEC.sim", "NAS100.sim", "US100", "USTEC", "NAS100"],
        "EURUSD": ["EURUSD.sim", "EURUSD"],
        "US500":  ["US500.sim", "SPX500.sim", "SP500.sim", "US500"],
        "XAUUSD": ["XAUUSD.sim", "GOLD.sim", "XAUUSD"],
        "CL":     ["USOIL.sim", "WTI.sim", "XTIUSD.sim", "OIL.sim"],
    }
    for ticker in aliases.get(logical, [logical]):
        try:
            bid, ask = await adapter.get_current_price(ticker)
            if bid > 0:
                _resolved_instruments[logical] = ticker
                logger.info("[RESOLVE] %s → %s (bid=%.2f)", logical, ticker, bid)
                return ticker
        except Exception:
            continue
    _resolved_instruments[logical] = None
    logger.warning("[RESOLVE] No ticker for %s", logical)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# POSITION MANAGEMENT — V21: PARTIAL EXITS + TRAILING
# ═══════════════════════════════════════════════════════════════════════════════

async def manage_open_positions(
    adapter: MT5Adapter, account, ctx: MarketContext,
    partial_mgr: PartialExitManager, target_engine: DailyTargetEngine,
) -> None:
    for pos in account.open_positions:
        try:
            if pos.stop_loss is None or pos.entry_price is None:
                continue
            risk = abs(pos.entry_price - pos.stop_loss)
            if risk <= 0:
                continue

            _is_long = pos.direction.value == "long"
            current = pos.current_price or pos.entry_price
            if _is_long:
                current_r = (current - pos.entry_price) / risk
            else:
                current_r = (pos.entry_price - current) / risk

            # V21: Partial exits first
            inst = getattr(pos, 'instrument', '') or getattr(pos, 'symbol', '') or ''
            pv = 20.0  # default NQ
            for key, val in POINT_VALUE.items():
                if key.lower() in inst.lower():
                    pv = val
                    break
            locked = await partial_mgr.manage(adapter, pos, point_value=pv)
            if locked and locked > 0:
                target_engine.record_trade(locked, "")  # partial profit locked

            # Flag big runner for target engine
            if current_r >= 2.0:
                target_engine.flag_big_runner(True)

            # Trailing stops (on remaining position after partial)
            _be = pos.entry_price
            _trail_05r = pos.entry_price + risk * 0.5 if _is_long else pos.entry_price - risk * 0.5
            _trail_1r = pos.entry_price + risk * 1.0 if _is_long else pos.entry_price - risk * 1.0
            _trail_2r = pos.entry_price + risk * 2.0 if _is_long else pos.entry_price - risk * 2.0

            _current_sl = pos.stop_loss

            def _sl_is_better(new_sl: float) -> bool:
                if _is_long:
                    return new_sl > _current_sl + 0.5
                else:
                    return new_sl < _current_sl - 0.5

            new_sl = None
            if current_r >= 1.0 and _sl_is_better(_be):
                new_sl = _be
            if current_r >= 1.5 and _sl_is_better(_trail_05r):
                new_sl = _trail_05r
            if current_r >= 2.0 and _sl_is_better(_trail_1r):
                new_sl = _trail_1r
            if current_r >= 3.0 and _sl_is_better(_trail_2r):
                new_sl = _trail_2r

            if new_sl is not None:
                try:
                    await adapter.modify_position(pos.position_id, new_stop_loss=round(new_sl, 2))
                    logger.info("[TRAIL] %s %.1fR → SL=%.2f (was %.2f)",
                                pos.position_id, current_r, new_sl, _current_sl)
                except Exception as e:
                    logger.error("[TRAIL] %s modify failed: %s", pos.position_id, e)
        except Exception as e:
            logger.error("[TRAIL] Pos %s: %s", pos.position_id, e)


# ═══════════════════════════════════════════════════════════════════════════════
# PRE-FLIGHT CHECK — V21: expanded for multi-instrument
# ═══════════════════════════════════════════════════════════════════════════════

def run_simulation_check() -> bool:
    logger.info("═══ TITAN FORGE V21 — PRE-FLIGHT CHECK ═══")
    checks = 0
    total = 5

    token = os.environ.get("METAAPI_TOKEN", "")
    acct = os.environ.get("METAAPI_ACCOUNT_ID", os.environ.get("FTMO_ACCOUNT_ID", ""))
    if token and acct:
        checks += 1
        logger.info("✅ MetaAPI credentials present")
    else:
        logger.error("❌ Missing METAAPI_TOKEN or METAAPI_ACCOUNT_ID")

    if os.environ.get("TELEGRAM_BOT_TOKEN", ""):
        checks += 1
        logger.info("✅ Telegram token present")
    else:
        checks += 1
        logger.warning("⚠️ No Telegram — alerts disabled")

    firm = os.environ.get("ACTIVE_FIRM", "FTMO")
    checks += 1
    logger.info("✅ Active firm: %s | Account type: %s", firm, FTMO_ACCOUNT_TYPE)

    from pathlib import Path
    ev_dir = Path(os.environ.get("EVIDENCE_PATH", "/data/evidence"))
    try:
        ev_dir.mkdir(parents=True, exist_ok=True)
        checks += 1
        logger.info("✅ Evidence dir: %s", ev_dir)
    except Exception:
        Path.home().joinpath("forge_evidence").mkdir(parents=True, exist_ok=True)
        checks += 1
        logger.warning("⚠️ Fallback evidence dir")

    active_setups = sum(1 for c in SETUP_CONFIG.values() if c.get("signal_fn") != "disabled")
    checks += 1
    logger.info("✅ %d setups registered (%d active)", len(SETUP_CONFIG), active_setups)

    polygon_key = os.environ.get("POLYGON_API_KEY", "")
    if polygon_key:
        logger.info("✅ Polygon API key present")
    else:
        logger.warning("⚠️ No POLYGON_API_KEY — MTF disabled")

    cleared = checks >= 4
    logger.info("═══ %s (%d/%d) ═══", "CLEARED" if cleared else "FAILED", checks, total)
    return cleared


# ═══════════════════════════════════════════════════════════════════════════════
# THE LIVE TRADING LOOP — V21: 24-HOUR, MULTI-INSTRUMENT, ADAPTIVE
# ═══════════════════════════════════════════════════════════════════════════════

async def live_trading_loop(adapter: MT5Adapter) -> None:
    # ── V20 components (kept) ─────────────────────────────────────────
    tracker = InstrumentTracker()
    risk_fortress = RiskFortress()
    evolver = get_evolver()
    session_memory = SessionMemory()
    candle_store = get_candle_store()
    anomaly_detector = get_anomaly_detector()
    corr_engine = get_correlation_engine()

    # ── V21 components (new) ──────────────────────────────────────────
    target_engine = DailyTargetEngine(target=2000.0)
    session_adapter = SessionAdapter()
    partial_mgr = PartialExitManager()
    perf_monitor = PerformanceMonitor()
    session_risk = SessionRiskTracker()
    cross_exploit = CrossMarketExploit()
    order_router = SmartOrderRouter()

    firm_id = os.environ.get("ACTIVE_FIRM", "FTMO")
    firm_state = PropFirmState(firm_id=firm_id, initial_balance=100_000,
                                current_balance=100_000, highest_eod_balance=100_000)
    firm_state.daily_start_balance = 100_000

    try:
        _init_acc = await adapter.get_account_state()
        if _init_acc.balance > 0:
            firm_state.initialize(_init_acc.balance)
            firm_state.reset_daily(_init_acc.balance)
            logger.info("[INIT] Live balance: $%.2f", _init_acc.balance)
    except Exception as e:
        logger.warning("[INIT] Could not fetch balance: %s", e)

    traded_setups: set[str] = set()
    _setup_cooldowns: dict[str, float] = {}
    COOLDOWN_SECONDS = 180
    last_session_date = date.today()
    cycle = 0
    ctx = MarketContext()
    atr_session_high = 0.0
    atr_session_low = float("inf")
    _last_m5_fetch = datetime.min.replace(tzinfo=timezone.utc)
    _last_m15_fetch = datetime.min.replace(tzinfo=timezone.utc)
    _last_target_update = 0.0

    # V21: Resolve all instruments at boot
    logger.info("🔱 RESOLVING INSTRUMENTS...")
    for inst in ["NAS100", "XAUUSD", "EURUSD", "CL", "US500"]:
        resolved = await resolve_instrument(adapter, inst)
        if resolved:
            logger.info("  ✅ %s → %s", inst, resolved)
        else:
            logger.warning("  ⚠️ %s — no valid symbol", inst)

    active_count = sum(1 for c in SETUP_CONFIG.values() if c.get("signal_fn") != "disabled")
    logger.info("🔱 TITAN FORGE %s — %d SETUPS ARMED. 24-HOUR MODE.", FORGE_VERSION, active_count)
    send_telegram(
        f"🔱 <b>TITAN FORGE {FORGE_VERSION} ONLINE</b>\n"
        f"{active_count} setups | 5 instruments | 24-hour\n"
        f"Account type: {FTMO_ACCOUNT_TYPE}\n"
        f"The full arsenal is armed."
    )

    try:
        ctx = fetch_market_context()
        logger.info("[INIT] VIX=%.1f (%s) Futures=%s ATR=%.0f",
                     ctx.vix, ctx.vix_regime, ctx.futures_bias, ctx.atr)
    except Exception as e:
        logger.warning("[INIT] Context fetch failed: %s", e)
        ctx = MarketContext()

    while True:
        cycle += 1
        try:
            today = date.today()

            # ── DAILY RESET ──────────────────────────────────────────
            if today != last_session_date:
                last_session_date = today
                tracker.reset()
                traded_setups.clear()
                _setup_cooldowns.clear()
                risk_fortress.reset_daily()
                session_memory.reset()
                session_risk.reset_daily()
                target_engine.reset_daily()
                session_adapter.reset()
                partial_mgr.reset()
                perf_monitor.end_of_day()
                cross_exploit = CrossMarketExploit()
                atr_session_high = 0.0
                atr_session_low = float("inf")

                try:
                    ctx = fetch_market_context()
                except Exception:
                    pass

                try:
                    acc = await adapter.get_account_state()
                    if acc.balance > 0:
                        firm_state.initialize(acc.balance)
                        firm_state.reset_daily(acc.balance)
                        risk_fortress.reset_weekly(acc.balance)
                except Exception:
                    pass

                # V21: GENESIS auto-evolution
                try:
                    evolve_result = auto_evolve(SETUP_CONFIG, send_telegram)
                    logger.info("[EVOLVE] %s", evolve_result.get("status", "unknown"))
                except Exception as e:
                    logger.warning("[EVOLVE] Failed: %s", e)

                try:
                    evidence = _evidence.get_recent_trades(30)
                    evolver.update_from_evidence(evidence)
                    deg = evolver.get_degradation_alert()
                    if deg:
                        logger.warning("[EVOLVE] %s", deg)
                        send_telegram(f"⚠️ <b>DEGRADATION</b>\n{deg}")
                except Exception:
                    pass

                _session = get_current_session()
                _regime = ctx.regime if hasattr(ctx, 'regime') else "NORMAL"
                plan = target_engine.get_plan(_regime)

                send_telegram(
                    f"🔱 <b>FORGE {FORGE_VERSION} — MORNING BRIEF</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📅 {today} ({ctx.day_name})\n"
                    f"💰 Balance: ${firm_state.current_balance:,.2f}\n"
                    f"🏢 Firm: {firm_id} ({FTMO_ACCOUNT_TYPE})\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 VIX: {ctx.vix:.1f} ({ctx.vix_regime})\n"
                    f"📈 Futures: {ctx.futures_pct*100:+.2f}% ({ctx.futures_bias})\n"
                    f"📏 PDH: {ctx.pdh:.0f} | PDL: {ctx.pdl:.0f}\n"
                    f"📐 ATR: {ctx.atr:.0f}\n"
                    f"🎯 Target: ${plan.target:.0f} | Mode: {plan.mode}\n"
                    f"🔫 {active_count} setups armed | 24-hour mode"
                )

            # ── MARKET HOURS — V21: 24-hour ──────────────────────────
            if not is_market_open():
                logger.info("[Cycle %d] Market closed or daily break.", cycle)
                await asyncio.sleep(60)
                continue

            # ── V21: Force close before daily break (standard accounts) ──
            if should_force_close_nq():
                try:
                    account = await adapter.get_account_state()
                    if account.open_position_count > 0:
                        logger.warning("[BREAK] Forcing NQ close before 17:00 ET break")
                        send_telegram("⚠️ <b>DAILY BREAK</b>\nClosing NQ positions before 17:00 ET")
                        await adapter.close_all_positions()
                except Exception as e:
                    logger.error("[BREAK] Force close failed: %s", e)
                await asyncio.sleep(60)
                continue

            # ── HEALTH ────────────────────────────────────────────────
            try:
                health = await adapter.health_check()
                if not health.is_healthy:
                    logger.warning("[Cycle %d] Unhealthy: %s", cycle, health.error)
                    await asyncio.sleep(30)
                    continue
            except Exception as e:
                logger.warning("[Cycle %d] Health: %s", cycle, e)
                await asyncio.sleep(30)
                continue

            # ── ACCOUNT ──────────────────────────────────────────────
            try:
                account = await adapter.get_account_state()
            except Exception as e:
                logger.error("[Cycle %d] Account: %s", cycle, e)
                await asyncio.sleep(30)
                continue

            if account.balance <= 0:
                logger.warning("[Cycle %d] Balance=0", cycle)
                await asyncio.sleep(30)
                continue

            firm_state.current_balance = account.balance
            firm_state.current_day_pnl = account.daily_pnl

            _24h_session = get_current_session()
            _session_params = SESSION_PARAMS.get(_24h_session, SESSION_PARAMS["RTH"])

            logger.info("[Cycle %d] %s | Bal=$%.2f Eq=$%.2f PnL=$%.2f Pos=%d",
                         cycle, _24h_session, account.balance, account.equity,
                         account.daily_pnl, account.open_position_count)

            # ── V21: Session risk budget check ───────────────────────
            can_trade_session, budget_msg = session_risk.can_trade_session(_24h_session)
            if not can_trade_session:
                logger.info("[Cycle %d] %s", cycle, budget_msg)
                await asyncio.sleep(60)
                continue

            # ── PRICE TRACKING ───────────────────────────────────────
            primary_inst = await resolve_instrument(adapter, "NAS100")
            if primary_inst:
                try:
                    bid, ask = await adapter.get_current_price(primary_inst)
                    if bid > 0:
                        _price_cache.update(primary_inst, bid, ask)
                        tracker.update(bid, ask, ctx)
                        if is_rth():
                            m = (bid + ask) / 2.0
                            if m > atr_session_high:
                                atr_session_high = m
                            if m < atr_session_low:
                                atr_session_low = m
                            if ctx.atr > 0 and atr_session_high > 0 and atr_session_low < float("inf"):
                                ctx.atr_consumed_pct = (atr_session_high - atr_session_low) / ctx.atr

                        corr_engine.update("NAS100", (bid + ask) / 2.0)
                        anomaly_detector.update((bid + ask) / 2.0, ctx.vix)
                except Exception as e:
                    logger.warning("[Cycle %d] Price err: %s", cycle, e)

            # V21: Track ES price for cross-market exploit
            es_inst = await resolve_instrument(adapter, "US500")
            if es_inst:
                try:
                    es_bid, es_ask = await adapter.get_current_price(es_inst)
                    if es_bid > 0:
                        cross_exploit.update_es((es_bid + es_ask) / 2.0)
                except Exception:
                    pass

            # Staggered Polygon candle fetching
            now_utc = datetime.now(timezone.utc)
            if is_rth():
                _fetched_any = False
                if (now_utc - _last_m5_fetch).total_seconds() >= 300:
                    try:
                        fetch_polygon_candles("NAS100", ["M5"])
                        _last_m5_fetch = now_utc
                        _fetched_any = True
                    except Exception as e:
                        logger.warning("[POLYGON] M5 fetch failed: %s", e)
                if (now_utc - _last_m15_fetch).total_seconds() >= 900:
                    try:
                        fetch_polygon_candles("NAS100", ["M15"])
                        _last_m15_fetch = now_utc
                        _fetched_any = True
                    except Exception as e:
                        logger.warning("[POLYGON] M15 fetch failed: %s", e)

                if _fetched_any:
                    m15_candles = candle_store.get("NAS100", "M15", 6)
                    h1_candles = candle_store.get("NAS100", "H1", 5)
                    ctx.mtf_trend_m15 = get_m15_trend(m15_candles)
                    ctx.mtf_trend_h1 = get_h1_trend(h1_candles)

            ctx.session_state = get_rth_session_state()
            ctx.minutes_remaining = session_minutes_remaining()
            ctx.sync_from_tracker(tracker)

            # ── MANAGE POSITIONS — V21: with partials ────────────────
            await manage_open_positions(adapter, account, ctx, partial_mgr, target_engine)

            # ── SESSION CLOSE ─────────────────────────────────────────
            should_close, close_reason = check_session_close_protection(firm_state)
            if should_close and account.open_position_count > 0:
                logger.warning("[CLOSE] %s", close_reason)
                send_telegram(f"📅 <b>SESSION CLOSE</b>\n{close_reason}")
                try:
                    await adapter.close_all_positions()
                except Exception as e:
                    logger.error("[CLOSE] %s", e)
                await asyncio.sleep(60)
                continue

            # ── NEWS BLACKOUT — V21: per-instrument ──────────────────
            # Check global blackout for primary instrument
            _in_blackout = _v20_is_news_blackout()

            # ── EMERGENCY ─────────────────────────────────────────────
            if firm_state.should_emergency_close(account.equity):
                logger.warning("[EMERGENCY] Near limit — closing all")
                send_telegram("🚨 <b>EMERGENCY</b>\nClosing ALL")
                try:
                    await adapter.close_all_positions()
                except Exception:
                    pass
                await asyncio.sleep(60)
                continue

            # ── POSITION LIMIT: 5 simultaneous ───────────────────────
            if account.open_position_count >= 5:
                logger.info("[Cycle %d] Max positions (5/5).", cycle)
                await asyncio.sleep(60)
                continue

            # ── ANOMALY CHECK ─────────────────────────────────────────
            anomaly = anomaly_detector.check()
            if anomaly:
                logger.warning("[ANOMALY] %s — pausing 1 cycle", anomaly)
                send_telegram(f"⚠️ <b>ANOMALY</b>\n{anomaly}")
                await asyncio.sleep(60)
                continue

            # ── V21: Performance auto-adjust ──────────────────────────
            perf_size_mult, perf_mode = perf_monitor.get_size_adjustment(account.daily_pnl)
            if perf_mode != "NORMAL":
                logger.info("[MONITOR] Mode: %s (%.0fx)", perf_mode, perf_size_mult)

            # ═══════════════════════════════════════════════════════════
            # DECISION PIPELINE V21 — THE FULL ARSENAL
            # ═══════════════════════════════════════════════════════════
            _all_candidates = []
            _cycle_signals = []

            mid = _price_cache.get_mid(primary_inst or "") or (
                tracker.price_history[-1] if tracker.price_history else 0)
            if mid <= 0:
                await asyncio.sleep(60)
                continue

            atr = ctx.atr if ctx.atr > 0 else 100.0

            # ── Regime Detection (with REVERSAL) ─────────────────────
            _regime = "NORMAL"
            _regime_bias = "neutral"
            ib_range = (tracker.ib_high - tracker.ib_low) if (
                tracker.ib_locked and tracker.ib_low != float("inf")) else 0

            if tracker.ib_locked and ib_range > 0:
                if ib_range >= atr * 0.6:
                    _regime = "TREND"
                elif ib_range <= atr * 0.3:
                    _regime = "CHOP"

                if tracker.ib_direction == "long":
                    _regime_bias = "long"
                elif tracker.ib_direction == "short":
                    _regime_bias = "short"

                if mid > tracker.ib_high + 2.0:
                    _regime_bias = "long"
                elif mid < tracker.ib_low - 2.0:
                    _regime_bias = "short"

            # REVERSAL detection
            if _regime == "TREND" and tracker.ib_direction:
                if tracker.ib_direction == "long" and mid < tracker.ib_low:
                    _regime = "REVERSAL"
                    _regime_bias = "short"
                elif tracker.ib_direction == "short" and mid > tracker.ib_high:
                    _regime = "REVERSAL"
                    _regime_bias = "long"

            _loop_vwap = tracker.vwap or tracker.open_price or mid
            if _regime_bias == "neutral" and _loop_vwap and _loop_vwap > 0:
                if mid > _loop_vwap * 1.003:
                    _regime_bias = "long"
                elif mid < _loop_vwap * 0.997:
                    _regime_bias = "short"

            ctx.regime = _regime
            ctx.regime_bias = _regime_bias

            # ── V21: Daily target plan ────────────────────────────────
            plan = target_engine.get_plan(_regime, ctx.minutes_remaining / 60.0,
                                           atr, ctx.atr_consumed_pct)

            # V21: Hourly target Telegram update
            if time.time() - _last_target_update >= 3600:
                _last_target_update = time.time()
                send_telegram(f"🎯 {target_engine.telegram_update()}")

            # ── V21: Fast mode at key levels ──────────────────────────
            key_levels = collect_key_levels(tracker, ctx)
            cycle_speed = get_cycle_speed(mid, key_levels, atr)

            # ── V21: MTF from candle store ────────────────────────────
            m5_candles = candle_store.get("NAS100", "M5", 3)

            # ── SCAN ALL SETUPS ───────────────────────────────────────
            for setup_id, config in SETUP_CONFIG.items():
                # Skip disabled
                if config.get("signal_fn") == "disabled":
                    continue

                # V21: Session filter — only fire during assigned sessions
                allowed_sessions = config.get("sessions", ["RTH"])
                if _24h_session not in allowed_sessions:
                    continue

                # Cooldown
                _last_trade_time = _setup_cooldowns.get(setup_id, 0)
                if time.time() - _last_trade_time < COOLDOWN_SECONDS:
                    continue

                # ATR exhaustion filter
                if is_rth() and ctx.atr_consumed_pct > 0.95 and setup_id not in ("VOL-06", "VOL-05"):
                    continue

                # Regime suppression
                regime_m = get_regime_mult(setup_id, _regime)
                if regime_m <= 0.0:
                    continue

                # V21: Instrument availability check
                inst_key = config.get("instrument", "NAS100")
                can_open, reason = can_open_new_position(inst_key)
                if not can_open:
                    continue

                # V21: Per-instrument news blackout
                inst_blackout, blackout_reason = is_news_blackout(inst_key)
                if inst_blackout:
                    continue

                # ── GENERATE SIGNAL (ONE condition per setup) ─────────
                signal = generate_signal(
                    setup_id, config, mid, tracker, ctx, atr,
                    cross_market_exploit=cross_exploit,
                )
                if signal.verdict != SignalVerdict.CONFIRMED:
                    continue

                # MTF confirmation for this direction
                ctx.mtf_m5_confirms = m5_confirms_m1(signal.direction, m5_candles)

                # V21: GENESIS calibrated WR
                calibrated = get_calibrated_wr(config["base_win_rate"]) if config["base_win_rate"] > 0 else None

                # ── BAYESIAN CONVICTION (BOOSTED) ─────────────────────
                live_wr = evolver.get_live_win_rate(setup_id)
                conviction = compute_bayesian_conviction(
                    prior_win_rate=config["base_win_rate"],
                    ctx=ctx, tracker=tracker,
                    direction=signal.direction, setup_id=setup_id,
                    live_win_rate=live_wr,
                    calibrated_wr=calibrated,
                )

                _cycle_signals.append(
                    f"{setup_id} {signal.direction} → {conviction.conviction_level} "
                    f"({conviction.posterior:.0%}, {conviction.confirming}/{conviction.total})"
                )

                if conviction.posterior < 0.35 or conviction.conviction_level == "REJECT":
                    _evidence.log_trade(TradeFingerprint(
                        trade_id=f"PH-{uuid.uuid4().hex[:8]}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        setup_id=setup_id, instrument=config["instrument"],
                        direction=signal.direction or "", entry_price=signal.entry_price or 0,
                        stop_loss=signal.stop_loss or 0, take_profit=signal.take_profit or 0,
                        firm_id=firm_id, vix=ctx.vix, vix_regime=ctx.vix_regime,
                        bayesian_posterior=conviction.posterior,
                        conviction_level=conviction.conviction_level,
                        regime=_regime, regime_bias=_regime_bias,
                        is_phantom=True, outcome="PHANTOM", capital_vehicle="PROP_FIRM",
                    ))
                    continue

                # 5-gate checklist
                risk_dollars = abs((signal.entry_price or 0) - (signal.stop_loss or 0)) * config["base_size"] * POINT_VALUE.get(inst_key, 20)
                open_risk = risk_dollars * account.open_position_count

                gate_result = pre_trade_checklist(
                    setup_id=setup_id, direction=signal.direction or "",
                    regime=_regime, ctx=ctx,
                    risk_dollars=risk_dollars, account_equity=account.equity,
                    open_risk=open_risk, minutes_remaining=ctx.minutes_remaining,
                    expected_hold_min=config.get("expected_hold_min", 30),
                    session_memory=session_memory, regime_mult=regime_m,
                )
                if not gate_result.all_pass:
                    _evidence.log_trade(TradeFingerprint(
                        trade_id=f"GF-{uuid.uuid4().hex[:8]}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        setup_id=setup_id, instrument=config["instrument"],
                        direction=signal.direction or "", entry_price=signal.entry_price or 0,
                        stop_loss=signal.stop_loss or 0, take_profit=signal.take_profit or 0,
                        firm_id=firm_id, bayesian_posterior=conviction.posterior,
                        conviction_level=conviction.conviction_level,
                        gate_failed=gate_result.failed_gate,
                        regime=_regime, regime_bias=_regime_bias,
                        is_phantom=True, outcome="GATE_FAIL", capital_vehicle="PROP_FIRM",
                    ))
                    continue

                # Session memory scalp-only enforcement
                _is_scalp = conviction.conviction_level == "SCALP"
                if session_memory.is_scalp_only and not _is_scalp:
                    if conviction.conviction_level not in ("ELITE", "HIGH"):
                        continue

                # V21: Dynamic R:R from ATR remaining
                if signal.direction and signal.entry_price:
                    dyn_tp = dynamic_target(signal.direction, signal.entry_price,
                                             atr, ctx.atr_consumed_pct, _regime)
                    # Use dynamic TP if it's more aggressive than signal's TP
                    if signal.direction == "long" and dyn_tp > (signal.take_profit or 0):
                        signal = Signal(signal.setup_id, signal.verdict, signal.direction,
                                        signal.entry_price, signal.stop_loss, dyn_tp,
                                        signal.conviction, signal.reason)
                    elif signal.direction == "short" and dyn_tp < (signal.take_profit or 999999):
                        signal = Signal(signal.setup_id, signal.verdict, signal.direction,
                                        signal.entry_price, signal.stop_loss, dyn_tp,
                                        signal.conviction, signal.reason)

                # Scalp override SL/TP
                ep = signal.entry_price or mid
                if _is_scalp and "NAS100" in config.get("instrument", ""):
                    scalp_sl, scalp_tp = 25.0, 35.0
                    if signal.direction == "long":
                        signal = Signal(signal.setup_id, signal.verdict, signal.direction,
                                        ep, round(ep - scalp_sl, 2), round(ep + scalp_tp, 2),
                                        signal.conviction, f"SCALP {signal.reason}")
                    else:
                        signal = Signal(signal.setup_id, signal.verdict, signal.direction,
                                        ep, round(ep + scalp_sl, 2), round(ep - scalp_tp, 2),
                                        signal.conviction, f"SCALP {signal.reason}")

                reward_dollars = abs((signal.take_profit or 0) - (signal.entry_price or 0)) * config["base_size"] * POINT_VALUE.get(inst_key, 20)
                if risk_dollars <= 0 or reward_dollars <= 0:
                    continue

                ev_result = compute_expected_value(
                    win_prob=conviction.posterior, reward_dollars=reward_dollars,
                    risk_dollars=risk_dollars, account_balance=account.balance,
                    max_position_pct=0.02, minutes_remaining=ctx.minutes_remaining,
                )

                if ev_result.ev_dollars < order_router.get_ev_threshold():
                    continue

                tier_weight = {"ELITE": 4.0, "HIGH": 3.0, "STANDARD": 2.0,
                              "REDUCED": 1.0, "SCALP": 0.5}.get(conviction.conviction_level, 0.5)
                weighted_ev = ev_result.net_ev * tier_weight

                # V21: Session adapter boost/penalty
                sa_mult = session_adapter.get_multiplier(setup_id)
                # V21: Target engine boost for trade types that are working
                te_mult = target_engine.get_size_adjustment(setup_id)

                _all_candidates.append({
                    "setup_id": setup_id, "config": config, "signal": signal,
                    "conviction": conviction, "ev_result": ev_result,
                    "is_scalp": _is_scalp, "direction": signal.direction,
                    "weighted_ev": weighted_ev * sa_mult * te_mult,
                    "sa_mult": sa_mult, "te_mult": te_mult,
                })

            # ── Anti-Conflict Filter ──────────────────────────────────
            if _all_candidates:
                _best_posterior = max(c["conviction"].posterior for c in _all_candidates)
                _best_direction = None
                for c in _all_candidates:
                    if c["conviction"].posterior == _best_posterior:
                        _best_direction = c["direction"]
                        break

                if _best_direction and _best_posterior >= 0.55:
                    _filtered = [c for c in _all_candidates
                                if not (c["is_scalp"] and c["direction"] != _best_direction)]
                    _all_candidates = _filtered

                if _regime_bias != "neutral":
                    _filtered2 = []
                    for c in _all_candidates:
                        _is_weak = c["conviction"].conviction_level in ("SCALP", "REDUCED")
                        if _is_weak and c["direction"] != _regime_bias:
                            continue
                        _filtered2.append(c)
                    _all_candidates = _filtered2

            best_action = None
            if _all_candidates:
                _all_candidates.sort(key=lambda c: c["weighted_ev"], reverse=True)
                best = _all_candidates[0]
                cl = best["conviction"].conviction_level
                _size_mult = {"ELITE": 1.0, "HIGH": 0.75, "STANDARD": 0.50,
                             "REDUCED": 0.30, "SCALP": 0.25}.get(cl, 0.25)
                best_action = best

            if _cycle_signals:
                logger.info("[Cycle %d][%s|%s|%s] Signals: %s", cycle, _24h_session, _regime, _regime_bias,
                           " | ".join(_cycle_signals[:5]))

            # ── EXECUTE ───────────────────────────────────────────────
            if best_action is None:
                await asyncio.sleep(cycle_speed)
                continue

            if _in_blackout:
                logger.info("[Cycle %d] %s blocked by NEWS BLACKOUT", cycle, best_action["setup_id"])
                await asyncio.sleep(cycle_speed)
                continue

            setup_id = best_action["setup_id"]
            config = best_action["config"]
            signal = best_action["signal"]
            conviction = best_action["conviction"]
            ev_result = best_action["ev_result"]
            _is_scalp = best_action["is_scalp"]

            cl_level = conviction.conviction_level
            _size_mult = {"ELITE": 1.0, "HIGH": 0.75, "STANDARD": 0.50,
                         "REDUCED": 0.30, "SCALP": 0.25}.get(cl_level, 0.25)

            # ═══ PRE-EXECUTION SAFETY GATES ═══════════════════════════

            # REJECT hard block
            if conviction.conviction_level == "REJECT":
                await asyncio.sleep(cycle_speed)
                continue

            inst_key = config.get("instrument", "NAS100")
            _target_inst = await resolve_instrument(adapter, inst_key)
            if not _target_inst:
                await asyncio.sleep(cycle_speed)
                continue

            # Block opposite direction on same instrument
            _direction_conflict = False
            if account.open_positions:
                for pos in account.open_positions:
                    _pos_inst = getattr(pos, 'instrument', None) or getattr(pos, 'symbol', None)
                    if _pos_inst == _target_inst:
                        pos_dir = pos.direction.value if hasattr(pos.direction, 'value') else str(pos.direction)
                        if pos_dir != signal.direction:
                            _direction_conflict = True
                            break
            if _direction_conflict:
                await asyncio.sleep(cycle_speed)
                continue

            # Max exposure 1.0 lots per instrument per direction
            _current_exposure = 0.0
            if account.open_positions:
                for pos in account.open_positions:
                    _pos_inst = getattr(pos, 'instrument', None) or getattr(pos, 'symbol', None)
                    if _pos_inst == _target_inst:
                        pos_dir = pos.direction.value if hasattr(pos.direction, 'value') else str(pos.direction)
                        if pos_dir == signal.direction:
                            _current_exposure += getattr(pos, 'size', 0) or getattr(pos, 'volume', 0) or 0

            # Sanity checks
            _entry = signal.entry_price or 0
            _sl = signal.stop_loss or 0
            _tp = signal.take_profit or 0
            _sl_dist = abs(_entry - _sl)
            _tp_dist = abs(_entry - _tp)
            _inst_atr = config.get("atr_default", 100)

            _sanity_fail = None
            if _tp_dist > _inst_atr * 2:
                _sanity_fail = f"TP unreachable: {_tp_dist:.0f}pts > {_inst_atr*2:.0f}"
            elif _sl_dist < 5.0 and inst_key == "NAS100":
                _sanity_fail = f"SL too tight: {_sl_dist:.1f}pts"
            elif _sl_dist > _inst_atr * 1.0:
                _sanity_fail = f"SL too wide: {_sl_dist:.0f}pts"
            elif ev_result.ev_dollars > 500:
                _sanity_fail = f"EV suspicious: ${ev_result.ev_dollars:.0f}"

            if _sanity_fail:
                logger.warning("[SANITY] %s: %s", setup_id, _sanity_fail)
                await asyncio.sleep(cycle_speed)
                continue

            # Risk fortress
            risk_decision = risk_fortress.evaluate(
                firm_state=firm_state, equity=account.equity,
                daily_pnl=account.daily_pnl, balance=account.balance, setup_id=setup_id,
            )
            if not risk_decision.can_trade:
                await asyncio.sleep(cycle_speed)
                continue

            # Stress test
            risk_dollars = abs(_entry - _sl) * config["base_size"] * POINT_VALUE.get(inst_key, 20)
            stress = monte_carlo_stress_test(
                current_pnl=account.daily_pnl, proposed_risk=risk_dollars,
                win_prob=conviction.posterior, current_positions=account.open_position_count,
                open_risk=risk_dollars * account.open_position_count,
                daily_limit=firm_state.daily_loss_limit,
                max_loss=firm_state.initial_balance * firm_state.total_loss_pct,
                current_equity=account.equity, vix=ctx.vix,
            )
            if not stress.risk_approved:
                logger.warning("[Cycle %d][%s] STRESS: %s", cycle, setup_id, stress.reason)
                await asyncio.sleep(cycle_speed)
                continue

            # ── LOT SIZING — V21: session + target + performance adjusted ──
            session_size_mult = session_memory.size_multiplier
            conv_mult = {"ELITE": 1.5, "HIGH": 1.2, "STANDARD": 1.0,
                         "REDUCED": 0.7, "SCALP": 0.5}.get(cl_level, 0.3)

            # V21: Apply session params, target engine, session adapter, perf monitor
            _session_size = _session_params["size_mult"]
            _sa_mult = best_action.get("sa_mult", 1.0)
            _te_mult = best_action.get("te_mult", 1.0)
            _plan_size = plan.size_mult

            lot_size = compute_kelly_size(
                win_prob=conviction.posterior,
                reward_risk_ratio=ev_result.reward_risk_ratio,
                account_balance=account.balance,
                base_lot_size=config["base_size"],
                firm_max_risk_pct=0.02,
                risk_multiplier=(risk_decision.size_multiplier * _size_mult *
                                session_size_mult * _session_size * _sa_mult *
                                _te_mult * _plan_size * perf_size_mult),
                vix_multiplier=ctx.vix_size_mult,
                day_multiplier=ctx.day_strength,
                conviction_mult=conv_mult,
            )

            min_lot = MIN_LOT.get(inst_key, 0.10)
            lot_size = max(min_lot, round(round(lot_size / min_lot) * min_lot, 2))
            lot_size = min(lot_size, 2.0)

            # Exposure cap
            _max_inst_exposure = 1.0
            _remaining_exposure = _max_inst_exposure - _current_exposure
            if _remaining_exposure <= 0:
                await asyncio.sleep(cycle_speed)
                continue
            if lot_size > _remaining_exposure:
                lot_size = round(round(_remaining_exposure / min_lot) * min_lot, 2)
                if lot_size < min_lot:
                    await asyncio.sleep(cycle_speed)
                    continue

            _trade_mode = "SCALP" if _is_scalp else cl_level

            # SL too tight fix for NQ
            if _sl_dist < 5.0 and inst_key == "NAS100":
                ep = signal.entry_price or 0
                if signal.direction == "long":
                    signal = Signal(signal.setup_id, signal.verdict, signal.direction,
                                    ep, round(ep - 5.0, 2), round(ep + 10.0, 2),
                                    signal.conviction, signal.reason)
                else:
                    signal = Signal(signal.setup_id, signal.verdict, signal.direction,
                                    ep, round(ep + 5.0, 2), round(ep - 10.0, 2),
                                    signal.conviction, signal.reason)

            delay = camouflage_entry_delay()
            await asyncio.sleep(delay)

            trade_id = f"TF-{uuid.uuid4().hex[:6]}"
            order = OrderRequest(
                instrument=_target_inst,
                direction=OrderDirection.LONG if signal.direction == "long" else OrderDirection.SHORT,
                size=lot_size, order_type=OrderType.MARKET,
                stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                comment=f"{setup_id}|{trade_id}|{conviction.posterior:.0%}",
            )

            _order_type_str = get_order_type(setup_id)
            logger.info("🔫 [%s][%s][%s][%s] %s %s %.2f lots | E=%.2f SL=%.2f TP=%.2f | "
                         "P=%.0f%% EV=$%.0f | R=%s | SA=%.2f TE=%.2f",
                         setup_id, _trade_mode, _24h_session, _order_type_str,
                         (signal.direction or "").upper(), _target_inst, lot_size,
                         signal.entry_price or 0, signal.stop_loss or 0, signal.take_profit or 0,
                         conviction.posterior * 100, ev_result.ev_dollars,
                         _regime, _sa_mult, _te_mult)

            result = await order_router.execute(
                adapter=adapter,
                order_request=order,
                setup_id=setup_id,
                conviction_level=cl_level,
                conviction_posterior=conviction.posterior,
                instrument_key=inst_key,
                signal_entry=signal.entry_price or mid,
            )

            if result.status.value == "filled":
                logger.info("✅ FILLED: %s @ %.5f", result.order_id, result.fill_price)
                traded_setups.add(setup_id)
                _setup_cooldowns[setup_id] = time.time()
                firm_state.record_size(lot_size)

                send_telegram(
                    f"🔫 <b>TRADE — {_trade_mode}</b>\n"
                    f"{setup_id} ({config['name']})\n"
                    f"{(signal.direction or '').upper()} {_target_inst} @ {result.fill_price:.2f}\n"
                    f"Size: {lot_size} | SL: {signal.stop_loss:.2f} | TP: {signal.take_profit:.2f}\n"
                    f"{conviction.conviction_level} ({conviction.posterior:.0%}) | EV: ${ev_result.ev_dollars:.0f}\n"
                    f"Regime: {_regime} ({_regime_bias}) | Session: {_24h_session}\n"
                    f"🎯 Target: ${plan.remaining:.0f} remaining"
                )

                _evidence.log_trade(TradeFingerprint(
                    trade_id=trade_id, timestamp=datetime.now(timezone.utc).isoformat(),
                    setup_id=setup_id, instrument=config["instrument"],
                    direction=signal.direction or "",
                    entry_price=result.fill_price or signal.entry_price or 0,
                    stop_loss=signal.stop_loss or 0, take_profit=signal.take_profit or 0,
                    lot_size=lot_size, firm_id=firm_id,
                    vix=ctx.vix, vix_regime=ctx.vix_regime,
                    futures_bias=ctx.futures_bias, futures_pct=ctx.futures_pct,
                    session_state=ctx.session_state.value, day_of_week=ctx.day_name,
                    atr=atr, atr_pct_consumed=ctx.atr_consumed_pct,
                    ib_direction=ctx.ib_direction, pdh=ctx.pdh, pdl=ctx.pdl,
                    regime=_regime, regime_bias=_regime_bias,
                    mtf_m15_trend=ctx.mtf_trend_m15, mtf_h1_trend=ctx.mtf_trend_h1,
                    mtf_m5_confirms=ctx.mtf_m5_confirms,
                    bayesian_posterior=conviction.posterior,
                    confluence_score=conviction.confirming,
                    expected_value=ev_result.ev_dollars,
                    conviction_level=conviction.conviction_level,
                    capital_vehicle="PROP_FIRM",
                ))
            else:
                logger.warning("❌ FAILED: %s — %s", result.status, result.error_message)

        except Exception as e:
            logger.error("[Cycle %d] Error: %s", cycle, e, exc_info=True)

        await asyncio.sleep(cycle_speed)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  NEXUS CAPITAL — TITAN FORGE %s                         ║", FORGE_VERSION)
    logger.info("║  40 SETUPS | 5 INSTRUMENTS | 24-HOUR | FULL ARSENAL     ║")
    logger.info("╚══════════════════════════════════════════════════════════╝")

    cleared = run_simulation_check()
    if not cleared:
        logger.error("Pre-flight failed. Exiting.")
        return

    account_id = os.environ.get("METAAPI_ACCOUNT_ID",
                                os.environ.get("FTMO_ACCOUNT_ID", ""))
    adapter = MT5Adapter(
        account_id=account_id, server="OANDA-Demo-1",
        password="", is_demo=os.environ.get("FTMO_IS_DEMO", "true").lower() == "true",
    )

    logger.info("Connecting to MetaAPI...")
    connected = await adapter.connect()
    if connected:
        logger.info("✅ Connected.")
    else:
        logger.error("❌ Connection failed.")
        return

    await live_trading_loop(adapter)


if __name__ == "__main__":
    asyncio.run(main())
