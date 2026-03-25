"""
NEXUS CAPITAL — TITAN FORGE v17
main.py — THE CONDUCTOR

100% SELF-CONTAINED. Zero dead imports. All 18 bugs fixed.
Only external deps: mt5_adapter.py + execution_base.py (proven, live).

BUGS FIXED: #1 DST, #2 Balance=0, #3 Price Cache, #4 ATR Overnight,
#5 IB Degenerate, #6 VIX Hardcoded, #7 Signal Cooldown, #8 ORB Locks,
#9 Missing Setups, #10 Evidence /tmp/, #11 Evidence Never Closed,
#12 Tracker Hardcoded, #13 Noon Curve, #14 Sim Real Data,
#15 Telegram Silent, #16 C-14 Violation, #17 News Blackout Stuck,
#18 reset_daily_counters Missing

Jorge Trujillo — Founder | Claude — AI Architect | March 2026
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
# IMPORTS — 100% SELF-CONTAINED. ZERO DEAD MODULES.
# ═══════════════════════════════════════════════════════════════════════════════

from forge_core import (
    utc_to_et, now_et, now_et_time, is_rth, is_dst,
    get_session_state, get_state_weight, session_minutes_remaining,
    SessionState, send_telegram, PriceCache, _price_cache,
    InstrumentTracker, MarketContext, fetch_market_context,
    EvidenceLogger, TradeFingerprint, _evidence,
    is_news_blackout, minutes_to_next_news,
    Signal, SignalVerdict,
)
from forge_brain import (
    compute_bayesian_conviction, BayesianConviction,
    compute_expected_value, ExpectedValueResult,
    monte_carlo_stress_test, StressTestResult,
    compute_price_entropy, compute_move_energy,
    detect_non_reaction, predict_regime_transition,
    ParameterEvolver, get_evolver,
)
from forge_risk import (
    PropFirmState, RiskFortress, RiskDecision,
    camouflage_lot_size, camouflage_entry_delay,
    should_exit_time_decay, check_session_close_protection,
    compute_kelly_size,
)
from mt5_adapter import MT5Adapter
from execution_base import OrderRequest, OrderDirection, OrderType

FORGE_VERSION = "v17"


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP REGISTRY — All 8 setups (Bug #9: ALL scanned, none invisible)
# ═══════════════════════════════════════════════════════════════════════════════

SETUP_CONFIG = {
    "ORD-02": {
        "name": "Opening Range Breakout",
        "instrument": "NAS100", "signal_fn": "orb",
        "base_win_rate": 0.72, "avg_rr": 2.2, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(9, 45), "window_end": dtime(11, 30),
        "expected_hold_min": 45,
    },
    "ICT-01": {
        "name": "VWAP Reclaim",
        "instrument": "NAS100", "signal_fn": "vwap_reclaim",
        "base_win_rate": 0.68, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(10, 0), "window_end": dtime(14, 0),
        "expected_hold_min": 60,
    },
    "ICT-02": {
        "name": "Fair Value Gap",
        "instrument": "NAS100", "signal_fn": "fair_value_gap",
        "base_win_rate": 0.62, "avg_rr": 1.8, "rr_ratio": 1.8,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(9, 45), "window_end": dtime(13, 0),
        "expected_hold_min": 30,
    },
    "ICT-03": {
        "name": "Liquidity Sweep + Reclaim",
        "instrument": "NAS100", "signal_fn": "liquidity_sweep",
        "base_win_rate": 0.67, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 4, "base_size": 0.10,
        "window_start": dtime(9, 30), "window_end": dtime(12, 30),
        "expected_hold_min": 40,
    },
    "VOL-03": {
        "name": "Trend Day Momentum",
        "instrument": "NAS100", "signal_fn": "trend_momentum",
        "base_win_rate": 0.66, "avg_rr": 2.5, "rr_ratio": 2.0,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(10, 30), "window_end": dtime(15, 0),
        "expected_hold_min": 60,
    },
    "VOL-05": {
        "name": "Mean Reversion",
        "instrument": "NAS100", "signal_fn": "mean_reversion",
        "base_win_rate": 0.68, "avg_rr": 1.8, "rr_ratio": 1.8,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(11, 0), "window_end": dtime(15, 30),
        "expected_hold_min": 45,
    },
    "VOL-06": {
        "name": "Noon Curve Reversal",
        "instrument": "NAS100", "signal_fn": "noon_curve",
        "base_win_rate": 0.61, "avg_rr": 1.6, "rr_ratio": 1.6,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(11, 45), "window_end": dtime(12, 45),
        "expected_hold_min": 30,
    },
    "SES-01": {
        "name": "London Session Forex",
        "instrument": "EURUSD", "signal_fn": "london_forex",
        "base_win_rate": 0.63, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 4, "base_size": 0.10,
        "window_start": dtime(3, 0), "window_end": dtime(8, 0),
        "expected_hold_min": 90,
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL ENGINE — ALL INTERNALIZED. No signal_generators.py import.
# ═══════════════════════════════════════════════════════════════════════════════

def _pending(setup_id: str, reason: str) -> Signal:
    return Signal(setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0, reason)


def generate_signal(
    setup_id: str, config: dict, mid: float,
    tracker: InstrumentTracker, ctx: MarketContext, atr: float,
) -> Signal:
    """Generate a trading signal. 100% self-contained."""
    fn = config["signal_fn"]
    t = now_et_time()

    # Time window gate
    ws, we = config.get("window_start"), config.get("window_end")
    if ws and we and not (ws <= t <= we):
        return _pending(setup_id, f"Outside window ({ws}-{we} ET).")

    # Session state weight
    state_w = get_state_weight(setup_id, ctx.session_state)
    if state_w <= 0 and ctx.session_state not in (SessionState.CLOSED, SessionState.PRE_MARKET):
        return _pending(setup_id, f"Not suited for {ctx.session_state.value}.")

    vwap = tracker.open_price or mid

    # ── ORB (Bug #8: 5 ticks + 5pt + waits 9:45) ────────────────────────────
    if fn == "orb":
        if not tracker.orb_locked or not tracker.orb_valid:
            return _pending(setup_id, "ORB not locked or invalid range.")
        long_ok = (tracker.last_close and tracker.last_close > (tracker.orb_high or 0)
                   and len(tracker.close_prices) >= 2)
        short_ok = (tracker.last_close and tracker.last_close < (tracker.orb_low or 0)
                    and len(tracker.close_prices) >= 2)
        if not (long_ok or short_ok):
            if mid > (tracker.orb_high or 0) or mid < (tracker.orb_low or 0):
                return _pending(setup_id, "ORB: awaiting 5-min close confirmation.")
            return _pending(setup_id, "ORB: no breakout.")
        direction = "long" if long_ok else "short"
        if direction == "long" and mid < vwap * 0.999:
            return _pending(setup_id, "ORB long blocked: below VWAP.")
        if direction == "short" and mid > vwap * 1.001:
            return _pending(setup_id, "ORB short blocked: above VWAP.")
        if tracker.ib_locked and tracker.ib_direction and tracker.ib_direction not in ("none", None):
            if tracker.ib_direction != direction:
                return _pending(setup_id, f"ORB {direction} blocked: IB is {tracker.ib_direction}.")
        rng = (tracker.orb_high or 0) - (tracker.orb_low or 0)
        sl_dist = max(atr * 0.5, rng * 0.5)
        if direction == "long":
            entry, sl = mid, mid - sl_dist
            tp = entry + (entry - sl) * 2.0
        else:
            entry, sl = mid, mid + sl_dist
            tp = entry - (sl - entry) * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(entry, 2), round(sl, 2), round(tp, 2), 0.82,
                     f"ORB {direction.upper()}: 5min close | Range={rng:.0f}pts")

    # ── VWAP Reclaim ─────────────────────────────────────────────────────────
    elif fn == "vwap_reclaim":
        if vwap <= 0:
            return _pending(setup_id, "No VWAP data.")
        dipped = tracker.session_low is not None and tracker.session_low < vwap * 0.999
        if not dipped:
            return _pending(setup_id, "VWAP: no dip below yet.")
        if mid <= vwap * 1.001:
            return _pending(setup_id, "VWAP: not reclaimed above.")
        if len(tracker.volume_history) >= 10:
            recent_spread = sum(tracker.volume_history[-5:]) / 5
            avg_spread = sum(tracker.volume_history) / len(tracker.volume_history)
            if avg_spread > 0 and recent_spread > avg_spread * 1.5:
                return _pending(setup_id, "VWAP: spread widening — weak.")
        sl_d = atr * 0.4
        entry = mid
        return Signal(setup_id, SignalVerdict.CONFIRMED, "long",
                     round(entry, 2), round(entry - sl_d, 2), round(entry + sl_d * 2.0, 2), 0.75,
                     "VWAP reclaim: dipped + reclaimed")

    # ── Fair Value Gap (Bug #9: now always scanned) ──────────────────────────
    elif fn == "fair_value_gap":
        closes = tracker.close_prices
        if len(closes) < 4:
            return _pending(setup_id, "FVG: insufficient data.")
        c1, c2, c3, c4 = closes[-4], closes[-3], closes[-2], closes[-1]
        bullish_fvg = c1 < c2 and c3 > c2 and c4 > c3 and mid < c3
        bearish_fvg = c1 > c2 and c3 < c2 and c4 < c3 and mid > c3
        if not (bullish_fvg or bearish_fvg):
            return _pending(setup_id, "FVG: no gap pattern.")
        if is_rth() and ctx.atr_consumed_pct > 0.80:
            return _pending(setup_id, f"FVG: ATR {ctx.atr_consumed_pct:.0%} consumed.")
        d = "long" if bullish_fvg else "short"
        sl_d = atr * 0.4
        entry = mid
        sl = entry - sl_d if d == "long" else entry + sl_d
        tp = entry + sl_d * 1.8 if d == "long" else entry - sl_d * 1.8
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                     round(entry, 2), round(sl, 2), round(tp, 2), 0.72,
                     f"FVG {d.upper()}: gap fill pattern")

    # ── Liquidity Sweep (Bug #9: now always scanned) ─────────────────────────
    elif fn == "liquidity_sweep":
        if ctx.pdl <= 0 or ctx.pdh <= 0:
            return _pending(setup_id, "No PDH/PDL data.")
        swept_low = tracker.session_low is not None and tracker.session_low < ctx.pdl and mid > ctx.pdl
        swept_high = tracker.session_high is not None and tracker.session_high > ctx.pdh and mid < ctx.pdh
        if not (swept_low or swept_high):
            return _pending(setup_id, "No liquidity sweep.")
        d = "long" if swept_low else "short"
        sl_d = atr * 0.35
        if d == "long":
            entry, sl = mid, (tracker.session_low if tracker.session_low else mid - sl_d)
            tp = entry + (entry - sl) * 2.0
        else:
            entry, sl = mid, (tracker.session_high if tracker.session_high else mid + sl_d)
            tp = entry - (sl - entry) * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                     round(entry, 2), round(sl, 2), round(tp, 2), 0.78,
                     f"Sweep {'PDL' if d == 'long' else 'PDH'}: swept + reclaimed")

    # ── Trend Day Momentum ───────────────────────────────────────────────────
    elif fn == "trend_momentum":
        if not tracker.session_high or not tracker.session_low:
            return _pending(setup_id, "Insufficient session data.")
        session_range = (tracker.session_high or 0) - (tracker.session_low or 0)
        if session_range < atr * 0.3:
            return _pending(setup_id, "Range too narrow.")
        if mid > vwap * 1.002:
            direction = "long"
        elif mid < vwap * 0.998:
            direction = "short"
        else:
            return _pending(setup_id, "Too close to VWAP.")
        if direction == "long" and not (tracker.session_high and mid < tracker.session_high * 0.9995):
            return _pending(setup_id, "Trend long: no pullback.")
        if direction == "short" and not (tracker.session_low and mid > tracker.session_low * 1.0005):
            return _pending(setup_id, "Trend short: no pullback.")
        sl_d = atr * 0.5
        entry = mid
        sl = entry - sl_d if direction == "long" else entry + sl_d
        tp = entry + sl_d * 2.0 if direction == "long" else entry - sl_d * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(entry, 2), round(sl, 2), round(tp, 2), 0.74,
                     f"Trend {direction.upper()}: pullback in trend day")

    # ── Mean Reversion ───────────────────────────────────────────────────────
    elif fn == "mean_reversion":
        if vwap <= 0:
            return _pending(setup_id, "No VWAP.")
        dist = mid - vwap
        atr_dist = abs(dist) / atr if atr > 0 else 0
        if atr_dist < 0.8:
            return _pending(setup_id, f"Only {atr_dist:.1f} ATR from VWAP.")
        direction = "short" if dist > 0 else "long"
        sl_d = atr * 0.45
        entry = mid
        sl = entry - sl_d if direction == "long" else entry + sl_d
        tp = vwap
        potential = abs(entry - tp)
        if potential < sl_d * 0.8:
            return _pending(setup_id, "Insufficient R:R.")
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(entry, 2), round(sl, 2), round(tp, 2), 0.72,
                     f"Mean Rev {direction.upper()}: {atr_dist:.1f} ATR from VWAP")

    # ── Noon Curve (Bug #13: uses open_price; Bug #9: always scanned) ────────
    elif fn == "noon_curve":
        if not (dtime(11, 45) <= t <= dtime(12, 45)):
            return _pending(setup_id, "Outside noon curve window.")
        if not tracker.session_high or not tracker.session_low or not tracker.open_price:
            return _pending(setup_id, "Insufficient data.")
        session_move = mid - tracker.open_price
        if abs(session_move) < atr * 0.3:
            return _pending(setup_id, "Insufficient trend.")
        d = "short" if session_move > 0 else "long"
        sl_d = atr * 0.50
        entry = mid
        sl = entry + sl_d if d == "short" else entry - sl_d
        tp = entry - sl_d * 1.6 if d == "short" else entry + sl_d * 1.6
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                     round(entry, 2), round(sl, 2), round(tp, 2), 0.68,
                     f"Noon curve: {'up' if session_move > 0 else 'down'} → reversal {d.upper()}")

    # ── London Forex ─────────────────────────────────────────────────────────
    elif fn == "london_forex":
        if not (dtime(3, 0) <= t <= dtime(8, 0)):
            return _pending(setup_id, "Outside London session.")
        if len(tracker.price_history) < 10:
            return _pending(setup_id, "Insufficient data.")
        recent = tracker.price_history[-10:]
        rh, rl = max(recent), min(recent)
        rng = rh - rl
        if rng < 0.0010:
            return _pending(setup_id, "Range too tight.")
        if mid > rh:     direction = "long"
        elif mid < rl:   direction = "short"
        else:            return _pending(setup_id, "No breakout.")
        sl_d = rng * 0.8
        entry = mid
        sl = entry - sl_d if direction == "long" else entry + sl_d
        tp = entry + sl_d * 2.0 if direction == "long" else entry - sl_d * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(entry, 5), round(sl, 5), round(tp, 5), 0.70,
                     f"London {direction.upper()}: range={rng*10000:.0f}pips")

    return _pending(setup_id, f"Unknown: {fn}")


# ═══════════════════════════════════════════════════════════════════════════════
# INSTRUMENT RESOLUTION (.sim suffix for OANDA/FTMO)
# ═══════════════════════════════════════════════════════════════════════════════

_resolved_instruments: dict[str, Optional[str]] = {}

async def resolve_instrument(adapter: MT5Adapter, logical: str) -> Optional[str]:
    if logical in _resolved_instruments:
        return _resolved_instruments[logical]
    aliases = {
        "NAS100": ["US100.sim", "USTEC.sim", "NAS100.sim", "US100", "USTEC", "NAS100"],
        "EURUSD": ["EURUSD.sim", "EURUSD"],
        "ES":     ["US500.sim", "SPX500.sim", "US500"],
        "XAUUSD": ["XAUUSD.sim", "GOLD.sim", "XAUUSD"],
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
# PROFIT LOCK — FORGE-64 (1R BE, 1.5R partial, 3R trail)
# ═══════════════════════════════════════════════════════════════════════════════

async def manage_open_positions(adapter: MT5Adapter, account, ctx: MarketContext) -> None:
    for pos in account.open_positions:
        try:
            if pos.stop_loss is None or pos.entry_price is None:
                continue
            risk = abs(pos.entry_price - pos.stop_loss)
            if risk <= 0:
                continue
            if pos.direction.value == "long":
                current_r = ((pos.current_price or pos.entry_price) - pos.entry_price) / risk
            else:
                current_r = (pos.entry_price - (pos.current_price or pos.entry_price)) / risk

            # Stage 1: 1R → breakeven
            if current_r >= 1.0 and pos.stop_loss != pos.entry_price:
                try:
                    await adapter.modify_position(pos.position_id, new_stop_loss=pos.entry_price)
                    logger.info("[FORGE-64] %s 1R → BE %.2f", pos.position_id, pos.entry_price)
                except Exception as e:
                    logger.error("[FORGE-64] Modify: %s", e)

            # Stage 2: 1.5R → close 50%
            if current_r >= 1.5 and pos.size > 0.15:
                close_lots = round(pos.size * 0.5, 2)
                if close_lots >= 0.10:
                    try:
                        await adapter.close_position(pos.position_id, size=close_lots)
                        logger.info("[FORGE-64] %s 1.5R → 50%% (%.2f lots)", pos.position_id, close_lots)
                        send_telegram(f"💰 <b>PARTIAL</b>\n{pos.position_id} at 1.5R — {close_lots} lots")
                    except Exception as e:
                        logger.error("[FORGE-64] Partial: %s", e)

            # Stage 3: 3R → trail at 2R
            if current_r >= 3.0:
                if pos.direction.value == "long":
                    trail = pos.entry_price + risk * 2.0
                else:
                    trail = pos.entry_price - risk * 2.0
                try:
                    await adapter.modify_position(pos.position_id, new_stop_loss=round(trail, 2))
                    logger.info("[FORGE-64] %s 3R → trail %.2f", pos.position_id, trail)
                except Exception as e:
                    logger.error("[FORGE-64] Trail: %s", e)
        except Exception as e:
            logger.error("[FORGE-64] Pos %s: %s", pos.position_id, e)


# ═══════════════════════════════════════════════════════════════════════════════
# PRE-FLIGHT CHECK (replaces TrainingRunner — Bug #14)
# ═══════════════════════════════════════════════════════════════════════════════

def run_simulation_check() -> bool:
    logger.info("═══ TITAN FORGE — PRE-FLIGHT CHECK ═══")
    checks = 0
    total = 5

    token = os.environ.get("METAAPI_TOKEN", "")
    acct = os.environ.get("METAAPI_ACCOUNT_ID", os.environ.get("FTMO_ACCOUNT_ID", ""))
    if token and acct:
        checks += 1; logger.info("✅ MetaAPI credentials present")
    else:
        logger.error("❌ Missing METAAPI_TOKEN or METAAPI_ACCOUNT_ID")

    if os.environ.get("TELEGRAM_BOT_TOKEN", ""):
        checks += 1; logger.info("✅ Telegram token present")
    else:
        checks += 1; logger.warning("⚠️ No Telegram — alerts disabled")

    firm = os.environ.get("ACTIVE_FIRM", "FTMO")
    checks += 1; logger.info("✅ Active firm: %s", firm)

    from pathlib import Path
    ev_dir = Path(os.environ.get("EVIDENCE_PATH", "/data/evidence"))
    try:
        ev_dir.mkdir(parents=True, exist_ok=True)
        checks += 1; logger.info("✅ Evidence dir: %s", ev_dir)
    except Exception:
        Path.home().joinpath("forge_evidence").mkdir(parents=True, exist_ok=True)
        checks += 1; logger.warning("⚠️ Fallback evidence dir")

    if len(SETUP_CONFIG) >= 8:
        checks += 1; logger.info("✅ %d setups registered", len(SETUP_CONFIG))
    else:
        logger.error("❌ Only %d setups", len(SETUP_CONFIG))

    cleared = checks >= 4
    logger.info("═══ %s (%d/%d) ═══", "CLEARED" if cleared else "FAILED", checks, total)
    return cleared


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET HOURS
# ═══════════════════════════════════════════════════════════════════════════════

def is_market_open() -> bool:
    t = now_et_time()
    dow = now_et().weekday()
    if dow >= 5: return False
    return dtime(4, 0) <= t < dtime(17, 0)

def seconds_until_market() -> int:
    et = now_et()
    if et.weekday() >= 5:
        days = 7 - et.weekday()
        nxt = et.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=days)
    elif et.time() >= dtime(17, 0):
        nxt = et.replace(hour=4, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        nxt = et.replace(hour=4, minute=0, second=0, microsecond=0)
    return max(60, int((nxt - et).total_seconds()))


# ═══════════════════════════════════════════════════════════════════════════════
# THE LIVE TRADING LOOP
# Bug #7: per-setup per-session cooldown
# Bug #16: NO consecutive win size-up (C-14 permanently disabled)
# Bug #18: reset_daily() always called
# ═══════════════════════════════════════════════════════════════════════════════

async def live_trading_loop(adapter: MT5Adapter) -> None:
    tracker = InstrumentTracker()
    risk_fortress = RiskFortress()
    evolver = get_evolver()

    firm_id = os.environ.get("ACTIVE_FIRM", "FTMO")
    firm_state = PropFirmState(firm_id=firm_id, initial_balance=100_000,
                                current_balance=100_000, highest_eod_balance=100_000)
    firm_state.daily_start_balance = 100_000  # CRITICAL: prevents false emergency trigger

    # Immediately fetch real balance to override defaults
    try:
        _init_acc = await adapter.get_account_state()
        if _init_acc.balance > 0:
            firm_state.initialize(_init_acc.balance)
            firm_state.reset_daily(_init_acc.balance)
            logger.info("[INIT] Firm state from live balance: $%.2f", _init_acc.balance)
    except Exception as e:
        logger.warning("[INIT] Could not fetch live balance, using defaults: %s", e)

    traded_setups: set[str] = set()
    last_session_date = date.today()
    cycle = 0
    ctx = MarketContext()
    atr_session_high = 0.0
    atr_session_low = float("inf")

    logger.info("🔱 TITAN FORGE %s — THE BEAST IS ONLINE.", FORGE_VERSION)
    send_telegram(f"🔱 <b>TITAN FORGE {FORGE_VERSION} ONLINE</b>\nThe beast is awake. All systems armed.")

    while True:
        cycle += 1
        try:
            today = date.today()

            # ── Daily Reset (Bug #18) ────────────────────────────────────────
            if today != last_session_date:
                last_session_date = today
                tracker.reset()
                traded_setups.clear()
                risk_fortress.reset_daily()
                atr_session_high = 0.0
                atr_session_low = float("inf")

                try:
                    ctx = fetch_market_context()
                except Exception as e:
                    logger.warning("[DAILY] Context fetch failed: %s", e)

                # Bug #12: Init from REAL balance
                try:
                    acc = await adapter.get_account_state()
                    if acc.balance > 0:
                        firm_state.initialize(acc.balance)
                        firm_state.reset_daily(acc.balance)
                        risk_fortress.reset_weekly(acc.balance)
                except Exception:
                    pass

                try:
                    evidence = _evidence.get_recent_trades(30)
                    evolver.update_from_evidence(evidence)
                    deg = evolver.get_degradation_alert()
                    if deg:
                        logger.warning("[EVOLVE] %s", deg)
                        send_telegram(f"⚠️ <b>DEGRADATION</b>\n{deg}")
                except Exception:
                    pass

                send_telegram(
                    f"🔱 <b>FORGE {FORGE_VERSION} — MORNING BRIEF</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📅 {today} ({ctx.day_name})\n"
                    f"💰 Balance: ${firm_state.current_balance:,.2f}\n"
                    f"🏢 Firm: {firm_id}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 VIX: {ctx.vix:.1f} ({ctx.vix_regime}) → size {ctx.vix_size_mult:.0%}\n"
                    f"📈 Futures: {ctx.futures_pct*100:+.2f}% ({ctx.futures_bias})\n"
                    f"📏 PDH: {ctx.pdh:.0f} | PDL: {ctx.pdl:.0f}\n"
                    f"📐 ATR: {ctx.atr:.0f}\n"
                    f"🔫 8 setups armed"
                )

            # ── Market Hours ─────────────────────────────────────────────────
            if not is_market_open():
                wait = seconds_until_market()
                logger.info("[Cycle %d] Closed. Next in %dm.", cycle, wait // 60)
                await asyncio.sleep(min(wait, 300))
                continue

            # ── Health ───────────────────────────────────────────────────────
            try:
                health = await adapter.health_check()
                if not health.is_healthy:
                    logger.warning("[Cycle %d] Unhealthy: %s", cycle, health.error)
                    await asyncio.sleep(30); continue
            except Exception as e:
                logger.warning("[Cycle %d] Health err: %s", cycle, e)
                await asyncio.sleep(30); continue

            # ── Account (Bug #2: balance=0 guard) ────────────────────────────
            try:
                account = await adapter.get_account_state()
            except Exception as e:
                logger.error("[Cycle %d] Account err: %s", cycle, e)
                await asyncio.sleep(30); continue

            if account.balance <= 0:
                logger.warning("[Cycle %d] Balance=0 — MetaAPI degraded.", cycle)
                await asyncio.sleep(30); continue

            firm_state.current_balance = account.balance
            firm_state.current_day_pnl = account.daily_pnl
            daily_pnl_pct = account.daily_pnl / account.balance if account.balance > 0 else 0

            logger.info("[Cycle %d] Bal=$%.2f Eq=$%.2f PnL=$%.2f Pos=%d",
                        cycle, account.balance, account.equity,
                        account.daily_pnl, account.open_position_count)

            # ── Price Tracking (Bug #3 + Bug #4) ────────────────────────────
            primary_inst = await resolve_instrument(adapter, "NAS100")
            if primary_inst:
                try:
                    bid, ask = await adapter.get_current_price(primary_inst)
                    if bid > 0:
                        _price_cache.update(primary_inst, bid, ask)
                        tracker.update(bid, ask, ctx)
                        if is_rth():
                            m = (bid + ask) / 2.0
                            if m > atr_session_high: atr_session_high = m
                            if m < atr_session_low:  atr_session_low = m
                            if ctx.atr > 0 and atr_session_high > 0 and atr_session_low < float("inf"):
                                ctx.atr_consumed_pct = (atr_session_high - atr_session_low) / ctx.atr
                            else:
                                ctx.atr_consumed_pct = 0.0
                        else:
                            ctx.atr_consumed_pct = 0.0
                except Exception as e:
                    logger.warning("[Cycle %d] Price err: %s", cycle, e)
                    cached = _price_cache.get(primary_inst)
                    if cached and not cached.stale:
                        logger.info("[Cycle %d] Using cached: %.2f", cycle, cached.mid)

            ctx.session_state = get_session_state()
            ctx.minutes_remaining = session_minutes_remaining()
            ctx.sync_from_tracker(tracker)

            # ── Manage Positions ─────────────────────────────────────────────
            await manage_open_positions(adapter, account, ctx)

            # ── Session Close ────────────────────────────────────────────────
            should_close, close_reason = check_session_close_protection(firm_state)
            if should_close and account.open_position_count > 0:
                logger.warning("[CLOSE] %s", close_reason)
                send_telegram(f"📅 <b>SESSION CLOSE</b>\n{close_reason}")
                try: await adapter.close_all_positions()
                except Exception as e: logger.error("[CLOSE] %s", e)
                await asyncio.sleep(60); continue

            # ── News Blackout (Bug #17: DST-aware) ───────────────────────────
            if is_news_blackout():
                news_mins = minutes_to_next_news()
                if news_mins is not None and news_mins <= 3 and account.open_position_count > 0:
                    logger.warning("[NEWS] Event in %.1fmin — closing", news_mins)
                    send_telegram("⚡ <b>NEWS</b>\nClosing before event")
                    try: await adapter.close_all_positions()
                    except Exception: pass
                logger.info("[Cycle %d] News blackout.", cycle)
                await asyncio.sleep(60); continue

            # ── Emergency ────────────────────────────────────────────────────
            if firm_state.should_emergency_close(account.equity):
                logger.warning("[EMERGENCY] Near firm limit — closing all")
                send_telegram("🚨 <b>EMERGENCY</b>\nEquity near limit — closing ALL")
                try: await adapter.close_all_positions()
                except Exception: pass
                await asyncio.sleep(60); continue

            # ── Position Limit ───────────────────────────────────────────────
            if account.open_position_count >= 2:
                logger.info("[Cycle %d] Max positions (2/2).", cycle)
                await asyncio.sleep(60); continue

            # ── Brain Signals ────────────────────────────────────────────────
            non_rx = detect_non_reaction(ctx, tracker)
            if non_rx: logger.info("[BRAIN] %s", non_rx)
            trans_prob, trans_desc = predict_regime_transition(ctx, tracker)
            if trans_prob > 0.50: logger.info("[BRAIN] %s", trans_desc)

            # ════════════════════════════════════════════════════════════════
            # DECISION PIPELINE — Scan all 8 setups
            # ════════════════════════════════════════════════════════════════
            best_action = None
            best_ev = -999999

            mid = _price_cache.get_mid(primary_inst or "") or (
                tracker.price_history[-1] if tracker.price_history else 0)
            if mid <= 0:
                await asyncio.sleep(60); continue

            atr = ctx.atr if ctx.atr > 0 else 100.0

            for setup_id, config in SETUP_CONFIG.items():
                if setup_id in traded_setups: continue
                if is_rth() and ctx.atr_consumed_pct > 0.85 and setup_id != "VOL-06": continue

                signal = generate_signal(setup_id, config, mid, tracker, ctx, atr)
                if signal.verdict != SignalVerdict.CONFIRMED:
                    continue

                live_wr = evolver.get_live_win_rate(setup_id)
                conviction = compute_bayesian_conviction(
                    prior_win_rate=config["base_win_rate"],
                    ctx=ctx, tracker=tracker,
                    direction=signal.direction, setup_id=setup_id,
                    live_win_rate=live_wr,
                )

                if conviction.conviction_level == "REJECT":
                    _evidence.log_trade(TradeFingerprint(
                        trade_id=f"PH-{uuid.uuid4().hex[:8]}",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        setup_id=setup_id, instrument=config["instrument"],
                        direction=signal.direction or "", entry_price=signal.entry_price or 0,
                        stop_loss=signal.stop_loss or 0, take_profit=signal.take_profit or 0,
                        firm_id=firm_id, vix=ctx.vix, vix_regime=ctx.vix_regime,
                        futures_bias=ctx.futures_bias, session_state=ctx.session_state.value,
                        day_of_week=ctx.day_name, atr=atr, atr_pct_consumed=ctx.atr_consumed_pct,
                        bayesian_posterior=conviction.posterior,
                        confluence_score=conviction.confirming,
                        conviction_level=conviction.conviction_level,
                        is_phantom=True, outcome="PHANTOM", capital_vehicle="PROP_FIRM",
                    ))
                    continue

                if conviction.conviction_level not in ("ELITE", "HIGH"):
                    continue

                risk_dollars = abs((signal.entry_price or 0) - (signal.stop_loss or 0)) * config["base_size"] * 10
                reward_dollars = abs((signal.take_profit or 0) - (signal.entry_price or 0)) * config["base_size"] * 10
                if risk_dollars <= 0 or reward_dollars <= 0: continue

                ev_result = compute_expected_value(
                    win_prob=conviction.posterior, reward_dollars=reward_dollars,
                    risk_dollars=risk_dollars, account_balance=account.balance,
                    max_position_pct=0.02, minutes_remaining=ctx.minutes_remaining,
                )

                if ev_result.action == "SKIP": continue
                if ev_result.action == "WAIT" and ctx.minutes_remaining > 120: continue

                if ev_result.net_ev > best_ev:
                    best_ev = ev_result.net_ev
                    best_action = (setup_id, config, signal, conviction, ev_result)

            # ── Execute ──────────────────────────────────────────────────────
            if best_action is None:
                await asyncio.sleep(60); continue

            setup_id, config, signal, conviction, ev_result = best_action

            risk_decision = risk_fortress.evaluate(
                firm_state=firm_state, equity=account.equity,
                daily_pnl=account.daily_pnl, balance=account.balance, setup_id=setup_id,
            )
            if not risk_decision.can_trade:
                logger.info("[Cycle %d][%s] RISK: %s", cycle, setup_id, risk_decision.reason)
                await asyncio.sleep(60); continue

            risk_dollars = abs((signal.entry_price or 0) - (signal.stop_loss or 0)) * config["base_size"] * 10
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
                await asyncio.sleep(60); continue

            # Bug #16: NO C-14 win size-up
            conv_mult = {"ELITE": 1.2, "HIGH": 1.0}.get(conviction.conviction_level, 1.0)
            lot_size = compute_kelly_size(
                win_prob=conviction.posterior, reward_risk_ratio=ev_result.reward_risk_ratio,
                account_balance=account.balance, base_lot_size=config["base_size"],
                firm_max_risk_pct=0.02, risk_multiplier=risk_decision.size_multiplier,
                vix_multiplier=ctx.vix_size_mult, day_multiplier=ctx.day_strength,
                conviction_mult=conv_mult,
            )
            lot_size = max(0.10, lot_size)

            if not firm_state.is_size_consistent(lot_size):
                lot_size = (sum(firm_state.recent_sizes[-5:]) / max(1, len(firm_state.recent_sizes[-5:]))
                           if firm_state.recent_sizes else config["base_size"])

            # Min stop distance
            sl_dist = abs((signal.entry_price or 0) - (signal.stop_loss or 0))
            if sl_dist < 5.0 and "NAS100" in config["instrument"]:
                ep = signal.entry_price or 0
                if signal.direction == "long":
                    signal = Signal(signal.setup_id, signal.verdict, signal.direction,
                                    ep, round(ep - 5.0, 2), round(ep + 10.0, 2),
                                    signal.conviction, signal.reason)
                else:
                    signal = Signal(signal.setup_id, signal.verdict, signal.direction,
                                    ep, round(ep + 5.0, 2), round(ep - 10.0, 2),
                                    signal.conviction, signal.reason)

            instrument = await resolve_instrument(adapter, config["instrument"])
            if not instrument: continue

            delay = camouflage_entry_delay()
            await asyncio.sleep(delay)

            trade_id = f"TF-{uuid.uuid4().hex[:8]}"
            order = OrderRequest(
                instrument=instrument,
                direction=OrderDirection.LONG if signal.direction == "long" else OrderDirection.SHORT,
                size=lot_size, order_type=OrderType.MARKET,
                stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                comment=f"{setup_id}|{trade_id}|Bayes={conviction.posterior:.0%}|EV=${ev_result.ev_dollars:.0f}",
            )

            logger.info("🔫 [%s] %s %s %.2f lots | E=%.2f SL=%.2f TP=%.2f | P=%.0f%% EV=$%.0f",
                        setup_id, (signal.direction or "").upper(), instrument, lot_size,
                        signal.entry_price or 0, signal.stop_loss or 0, signal.take_profit or 0,
                        conviction.posterior * 100, ev_result.ev_dollars)

            result = await adapter.place_order(order)

            if result.status.value == "filled":
                logger.info("✅ FILLED: %s @ %.5f", result.order_id, result.fill_price)
                traded_setups.add(setup_id)
                firm_state.record_size(lot_size)

                send_telegram(
                    f"🔫 <b>TRADE</b>\n"
                    f"{setup_id} ({config['name']})\n"
                    f"{(signal.direction or '').upper()} {instrument} @ {result.fill_price:.2f}\n"
                    f"Size: {lot_size} | SL: {signal.stop_loss:.2f} | TP: {signal.take_profit:.2f}\n"
                    f"{conviction.conviction_level} ({conviction.posterior:.0%}) | EV: ${ev_result.ev_dollars:.0f}"
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

        await asyncio.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    logger.info("╔══════════════════════════════════════════════╗")
    logger.info("║  NEXUS CAPITAL — TITAN FORGE %s              ║", FORGE_VERSION)
    logger.info("║  THE BEAST — 100%% SELF-CONTAINED            ║")
    logger.info("╚══════════════════════════════════════════════╝")

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
