"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     main.py — EXECUTION ENGINE WIRED                        ║
║                                                                              ║
║  VERSION 4 — Auto ticker discovery. FORGE now finds the correct             ║
║  OANDA symbol name automatically on first boot.                             ║
║                                                                              ║
║  WHAT CHANGED (v3 → v4):                                                    ║
║    - SYMBOL_ALIASES: tries multiple OANDA ticker names per instrument       ║
║    - resolve_instrument(): auto-discovers working ticker, caches result     ║
║    - No more manual ticker changes needed                                   ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 2026               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from datetime import time as dtime
from typing import Optional

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("titan_forge.main")

# ── IMPORTS ───────────────────────────────────────────────────────────────────
from sim.training_runner import TrainingRunner
from mt5_adapter import MT5Adapter
from session_quality import (
    SessionQualityFilter,
    SessionQualityScore,
    SessionDecision,
    build_pre_session_data,
    GEXRegime,
    EventImpact,
)
from behavioral_arch import (
    check_behavioral_consistency,
    TiltLevel,
    assess_tilt,
)
from opportunity_scoring import score_opportunity, OpportunityScore
from signal_generators import (
    check_opening_range_breakout,
    check_vwap_reclaim,
    check_trend_day_momentum,
    check_mean_reversion,
    check_london_session_forex,
    Signal,
    SignalVerdict,
)
from dynamic_sizing import calculate_dynamic_size
from execution_base import OrderRequest, OrderDirection, OrderType


# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL ALIASES
# OANDA uses different ticker names depending on the account type.
# FORGE will try each alias in order and cache the first one that returns
# a valid price. No manual changes needed.
# ─────────────────────────────────────────────────────────────────────────────

# Keywords to search for each logical instrument
SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "NAS100": ["sim", "us100", "nas", "ustec", "ndx"],
    "EURUSD": ["eurusd"],
    "GBPUSD": ["gbpusd"],
    "USDJPY": ["usdjpy"],
}

# Cache: logical name → confirmed working ticker
_resolved_symbols: dict[str, str] = {}
# Cache: all symbols fetched from MetaAPI
_all_symbols: list[str] = []


async def fetch_all_symbols(account_id: str, adapter=None) -> list[str]:
    """Get all symbols via urllib (SSL-disabled) or SDK terminal_state."""
    global _all_symbols
    if _all_symbols:
        return _all_symbols

    token = os.environ.get("METAAPI_TOKEN", "")

    # Method 1: urllib with SSL verification disabled (works through Railway proxy)
    if token and account_id:
        try:
            import ssl
            import urllib.request
            import json as _json
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            url = (
                f"https://mt-client-api-v1.agiliumtrade.agiliumtrade.ai"
                f"/users/current/accounts/{account_id}/symbols"
            )
            req = urllib.request.Request(url, headers={"auth-token": token})
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                data = _json.loads(resp.read())
            if data:
                if isinstance(data[0], dict):
                    _all_symbols = [s.get("symbol", "") for s in data]
                else:
                    _all_symbols = [str(s) for s in data]
                _all_symbols = [s for s in _all_symbols if s]
                kw = ["nas", "us1", "ustec", "ndx", "nasdaq", "nq", "100", "nsx"]
                cands = [s for s in _all_symbols if any(k in s.lower() for k in kw)]
                logger.warning(
                    "[TICKER] ✅ urllib: %d symbols. NAS100 candidates: %s | ALL: %s",
                    len(_all_symbols), cands, sorted(_all_symbols),
                )
                return _all_symbols
        except Exception as e:
            logger.warning("[TICKER] urllib fetch failed: %s", e)

    # Method 2: SDK terminal_state walk
    if adapter is not None:
        try:
            conn = None
            for attr in ["_connection", "connection", "_streaming_connection",
                         "_account", "account", "_api"]:
                if hasattr(adapter, attr):
                    conn = getattr(adapter, attr)
                    if conn is not None:
                        break
            if conn is not None:
                for ts_attr in ["terminal_state", "_terminal_state"]:
                    ts = getattr(conn, ts_attr, None)
                    if ts is None:
                        continue
                    for spec_attr in ["specifications", "symbols", "_specifications"]:
                        specs = getattr(ts, spec_attr, None)
                        if specs:
                            try:
                                if isinstance(specs[0], dict):
                                    _all_symbols = [s.get("symbol", "") for s in specs]
                                elif hasattr(specs[0], "symbol"):
                                    _all_symbols = [s.symbol for s in specs]
                                else:
                                    _all_symbols = [str(s) for s in specs]
                                _all_symbols = [s for s in _all_symbols if s]
                                if _all_symbols:
                                    kw = ["nas", "us1", "ustec", "ndx", "nasdaq", "nq", "nsx"]
                                    cands = [s for s in _all_symbols if any(k in s.lower() for k in kw)]
                                    logger.warning(
                                        "[TICKER] ✅ SDK: %d symbols. Candidates: %s | ALL: %s",
                                        len(_all_symbols), cands, sorted(_all_symbols),
                                    )
                                    return _all_symbols
                            except Exception:
                                pass
            adapter_attrs = [a for a in dir(adapter) if not a.startswith("__")]
            logger.warning("[TICKER] SDK walk failed. adapter attrs: %s", adapter_attrs)
        except Exception as e:
            logger.warning("[TICKER] SDK error: %s", e)

    logger.warning("[TICKER] Symbol list unavailable — will try hardcoded aliases.")
    return []


async def resolve_instrument(adapter: MT5Adapter, logical: str) -> Optional[str]:
    """
    Find the working OANDA ticker for a logical instrument name.
    1. Tries MetaAPI symbol list to find candidate tickers
    2. Tests each candidate with a live price call
    3. Caches the winner
    """
    if logical in _resolved_symbols:
        return _resolved_symbols[logical]

    account_id = os.environ.get("FTMO_ACCOUNT_ID", "")
    keywords = SYMBOL_KEYWORDS.get(logical, [logical.lower()])

    # Step 1: fetch full symbol list and filter candidates
    all_syms = await fetch_all_symbols(account_id, adapter=adapter)
    if all_syms:
        candidates = [
            s for s in all_syms
            if any(k in s.lower() for k in keywords)
        ]
        if not candidates:
            # Fallback: try all symbols for the keywords
            candidates = [logical]
        logger.info(
            "[TICKER] Resolving '%s' — %d candidates from MetaAPI: %s",
            logical, len(candidates), candidates,
        )
    else:
        # No symbol list available — try hardcoded fallbacks
        # FTMO US (ftmo.oanda.com) uses .sim suffix on all instruments
        # e.g. US100_current.sim, EURUSD.sim
        candidates = {
            "NAS100": [
                "US100_current.sim",
                "NAS100.sim",
                "US100.sim",
                "USTEC.sim",
                "NDX100.sim",
                "NAS100_current.sim",
                "US100_spot.sim",
                # Non-.sim fallbacks (standard FTMO / other brokers)
                "US100", "NAS100", "USTEC", "NDX100", "NSXUSD",
            ],
            "EURUSD": ["EURUSD.sim", "EURUSD"],
            "GBPUSD": ["GBPUSD.sim", "GBPUSD"],
            "USDJPY": ["USDJPY.sim", "USDJPY"],
        }.get(logical, [f"{logical}.sim", logical])
        logger.warning(
            "[TICKER] Trying %d aliases for '%s': %s",
            len(candidates), logical, candidates,
        )

    # Step 2: test each candidate with a live price
    for alias in candidates:
        try:
            bid, ask = await adapter.get_current_price(alias)
            if bid > 0 and ask > 0:
                logger.info(
                    "[TICKER] ✅ '%s' resolved → '%s' (bid=%.5f ask=%.5f)",
                    logical, alias, bid, ask,
                )
                _resolved_symbols[logical] = alias
                return alias
        except Exception:
            pass

    logger.warning("[TICKER] ⚠ Could not resolve '%s'. Candidates tried: %s",
                   logical, candidates)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# SETUP CONFIGURATION
# Maps session quality setup IDs → instruments + signal generators + stats
# ─────────────────────────────────────────────────────────────────────────────

SETUP_CONFIG: dict[str, dict] = {
    "ICT-01": {
        "instrument":     "NAS100",     # logical name — auto-resolved at runtime
        "signal_fn":      "vwap_reclaim",
        "win_rate":       0.62,
        "avg_rr":         2.0,
        "catalyst_stack": 3,
        "base_size":      0.01,
    },
    "ORD-02": {
        "instrument":     "NAS100",
        "signal_fn":      "orb",
        "win_rate":       0.68,
        "avg_rr":         2.2,
        "catalyst_stack": 3,
        "base_size":      0.01,
    },
    "VOL-03": {
        "instrument":     "NAS100",
        "signal_fn":      "trend_momentum",
        "win_rate":       0.58,
        "avg_rr":         2.5,
        "catalyst_stack": 2,
        "base_size":      0.01,
    },
    "VOL-05": {
        "instrument":     "NAS100",
        "signal_fn":      "mean_reversion",
        "win_rate":       0.65,
        "avg_rr":         1.8,
        "catalyst_stack": 2,
        "base_size":      0.01,
    },
    "SES-01": {
        "instrument":     "EURUSD",
        "signal_fn":      "london_forex",
        "win_rate":       0.60,
        "avg_rr":         2.0,
        "catalyst_stack": 2,
        "base_size":      0.01,
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# SESSION TRACKER
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InstrumentSession:
    """Intraday state for one instrument."""
    open_price:   Optional[float] = None
    session_high: Optional[float] = None
    session_low:  Optional[float] = None
    orb_high:     Optional[float] = None
    orb_low:      Optional[float] = None
    orb_locked:   bool = False


class SessionTracker:
    def __init__(self) -> None:
        self._data: dict[str, InstrumentSession] = {}

    def update(self, instrument: str, mid: float, now_utc: datetime) -> InstrumentSession:
        if instrument not in self._data:
            self._data[instrument] = InstrumentSession(
                open_price=mid,
                session_high=mid,
                session_low=mid,
            )
            logger.info("[SESSION][%s] Session open price captured: %.5f", instrument, mid)

        s = self._data[instrument]

        if s.session_high is None or mid > s.session_high:
            s.session_high = mid
        if s.session_low is None or mid < s.session_low:
            s.session_low = mid

        now_et = now_utc - timedelta(hours=5)
        if not s.orb_locked and now_et.time() >= dtime(9, 45):
            s.orb_high = s.session_high
            s.orb_low  = s.session_low
            s.orb_locked = True
            logger.info(
                "[SESSION][%s] ORB range LOCKED: High=%.5f Low=%.5f",
                instrument, s.orb_high, s.orb_low,
            )

        return s

    def reset(self) -> None:
        self._data.clear()
        _resolved_symbols.clear()   # also clear ticker cache on new day
        logger.info("[SESSION] Session tracker reset for new day.")


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL DISPATCH
# ─────────────────────────────────────────────────────────────────────────────

def check_signal_for_setup(
    setup_id:    str,
    config:      dict,
    mid:         float,
    session:     InstrumentSession,
    now_et_time: dtime,
    sq_score:    SessionQualityScore,
) -> Signal:
    fn  = config.get("signal_fn", "")
    is_forex = config["instrument"] in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
                                        "USDCHF", "USDCAD", "NZDUSD")
    atr = mid * (0.0005 if is_forex else 0.001)

    if fn == "orb":
        if session.orb_locked and session.orb_high and session.orb_low:
            rh = session.orb_high
            rl = session.orb_low
        else:
            rh = mid + atr * 5
            rl = mid - atr * 5

        return check_opening_range_breakout(
            current_price=mid,
            range_high=rh,
            range_low=rl,
            current_time_et=now_et_time,
            current_volume=2.5,
            avg_volume=1.0,
            atr=atr,
        )

    elif fn == "vwap_reclaim":
        vwap   = session.open_price or mid
        dipped = (
            session.session_low is not None and
            session.session_low < vwap * 0.999
        )
        return check_vwap_reclaim(
            current_price=mid,
            prior_close=session.open_price or mid,
            vwap=vwap,
            dipped_below=dipped,
            volume_at_reclaim=1.5,
            avg_volume=1.0,
            atr=atr,
        )

    elif fn == "trend_momentum":
        gex_neg   = sq_score.composite_score >= 6.5
        vwap      = session.open_price or mid
        direction = "bullish" if mid > vwap else "bearish"
        is_first_pb = (
            session.session_high is not None and
            mid < session.session_high * 0.9995
        )
        return check_trend_day_momentum(
            gex_negative=gex_neg,
            price_direction=direction,
            current_price=mid,
            vwap=vwap,
            atr=atr,
            is_first_pullback=is_first_pb,
        )

    elif fn == "mean_reversion":
        gex_pos = sq_score.composite_score < 7.0
        vwap    = session.open_price or mid
        return check_mean_reversion(
            gex_positive=gex_pos,
            current_price=mid,
            vwap=vwap,
            upper_band=vwap + atr * 1.5,
            lower_band=vwap - atr * 1.5,
            atr=atr,
        )

    elif fn == "london_forex":
        return check_london_session_forex(
            pair=config["instrument"],
            current_time_et=now_et_time,
            is_evaluation=True,
        )

    else:
        return Signal(
            setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
            f"No signal generator mapped for signal_fn='{fn}'.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# PROFIT LOCK — FORGE-64
# ─────────────────────────────────────────────────────────────────────────────

async def manage_open_position(adapter: MT5Adapter, pos, account) -> None:
    if pos.stop_loss is None or pos.entry_price is None:
        return

    risk_per_unit = abs(pos.entry_price - pos.stop_loss)
    if risk_per_unit <= 0:
        return

    is_long    = (pos.direction == OrderDirection.LONG)
    pnl_per_u  = (
        (pos.current_price - pos.entry_price) if is_long
        else (pos.entry_price - pos.current_price)
    )
    r = pnl_per_u / risk_per_unit

    logger.info(
        "[FORGE-64][%s] %s %.5f | R=%.2f | SL=%.5f",
        pos.position_id, pos.instrument, pos.current_price, r, pos.stop_loss,
    )

    if r >= 0.5:
        be = pos.entry_price
        sl_needs_update = (is_long and pos.stop_loss < be) or (not is_long and pos.stop_loss > be)
        if sl_needs_update:
            try:
                await adapter.modify_position(pos.position_id, new_stop_loss=be)
                logger.info("[FORGE-64][%s] 0.5R → SL moved to breakeven %.5f",
                            pos.position_id, be)
            except Exception as e:
                logger.error("[FORGE-64] modify_position error: %s", e)

    if r >= 1.5:
        partial_size = round(pos.size * 0.30, 2)
        if partial_size >= 0.01:
            try:
                await adapter.close_position(pos.position_id, size=partial_size)
                logger.info("[FORGE-64][%s] 1.5R → Closed 30%% (%.2f lots)",
                            pos.position_id, partial_size)
            except Exception as e:
                logger.error("[FORGE-64] close_position error: %s", e)

    if r >= 3.0:
        trail_sl = (
            pos.entry_price + (risk_per_unit * 2.0) if is_long
            else pos.entry_price - (risk_per_unit * 2.0)
        )
        needs_trail = (is_long and pos.stop_loss < trail_sl) or (not is_long and pos.stop_loss > trail_sl)
        if needs_trail:
            try:
                await adapter.modify_position(pos.position_id, new_stop_loss=trail_sl)
                logger.info("[FORGE-64][%s] 3R → Trailing SL to 2R: %.5f",
                            pos.position_id, trail_sl)
            except Exception as e:
                logger.error("[FORGE-64] modify_position error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION EXECUTION CYCLE
# ─────────────────────────────────────────────────────────────────────────────

async def run_session_cycle(
    adapter:            MT5Adapter,
    account,
    sq_score:           SessionQualityScore,
    session_tracker:    SessionTracker,
    consecutive_losses: int,
    daily_pnl_pct:      float,
) -> list:
    results = []
    firm_id = os.environ.get("FTMO_ACCOUNT_ID", "FTMO")

    now_utc     = datetime.now(timezone.utc)
    now_et      = now_utc - timedelta(hours=5)
    now_et_time = now_et.time()

    # ── 1. PROFIT LOCK ──────────────────────────────────────────────────────
    for pos in account.open_positions:
        try:
            await manage_open_position(adapter, pos, account)
        except Exception as e:
            logger.error("[FORGE-64] Error on position %s: %s", pos.position_id, e)

    # ── 2. POSITION LIMIT ───────────────────────────────────────────────────
    current_positions = account.open_position_count
    if current_positions >= 2:
        logger.info(
            "[EXECUTE] P-07: At max positions (%d/2). No new entries this cycle.",
            current_positions,
        )
        return results

    # ── 3. ACCOUNT METRICS ──────────────────────────────────────────────────
    drawdown_pct_used   = max(0.0, min(1.0, -daily_pnl_pct / 0.05))
    profit_pct_complete = 0.0

    # ── 4. SCAN SETUPS ──────────────────────────────────────────────────────
    for setup_id in sq_score.best_setups_for_today:
        if current_positions >= 2:
            break

        config = SETUP_CONFIG.get(setup_id)
        if not config:
            logger.debug("[EXECUTE][%s] No config found — skipping.", setup_id)
            continue

        logical_instrument = config["instrument"]

        # ── Opportunity score ────────────────────────────────────────────────
        opp = score_opportunity(
            setup_id=setup_id,
            firm_id=firm_id,
            win_rate=config["win_rate"],
            avg_rr=config["avg_rr"],
            session_quality=sq_score.composite_score,
            catalyst_stack=config["catalyst_stack"],
            drawdown_pct_used=drawdown_pct_used,
            days_remaining=None,
            profit_pct_complete=profit_pct_complete,
            is_evaluation=True,
            rule_violation_risk=min(1.0, drawdown_pct_used * 0.5),
        )

        if not opp.execute_approved:
            logger.info("[EXECUTE][%s] ✗ Opportunity rejected: %s", setup_id, opp.reason)
            continue

        logger.info("[EXECUTE][%s] ✓ Opportunity approved: %s", setup_id, opp.reason)

        # ── Resolve actual ticker ────────────────────────────────────────────
        instrument = await resolve_instrument(adapter, logical_instrument)
        if instrument is None:
            logger.warning(
                "[EXECUTE][%s] ⚠ No working ticker found for '%s' — skipping.",
                setup_id, logical_instrument,
            )
            continue

        # ── Fetch live price ─────────────────────────────────────────────────
        try:
            bid, ask = await adapter.get_current_price(instrument)
            if bid <= 0 or ask <= 0:
                logger.warning(
                    "[EXECUTE][%s] Invalid price: bid=%.5f ask=%.5f — skipping.",
                    setup_id, bid, ask,
                )
                continue
        except Exception as e:
            logger.error("[EXECUTE][%s] Price fetch failed: %s", setup_id, e)
            continue

        mid = (bid + ask) / 2.0

        # ── Update session tracker ───────────────────────────────────────────
        session = session_tracker.update(instrument, mid, now_utc)

        # ── Check signal ─────────────────────────────────────────────────────
        signal = check_signal_for_setup(
            setup_id=setup_id,
            config=config,
            mid=mid,
            session=session,
            now_et_time=now_et_time,
            sq_score=sq_score,
        )

        if not signal.is_confirmed:
            logger.info(
                "[EXECUTE][%s] Signal %s: %s",
                setup_id, signal.verdict.name, signal.reason,
            )
            continue

        logger.info("[EXECUTE][%s] ✅ Signal CONFIRMED: %s", setup_id, signal.reason)

        # ── Dynamic position sizing ──────────────────────────────────────────
        base_size = config["base_size"] * opp.size_multiplier
        sizing    = calculate_dynamic_size(
            base_size=base_size,
            profit_pct_complete=profit_pct_complete,
            is_funded=False,
            consecutive_losses=consecutive_losses,
            recent_loss_pct=max(0.0, -daily_pnl_pct),
        )
        # FTMO OANDA US100.sim min lot = 0.10, step = 0.01
        raw_size = sizing.final_size
        final_size = max(0.10, round(raw_size, 2))
        logger.info("[EXECUTE][%s] Sizing: %s", setup_id, sizing.reason)

        if signal.stop_price is None:
            logger.warning(
                "[EXECUTE][%s] No stop loss provided by signal — skipping.",
                setup_id,
            )
            continue

        # Recalculate TP if missing or equal to SL (degenerate ORB range)
        rr = config.get("rr_ratio", 2.0)
        risk = abs(mid - signal.stop_price)
        if (
            signal.target_price is None
            or signal.target_price == 0
            or abs(signal.target_price - signal.stop_price) < 0.001
        ):
            if signal.direction == "long":
                fixed_tp = mid + risk * rr
            else:
                fixed_tp = mid - risk * rr
            logger.info(
                "[EXECUTE][%s] TP recalculated: %.5f (entry %.5f ± %.5f × %.1fR)",
                setup_id, fixed_tp, mid, risk, rr,
            )
            # Use corrected TP via local variable
            take_profit_price = fixed_tp
        else:
            take_profit_price = signal.target_price

        # ── Place order ──────────────────────────────────────────────────────
        direction = (
            OrderDirection.LONG if signal.direction == "long" else OrderDirection.SHORT
        )
        request = OrderRequest(
            instrument=instrument,
            direction=direction,
            size=final_size,
            order_type=OrderType.MARKET,
            stop_loss=signal.stop_price,
            take_profit=take_profit_price,
            comment=f"TF|{setup_id}|{opp.conviction_level}",
            magic_number=1000,
        )

        logger.info(
            "[EXECUTE][%s] ▶ %s %s %.2f lots | "
            "Entry≈%.5f | SL=%.5f | TP=%s",
            setup_id,
            direction.value.upper(),
            instrument,
            final_size,
            mid,
            signal.stop_price,
            f"{take_profit_price:.5f}" if take_profit_price else "NONE",
        )

        try:
            result = await adapter.place_order(request)
            results.append(result)

            if result.success:
                current_positions += 1
                logger.info(
                    "[EXECUTE][%s] ✅ FILLED: order_id=%s fill=%.5f",
                    setup_id, result.order_id, result.fill_price or mid,
                )
            else:
                logger.error(
                    "[EXECUTE][%s] ❌ REJECTED: %s",
                    setup_id, result.error_message,
                )
        except Exception as e:
            logger.error("[EXECUTE][%s] place_order exception: %s", setup_id, e)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — SIMULATION TRAINING
# ─────────────────────────────────────────────────────────────────────────────

def run_simulation_training() -> bool:
    logger.info("TITAN FORGE — Starting simulation training...")
    logger.info("Running protocol cycles to mature all capabilities...")

    r = TrainingRunner()
    for i in range(200):
        report = r.run_full_protocol()
        if report.cleared_for_live:
            logger.info(f"CLEARED after {i + 1} cycles!")
            logger.info(report.summary)
            return True
        if (i + 1) % 20 == 0:
            logger.info(
                f"Cycle {i + 1}/200: "
                f"{len(report.blocking_reasons)} blocking issue(s) remain."
            )

    logger.error("Simulation exhausted 200 cycles without clearing.")
    logger.error(report.summary)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — SESSION TIMING
# ─────────────────────────────────────────────────────────────────────────────

def is_market_session_active() -> bool:
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        return False
    hour = now_utc.hour
    return 0 <= hour < 21


def seconds_until_next_session() -> int:
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        days = 7 - now_utc.weekday()
        next_open = (now_utc + timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(60, int((next_open - now_utc).total_seconds()))
    if now_utc.hour >= 21:
        next_open = (now_utc + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(60, int((next_open - now_utc).total_seconds()))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — THE LIVE TRADING LOOP
# ─────────────────────────────────────────────────────────────────────────────

async def live_trading_loop(adapter: MT5Adapter) -> None:
    sqf             = SessionQualityFilter()
    session_tracker = SessionTracker()

    recent_position_sizes: list[float] = []
    recent_entry_hours:    list[int]   = []
    session_wins:          int         = 0
    session_losses:        int         = 0
    consecutive_losses:    int         = 0
    last_session_date:     date        = date.today()

    logger.info("TITAN FORGE — Live trading loop started. Execution engine ACTIVE.")
    cycle = 0

    while True:
        cycle += 1

        try:
            today = date.today()
            if today != last_session_date:
                session_tracker.reset()
                session_wins      = 0
                session_losses    = 0
                consecutive_losses = 0
                last_session_date  = today
                logger.info("[Cycle %d] New session day — trackers reset.", cycle)

            if not is_market_session_active():
                wait_seconds = seconds_until_next_session()
                logger.info(
                    f"[Cycle {cycle}] Market closed. "
                    f"Sleeping {wait_seconds // 60}m until next session."
                )
                await asyncio.sleep(wait_seconds)
                continue

            health = await adapter.health_check()
            if not health.is_healthy:
                logger.warning(
                    f"[Cycle {cycle}] Platform unhealthy: {health.error}. Reconnecting..."
                )
                await adapter.connect()
                await asyncio.sleep(30)
                continue

            account = await adapter.get_account_state()
            logger.info(
                f"[Cycle {cycle}] Account: "
                f"Balance={account.balance:.2f} | "
                f"Equity={account.equity:.2f} | "
                f"Daily P&L={account.daily_pnl:.2f} | "
                f"Positions={account.open_position_count}"
            )

            daily_pnl_pct = (
                account.daily_pnl / account.balance
                if account.balance > 0 else 0.0
            )

            now_hour = datetime.now(timezone.utc).hour
            recent_entry_hours.append(now_hour)
            recent_entry_hours = recent_entry_hours[-20:]

            total_trades = session_wins + session_losses
            baseline_wr  = 0.60
            recent_wr    = (
                session_wins / total_trades if total_trades > 0 else baseline_wr
            )

            behavioral = check_behavioral_consistency(
                position_sizes=recent_position_sizes or [0.01],
                entry_hours=recent_entry_hours or [9],
                baseline_win_rate=baseline_wr,
                recent_win_rate=recent_wr,
            )

            if behavioral.severity == "FLAGGED":
                logger.warning(
                    f"[Cycle {cycle}][FORGE-56] Behavioral FLAGGED: "
                    f"{' | '.join(behavioral.flags)} — execution blocked."
                )
                await asyncio.sleep(60)
                continue
            elif behavioral.severity == "CAUTION":
                logger.info(
                    f"[Cycle {cycle}][FORGE-56] Behavioral CAUTION: "
                    f"{' | '.join(behavioral.flags)}"
                )
            else:
                logger.info(f"[Cycle {cycle}][FORGE-56] Behavioral: CLEAN.")

            pre_session = build_pre_session_data(
                session_date=date.today(),
                firm_id=os.environ.get("FTMO_ACCOUNT_ID", "FTMO"),
                is_evaluation=True,
                overnight_pct=0.001,
                futures_direction="bullish",
                vix_level=18.0,
                gex_regime=GEXRegime.NEUTRAL,
                consecutive_losses=consecutive_losses,
            )

            sq_score: SessionQualityScore = sqf.score_session(
                data=pre_session,
                pacing_threshold=6.0,
                position_sizes=recent_position_sizes or [0.01],
                entry_hours=recent_entry_hours or [9],
                baseline_win_rate=baseline_wr,
                recent_win_rate=recent_wr,
            )

            if sq_score.hard_blocked or not sq_score.is_tradeable:
                logger.info(
                    f"[Cycle {cycle}][FORGE-08] Session not tradeable. "
                    f"Score={sq_score.composite_score:.1f} | "
                    f"Decision={sq_score.decision.name} | "
                    f"Reason={sq_score.reason}"
                )
                await asyncio.sleep(300)
                continue

            logger.info(
                f"[Cycle {cycle}][FORGE-08] Session tradeable. "
                f"Score={sq_score.composite_score:.1f} | "
                f"Decision={sq_score.decision.name} | "
                f"Best setups={sq_score.best_setups_for_today}"
            )

            trade_results = await run_session_cycle(
                adapter=adapter,
                account=account,
                sq_score=sq_score,
                session_tracker=session_tracker,
                consecutive_losses=consecutive_losses,
                daily_pnl_pct=daily_pnl_pct,
            )

            for result in trade_results:
                if result.success:
                    session_wins       += 1
                    consecutive_losses  = 0
                    recent_position_sizes.append(result.size)
                    recent_position_sizes = recent_position_sizes[-20:]
                else:
                    session_losses     += 1
                    consecutive_losses += 1

            await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info("TITAN FORGE — Shutdown signal. Closing gracefully.")
            await adapter.disconnect()
            return

        except Exception as e:
            logger.error(
                f"[Cycle {cycle}] Unexpected error: {type(e).__name__}: {e}. "
                f"Retrying in 60 seconds."
            )
            await asyncio.sleep(60)
            continue


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    cleared = run_simulation_training()
    if not cleared:
        logger.error(
            "FORGE failed to clear simulation training. "
            "Review strategy configuration before redeploying."
        )
        await asyncio.sleep(600)
        return

    logger.info("TITAN FORGE — Simulation cleared. Connecting to FTMO MT5...")

    adapter = MT5Adapter(
        account_id=os.environ.get("FTMO_ACCOUNT_ID", ""),
        server=os.environ.get("FTMO_API_URL", ""),
        password=os.environ.get("FTMO_PASSWORD", ""),
        is_demo=os.environ.get("FTMO_IS_DEMO", "true").lower() == "true",
    )

    connected = await adapter.connect()
    if not connected:
        logger.error(
            "FORGE failed to connect to FTMO MT5. "
            "Check Railway env vars: FTMO_ACCOUNT_ID, FTMO_API_URL, FTMO_PASSWORD."
        )
        await asyncio.sleep(300)
        return

    logger.info(
        f"TITAN FORGE — Connected. "
        f"Mode: {'DEMO' if adapter.is_demo else 'LIVE'}. "
        f"Account: {adapter.account_id}."
    )

    # ── SYMBOL DISCOVERY ON BOOT ──────────────────────────────────────────
    logger.info("[TICKER] Fetching full symbol list from MetaAPI...")
    syms = await fetch_all_symbols(adapter.account_id, adapter=adapter)
    if syms:
        logger.info("[TICKER] All available symbols (%d total): %s", len(syms), syms)
    else:
        logger.warning("[TICKER] Could not fetch symbol list — will try hardcoded aliases.")

    await live_trading_loop(adapter)


if __name__ == "__main__":
    asyncio.run(main())
