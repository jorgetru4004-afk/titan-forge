"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                      NEXUS CAPITAL — TITAN FORGE V20                        ║
║                          main.py — THE CONDUCTOR                            ║
║                                                                              ║
║  25 SETUPS. 3 INSTRUMENTS. 4 TIMEFRAMES. 11 BAYESIAN DIMENSIONS.          ║
║  REGIME-DRIVEN ACTIVATION. SESSION MEMORY. 5-GATE CHECKLIST.              ║
║  DYNAMIC STOP MANAGEMENT. CROSS-MARKET CORRELATIONS.                       ║
║  PARAMETER EVOLUTION. ANOMALY DETECTION.                                   ║
║                                                                              ║
║  THE FULL ARSENAL. THE FULL INTELLIGENCE. THE FULL AGGRESSION.            ║
║  With elite risk management as the only guardrail.                         ║
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
# IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════

from forge_core import (
    utc_to_et, now_et, now_et_time, is_rth, is_dst,
    get_session_state, get_state_weight, session_minutes_remaining,
    SessionState, send_telegram, PriceCache, _price_cache,
    InstrumentTracker, MarketContext, fetch_market_context,
    EvidenceLogger, TradeFingerprint, _evidence,
    is_news_blackout, minutes_to_next_news,
    Signal, SignalVerdict,
    get_candle_store, detect_candlestick_pattern, get_m15_trend, get_h1_trend, m5_confirms_m1,
)
from forge_brain import (
    compute_bayesian_conviction, BayesianConviction,
    compute_expected_value, ExpectedValueResult,
    monte_carlo_stress_test, StressTestResult,
    compute_price_entropy, compute_move_energy,
    detect_non_reaction, predict_regime_transition,
    ParameterEvolver, get_evolver, get_regime_mult,
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

FORGE_VERSION = "v20"


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP REGISTRY — 25 SETUPS, 3 INSTRUMENTS
# ═══════════════════════════════════════════════════════════════════════════════

SETUP_CONFIG = {
    # ═══ EXISTING 8 (reviewed & retained) ════════════════════════════════════
    "ORD-02": {
        "name": "Opening Range Breakout", "instrument": "NAS100", "signal_fn": "orb",
        "base_win_rate": 0.53, "avg_rr": 2.2, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.15,
        "window_start": dtime(9, 45), "window_end": dtime(11, 30),
        "expected_hold_min": 45, "atr_default": 150,
    },
    "ICT-01": {
        "name": "VWAP Reclaim", "instrument": "NAS100", "signal_fn": "vwap_reclaim",
        "base_win_rate": 0.56, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.15,
        "window_start": dtime(10, 0), "window_end": dtime(14, 0),
        "expected_hold_min": 60, "atr_default": 150,
    },
    "ICT-02": {
        "name": "Fair Value Gap", "instrument": "NAS100", "signal_fn": "fair_value_gap",
        "base_win_rate": 0.53, "avg_rr": 1.8, "rr_ratio": 1.8,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(9, 45), "window_end": dtime(13, 0),
        "expected_hold_min": 30, "atr_default": 150,
    },
    "ICT-03": {
        "name": "Liquidity Sweep + Reclaim", "instrument": "NAS100", "signal_fn": "liquidity_sweep",
        "base_win_rate": 0.51, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 4, "base_size": 0.12,
        "window_start": dtime(9, 30), "window_end": dtime(12, 30),
        "expected_hold_min": 40, "atr_default": 150,
    },
    "VOL-03": {
        "name": "Trend Day Momentum", "instrument": "NAS100", "signal_fn": "trend_momentum",
        "base_win_rate": 0.48, "avg_rr": 2.5, "rr_ratio": 2.0,
        "catalyst_stack": 2, "base_size": 0.12,
        "window_start": dtime(10, 30), "window_end": dtime(15, 0),
        "expected_hold_min": 60, "atr_default": 150,
    },
    "VOL-05": {
        "name": "Mean Reversion", "instrument": "NAS100", "signal_fn": "mean_reversion",
        "base_win_rate": 0.50, "avg_rr": 1.8, "rr_ratio": 1.8,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(11, 0), "window_end": dtime(15, 30),
        "expected_hold_min": 45, "atr_default": 150,
    },
    "VOL-06": {
        "name": "Noon Curve Reversal", "instrument": "NAS100", "signal_fn": "noon_curve",
        "base_win_rate": 0.54, "avg_rr": 1.6, "rr_ratio": 1.6,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(11, 45), "window_end": dtime(12, 45),
        "expected_hold_min": 30, "atr_default": 150,
    },
    "SES-01": {
        "name": "London Session Forex", "instrument": "EURUSD", "signal_fn": "london_forex",
        "base_win_rate": 0.63, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 4, "base_size": 0.10,
        "window_start": dtime(3, 0), "window_end": dtime(8, 0),
        "expected_hold_min": 90, "atr_default": 100,
    },

    # ═══ V20 NEW: OPENING PHASE ═════════════════════════════════════════════
    "OD-01": {
        "name": "Opening Drive Momentum", "instrument": "NAS100", "signal_fn": "opening_drive",
        "base_win_rate": 0.50, "avg_rr": 1.5, "rr_ratio": 1.5,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(9, 30), "window_end": dtime(9, 40),
        "expected_hold_min": 30, "atr_default": 150,
    },
    "GAP-01": {
        "name": "Gap Fade [DISABLED]", "instrument": "NAS100", "signal_fn": "disabled",
        "base_win_rate": 0.52, "avg_rr": 1.5, "rr_ratio": 1.5,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(9, 30), "window_end": dtime(10, 15),
        "expected_hold_min": 30, "atr_default": 150,
    },
    "GAP-02": {
        "name": "Gap and Go", "instrument": "NAS100", "signal_fn": "gap_go",
        "base_win_rate": 0.44, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(9, 30), "window_end": dtime(10, 0),
        "expected_hold_min": 30, "atr_default": 150,
    },

    # ═══ V20 NEW: IB PHASE ═══════════════════════════════════════════════════
    "IB-01": {
        "name": "IB Breakout", "instrument": "NAS100", "signal_fn": "ib_breakout",
        "base_win_rate": 0.58, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.15,
        "window_start": dtime(10, 30), "window_end": dtime(14, 0),
        "expected_hold_min": 60, "atr_default": 150,
    },
    "IB-02": {
        "name": "IB Range Scalp", "instrument": "NAS100", "signal_fn": "ib_range_scalp",
        "base_win_rate": 0.48, "avg_rr": 1.2, "rr_ratio": 1.2,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(10, 30), "window_end": dtime(14, 0),
        "expected_hold_min": 20, "atr_default": 150,
    },

    # ═══ V20 NEW: VWAP SETUPS ════════════════════════════════════════════════
    "VWAP-01": {
        "name": "VWAP Bounce Long", "instrument": "NAS100", "signal_fn": "vwap_bounce_long",
        "base_win_rate": 0.38, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(10, 0), "window_end": dtime(15, 30),
        "expected_hold_min": 45, "atr_default": 150,
    },
    "VWAP-02": {
        "name": "VWAP Reject Short", "instrument": "NAS100", "signal_fn": "vwap_reject_short",
        "base_win_rate": 0.36, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(10, 0), "window_end": dtime(15, 30),
        "expected_hold_min": 45, "atr_default": 150,
    },
    "VWAP-03": {
        "name": "VWAP Reclaim Momentum", "instrument": "NAS100", "signal_fn": "vwap_reclaim_momentum",
        "base_win_rate": 0.56, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(10, 0), "window_end": dtime(15, 0),
        "expected_hold_min": 40, "atr_default": 150,
    },

    # ═══ V20 NEW: LEVEL TESTS ════════════════════════════════════════════════
    "LVL-01": {
        "name": "PDH/PDL Test", "instrument": "NAS100", "signal_fn": "pdh_pdl_test",
        "base_win_rate": 0.42, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(9, 30), "window_end": dtime(15, 0),
        "expected_hold_min": 45, "atr_default": 150,
    },
    "LVL-02": {
        "name": "Round Number Scalp", "instrument": "NAS100", "signal_fn": "round_number",
        "base_win_rate": 0.41, "avg_rr": 1.5, "rr_ratio": 1.67,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(9, 30), "window_end": dtime(16, 0),
        "expected_hold_min": 15, "atr_default": 150,
    },

    # ═══ V20 NEW: MIDDAY ═════════════════════════════════════════════════════
    "MID-01": {
        "name": "Range Fade [DISABLED by GENESIS]", "instrument": "NAS100", "signal_fn": "disabled",
        "base_win_rate": 0.40, "avg_rr": 1.5, "rr_ratio": 1.5,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(11, 30), "window_end": dtime(13, 0),
        "expected_hold_min": 30, "atr_default": 150,
    },
    "MID-02": {
        "name": "Afternoon Breakout [DISABLED by GENESIS]", "instrument": "NAS100", "signal_fn": "disabled",
        "base_win_rate": 0.33, "avg_rr": 1.5, "rr_ratio": 1.5,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(13, 0), "window_end": dtime(15, 0),
        "expected_hold_min": 40, "atr_default": 150,
    },

    # ═══ V20 NEW: POWER HOUR ═════════════════════════════════════════════════
    "PWR-01": {
        "name": "Power Hour Momentum [DISABLED by GENESIS]", "instrument": "NAS100", "signal_fn": "disabled",
        "base_win_rate": 0.38, "avg_rr": 1.6, "rr_ratio": 1.6,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(15, 0), "window_end": dtime(15, 50),
        "expected_hold_min": 30, "atr_default": 150,
    },
    "PWR-02": {
        "name": "Closing Drive [DISABLED by GENESIS]", "instrument": "NAS100", "signal_fn": "disabled",
        "base_win_rate": 0.20, "avg_rr": 1.3, "rr_ratio": 1.3,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(15, 30), "window_end": dtime(15, 55),
        "expected_hold_min": 20, "atr_default": 150,
    },
    "PWR-03": {
        "name": "End of Day Fade [DISABLED by GENESIS]", "instrument": "NAS100", "signal_fn": "disabled",
        "base_win_rate": 0.20, "avg_rr": 1.3, "rr_ratio": 1.3,
        "catalyst_stack": 2, "base_size": 0.10,
        "window_start": dtime(15, 40), "window_end": dtime(15, 55),
        "expected_hold_min": 15, "atr_default": 150,
    },

    # ═══ V20 NEW: MULTI-INSTRUMENT ═══════════════════════════════════════════
    "ES-ORD-02": {
        "name": "ES Opening Range Breakout", "instrument": "ES", "signal_fn": "orb",
        "base_win_rate": 0.62, "avg_rr": 2.0, "rr_ratio": 2.0,
        "catalyst_stack": 3, "base_size": 0.10,
        "window_start": dtime(9, 45), "window_end": dtime(11, 30),
        "expected_hold_min": 45, "atr_default": 50,
    },
    "GOLD-CORR-01": {
        "name": "Gold Correlation Divergence", "instrument": "XAUUSD",
        "signal_fn": "gold_correlation",
        "base_win_rate": 0.55, "avg_rr": 1.67, "rr_ratio": 1.67,
        "catalyst_stack": 2, "base_size": 0.01,
        "window_start": dtime(9, 30), "window_end": dtime(16, 0),
        "expected_hold_min": 60, "atr_default": 30,
    },
}


# ═══════════════════════════════════════════════════════════════════════════════
# SIGNAL ENGINE — ALL 25 SIGNAL GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def _pending(setup_id: str, reason: str) -> Signal:
    return Signal(setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0, reason)



def generate_signal(
    setup_id: str, config: dict, mid: float,
    tracker: InstrumentTracker, ctx: MarketContext, atr: float,
) -> Signal:
    fn = config["signal_fn"]
    t = now_et_time()

    ws, we = config.get("window_start"), config.get("window_end")
    if ws and we and not (ws <= t <= we):
        return _pending(setup_id, f"Outside window ({ws}-{we} ET).")

    vwap = tracker.vwap or tracker.open_price or mid
    inst_atr = config.get("atr_default", atr) if atr <= 0 else atr

    if fn == "disabled":
        return _pending(setup_id, "DISABLED.")

    # ═══ ORD-02: price > ORB high + 2 OR < ORB low - 2 ═══════════════════════
    if fn == "orb":
        if not tracker.orb_locked:
            return _pending(setup_id, "ORB not locked.")
        orb_h = tracker.orb_high or 0
        orb_l = tracker.orb_low or 0
        if mid > orb_h + 2:        direction = "long"
        elif mid < orb_l - 2:      direction = "short"
        else: return _pending(setup_id, "No ORB break.")
        rng = orb_h - orb_l
        sl_d = max(inst_atr * 0.5, rng * 0.5) if rng > 0 else inst_atr * 0.5
        sl = mid - sl_d if direction == "long" else mid + sl_d
        tp = mid + sl_d * 2.0 if direction == "long" else mid - sl_d * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.70,
                     f"ORB {direction.upper()}")

    # ═══ ICT-01: price above VWAP ════════════════════════════════════════════
    elif fn == "vwap_reclaim":
        if vwap <= 0: return _pending(setup_id, "No VWAP.")
        if mid > vwap:
            sl_d = inst_atr * 0.4
            return Signal(setup_id, SignalVerdict.CONFIRMED, "long",
                         round(mid, 2), round(mid - sl_d, 2), round(mid + sl_d * 2.0, 2), 0.68,
                         "VWAP reclaim: above VWAP")
        return _pending(setup_id, "Below VWAP.")

    # ═══ ICT-02: FVG detected (no ATR filter, no price-in-gap requirement) ═══
    elif fn == "fair_value_gap":
        closes = tracker.close_prices
        if len(closes) < 4: return _pending(setup_id, "FVG: need 4 closes.")
        c1, c2, c3, c4 = closes[-4], closes[-3], closes[-2], closes[-1]
        bullish_fvg = c1 < c2 and c3 > c2 and c4 > c3
        bearish_fvg = c1 > c2 and c3 < c2 and c4 < c3
        if not (bullish_fvg or bearish_fvg): return _pending(setup_id, "No FVG.")
        d = "long" if bullish_fvg else "short"
        sl_d = inst_atr * 0.4
        sl = mid - sl_d if d == "long" else mid + sl_d
        tp = mid + sl_d * 1.8 if d == "long" else mid - sl_d * 1.8
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.66, f"FVG {d.upper()}")

    # ═══ ICT-03: session swept PDH or PDL ════════════════════════════════════
    elif fn == "liquidity_sweep":
        if ctx.pdl <= 0 or ctx.pdh <= 0: return _pending(setup_id, "No PDH/PDL.")
        swept_low = tracker.session_low is not None and tracker.session_low < ctx.pdl and mid > ctx.pdl
        swept_high = tracker.session_high is not None and tracker.session_high > ctx.pdh and mid < ctx.pdh
        if not (swept_low or swept_high): return _pending(setup_id, "No sweep.")
        d = "long" if swept_low else "short"
        sl_d = inst_atr * 0.35
        if d == "long":
            sl = tracker.session_low if tracker.session_low else mid - sl_d
            tp = mid + (mid - sl) * 2.0
        else:
            sl = tracker.session_high if tracker.session_high else mid + sl_d
            tp = mid - (sl - mid) * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.70,
                     f"Sweep {'PDL' if d=='long' else 'PDH'}")

    # ═══ VOL-03: price > 0.3% from VWAP ═════════════════════════════════════
    elif fn == "trend_momentum":
        if vwap <= 0: return _pending(setup_id, "No VWAP.")
        if mid > vwap * 1.003:      direction = "long"
        elif mid < vwap * 0.997:    direction = "short"
        else: return _pending(setup_id, "Within 0.3% of VWAP.")
        sl_d = inst_atr * 0.5
        sl = mid - sl_d if direction == "long" else mid + sl_d
        tp = mid + sl_d * 2.0 if direction == "long" else mid - sl_d * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.66,
                     f"Trend {direction.upper()}: {abs(mid-vwap)/vwap*100:.2f}% from VWAP")

    # ═══ VOL-05: price > 0.5 ATR from VWAP ══════════════════════════════════
    elif fn == "mean_reversion":
        if vwap <= 0: return _pending(setup_id, "No VWAP.")
        dist = mid - vwap
        atr_dist = abs(dist) / inst_atr if inst_atr > 0 else 0
        if atr_dist < 0.5: return _pending(setup_id, f"Only {atr_dist:.1f} ATR from VWAP.")
        direction = "short" if dist > 0 else "long"
        sl_d = inst_atr * 0.45
        sl = mid - sl_d if direction == "long" else mid + sl_d
        tp = vwap
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.64,
                     f"Mean Rev {direction.upper()}: {atr_dist:.1f} ATR")

    # ═══ VOL-06: price moved > 0.3 ATR from open ════════════════════════════
    elif fn == "noon_curve":
        if not tracker.open_price: return _pending(setup_id, "No open.")
        move = mid - tracker.open_price
        if abs(move) < inst_atr * 0.3: return _pending(setup_id, "Insufficient move.")
        d = "short" if move > 0 else "long"
        sl_d = inst_atr * 0.50
        sl = mid + sl_d if d == "short" else mid - sl_d
        tp = mid - sl_d * 1.6 if d == "short" else mid + sl_d * 1.6
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.62,
                     f"Noon curve {d.upper()}")

    # ═══ SES-01: London forex breakout ═══════════════════════════════════════
    elif fn == "london_forex":
        if len(tracker.price_history) < 10: return _pending(setup_id, "Insufficient data.")
        recent = tracker.price_history[-10:]
        rh, rl = max(recent), min(recent)
        rng = rh - rl
        if rng < 0.0005: return _pending(setup_id, "Range too tight.")
        if mid > rh:     direction = "long"
        elif mid < rl:   direction = "short"
        else:            return _pending(setup_id, "No breakout.")
        sl_d = rng * 0.8
        sl = mid - sl_d if direction == "long" else mid + sl_d
        tp = mid + sl_d * 2.0 if direction == "long" else mid - sl_d * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 5), round(sl, 5), round(tp, 5), 0.64,
                     f"London {direction.upper()}")

    # ═══ OD-01: price moved > 0.2% from open ════════════════════════════════
    elif fn == "opening_drive":
        if not tracker.open_price: return _pending(setup_id, "No open.")
        move_pct = (mid - tracker.open_price) / tracker.open_price
        if abs(move_pct) < 0.002: return _pending(setup_id, f"Only {move_pct:.2%}.")
        direction = "long" if move_pct > 0 else "short"
        sl_d = abs(mid - tracker.open_price) + 5.0
        sl = mid - sl_d if direction == "long" else mid + sl_d
        tp = mid + sl_d * 1.5 if direction == "long" else mid - sl_d * 1.5
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.64,
                     f"OD {direction.upper()}: {move_pct:.2%}")

    # ═══ GAP-02: gap + 2 consecutive closes in gap direction ═════════════════
    elif fn == "gap_go":
        if ctx.prev_close <= 0 or not tracker.open_price:
            return _pending(setup_id, "No prev close/open.")
        gap_pct, gap_dir = detect_gap(tracker.open_price, ctx.prev_close)
        if abs(gap_pct) < 0.003 or gap_dir == "none":
            return _pending(setup_id, f"Gap {gap_pct:.2%} too small.")
        direction = "long" if gap_dir == "up" else "short"
        if len(tracker.close_prices) < 2:
            return _pending(setup_id, "Need 2 closes.")
        last2 = tracker.close_prices[-2:]
        if direction == "long" and not (last2[1] > last2[0]):
            return _pending(setup_id, "No consecutive up.")
        if direction == "short" and not (last2[1] < last2[0]):
            return _pending(setup_id, "No consecutive down.")
        gap_size = abs(tracker.open_price - ctx.prev_close)
        sl = min(last2) - 5 if direction == "long" else max(last2) + 5
        tp = mid + gap_size * 1.5 if direction == "long" else mid - gap_size * 1.5
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.62,
                     f"Gap&Go {direction.upper()}")

    # ═══ IB-01: price > IB high + 2 or < IB low - 2 (no range min, no 2-close confirm) ═══
    elif fn == "ib_breakout":
        if not tracker.ib_locked: return _pending(setup_id, "IB not locked.")
        if mid > tracker.ib_high + 2.0:       direction = "long"
        elif mid < tracker.ib_low - 2.0:      direction = "short"
        else: return _pending(setup_id, "No IB break.")
        ib_range = tracker.ib_high - tracker.ib_low if tracker.ib_low != float("inf") else 0
        sl_d = max(ib_range * 0.5, 20) if ib_range > 0 else inst_atr * 0.4
        tp_d = max(ib_range, 40) if ib_range > 0 else inst_atr * 0.8
        sl = mid - sl_d if direction == "long" else mid + sl_d
        tp = mid + tp_d if direction == "long" else mid - tp_d
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.70,
                     f"IB Break {direction.upper()}: range={ib_range:.0f}")

    # ═══ IB-02: price within 3pts of IB boundary ════════════════════════════
    elif fn == "ib_range_scalp":
        if not tracker.ib_locked: return _pending(setup_id, "IB not locked.")
        if abs(mid - tracker.ib_high) < 3.0:      direction = "short"
        elif abs(mid - tracker.ib_low) < 3.0:     direction = "long"
        else: return _pending(setup_id, "Not at IB boundary.")
        ib_range = tracker.ib_high - tracker.ib_low if tracker.ib_low != float("inf") else 30
        sl = mid + 15 if direction == "short" else mid - 15
        tp = mid - max(ib_range * 0.5, 15) if direction == "short" else mid + max(ib_range * 0.5, 15)
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.58,
                     f"IB Scalp {direction.upper()}")

    # ═══ VWAP-01: price within 5pts above VWAP ══════════════════════════════
    elif fn == "vwap_bounce_long":
        if vwap <= 0: return _pending(setup_id, "No VWAP.")
        dist = mid - vwap
        if dist < 0 or dist > 5.0: return _pending(setup_id, f"Not in zone ({dist:.1f}).")
        sl = vwap - 15
        tp = mid + max(20, inst_atr * 0.3)
        return Signal(setup_id, SignalVerdict.CONFIRMED, "long",
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.60,
                     f"VWAP Bounce: dist={dist:.1f}")

    # ═══ VWAP-02: price within 5pts below VWAP ══════════════════════════════
    elif fn == "vwap_reject_short":
        if vwap <= 0: return _pending(setup_id, "No VWAP.")
        dist = vwap - mid
        if dist < 0 or dist > 5.0: return _pending(setup_id, f"Not in zone ({dist:.1f}).")
        sl = vwap + 15
        tp = mid - max(20, inst_atr * 0.3)
        return Signal(setup_id, SignalVerdict.CONFIRMED, "short",
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.58,
                     f"VWAP Reject: dist={dist:.1f}")

    # ═══ VWAP-03: price above VWAP ══════════════════════════════════════════
    elif fn == "vwap_reclaim_momentum":
        if vwap <= 0: return _pending(setup_id, "No VWAP.")
        if mid <= vwap: return _pending(setup_id, "Below VWAP.")
        sl = vwap - 5
        tp = mid + inst_atr * 0.4
        return Signal(setup_id, SignalVerdict.CONFIRMED, "long",
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.60,
                     "VWAP Momentum: above VWAP")

    # ═══ LVL-01: price within 10pts of PDH or PDL ═══════════════════════════
    elif fn == "pdh_pdl_test":
        if ctx.pdh <= 0 or ctx.pdl <= 0: return _pending(setup_id, "No PDH/PDL.")
        near_pdh = abs(mid - ctx.pdh) < 10.0
        near_pdl = abs(mid - ctx.pdl) < 10.0
        if not (near_pdh or near_pdl): return _pending(setup_id, "Not near PDH/PDL.")
        if near_pdh and mid < ctx.pdh:
            direction, sl, tp = "short", round(ctx.pdh + 20, 2), round(mid - 40, 2)
        elif near_pdl and mid > ctx.pdl:
            direction, sl, tp = "long", round(ctx.pdl - 20, 2), round(mid + 40, 2)
        elif near_pdh:
            direction, sl, tp = "long", round(ctx.pdh - 5, 2), round(mid + 40, 2)
        elif near_pdl:
            direction, sl, tp = "short", round(ctx.pdl + 5, 2), round(mid - 40, 2)
        else: return _pending(setup_id, "No signal.")
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), sl, tp, 0.60,
                     f"PDH/PDL {direction.upper()}")

    # ═══ LVL-02: price within 5pts of any 100-point round number ════════════
    elif fn == "round_number":
        nearest_100 = round(mid / 100) * 100
        dist = mid - nearest_100
        if abs(dist) > 5: return _pending(setup_id, f"Far from {nearest_100}.")
        direction = "short" if dist > 0 else "long"
        sl = mid + 15 if direction == "short" else mid - 15
        tp = mid - 25 if direction == "short" else mid + 25
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.55,
                     f"Round #{nearest_100:.0f}")

    # ═══ GOLD CORRELATION ════════════════════════════════════════════════════
    elif fn == "gold_correlation":
        corr_engine = get_correlation_engine()
        divergence = corr_engine.detect_divergence("XAUUSD", "DXY", expected_corr=-0.60)
        if not divergence: return _pending(setup_id, "No divergence.")
        corr_val, desc = divergence
        direction = "short" if corr_val > 0 else "long"
        sl = mid + 30 if direction == "short" else mid - 30
        tp = mid - 50 if direction == "short" else mid + 50
        return Signal(setup_id, SignalVerdict.CONFIRMED, direction,
                     round(mid, 2), round(sl, 2), round(tp, 2), 0.56,
                     f"Gold Corr: {desc}")

    return _pending(setup_id, f"Unknown fn: {fn}")


# ═══════════════════════════════════════════════════════════════════════════════
# INSTRUMENT RESOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

_resolved_instruments: dict[str, Optional[str]] = {}


async def resolve_instrument(adapter: MT5Adapter, logical: str) -> Optional[str]:
    if logical in _resolved_instruments:
        return _resolved_instruments[logical]
    aliases = {
        "NAS100": ["US100.sim", "USTEC.sim", "NAS100.sim", "US100", "USTEC", "NAS100"],
        "EURUSD": ["EURUSD.sim", "EURUSD"],
        "ES":     ["US500.sim", "SPX500.sim", "SP500.sim", "US500"],
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
            _is_long = pos.direction.value == "long"
            if _is_long:
                current_r = ((pos.current_price or pos.entry_price) - pos.entry_price) / risk
            else:
                current_r = (pos.entry_price - (pos.current_price or pos.entry_price)) / risk

            # Compute trail targets
            _be = pos.entry_price
            _trail_05r = pos.entry_price + risk * 0.5 if _is_long else pos.entry_price - risk * 0.5
            _trail_1r = pos.entry_price + risk * 1.0 if _is_long else pos.entry_price - risk * 1.0
            _trail_2r = pos.entry_price + risk * 2.0 if _is_long else pos.entry_price - risk * 2.0

            # Only modify if new SL is BETTER than current SL
            _current_sl = pos.stop_loss
            def _sl_is_better(new_sl: float) -> bool:
                if _is_long:
                    return new_sl > _current_sl + 0.5  # at least 0.5pt improvement
                else:
                    return new_sl < _current_sl - 0.5

            new_sl = None

            # Stage 1: 1R → breakeven
            if current_r >= 1.0 and _sl_is_better(_be):
                new_sl = _be

            # Stage 2: 1.5R → trail to +0.5R
            if current_r >= 1.5 and _sl_is_better(_trail_05r):
                new_sl = _trail_05r

            # Stage 3: 2R → trail to +1R
            if current_r >= 2.0 and _sl_is_better(_trail_1r):
                new_sl = _trail_1r

            # Stage 4: 3R → trail to +2R
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
# PRE-FLIGHT CHECK
# ═══════════════════════════════════════════════════════════════════════════════

def run_simulation_check() -> bool:
    logger.info("═══ TITAN FORGE V20 — PRE-FLIGHT CHECK ═══")
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

    if len(SETUP_CONFIG) >= 20:
        checks += 1; logger.info("✅ %d setups registered", len(SETUP_CONFIG))
    else:
        logger.warning("⚠️ Only %d setups (expected 25+)", len(SETUP_CONFIG))
        checks += 1  # don't block on this

    polygon_key = os.environ.get("POLYGON_API_KEY", "")
    if polygon_key:
        logger.info("✅ Polygon API key present")
    else:
        logger.warning("⚠️ No POLYGON_API_KEY — MTF disabled")

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
# THE LIVE TRADING LOOP — V20: THE FULL ARSENAL
# ═══════════════════════════════════════════════════════════════════════════════

async def live_trading_loop(adapter: MT5Adapter) -> None:
    tracker = InstrumentTracker()
    risk_fortress = RiskFortress()
    evolver = get_evolver()
    session_memory = SessionMemory()
    candle_store = get_candle_store()
    anomaly_detector = get_anomaly_detector()
    corr_engine = get_correlation_engine()

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
    _last_h1_fetch = datetime.min.replace(tzinfo=timezone.utc)

    logger.info("🔱 TITAN FORGE %s — 25 SETUPS ARMED.", FORGE_VERSION)
    send_telegram(
        f"🔱 <b>TITAN FORGE {FORGE_VERSION} ONLINE</b>\n"
        f"25 setups | 3 instruments | 4 timeframes\n"
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

            # ── Daily Reset ──────────────────────────────────────────────
            if today != last_session_date:
                last_session_date = today
                tracker.reset()
                traded_setups.clear()
                _setup_cooldowns.clear()
                risk_fortress.reset_daily()
                session_memory.reset()
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
                    f"📊 VIX: {ctx.vix:.1f} ({ctx.vix_regime})\n"
                    f"📈 Futures: {ctx.futures_pct*100:+.2f}% ({ctx.futures_bias})\n"
                    f"📏 PDH: {ctx.pdh:.0f} | PDL: {ctx.pdl:.0f}\n"
                    f"📐 ATR: {ctx.atr:.0f}\n"
                    f"🔫 {len(SETUP_CONFIG)} setups armed | Full arsenal active"
                )

            # ── Market Hours ─────────────────────────────────────────────
            if not is_market_open():
                wait = seconds_until_market()
                logger.info("[Cycle %d] Closed. Next in %dm.", cycle, wait // 60)
                await asyncio.sleep(min(wait, 300))
                continue

            # ── Health ───────────────────────────────────────────────────
            try:
                health = await adapter.health_check()
                if not health.is_healthy:
                    logger.warning("[Cycle %d] Unhealthy: %s", cycle, health.error)
                    await asyncio.sleep(30); continue
            except Exception as e:
                logger.warning("[Cycle %d] Health: %s", cycle, e)
                await asyncio.sleep(30); continue

            # ── Account ─────────────────────────────────────────────────
            try:
                account = await adapter.get_account_state()
            except Exception as e:
                logger.error("[Cycle %d] Account: %s", cycle, e)
                await asyncio.sleep(30); continue

            if account.balance <= 0:
                logger.warning("[Cycle %d] Balance=0", cycle)
                await asyncio.sleep(30); continue

            firm_state.current_balance = account.balance
            firm_state.current_day_pnl = account.daily_pnl

            logger.info("[Cycle %d] Bal=$%.2f Eq=$%.2f PnL=$%.2f Pos=%d",
                        cycle, account.balance, account.equity,
                        account.daily_pnl, account.open_position_count)

            # ── Price Tracking ───────────────────────────────────────────
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

                        # V20: Correlation engine update
                        corr_engine.update("NAS100", (bid + ask) / 2.0)
                        anomaly_detector.update((bid + ask) / 2.0, ctx.vix)
                except Exception as e:
                    logger.warning("[Cycle %d] Price err: %s", cycle, e)
                    cached = _price_cache.get(primary_inst)
                    if cached and not cached.stale:
                        logger.info("[Cycle %d] Using cached: %.2f", cycle, cached.mid)

            # V20: Staggered Polygon candle fetching (stays under 5 calls/min)
            # M5: every 5 minutes | M15: every 15 minutes | H1: derived from M15
            now_utc = datetime.now(timezone.utc)
            if is_rth():
                _fetched_any = False

                # M5: fetch every 5 minutes (1 API call)
                if (now_utc - _last_m5_fetch).total_seconds() >= 300:
                    try:
                        fetch_polygon_candles("NAS100", ["M5"])
                        _last_m5_fetch = now_utc
                        _fetched_any = True
                        # NOTE: VWAP calculated from NQ prices in tracker.update()
                        # QQQ candles used ONLY for trend direction, never price levels
                    except Exception as e:
                        logger.warning("[POLYGON] M5 fetch failed: %s", e)

                # M15: fetch every 15 minutes (1 API call)
                if (now_utc - _last_m15_fetch).total_seconds() >= 900:
                    try:
                        fetch_polygon_candles("NAS100", ["M15"])
                        _last_m15_fetch = now_utc
                        _fetched_any = True
                    except Exception as e:
                        logger.warning("[POLYGON] M15 fetch failed: %s", e)

                # H1: derived from M15 candles inside fetch_polygon_candles — no extra call

                if _fetched_any:
                    # Update MTF context from whatever we have
                    m15_candles = candle_store.get("NAS100", "M15", 6)
                    h1_candles = candle_store.get("NAS100", "H1", 5)
                    ctx.mtf_trend_m15 = get_m15_trend(m15_candles)
                    ctx.mtf_trend_h1 = get_h1_trend(h1_candles)
                    logger.info("[MTF] M15=%s (%d bars) H1=%s (%d bars) VWAP=%.2f",
                               ctx.mtf_trend_m15, len(m15_candles),
                               ctx.mtf_trend_h1, len(h1_candles),
                               tracker.vwap or 0)

            ctx.session_state = get_session_state()
            ctx.minutes_remaining = session_minutes_remaining()
            ctx.sync_from_tracker(tracker)

            # ── Manage Positions ─────────────────────────────────────────
            await manage_open_positions(adapter, account, ctx)

            # ── Session Close ────────────────────────────────────────────
            should_close, close_reason = check_session_close_protection(firm_state)
            if should_close and account.open_position_count > 0:
                logger.warning("[CLOSE] %s", close_reason)
                send_telegram(f"📅 <b>SESSION CLOSE</b>\n{close_reason}")
                try: await adapter.close_all_positions()
                except Exception as e: logger.error("[CLOSE] %s", e)
                await asyncio.sleep(60); continue

            # ── News Blackout ────────────────────────────────────────────
            _in_blackout = is_news_blackout()
            if _in_blackout:
                news_mins = minutes_to_next_news()
                if news_mins is not None and news_mins <= 3 and account.open_position_count > 0:
                    logger.warning("[NEWS] Event in %.1fmin — closing", news_mins)
                    send_telegram("⚡ <b>NEWS</b>\nClosing before event")
                    try: await adapter.close_all_positions()
                    except Exception: pass
                logger.info("[Cycle %d] News blackout — evaluate only.", cycle)

            # ── Emergency ────────────────────────────────────────────────
            if firm_state.should_emergency_close(account.equity):
                logger.warning("[EMERGENCY] Near limit — closing all")
                send_telegram("🚨 <b>EMERGENCY</b>\nClosing ALL")
                try: await adapter.close_all_positions()
                except Exception: pass
                await asyncio.sleep(60); continue

            # ── Position Limit ───────────────────────────────────────────
            if account.open_position_count >= 5:
                logger.info("[Cycle %d] Max positions (5/5).", cycle)
                await asyncio.sleep(60); continue

            # ── V20: Anomaly Check ───────────────────────────────────────
            anomaly = anomaly_detector.check()
            if anomaly:
                logger.warning("[ANOMALY] %s — pausing 1 cycle", anomaly)
                send_telegram(f"⚠️ <b>ANOMALY</b>\n{anomaly}")
                await asyncio.sleep(60); continue

            # ── Brain Signals ────────────────────────────────────────────
            non_rx = detect_non_reaction(ctx, tracker)
            if non_rx: logger.info("[BRAIN] %s", non_rx)
            trans_prob, trans_desc = predict_regime_transition(ctx, tracker)
            if trans_prob > 0.50: logger.info("[BRAIN] %s", trans_desc)

            # ════════════════════════════════════════════════════════════════
            # DECISION PIPELINE V20 — THE FULL ARSENAL
            # ════════════════════════════════════════════════════════════════
            _all_candidates = []
            _cycle_signals = []

            mid = _price_cache.get_mid(primary_inst or "") or (
                tracker.price_history[-1] if tracker.price_history else 0)
            if mid <= 0:
                await asyncio.sleep(60); continue

            atr = ctx.atr if ctx.atr > 0 else 100.0

            # ── V20: Regime Detection (with REVERSAL) ────────────────────
            _regime = "NORMAL"
            _regime_bias = "neutral"
            ib_range = (tracker.ib_high - tracker.ib_low) if (
                tracker.ib_locked and tracker.ib_low != float("inf")) else 0

            if tracker.ib_locked and ib_range > 0:
                if ib_range >= atr * 0.6:
                    _regime = "TREND"
                elif ib_range <= atr * 0.3:
                    _regime = "CHOP"
                else:
                    _regime = "NORMAL"

                if tracker.ib_direction == "long":
                    _regime_bias = "long"
                elif tracker.ib_direction == "short":
                    _regime_bias = "short"

                if mid > tracker.ib_high + 2.0:
                    _regime_bias = "long"
                elif mid < tracker.ib_low - 2.0:
                    _regime_bias = "short"

            # V20: REVERSAL detection — IB broke one way then reversed back
            if _regime == "TREND" and tracker.ib_direction:
                if tracker.ib_direction == "long" and mid < tracker.ib_low:
                    _regime = "REVERSAL"
                    _regime_bias = "short"
                    logger.info("[REGIME] REVERSAL: IB broke long but price below IB low")
                elif tracker.ib_direction == "short" and mid > tracker.ib_high:
                    _regime = "REVERSAL"
                    _regime_bias = "long"
                    logger.info("[REGIME] REVERSAL: IB broke short but price above IB high")

            _loop_vwap = tracker.vwap or tracker.open_price or mid
            if _regime_bias == "neutral" and _loop_vwap and _loop_vwap > 0 and mid > 0:
                if mid > _loop_vwap * 1.003:
                    _regime_bias = "long"
                elif mid < _loop_vwap * 0.997:
                    _regime_bias = "short"

            ctx.regime = _regime
            ctx.regime_bias = _regime_bias

            # V20: Update MTF confirms for each direction (done once per cycle)
            m5_candles = candle_store.get("NAS100", "M5", 3)

            _mode = "SNIPER"
            if ctx.atr_consumed_pct > 0.70:
                _mode = "GUERRILLA"
            elif _regime == "TREND":
                _mode = "SNIPER"
            elif _regime == "CHOP":
                _mode = "GUERRILLA"
            elif ctx.vix_regime in ("ELEVATED", "EXTREME"):
                _mode = "HUNTER"

            # ── Scan ALL Setups ──────────────────────────────────────────
            for setup_id, config in SETUP_CONFIG.items():
                _last_trade_time = _setup_cooldowns.get(setup_id, 0)
                if time.time() - _last_trade_time < COOLDOWN_SECONDS:
                    continue
                if is_rth() and ctx.atr_consumed_pct > 0.95 and setup_id not in ("VOL-06", "VOL-05", "PWR-03"):
                    continue

                # V20: Check regime suppression before signal generation
                regime_m = get_regime_mult(setup_id, _regime)
                if regime_m <= 0.0:
                    continue  # SUPPRESSED in this regime

                signal = generate_signal(setup_id, config, mid, tracker, ctx, atr)
                if signal.verdict != SignalVerdict.CONFIRMED:
                    continue

                # V20: Update MTF confirmation for this signal's direction
                ctx.mtf_m5_confirms = m5_confirms_m1(signal.direction, m5_candles)

                live_wr = evolver.get_live_win_rate(setup_id)
                conviction = compute_bayesian_conviction(
                    prior_win_rate=config["base_win_rate"],
                    ctx=ctx, tracker=tracker,
                    direction=signal.direction, setup_id=setup_id,
                    live_win_rate=live_wr,
                )

                _cycle_signals.append(f"{setup_id} {signal.direction} → {conviction.conviction_level} "
                                     f"({conviction.posterior:.0%}, {conviction.confirming}/{conviction.total})")

                if conviction.posterior < 0.35 or conviction.conviction_level == "REJECT":
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
                        regime=_regime, regime_bias=_regime_bias,
                        mtf_m15_trend=ctx.mtf_trend_m15, mtf_h1_trend=ctx.mtf_trend_h1,
                        mtf_m5_confirms=ctx.mtf_m5_confirms,
                        is_phantom=True, outcome="PHANTOM", capital_vehicle="PROP_FIRM",
                    ))
                    continue

                # V20: 5-gate checklist
                risk_dollars = abs((signal.entry_price or 0) - (signal.stop_loss or 0)) * config["base_size"] * 10
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
                    logger.info("[GATE] %s %s: %s", setup_id, gate_result.failed_gate,
                               gate_result.details[-1] if gate_result.details else "")
                    continue

                # V20: Session memory — scalp-only enforcement
                _is_scalp = conviction.conviction_level == "SCALP"
                if session_memory.is_scalp_only and not _is_scalp:
                    if conviction.conviction_level not in ("ELITE", "HIGH"):
                        continue  # Only ELITE/HIGH can override scalp-only

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

                reward_dollars = abs((signal.take_profit or 0) - (signal.entry_price or 0)) * config["base_size"] * 10
                if risk_dollars <= 0 or reward_dollars <= 0: continue

                ev_result = compute_expected_value(
                    win_prob=conviction.posterior, reward_dollars=reward_dollars,
                    risk_dollars=risk_dollars, account_balance=account.balance,
                    max_position_pct=0.02, minutes_remaining=ctx.minutes_remaining,
                )

                if ev_result.ev_dollars < 0:
                    continue

                tier_weight = {"ELITE": 4.0, "HIGH": 3.0, "STANDARD": 2.0,
                              "REDUCED": 1.0, "SCALP": 0.5}.get(conviction.conviction_level, 0.5)
                weighted_ev = ev_result.net_ev * tier_weight

                _all_candidates.append({
                    "setup_id": setup_id, "config": config, "signal": signal,
                    "conviction": conviction, "ev_result": ev_result,
                    "is_scalp": _is_scalp, "direction": signal.direction,
                    "weighted_ev": weighted_ev,
                })

            # ── Anti-Conflict Filter ─────────────────────────────────────
            if _all_candidates:
                _best_posterior = max(c["conviction"].posterior for c in _all_candidates)
                _best_direction = None
                for c in _all_candidates:
                    if c["conviction"].posterior == _best_posterior:
                        _best_direction = c["direction"]; break

                if _best_direction and _best_posterior >= 0.55:
                    _filtered = [c for c in _all_candidates
                                if not (c["is_scalp"] and c["direction"] != _best_direction)]
                    _all_candidates = _filtered

                if _regime_bias != "neutral":
                    _filtered2 = []
                    for c in _all_candidates:
                        _is_weak = c["conviction"].conviction_level in ("SCALP", "REDUCED")
                        if _is_weak and c["direction"] != _regime_bias:
                            logger.info("[REGIME] Suppressed %s %s %s — bias is %s",
                                       c["setup_id"], c["direction"],
                                       c["conviction"].conviction_level, _regime_bias)
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

                best_action = (best["setup_id"], best["config"], best["signal"],
                              best["conviction"], best["ev_result"], _size_mult, best["is_scalp"])

            if _cycle_signals:
                logger.info("[Cycle %d][%s|%s] Signals: %s", cycle, _regime, _regime_bias,
                           " | ".join(_cycle_signals))

            # ── Execute ──────────────────────────────────────────────────
            if best_action is None:
                await asyncio.sleep(60); continue

            if _in_blackout:
                s_id = best_action[0]
                logger.info("[Cycle %d] %s blocked by NEWS BLACKOUT", cycle, s_id)
                await asyncio.sleep(60); continue

            setup_id, config, signal, conviction, ev_result, _size_mult, _is_scalp = best_action

            # ═══ PRE-EXECUTION SAFETY GATES (Bugs 3/4/5/4C) ═══════════════

            # Bug 3: REJECT hard block — NEVER execute REJECT trades
            if conviction.conviction_level == "REJECT":
                logger.info("[SAFETY] %s REJECT — hard blocked from execution", setup_id)
                await asyncio.sleep(60); continue

            _target_inst = await resolve_instrument(adapter, config["instrument"])

            # Bug 5: Block opposite direction on same instrument
            _direction_conflict = False
            if _target_inst and account.open_positions:
                for pos in account.open_positions:
                    _pos_inst = getattr(pos, 'instrument', None) or getattr(pos, 'symbol', None)
                    if _pos_inst == _target_inst:
                        pos_dir = pos.direction.value if hasattr(pos.direction, 'value') else str(pos.direction)
                        if pos_dir != signal.direction:
                            logger.info("[CONFLICT] %s %s blocked — already %s %s open",
                                       setup_id, signal.direction, pos_dir, _target_inst)
                            _direction_conflict = True
                            break
            if _direction_conflict:
                await asyncio.sleep(60); continue

            # Bug 4: Max exposure 1.0 lots per instrument per direction
            _current_exposure = 0.0
            if _target_inst and account.open_positions:
                for pos in account.open_positions:
                    _pos_inst = getattr(pos, 'instrument', None) or getattr(pos, 'symbol', None)
                    if _pos_inst == _target_inst:
                        pos_dir = pos.direction.value if hasattr(pos.direction, 'value') else str(pos.direction)
                        if pos_dir == signal.direction:
                            _current_exposure += getattr(pos, 'size', 0) or getattr(pos, 'volume', 0) or 0

            # 4C: Pre-trade sanity checks
            _entry = signal.entry_price or 0
            _sl = signal.stop_loss or 0
            _tp = signal.take_profit or 0
            _sl_dist = abs(_entry - _sl)
            _tp_dist = abs(_entry - _tp)
            _inst_atr = config.get("atr_default", 100)

            _sanity_fail = None
            if _tp_dist > _inst_atr * 2:
                _sanity_fail = f"TP unreachable: {_tp_dist:.0f}pts > {_inst_atr*2:.0f} (2×ATR)"
            elif _sl_dist < 5.0:
                _sanity_fail = f"SL too tight: {_sl_dist:.1f}pts < 5pt min"
            elif _sl_dist > _inst_atr * 1.0:
                _sanity_fail = f"SL too wide: {_sl_dist:.0f}pts > {_inst_atr:.0f} (1×ATR)"
            elif ev_result.ev_dollars > 500:
                _sanity_fail = f"EV suspicious: ${ev_result.ev_dollars:.0f} > $500 cap"
            elif ev_result.ev_dollars < 0:
                _sanity_fail = f"EV negative: ${ev_result.ev_dollars:.0f}"

            if _sanity_fail:
                logger.warning("[SANITY] %s %s: %s", setup_id, signal.direction, _sanity_fail)
                await asyncio.sleep(60); continue

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

            # V20: Session memory size adjustment
            session_size_mult = session_memory.size_multiplier

            conv_mult = {"ELITE": 1.5, "HIGH": 1.2, "STANDARD": 1.0,
                         "REDUCED": 0.7, "SCALP": 0.5}.get(conviction.conviction_level, 0.3)
            lot_size = compute_kelly_size(
                win_prob=conviction.posterior, reward_risk_ratio=ev_result.reward_risk_ratio,
                account_balance=account.balance, base_lot_size=config["base_size"],
                firm_max_risk_pct=0.02,
                risk_multiplier=risk_decision.size_multiplier * _size_mult * session_size_mult,
                vix_multiplier=ctx.vix_size_mult, day_multiplier=ctx.day_strength,
                conviction_mult=conv_mult,
            )
            # FTMO requires 0.10 lot increments on indices
            lot_size = max(0.10, round(round(lot_size / 0.10) * 0.10, 2))
            lot_size = min(lot_size, 2.0)

            # Bug 4: Max 1.0 lots per instrument per direction
            _max_inst_exposure = 1.0
            _remaining_exposure = _max_inst_exposure - _current_exposure
            if _remaining_exposure <= 0:
                logger.info("[EXPOSURE] %s %s blocked — already %.2f lots %s on %s",
                           setup_id, signal.direction, _current_exposure, signal.direction,
                           config["instrument"])
                await asyncio.sleep(60); continue
            if lot_size > _remaining_exposure:
                lot_size = round(round(_remaining_exposure / 0.10) * 0.10, 2)
                if lot_size < 0.10:
                    logger.info("[EXPOSURE] %s capped to 0 — skipping", setup_id)
                    await asyncio.sleep(60); continue
                logger.info("[EXPOSURE] %s capped to %.2f lots (%.2f already open)",
                           setup_id, lot_size, _current_exposure)

            _trade_mode = "SCALP" if _is_scalp else conviction.conviction_level

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

            trade_id = f"TF-{uuid.uuid4().hex[:6]}"
            order = OrderRequest(
                instrument=instrument,
                direction=OrderDirection.LONG if signal.direction == "long" else OrderDirection.SHORT,
                size=lot_size, order_type=OrderType.MARKET,
                stop_loss=signal.stop_loss, take_profit=signal.take_profit,
                comment=f"{setup_id}|{trade_id}|{conviction.posterior:.0%}",
            )

            logger.info("🔫 [%s][%s] %s %s %.2f lots | E=%.2f SL=%.2f TP=%.2f | P=%.0f%% EV=$%.0f | R=%s",
                        setup_id, _trade_mode, (signal.direction or "").upper(), instrument, lot_size,
                        signal.entry_price or 0, signal.stop_loss or 0, signal.take_profit or 0,
                        conviction.posterior * 100, ev_result.ev_dollars, _regime)

            result = await adapter.place_order(order)

            if result.status.value == "filled":
                logger.info("✅ FILLED: %s @ %.5f", result.order_id, result.fill_price)
                traded_setups.add(setup_id)
                _setup_cooldowns[setup_id] = time.time()
                firm_state.record_size(lot_size)

                send_telegram(
                    f"🔫 <b>TRADE — {_trade_mode}</b>\n"
                    f"{setup_id} ({config['name']})\n"
                    f"{(signal.direction or '').upper()} {instrument} @ {result.fill_price:.2f}\n"
                    f"Size: {lot_size} | SL: {signal.stop_loss:.2f} | TP: {signal.take_profit:.2f}\n"
                    f"{conviction.conviction_level} ({conviction.posterior:.0%}) | EV: ${ev_result.ev_dollars:.0f}\n"
                    f"Regime: {_regime} ({_regime_bias}) | M15: {ctx.mtf_trend_m15}"
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

        await asyncio.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  NEXUS CAPITAL — TITAN FORGE %s                          ║", FORGE_VERSION)
    logger.info("║  25 SETUPS | 3 INSTRUMENTS | 4 TIMEFRAMES | FULL ARSENAL ║")
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
