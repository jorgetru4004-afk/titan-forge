"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     main.py — EXECUTION ENGINE WIRED                        ║
║                                                                              ║
║  VERSION 3 — Execution engine live. FORGE now scans, scores, and trades.   ║
║                                                                              ║
║  WHAT CHANGED (v2 → v3):                                                    ║
║    - SessionTracker: tracks ORB range + session H/L across 60s cycles      ║
║    - check_signal_for_setup(): routes setup IDs to signal generators        ║
║    - run_session_cycle(): score → signal → size → place                    ║
║    - manage_open_position(): FORGE-64 profit lock on every open position   ║
║    - Trade results update session_wins/losses/consecutive_losses            ║
║    - Behavioral FLAGGED now blocks execution (was just logged)              ║
║                                                                              ║
║  DATA APPROXIMATIONS (until Polygon/Alpaca feed is integrated):             ║
║    - ATR:    0.1% of price for indices, 0.05% for forex                    ║
║    - VWAP:   Session open price                                             ║
║    - Volume: Assumed confirmed (TODO: real volume from data feed)           ║
║    - ORB:    Tracked from first cycle, locked after 9:45am ET              ║
║                                                                              ║
║  ⚠ INSTRUMENT NAMES: Verify NAS100 exact ticker on your OANDA demo         ║
║    account — may be "US100", "XNASDAQ100", or "US100"                     ║
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
# SETUP CONFIGURATION
# Maps session quality setup IDs → instruments + signal generators + stats
# ─────────────────────────────────────────────────────────────────────────────

SETUP_CONFIG: dict[str, dict] = {
    "ICT-01": {
        "instrument":     "US100",     # ⚠ Verify exact name on OANDA demo
        "signal_fn":      "vwap_reclaim",
        "win_rate":       0.62,
        "avg_rr":         2.0,
        "catalyst_stack": 3,
        "base_size":      0.01,
    },
    "ORD-02": {
        "instrument":     "US100",
        "signal_fn":      "orb",
        "win_rate":       0.68,
        "avg_rr":         2.2,
        "catalyst_stack": 3,
        "base_size":      0.01,
    },
    "VOL-03": {
        "instrument":     "US100",
        "signal_fn":      "trend_momentum",
        "win_rate":       0.58,
        "avg_rr":         2.5,
        "catalyst_stack": 2,
        "base_size":      0.01,
    },
    "VOL-05": {
        "instrument":     "US100",
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
# Maintains per-instrument price history across the 60-second cycles.
# Eliminates the need for a live data feed for ORB range + session H/L.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InstrumentSession:
    """Intraday state for one instrument."""
    open_price:   Optional[float] = None
    session_high: Optional[float] = None
    session_low:  Optional[float] = None
    orb_high:     Optional[float] = None   # Locked at 9:45am ET
    orb_low:      Optional[float] = None
    orb_locked:   bool = False


class SessionTracker:
    """
    Tracks intraday data across cycles using prices from the adapter.
    Resets automatically at session open (when open_price is None).
    """

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

        # Update running high/low
        if s.session_high is None or mid > s.session_high:
            s.session_high = mid
        if s.session_low is None or mid < s.session_low:
            s.session_low = mid

        # Lock ORB range once after 9:45am ET
        # TODO: proper DST handling — currently hardcodes UTC-5
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
        """Reset all sessions (call at start of new trading day)."""
        self._data.clear()
        logger.info("[SESSION] Session tracker reset for new day.")


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL DISPATCH
# Routes setup_id → correct FORGE signal generator with best available data.
# ─────────────────────────────────────────────────────────────────────────────

def check_signal_for_setup(
    setup_id:    str,
    config:      dict,
    mid:         float,
    session:     InstrumentSession,
    now_et_time: dtime,
    sq_score:    SessionQualityScore,
) -> Signal:
    """
    Dispatch setup_id to the correct signal generator.
    Uses session-tracked data for ORB range and VWAP approximation.
    Volume is approximated until Polygon/Alpaca feed is integrated.
    """
    fn  = config.get("signal_fn", "")
    # ATR approximation: 0.1% for indices, 0.05% for forex
    is_forex = config["instrument"] in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
                                        "USDCHF", "USDCAD", "NZDUSD")
    atr = mid * (0.0005 if is_forex else 0.001)

    # ── Opening Range Breakout (ORD-02) ───────────────────────────────────────
    if fn == "orb":
        if session.orb_locked and session.orb_high and session.orb_low:
            rh = session.orb_high
            rl = session.orb_low
        else:
            # ORB not yet locked — return PENDING so we wait
            rh = mid + atr * 5
            rl = mid - atr * 5

        return check_opening_range_breakout(
            current_price=mid,
            range_high=rh,
            range_low=rl,
            current_time_et=now_et_time,
            current_volume=2.5,    # TODO: real volume from Polygon/Alpaca
            avg_volume=1.0,
            atr=atr,
        )

    # ── VWAP Reclaim (ICT-01) ─────────────────────────────────────────────────
    elif fn == "vwap_reclaim":
        vwap   = session.open_price or mid
        # Dip detected if session_low traded 0.1% below session open
        dipped = (
            session.session_low is not None and
            session.session_low < vwap * 0.999
        )
        return check_vwap_reclaim(
            current_price=mid,
            prior_close=session.open_price or mid,
            vwap=vwap,
            dipped_below=dipped,
            volume_at_reclaim=1.5,  # TODO: real volume
            avg_volume=1.0,
            atr=atr,
        )

    # ── Trend Day Momentum (VOL-03) ───────────────────────────────────────────
    elif fn == "trend_momentum":
        # Proxy GEX negative: session quality above 6.5 suggests trending day
        gex_neg   = sq_score.composite_score >= 6.5
        vwap      = session.open_price or mid
        direction = "bullish" if mid > vwap else "bearish"
        # First pullback: price pulled back from session high by at least 0.05%
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

    # ── Mean Reversion (VOL-05) ───────────────────────────────────────────────
    elif fn == "mean_reversion":
        # Proxy GEX positive: session quality below 7.0 suggests ranging
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

    # ── London Session Forex (SES-01) ─────────────────────────────────────────
    elif fn == "london_forex":
        return check_london_session_forex(
            pair=config["instrument"],
            current_time_et=now_et_time,
            is_evaluation=True,
        )

    # ── Unknown ───────────────────────────────────────────────────────────────
    else:
        return Signal(
            setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
            f"No signal generator mapped for signal_fn='{fn}'.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# PROFIT LOCK — FORGE-64
# Runs on every open position each cycle.
# ─────────────────────────────────────────────────────────────────────────────

async def manage_open_position(adapter: MT5Adapter, pos, account) -> None:
    """
    FORGE-64: Three-stage profit lock.
      0.5R → Move SL to breakeven
      1.5R → Close 30% (partial exit)
      3.0R → Trail SL to 2R level
    """
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

    # ── Stage 1: 0.5R → Breakeven ─────────────────────────────────────────────
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

    # ── Stage 2: 1.5R → Close 30% ─────────────────────────────────────────────
    if r >= 1.5:
        partial_size = round(pos.size * 0.30, 2)
        if partial_size >= 0.01:
            try:
                await adapter.close_position(pos.position_id, size=partial_size)
                logger.info("[FORGE-64][%s] 1.5R → Closed 30%% (%.2f lots)",
                            pos.position_id, partial_size)
            except Exception as e:
                logger.error("[FORGE-64] close_position error: %s", e)

    # ── Stage 3: 3R → Trail SL to 2R ─────────────────────────────────────────
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
# Replaces the "Awaiting execution engine wire-up" placeholder.
# Called once per 60-second loop iteration.
# ─────────────────────────────────────────────────────────────────────────────

async def run_session_cycle(
    adapter:            MT5Adapter,
    account,
    sq_score:           SessionQualityScore,
    session_tracker:    SessionTracker,
    consecutive_losses: int,
    daily_pnl_pct:      float,
) -> list:
    """
    One complete execution cycle:
      1. FORGE-64: Profit lock on all open positions
      2. P-07: Enforce max 2 simultaneous positions
      3. For each approved setup: score → signal → size → place
    Returns list of OrderResult from any orders placed this cycle.
    """
    results = []
    firm_id = os.environ.get("FTMO_ACCOUNT_ID", "FTMO")

    now_utc     = datetime.now(timezone.utc)
    now_et      = now_utc - timedelta(hours=5)   # TODO: proper DST
    now_et_time = now_et.time()

    # ── 1. PROFIT LOCK — manage every open position ────────────────────────────
    for pos in account.open_positions:
        try:
            await manage_open_position(adapter, pos, account)
        except Exception as e:
            logger.error("[FORGE-64] Error on position %s: %s", pos.position_id, e)

    # ── 2. POSITION LIMIT — P-07 max 2 simultaneous ────────────────────────────
    current_positions = account.open_position_count
    if current_positions >= 2:
        logger.info(
            "[EXECUTE] P-07: At max positions (%d/2). No new entries this cycle.",
            current_positions,
        )
        return results

    # ── 3. ACCOUNT METRICS ─────────────────────────────────────────────────────
    drawdown_pct_used   = max(0.0, min(1.0, -daily_pnl_pct / 0.05))
    profit_pct_complete = 0.0   # TODO: integrate evaluation_state.py

    # ── 4. SCAN SETUPS ─────────────────────────────────────────────────────────
    for setup_id in sq_score.best_setups_for_today:
        if current_positions >= 2:
            break

        config = SETUP_CONFIG.get(setup_id)
        if not config:
            logger.debug("[EXECUTE][%s] No config found — skipping.", setup_id)
            continue

        instrument = config["instrument"]

        # ── Opportunity score ─────────────────────────────────────────────
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

        # ── Fetch live price ──────────────────────────────────────────────
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

        # ── Update session tracker ────────────────────────────────────────
        session = session_tracker.update(instrument, mid, now_utc)

        # ── Check signal ──────────────────────────────────────────────────
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

        # ── Dynamic position sizing ────────────────────────────────────────
        base_size = config["base_size"] * opp.size_multiplier
        sizing    = calculate_dynamic_size(
            base_size=base_size,
            profit_pct_complete=profit_pct_complete,
            is_funded=False,
            consecutive_losses=consecutive_losses,
            recent_loss_pct=max(0.0, -daily_pnl_pct),
        )
        final_size = max(0.01, round(sizing.final_size, 2))
        logger.info("[EXECUTE][%s] Sizing: %s", setup_id, sizing.reason)

        # ── Stop loss is required (FORGE-11) ──────────────────────────────
        if signal.stop_price is None:
            logger.warning(
                "[EXECUTE][%s] No stop loss provided by signal — skipping. "
                "FORGE-11 requires SL on every order.",
                setup_id,
            )
            continue

        # ── Place order ────────────────────────────────────────────────────
        direction = (
            OrderDirection.LONG if signal.direction == "long" else OrderDirection.SHORT
        )
        request = OrderRequest(
            instrument=instrument,
            direction=direction,
            size=final_size,
            order_type=OrderType.MARKET,
            stop_loss=signal.stop_price,
            take_profit=signal.target_price,
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
            f"{signal.target_price:.5f}" if signal.target_price else "NONE",
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
    """Run simulation cycles until FORGE clears for live trading."""
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
    """Returns True if any tradeable session is currently active."""
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        return False
    hour = now_utc.hour
    return 0 <= hour < 21


def seconds_until_next_session() -> int:
    """Seconds until the next session opens."""
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
    """
    Continuous live trading loop. Never returns.
    Each 60-second cycle: health → account → behavioral → session quality
                          → execution engine → sleep.
    """
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
            # ── RESET SESSION TRACKER ON NEW DAY ──────────────────────────────
            today = date.today()
            if today != last_session_date:
                session_tracker.reset()
                session_wins      = 0
                session_losses    = 0
                consecutive_losses = 0
                last_session_date  = today
                logger.info("[Cycle %d] New session day — trackers reset.", cycle)

            # ── WAIT FOR MARKET SESSION ────────────────────────────────────────
            if not is_market_session_active():
                wait_seconds = seconds_until_next_session()
                logger.info(
                    f"[Cycle {cycle}] Market closed. "
                    f"Sleeping {wait_seconds // 60}m until next session."
                )
                await asyncio.sleep(wait_seconds)
                continue

            # ── HEALTH CHECK ───────────────────────────────────────────────────
            health = await adapter.health_check()
            if not health.is_healthy:
                logger.warning(
                    f"[Cycle {cycle}] Platform unhealthy: {health.error}. Reconnecting..."
                )
                await adapter.connect()
                await asyncio.sleep(30)
                continue

            # ── ACCOUNT STATE ──────────────────────────────────────────────────
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

            # ── BEHAVIORAL CONSISTENCY CHECK — FORGE-56 ────────────────────────
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

            # ── SESSION QUALITY CHECK — FORGE-08 ──────────────────────────────
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

            # ── EXECUTION ENGINE ───────────────────────────────────────────────
            trade_results = await run_session_cycle(
                adapter=adapter,
                account=account,
                sq_score=sq_score,
                session_tracker=session_tracker,
                consecutive_losses=consecutive_losses,
                daily_pnl_pct=daily_pnl_pct,
            )

            # ── UPDATE SESSION STATS ───────────────────────────────────────────
            for result in trade_results:
                if result.success:
                    session_wins       += 1
                    consecutive_losses  = 0
                    recent_position_sizes.append(result.size)
                    recent_position_sizes = recent_position_sizes[-20:]
                else:
                    session_losses     += 1
                    consecutive_losses += 1

            # ── SLEEP UNTIL NEXT CYCLE ─────────────────────────────────────────
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
    """
    1. Simulation training until cleared.
    2. Connect to FTMO via MT5Adapter.
    3. Enter live trading loop — never exits.
    """

    # ── STEP 1: SIMULATION ────────────────────────────────────────────────────
    cleared = run_simulation_training()
    if not cleared:
        logger.error(
            "FORGE failed to clear simulation training. "
            "Review strategy configuration before redeploying."
        )
        await asyncio.sleep(600)
        return

    # ── STEP 2: CONNECT ───────────────────────────────────────────────────────
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

    # ── STEP 3: LIVE LOOP — NEVER EXITS ──────────────────────────────────────
    await live_trading_loop(adapter)


if __name__ == "__main__":
    asyncio.run(main())
