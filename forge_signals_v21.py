"""
FORGE v21 — STRIPPED SIGNAL GENERATORS
========================================
EVERY SETUP: ONE CONDITION TO CONFIRMED.
The signal generator FINDS opportunities.
The Bayesian engine EVALUATES them.

If the trade is bad, Bayes scores it at 35% and rejects.
If the trade is good, Bayes scores it at 85% and sizes it big.

33+ active setups across NAS100, XAUUSD, EURUSD, CL, US500.
24-hour coverage: Asian, London, Pre-Market, RTH, Extended.

NEXUS Capital — Jorge Trujillo | Claude — AI Architect | March 2026
"""
from __future__ import annotations
import logging
from datetime import time as dtime
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger("FORGE.signals")


# ─────────────────────────────────────────────────────────────────
# SIGNAL TYPES
# ─────────────────────────────────────────────────────────────────

class SignalVerdict:
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"


class Signal:
    __slots__ = ('setup_id', 'verdict', 'direction', 'entry_price',
                 'stop_loss', 'take_profit', 'conviction', 'reason')

    def __init__(self, setup_id, verdict, direction, entry_price,
                 stop_loss, take_profit, conviction, reason):
        self.setup_id = setup_id
        self.verdict = verdict
        self.direction = direction
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.conviction = conviction
        self.reason = reason


def _pending(setup_id: str, reason: str) -> Signal:
    return Signal(setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0, reason)


# ─────────────────────────────────────────────────────────────────
# SETUP CONFIG — V21: 40 setups, 33+ active
# ─────────────────────────────────────────────────────────────────

SETUP_CONFIG: Dict[str, dict] = {
    # ═══ RTH — NAS100 ═══════════════════════════════════════════════════════
    "ORD-02": {
        "name": "Opening Range Breakout", "instrument": "NAS100", "signal_fn": "orb",
        "base_win_rate": 0.53, "avg_rr": 2.2, "rr_ratio": 2.0,
        "base_size": 0.15, "atr_default": 150,
        "sessions": ["RTH"],
        "window_start": dtime(9, 45), "window_end": dtime(11, 30),
        "expected_hold_min": 45,
    },
    "ICT-01": {
        "name": "VWAP Reclaim", "instrument": "NAS100", "signal_fn": "vwap_reclaim",
        "base_win_rate": 0.56, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.15, "atr_default": 150,
        "sessions": ["RTH", "LONDON"],
        "window_start": dtime(10, 0), "window_end": dtime(14, 0),
        "expected_hold_min": 60,
    },
    "ICT-02": {
        "name": "Fair Value Gap", "instrument": "NAS100", "signal_fn": "fair_value_gap",
        "base_win_rate": 0.53, "avg_rr": 1.8, "rr_ratio": 1.8,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH", "LONDON"],
        "window_start": dtime(9, 45), "window_end": dtime(13, 0),
        "expected_hold_min": 30,
    },
    "ICT-03": {
        "name": "Liquidity Sweep + Reclaim", "instrument": "NAS100", "signal_fn": "liquidity_sweep",
        "base_win_rate": 0.51, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.12, "atr_default": 150,
        "sessions": ["RTH"],
        "window_start": dtime(9, 30), "window_end": dtime(12, 30),
        "expected_hold_min": 40,
    },
    "VOL-03": {
        "name": "Trend Day Momentum", "instrument": "NAS100", "signal_fn": "trend_momentum",
        "base_win_rate": 0.48, "avg_rr": 2.5, "rr_ratio": 2.0,
        "base_size": 0.12, "atr_default": 150,
        "sessions": ["RTH", "LONDON"],
        "window_start": dtime(10, 30), "window_end": dtime(15, 0),
        "expected_hold_min": 60,
    },
    "VOL-05": {
        "name": "Mean Reversion", "instrument": "NAS100", "signal_fn": "mean_reversion",
        "base_win_rate": 0.50, "avg_rr": 1.8, "rr_ratio": 1.8,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH", "ASIAN", "EXTENDED"],
        "window_start": dtime(11, 0), "window_end": dtime(15, 30),
        "expected_hold_min": 45,
    },
    "VOL-06": {
        "name": "Noon Curve Reversal", "instrument": "NAS100", "signal_fn": "noon_curve",
        "base_win_rate": 0.54, "avg_rr": 1.6, "rr_ratio": 1.6,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH"],
        "window_start": dtime(11, 45), "window_end": dtime(12, 45),
        "expected_hold_min": 30,
    },
    "OD-01": {
        "name": "Opening Drive Momentum", "instrument": "NAS100", "signal_fn": "opening_drive",
        "base_win_rate": 0.50, "avg_rr": 1.5, "rr_ratio": 1.5,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH"],
        "window_start": dtime(9, 30), "window_end": dtime(9, 40),
        "expected_hold_min": 30,
    },
    "GAP-02": {
        "name": "Gap and Go", "instrument": "NAS100", "signal_fn": "gap_go",
        "base_win_rate": 0.44, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH", "PRE_MARKET"],
        "window_start": dtime(9, 30), "window_end": dtime(10, 0),
        "expected_hold_min": 30,
    },
    "IB-01": {
        "name": "IB Breakout", "instrument": "NAS100", "signal_fn": "ib_breakout",
        "base_win_rate": 0.58, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.15, "atr_default": 150,
        "sessions": ["RTH"],
        "window_start": dtime(10, 30), "window_end": dtime(14, 0),
        "expected_hold_min": 60,
    },
    "IB-02": {
        "name": "IB Range Scalp", "instrument": "NAS100", "signal_fn": "ib_range_scalp",
        "base_win_rate": 0.48, "avg_rr": 1.2, "rr_ratio": 1.2,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH"],
        "window_start": dtime(10, 30), "window_end": dtime(14, 0),
        "expected_hold_min": 20,
    },
    "VWAP-01": {
        "name": "VWAP Bounce Long", "instrument": "NAS100", "signal_fn": "vwap_bounce_long",
        "base_win_rate": 0.38, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH", "LONDON"],
        "window_start": dtime(10, 0), "window_end": dtime(15, 30),
        "expected_hold_min": 45,
    },
    "VWAP-02": {
        "name": "VWAP Reject Short", "instrument": "NAS100", "signal_fn": "vwap_reject_short",
        "base_win_rate": 0.36, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH", "LONDON"],
        "window_start": dtime(10, 0), "window_end": dtime(15, 30),
        "expected_hold_min": 45,
    },
    "VWAP-03": {
        "name": "VWAP Reclaim Momentum", "instrument": "NAS100", "signal_fn": "vwap_reclaim_momentum",
        "base_win_rate": 0.56, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH"],
        "window_start": dtime(10, 0), "window_end": dtime(15, 0),
        "expected_hold_min": 40,
    },
    "LVL-01": {
        "name": "PDH/PDL Test", "instrument": "NAS100", "signal_fn": "pdh_pdl_test",
        "base_win_rate": 0.42, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH", "LONDON", "PRE_MARKET"],
        "window_start": dtime(9, 30), "window_end": dtime(15, 0),
        "expected_hold_min": 45,
    },
    "LVL-02": {
        "name": "Round Number Scalp", "instrument": "NAS100", "signal_fn": "round_number",
        "base_win_rate": 0.41, "avg_rr": 1.5, "rr_ratio": 1.67,
        "base_size": 0.10, "atr_default": 150,
        "sessions": ["RTH"],
        "window_start": dtime(9, 30), "window_end": dtime(16, 0),
        "expected_hold_min": 15,
    },
    # ═══ MULTI-INSTRUMENT (existing) ══════════════════════════════════════
    "SES-01": {
        "name": "London Session Forex", "instrument": "EURUSD", "signal_fn": "london_forex",
        "base_win_rate": 0.63, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.10, "atr_default": 0.0070,
        "sessions": ["LONDON"],
        "window_start": dtime(3, 0), "window_end": dtime(8, 0),
        "expected_hold_min": 90,
    },
    "ES-ORD-02": {
        "name": "ES Opening Range Breakout", "instrument": "US500", "signal_fn": "orb",
        "base_win_rate": 0.62, "avg_rr": 2.0, "rr_ratio": 2.0,
        "base_size": 0.10, "atr_default": 50,
        "sessions": ["RTH"],
        "window_start": dtime(9, 45), "window_end": dtime(11, 30),
        "expected_hold_min": 45,
    },
    "GOLD-CORR-01": {
        "name": "Gold Correlation Divergence", "instrument": "XAUUSD",
        "signal_fn": "gold_correlation",
        "base_win_rate": 0.55, "avg_rr": 1.67, "rr_ratio": 1.67,
        "base_size": 0.01, "atr_default": 40,
        "sessions": ["RTH", "LONDON"],
        "window_start": dtime(9, 30), "window_end": dtime(16, 0),
        "expected_hold_min": 60,
    },
    # ═══ V21 NEW: EXTENDED SESSION ════════════════════════════════════════
    "ASIA-GOLD-01": {
        "name": "Asian Gold Trend", "instrument": "XAUUSD",
        "signal_fn": "asian_gold_trend",
        "base_win_rate": 0.55, "base_size": 0.05, "atr_default": 40,
        "sessions": ["ASIAN"], "avg_rr": 2.0, "rr_ratio": 2.0,
        "window_start": dtime(19, 0), "window_end": dtime(3, 0),
        "expected_hold_min": 45,
    },
    "LONDON-GOLD-01": {
        "name": "London Gold Breakout", "instrument": "XAUUSD",
        "signal_fn": "london_gold_breakout",
        "base_win_rate": 0.57, "base_size": 0.05, "atr_default": 40,
        "sessions": ["LONDON"], "avg_rr": 2.0, "rr_ratio": 2.0,
        "window_start": dtime(3, 0), "window_end": dtime(5, 0),
        "expected_hold_min": 45,
    },
    "LONDON-FX-01": {
        "name": "London EURUSD Momentum", "instrument": "EURUSD",
        "signal_fn": "london_fx_momentum",
        "base_win_rate": 0.56, "base_size": 0.10, "atr_default": 0.0070,
        "sessions": ["LONDON"], "avg_rr": 2.0, "rr_ratio": 2.0,
        "window_start": dtime(3, 0), "window_end": dtime(8, 0),
        "expected_hold_min": 60,
    },
    "LONDON-NQ-01": {
        "name": "London NQ Overnight Range Break", "instrument": "NAS100",
        "signal_fn": "london_nq_range",
        "base_win_rate": 0.54, "base_size": 0.10, "atr_default": 150,
        "sessions": ["LONDON"], "avg_rr": 2.0, "rr_ratio": 2.0,
        "window_start": dtime(3, 0), "window_end": dtime(8, 0),
        "expected_hold_min": 60,
    },
    "PRE-RANGE-01": {
        "name": "Pre-Market NQ Range Break", "instrument": "NAS100",
        "signal_fn": "pre_market_range",
        "base_win_rate": 0.53, "base_size": 0.10, "atr_default": 150,
        "sessions": ["PRE_MARKET"], "avg_rr": 2.0, "rr_ratio": 2.0,
        "window_start": dtime(8, 0), "window_end": dtime(9, 30),
        "expected_hold_min": 30,
    },
    "NEWS-MOM-01": {
        "name": "Economic Data Momentum", "instrument": "NAS100",
        "signal_fn": "news_momentum",
        "base_win_rate": 0.55, "base_size": 0.10, "atr_default": 150,
        "sessions": ["PRE_MARKET"], "avg_rr": 2.0, "rr_ratio": 2.0,
        "window_start": dtime(8, 28), "window_end": dtime(9, 0),
        "expected_hold_min": 15,
    },
    "ASIA-REVERT-01": {
        "name": "Asian NQ Mean Reversion", "instrument": "NAS100",
        "signal_fn": "asian_nq_revert",
        "base_win_rate": 0.54, "base_size": 0.10, "atr_default": 150,
        "sessions": ["ASIAN"], "avg_rr": 1.4, "rr_ratio": 1.4,
        "window_start": dtime(19, 0), "window_end": dtime(3, 0),
        "expected_hold_min": 30,
    },
    "EXT-REVERT-01": {
        "name": "Extended Hours NQ Reversion", "instrument": "NAS100",
        "signal_fn": "extended_nq_revert",
        "base_win_rate": 0.53, "base_size": 0.10, "atr_default": 150,
        "sessions": ["EXTENDED"], "avg_rr": 1.4, "rr_ratio": 1.4,
        "window_start": dtime(16, 0), "window_end": dtime(17, 0),
        "expected_hold_min": 20,
    },
    # ═══ V21 NEW: CRUDE OIL (CL) — Ghost proved +125.7R on March 27 ═════
    "CL-TREND-01": {
        "name": "Oil Trend Follow", "instrument": "CL",
        "signal_fn": "oil_trend",
        "base_win_rate": 0.58, "base_size": 0.10, "atr_default": 2.50,
        "sessions": ["RTH", "LONDON"], "avg_rr": 2.0, "rr_ratio": 2.0,
        "window_start": dtime(9, 0), "window_end": dtime(15, 0),
        "expected_hold_min": 60,
    },
    "CL-MOM-01": {
        "name": "Oil Momentum", "instrument": "CL",
        "signal_fn": "oil_momentum",
        "base_win_rate": 0.55, "base_size": 0.10, "atr_default": 2.50,
        "sessions": ["RTH", "LONDON", "ASIAN"], "avg_rr": 2.0, "rr_ratio": 2.0,
        "window_start": dtime(9, 0), "window_end": dtime(15, 0),
        "expected_hold_min": 30,
    },
    "CL-GAP-01": {
        "name": "Oil Gap Fade", "instrument": "CL",
        "signal_fn": "oil_gap_fade",
        "base_win_rate": 0.65, "base_size": 0.10, "atr_default": 2.50,
        "sessions": ["RTH"], "avg_rr": 1.5, "rr_ratio": 1.5,
        "window_start": dtime(9, 0), "window_end": dtime(10, 0),
        "expected_hold_min": 30,
    },
    # ═══ V21 NEW: CROSS-MARKET SPEED EXPLOIT ══════════════════════════════
    "ES-LEAD-01": {
        "name": "ES→NQ Speed Exploit", "instrument": "NAS100",
        "signal_fn": "es_lead",
        "base_win_rate": 0.56, "base_size": 0.15, "atr_default": 150,
        "sessions": ["RTH"], "avg_rr": 1.8, "rr_ratio": 1.8,
        "window_start": dtime(9, 30), "window_end": dtime(15, 30),
        "expected_hold_min": 15,
    },
    # ═══ DISABLED ═════════════════════════════════════════════════════════
    "GAP-01":  {"name": "Gap Fade [DISABLED]", "signal_fn": "disabled", "instrument": "NAS100",
                "base_win_rate": 0.52, "base_size": 0.10, "atr_default": 150, "sessions": ["RTH"]},
    "MID-01":  {"name": "Range Fade [DISABLED]", "signal_fn": "disabled", "instrument": "NAS100",
                "base_win_rate": 0.40, "base_size": 0.10, "atr_default": 150, "sessions": ["RTH"]},
    "MID-02":  {"name": "Afternoon Breakout [DISABLED]", "signal_fn": "disabled", "instrument": "NAS100",
                "base_win_rate": 0.33, "base_size": 0.10, "atr_default": 150, "sessions": ["RTH"]},
    "PWR-01":  {"name": "Power Hour [DISABLED]", "signal_fn": "disabled", "instrument": "NAS100",
                "base_win_rate": 0.38, "base_size": 0.10, "atr_default": 150, "sessions": ["RTH"]},
    "PWR-02":  {"name": "Closing Drive [DISABLED]", "signal_fn": "disabled", "instrument": "NAS100",
                "base_win_rate": 0.20, "base_size": 0.10, "atr_default": 150, "sessions": ["RTH"]},
    "PWR-03":  {"name": "EOD Fade [DISABLED]", "signal_fn": "disabled", "instrument": "NAS100",
                "base_win_rate": 0.20, "base_size": 0.10, "atr_default": 150, "sessions": ["RTH"]},
}


# ─────────────────────────────────────────────────────────────────
# STRIPPED SIGNAL GENERATORS — ONE CONDITION EACH
# ─────────────────────────────────────────────────────────────────
# BEFORE: each generator had 5+ conditions stacked.
#   On a 400-point trend day, ZERO signals cleared all conditions.
# AFTER: ONE condition. Price broke ORB? CONFIRMED. That's it.
#   If the trade is bad, Bayes scores it at 35% and rejects.
#   If the trade is good, Bayes scores it at 85% and sizes big.

def generate_signal(
    setup_id: str, config: dict, mid: float,
    tracker, ctx, atr: float,
    cross_market_exploit=None,  # CrossMarketExploit instance
) -> Signal:
    """Generate signal with ONE condition per setup."""
    fn = config.get("signal_fn", "disabled")
    inst_atr = config.get("atr_default", atr) if atr <= 0 else atr

    if fn == "disabled":
        return _pending(setup_id, "DISABLED")

    vwap = (getattr(tracker, 'rth_vwap', 0) or
            getattr(tracker, 'vwap', 0) or
            getattr(tracker, 'open_price', 0) or mid)

    # ═══ ORB: price > ORB high + 2 OR < ORB low - 2 ═══════════════════════
    if fn == "orb":
        if not getattr(tracker, 'orb_locked', False):
            return _pending(setup_id, "ORB not locked")
        orb_h = getattr(tracker, 'orb_high', 0) or 0
        orb_l = getattr(tracker, 'orb_low', 0) or 0
        if mid > orb_h + 2:
            d = "long"
        elif mid < orb_l - 2:
            d = "short"
        else:
            return _pending(setup_id, "No ORB break")
        rng = max(orb_h - orb_l, 10)
        sl_d = max(inst_atr * 0.3, rng * 0.5)
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 2.0 if d == "long" else mid - sl_d * 2.0, 2),
                      0.70, f"ORB {d.upper()}")

    # ═══ VWAP reclaim: price crossed above VWAP ═══════════════════════════
    if fn == "vwap_reclaim":
        if vwap <= 0:
            return _pending(setup_id, "No VWAP")
        if mid > vwap:
            sl_d = inst_atr * 0.3
            return Signal(setup_id, SignalVerdict.CONFIRMED, "long",
                          round(mid, 2), round(mid - sl_d, 2), round(mid + sl_d * 2, 2),
                          0.68, "Above VWAP")
        return _pending(setup_id, "Below VWAP")

    # ═══ FVG: fair value gap detected ═════════════════════════════════════
    if fn == "fair_value_gap":
        closes = getattr(tracker, 'close_prices', [])
        if len(closes) < 4:
            return _pending(setup_id, "Need 4 closes")
        c1, c2, c3, c4 = closes[-4:]
        bull = c1 < c2 and c3 > c2 and c4 > c3
        bear = c1 > c2 and c3 < c2 and c4 < c3
        if bull:
            d = "long"
        elif bear:
            d = "short"
        else:
            return _pending(setup_id, "No FVG")
        sl_d = inst_atr * 0.3
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 1.8 if d == "long" else mid - sl_d * 1.8, 2),
                      0.66, f"FVG {d.upper()}")

    # ═══ Liquidity sweep: session swept PDH or PDL then reclaimed ═════════
    if fn == "liquidity_sweep":
        pdh = getattr(ctx, 'pdh', 0) or 0
        pdl = getattr(ctx, 'pdl', 0) or 0
        s_low = getattr(tracker, 'session_low', None)
        s_high = getattr(tracker, 'session_high', None)
        if pdl <= 0 or pdh <= 0:
            return _pending(setup_id, "No PDH/PDL")
        swept_low = s_low is not None and s_low < pdl and mid > pdl
        swept_high = s_high is not None and s_high > pdh and mid < pdh
        if swept_low:
            d = "long"
        elif swept_high:
            d = "short"
        else:
            return _pending(setup_id, "No sweep")
        sl_d = inst_atr * 0.35
        sl = (s_low if d == "long" else s_high) or (mid - sl_d if d == "long" else mid + sl_d)
        tp_d = abs(mid - sl) * 2.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2), round(sl, 2),
                      round(mid + tp_d if d == "long" else mid - tp_d, 2),
                      0.70, f"Sweep {'PDL' if d == 'long' else 'PDH'}")

    # ═══ Trend momentum: price > 0.3% from VWAP ══════════════════════════
    if fn == "trend_momentum":
        if vwap <= 0:
            return _pending(setup_id, "No VWAP")
        if mid > vwap * 1.003:
            d = "long"
        elif mid < vwap * 0.997:
            d = "short"
        else:
            return _pending(setup_id, "Within 0.3% of VWAP")
        sl_d = inst_atr * 0.35
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 2 if d == "long" else mid - sl_d * 2, 2),
                      0.66, f"Trend {d.upper()}")

    # ═══ Mean reversion: price > 0.5 ATR from VWAP ═══════════════════════
    if fn == "mean_reversion":
        if vwap <= 0:
            return _pending(setup_id, "No VWAP")
        dist = abs(mid - vwap) / inst_atr if inst_atr > 0 else 0
        if dist < 0.5:
            return _pending(setup_id, f"Only {dist:.1f} ATR from VWAP")
        d = "short" if mid > vwap else "long"
        sl_d = inst_atr * 0.35
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(vwap, 2), 0.64, f"Mean Rev {d.upper()}")

    # ═══ Noon curve: price moved > 0.3 ATR from open ═════════════════════
    if fn == "noon_curve":
        op = getattr(tracker, 'open_price', 0) or getattr(tracker, 'session_open', 0)
        if not op:
            return _pending(setup_id, "No open")
        move = mid - op
        if abs(move) < inst_atr * 0.3:
            return _pending(setup_id, "Insufficient move")
        d = "short" if move > 0 else "long"
        sl_d = inst_atr * 0.35
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid + sl_d if d == "short" else mid - sl_d, 2),
                      round(mid - sl_d * 1.6 if d == "short" else mid + sl_d * 1.6, 2),
                      0.62, f"Noon curve {d.upper()}")

    # ═══ Opening drive: price moved > 0.2% from open ═════════════════════
    if fn == "opening_drive":
        op = getattr(tracker, 'open_price', 0) or getattr(tracker, 'session_open', 0)
        if not op:
            return _pending(setup_id, "No open")
        move_pct = (mid - op) / op
        if abs(move_pct) < 0.002:
            return _pending(setup_id, f"Only {move_pct:.2%}")
        d = "long" if move_pct > 0 else "short"
        sl_d = abs(mid - op) + 5.0
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 1.5 if d == "long" else mid - sl_d * 1.5, 2),
                      0.64, f"OD {d.upper()}: {move_pct:.2%}")

    # ═══ Gap and go: gap + continuation ═══════════════════════════════════
    if fn == "gap_go":
        prev_close = getattr(ctx, 'prev_close', 0) or 0
        op = getattr(tracker, 'open_price', 0) or 0
        if prev_close <= 0 or op <= 0:
            return _pending(setup_id, "No prev close/open")
        gap_pct = (op - prev_close) / prev_close
        if abs(gap_pct) < 0.003:
            return _pending(setup_id, f"Gap {gap_pct:.2%} too small")
        d = "long" if gap_pct > 0 else "short"
        sl_d = inst_atr * 0.3
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 2 if d == "long" else mid - sl_d * 2, 2),
                      0.62, f"Gap&Go {d.upper()}")

    # ═══ IB breakout: price > IB high + 2 or < IB low - 2 ════════════════
    if fn == "ib_breakout":
        if not getattr(tracker, 'ib_locked', False):
            return _pending(setup_id, "IB not locked")
        ib_h = getattr(tracker, 'ib_high', 0) or 0
        ib_l = getattr(tracker, 'ib_low', float('inf'))
        if mid > ib_h + 2:
            d = "long"
        elif mid < ib_l - 2:
            d = "short"
        else:
            return _pending(setup_id, "No IB break")
        ib_range = ib_h - ib_l if ib_l < 999999 else 0
        sl_d = max(ib_range * 0.5, 20)
        tp_d = max(ib_range, 40)
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + tp_d if d == "long" else mid - tp_d, 2),
                      0.70, f"IB Break {d.upper()}")

    # ═══ IB range scalp: price within 3pts of IB boundary ════════════════
    if fn == "ib_range_scalp":
        if not getattr(tracker, 'ib_locked', False):
            return _pending(setup_id, "IB not locked")
        ib_h = getattr(tracker, 'ib_high', 0) or 0
        ib_l = getattr(tracker, 'ib_low', float('inf'))
        if abs(mid - ib_h) < 3:
            d = "short"
        elif ib_l < 999999 and abs(mid - ib_l) < 3:
            d = "long"
        else:
            return _pending(setup_id, "Not at IB boundary")
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid + 15 if d == "short" else mid - 15, 2),
                      round(mid - 25 if d == "short" else mid + 25, 2),
                      0.58, f"IB Scalp {d.upper()}")

    # ═══ VWAP bounce long: price within 5pts above VWAP ══════════════════
    if fn == "vwap_bounce_long":
        if vwap <= 0:
            return _pending(setup_id, "No VWAP")
        dist = mid - vwap
        if dist < 0 or dist > 5.0:
            return _pending(setup_id, f"Not in zone ({dist:.1f})")
        return Signal(setup_id, SignalVerdict.CONFIRMED, "long",
                      round(mid, 2), round(vwap - 15, 2),
                      round(mid + max(20, inst_atr * 0.3), 2),
                      0.60, f"VWAP Bounce: dist={dist:.1f}")

    # ═══ VWAP reject short: price within 5pts below VWAP ═════════════════
    if fn == "vwap_reject_short":
        if vwap <= 0:
            return _pending(setup_id, "No VWAP")
        dist = vwap - mid
        if dist < 0 or dist > 5.0:
            return _pending(setup_id, f"Not in zone ({dist:.1f})")
        return Signal(setup_id, SignalVerdict.CONFIRMED, "short",
                      round(mid, 2), round(vwap + 15, 2),
                      round(mid - max(20, inst_atr * 0.3), 2),
                      0.58, f"VWAP Reject: dist={dist:.1f}")

    # ═══ VWAP reclaim momentum: price above VWAP ═════════════════════════
    if fn == "vwap_reclaim_momentum":
        if vwap <= 0 or mid <= vwap:
            return _pending(setup_id, "Below VWAP")
        return Signal(setup_id, SignalVerdict.CONFIRMED, "long",
                      round(mid, 2), round(vwap - 5, 2),
                      round(mid + inst_atr * 0.4, 2),
                      0.60, "VWAP Momentum")

    # ═══ PDH/PDL test: price within 10pts ═════════════════════════════════
    if fn == "pdh_pdl_test":
        pdh = getattr(ctx, 'pdh', 0) or 0
        pdl = getattr(ctx, 'pdl', 0) or 0
        if pdh <= 0 or pdl <= 0:
            return _pending(setup_id, "No PDH/PDL")
        near_pdh = abs(mid - pdh) < 10
        near_pdl = abs(mid - pdl) < 10
        if not (near_pdh or near_pdl):
            return _pending(setup_id, "Not near PDH/PDL")
        if near_pdh and mid < pdh:
            d, sl, tp = "short", pdh + 20, mid - 40
        elif near_pdl and mid > pdl:
            d, sl, tp = "long", pdl - 20, mid + 40
        elif near_pdh:
            d, sl, tp = "long", pdh - 5, mid + 40
        else:
            d, sl, tp = "short", pdl + 5, mid - 40
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2), round(sl, 2), round(tp, 2),
                      0.60, f"PDH/PDL {d.upper()}")

    # ═══ Round number: price within 5pts of 100-level ═════════════════════
    if fn == "round_number":
        nearest = round(mid / 100) * 100
        dist = mid - nearest
        if abs(dist) > 5:
            return _pending(setup_id, f"Far from {nearest}")
        d = "short" if dist > 0 else "long"
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid + 15 if d == "short" else mid - 15, 2),
                      round(mid - 25 if d == "short" else mid + 25, 2),
                      0.55, f"Round #{nearest:.0f}")

    # ═══ London forex: breakout of recent range ═══════════════════════════
    if fn == "london_forex":
        prices = getattr(tracker, 'price_history', [])
        if len(prices) < 10:
            return _pending(setup_id, "Insufficient data")
        recent = prices[-10:]
        rh, rl = max(recent), min(recent)
        rng = rh - rl
        if rng < 0.0005:
            return _pending(setup_id, "Range too tight")
        if mid > rh:
            d = "long"
        elif mid < rl:
            d = "short"
        else:
            return _pending(setup_id, "No breakout")
        sl_d = rng * 0.8
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 5),
                      round(mid - sl_d if d == "long" else mid + sl_d, 5),
                      round(mid + sl_d * 2 if d == "long" else mid - sl_d * 2, 5),
                      0.64, f"London {d.upper()}")

    # ═══ Gold correlation: divergence from DXY ════════════════════════════
    if fn == "gold_correlation":
        try:
            from forge_market import get_correlation_engine
            corr_engine = get_correlation_engine()
            divergence = corr_engine.detect_divergence("XAUUSD", "DXY", expected_corr=-0.60)
            if not divergence:
                return _pending(setup_id, "No divergence")
            corr_val, desc = divergence
            d = "short" if corr_val > 0 else "long"
            return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                          round(mid, 2),
                          round(mid + 30 if d == "short" else mid - 30, 2),
                          round(mid - 50 if d == "short" else mid + 50, 2),
                          0.56, f"Gold Corr: {desc}")
        except Exception:
            return _pending(setup_id, "Correlation unavailable")

    # ═══ V21: Asian Gold Trend — gold trends during Tokyo ═════════════════
    if fn == "asian_gold_trend":
        prices = getattr(tracker, 'price_history', [])
        if len(prices) < 15:
            return _pending(setup_id, "Insufficient data")
        op = prices[0] if prices else mid
        move_pct = (mid - op) / op if op > 0 else 0
        if abs(move_pct) < 0.003:
            return _pending(setup_id, f"Only {move_pct:.2%} from Asian open")
        d = "long" if move_pct > 0 else "short"
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - 30 if d == "long" else mid + 30, 2),
                      round(mid + 60 if d == "long" else mid - 60, 2),
                      0.60, f"Gold Asian {d.upper()}: {move_pct:.2%}")

    # ═══ V21: London Gold Breakout ════════════════════════════════════════
    if fn == "london_gold_breakout":
        orb_h = getattr(tracker, 'orb_high', 0) or 0
        orb_l = getattr(tracker, 'orb_low', float('inf'))
        if orb_h <= 0 or not getattr(tracker, 'orb_locked', False):
            return _pending(setup_id, "Gold ORB not locked")
        if mid > orb_h + 1:
            d = "long"
        elif orb_l < 999999 and mid < orb_l - 1:
            d = "short"
        else:
            return _pending(setup_id, "No gold breakout")
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - 25 if d == "long" else mid + 25, 2),
                      round(mid + 50 if d == "long" else mid - 50, 2),
                      0.65, f"London Gold {d.upper()}")

    # ═══ V21: London FX Momentum — EURUSD Asian range break ══════════════
    if fn == "london_fx_momentum":
        ah = getattr(tracker, 'asian_high', 0) or getattr(tracker, 'overnight_high', 0)
        al = getattr(tracker, 'asian_low', float('inf')) or getattr(tracker, 'overnight_low', float('inf'))
        if ah <= 0 or al >= 999999:
            return _pending(setup_id, "No Asian range")
        if mid > ah:
            d = "long"
        elif mid < al:
            d = "short"
        else:
            return _pending(setup_id, "Within Asian range")
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 5),
                      round(mid - 0.0020 if d == "long" else mid + 0.0020, 5),
                      round(mid + 0.0040 if d == "long" else mid - 0.0040, 5),
                      0.60, f"London FX {d.upper()}")

    # ═══ V21: London NQ Overnight Range Break ═════════════════════════════
    if fn == "london_nq_range":
        oh = getattr(tracker, 'overnight_high', 0)
        ol = getattr(tracker, 'overnight_low', float('inf'))
        if oh <= 0 or ol >= 999999:
            return _pending(setup_id, "No overnight range")
        if mid > oh + 2:
            d = "long"
        elif mid < ol - 2:
            d = "short"
        else:
            return _pending(setup_id, "Within overnight range")
        sl_d = inst_atr * 0.3
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 2 if d == "long" else mid - sl_d * 2, 2),
                      0.60, f"London NQ {d.upper()}")

    # ═══ V21: Pre-Market Range Break ══════════════════════════════════════
    if fn == "pre_market_range":
        prices = getattr(tracker, 'price_history', [])
        if len(prices) < 10:
            return _pending(setup_id, "Insufficient data")
        rh = max(prices[-10:])
        rl = min(prices[-10:])
        if mid > rh + 2:
            d = "long"
        elif mid < rl - 2:
            d = "short"
        else:
            return _pending(setup_id, "No pre-market break")
        sl_d = inst_atr * 0.25
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 2 if d == "long" else mid - sl_d * 2, 2),
                      0.58, f"Pre-Mkt {d.upper()}")

    # ═══ V21: News Momentum — major data release ═════════════════════════
    if fn == "news_momentum":
        prices = getattr(tracker, 'price_history', [])
        if len(prices) < 5:
            return _pending(setup_id, "Insufficient data")
        recent_move = prices[-1] - prices[-5] if len(prices) >= 5 else 0
        if abs(recent_move) < 30:
            return _pending(setup_id, f"Only {recent_move:.0f}pt move")
        d = "long" if recent_move > 0 else "short"
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - 20 if d == "long" else mid + 20, 2),
                      round(mid + 40 if d == "long" else mid - 40, 2),
                      0.62, f"News Mom {d.upper()}: {recent_move:.0f}pt")

    # ═══ V21: Asian NQ Mean Reversion ═════════════════════════════════════
    if fn == "asian_nq_revert":
        ovwap = getattr(tracker, 'overnight_vwap', 0)
        if ovwap <= 0:
            ovwap = vwap
        if ovwap <= 0:
            return _pending(setup_id, "No overnight VWAP")
        dist = abs(mid - ovwap) / inst_atr if inst_atr > 0 else 0
        if dist < 0.5:
            return _pending(setup_id, f"Only {dist:.1f} ATR from VWAP")
        d = "short" if mid > ovwap else "long"
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - 25 if d == "long" else mid + 25, 2),
                      round(mid + 35 if d == "long" else mid - 35, 2),
                      0.58, f"Asian Rev {d.upper()}")

    # ═══ V21: Extended Hours Reversion ════════════════════════════════════
    if fn == "extended_nq_revert":
        if vwap <= 0:
            return _pending(setup_id, "No VWAP")
        dist = abs(mid - vwap) / inst_atr if inst_atr > 0 else 0
        if dist < 0.5:
            return _pending(setup_id, f"Only {dist:.1f} ATR from VWAP")
        d = "short" if mid > vwap else "long"
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - 25 if d == "long" else mid + 25, 2),
                      round(mid + 35 if d == "long" else mid - 35, 2),
                      0.56, f"Ext Rev {d.upper()}")

    # ═══ V21: Oil Trend Follow ════════════════════════════════════════════
    if fn == "oil_trend":
        op = getattr(tracker, 'session_open', 0) or getattr(tracker, 'open_price', 0)
        if not op:
            return _pending(setup_id, "No CL open")
        move_pct = (mid - op) / op if op > 0 else 0
        if abs(move_pct) < 0.01:
            return _pending(setup_id, f"Only {move_pct:.2%}")
        d = "long" if move_pct > 0 else "short"
        sl_d = inst_atr * 0.4
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 2 if d == "long" else mid - sl_d * 2, 2),
                      0.64, f"Oil Trend {d.upper()}: {move_pct:.2%}")

    # ═══ V21: Oil Momentum ════════════════════════════════════════════════
    if fn == "oil_momentum":
        prices = getattr(tracker, 'price_history', [])
        if len(prices) < 15:
            return _pending(setup_id, "Insufficient data")
        move_15 = (prices[-1] - prices[-15]) / prices[-15] if prices[-15] > 0 else 0
        if abs(move_15) < 0.005:
            return _pending(setup_id, f"Only {move_15:.2%} in 15min")
        d = "long" if move_15 > 0 else "short"
        sl_d = inst_atr * 0.35
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 2 if d == "long" else mid - sl_d * 2, 2),
                      0.60, f"Oil Mom {d.upper()}: {move_15:.2%}")

    # ═══ V21: Oil Gap Fade — Ghost showed 28W 0L on March 27 ═════════════
    if fn == "oil_gap_fade":
        prev_close = getattr(ctx, 'prev_close', 0) or 0
        op = getattr(tracker, 'session_open', 0) or getattr(tracker, 'open_price', 0) or 0
        if prev_close <= 0 or op <= 0:
            return _pending(setup_id, "No CL prev close/open")
        gap_pct = (op - prev_close) / prev_close
        if abs(gap_pct) < 0.005:
            return _pending(setup_id, f"Gap {gap_pct:.2%} too small")
        d = "short" if gap_pct > 0 else "long"  # FADE the gap
        sl_d = inst_atr * 0.4
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(prev_close, 2),  # target = fill the gap
                      0.70, f"Oil Gap Fade {d.upper()}: gap={gap_pct:.2%}")

    # ═══ V21: ES→NQ Speed Exploit ════════════════════════════════════════
    if fn == "es_lead":
        if cross_market_exploit is None:
            return _pending(setup_id, "No ES exploit engine")
        result = cross_market_exploit.check(tracker)
        if not result or not result.get("signal"):
            return _pending(setup_id, "No ES lead signal")
        d = result["direction"]
        sl_d = inst_atr * 0.25
        return Signal(setup_id, SignalVerdict.CONFIRMED, d,
                      round(mid, 2),
                      round(mid - sl_d if d == "long" else mid + sl_d, 2),
                      round(mid + sl_d * 1.8 if d == "long" else mid - sl_d * 1.8, 2),
                      0.68, result["reason"])

    return _pending(setup_id, f"Unknown fn: {fn}")
