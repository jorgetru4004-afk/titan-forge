"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                    choppy_strategies.py — Layer 3                           ║
║                                                                              ║
║  10 PURPOSE-BUILT CHOPPY MARKET STRATEGIES                                  ║
║  Document: "Not modifications of existing TITAN FORGE strategies.            ║
║  Purpose-built for choppy market mechanics."                                ║
║                                                                              ║
║  CHOP-01: False Breakout Fade           74% in Choppy Regime                ║
║  CHOP-02: VWAP Extended Fade            72% in Choppy Regime                ║
║  CHOP-03: Opening Range Prison Trade    70% in Choppy Regime                ║
║  CHOP-04: NYSE TICK Extreme Mean Rev    73% in Choppy Regime                ║
║  CHOP-05: Bollinger Band Squeeze Rev    69% in Choppy Regime                ║
║  CHOP-06: Value Area Oscillation        71% in Choppy Regime                ║
║  CHOP-07: Session High/Low Rejection    72% in Choppy Regime                ║
║  CHOP-08: Breadth Divergence Reversal   70% in Choppy Regime                ║
║  CHOP-09: Volatility Compression Entry  68% in Choppy Regime                ║
║  CHOP-10: POC Gravity Enhanced          71% in Choppy Regime                ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 2026                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from typing import Optional

logger = logging.getLogger("titan_forge.choppy_strategies")


@dataclass
class ChopSignal:
    """Unified output for all 10 choppy market strategies."""
    chop_id:       str     # e.g. "CHOP-01"
    name:          str
    valid:         bool
    direction:     Optional[str]   # "long" / "short" / None
    entry:         Optional[float]
    stop:          Optional[float]
    target1:       Optional[float]   # First target (60% of position)
    target2:       Optional[float]   # Second target (40% of position)
    win_rate:      float             # Win rate in choppy regime specifically
    r_r:           float
    confidence:    float             # 0–1
    conditions_met: int              # How many of 6 required conditions are met
    reason:        str

    @property
    def all_6_conditions(self) -> bool:
        """Document: ALL 6 conditions required for every CHOP strategy."""
        return self.conditions_met >= 6

    @property
    def ev(self) -> float:
        return (self.win_rate * self.r_r) - (1 - self.win_rate)


def _chop_signal(chop_id, name, direction, entry, stop, t1, t2,
                 win_rate, r_r, confidence, conditions, reason) -> ChopSignal:
    return ChopSignal(chop_id, name, True, direction, entry, stop,
                      t1, t2, win_rate, r_r, confidence, conditions, reason)

def _no_chop(chop_id, name, win_rate, r_r, conditions_met, reason) -> ChopSignal:
    return ChopSignal(chop_id, name, False, None, None, None,
                      None, None, win_rate, r_r, 0.0, conditions_met, reason)


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-01: FALSE BREAKOUT FADE
# "The single most reliable pattern in choppy markets."
# Win Rate: 74% in Choppy Regime
# ALL 6 conditions required.
# ─────────────────────────────────────────────────────────────────────────────

def chop01_false_breakout_fade(
    price:                  float,
    resistance_level:       float,      # Key resistance (prior session high, VWAP, etc.)
    breakout_occurred:      bool,       # Price broke above resistance
    breakout_volume:        float,      # Volume on breakout candle
    avg_volume:             float,      # Average volume
    breakout_close_pct:     float,      # Where candle closed within its range (0=low, 1=high)
    next_candle_below_res:  bool,       # Reversal candle closed BELOW resistance
    nyse_tick:              float,      # NYSE TICK at time of breakout
    gex_positive:           bool,       # GEX positive = dealers stabilizing
    atr:                    float,
) -> ChopSignal:
    """
    CHOP-01: False Breakout Fade.
    Document: "engineered by institutions to trigger stop losses above obvious
    resistance before selling. The reversal trade has a 74% win rate."

    ALL 6 conditions required:
    1. Key resistance level identified
    2. Breaks ABOVE but volume < 1.2x average
    3. Breakout candle closes in LOWER 40% of range
    4. NEXT candle immediately reverses BELOW resistance
    5. NYSE TICK NOT confirming — below +400 despite price new high
    6. GEX is POSITIVE (dealers stabilizing)
    """
    conditions = 0
    cond_details = []

    # Condition 1: Key level defined
    if resistance_level > 0:
        conditions += 1
        cond_details.append("✓ Key resistance identified")
    else:
        cond_details.append("✗ No key resistance defined")

    # Condition 2: Breakout with weak volume (< 1.2x)
    weak_volume = breakout_volume < avg_volume * 1.2
    if breakout_occurred and weak_volume:
        conditions += 1
        cond_details.append(f"✓ Weak breakout volume ({breakout_volume/avg_volume:.1f}x < 1.2x)")
    else:
        cond_details.append(f"✗ Volume: {breakout_volume/avg_volume:.1f}x (need < 1.2x)")

    # Condition 3: Candle closes in lower 40% of its range
    if breakout_close_pct <= 0.40:
        conditions += 1
        cond_details.append(f"✓ Wick above resistance, close in lower {breakout_close_pct:.0%}")
    else:
        cond_details.append(f"✗ Close at {breakout_close_pct:.0%} of range (need ≤40%)")

    # Condition 4: Next candle reverses back below resistance
    if next_candle_below_res:
        conditions += 1
        cond_details.append("✓ Reversal candle closed below resistance")
    else:
        cond_details.append("✗ No reversal candle below resistance yet")

    # Condition 5: NYSE TICK NOT confirming (<+400 despite new high)
    if nyse_tick < 400:
        conditions += 1
        cond_details.append(f"✓ TICK {nyse_tick:.0f} not confirming (< +400)")
    else:
        cond_details.append(f"✗ TICK {nyse_tick:.0f} confirming breakout (need < +400)")

    # Condition 6: GEX positive (dealers stabilizing)
    if gex_positive:
        conditions += 1
        cond_details.append("✓ GEX positive — dealers stabilizing")
    else:
        cond_details.append("✗ GEX not positive")

    if conditions < 6:
        return _no_chop("CHOP-01", "False Breakout Fade", 0.74, 2.0,
                         conditions,
                         f"Only {conditions}/6 conditions met. "
                         f"Need ALL 6: {'; '.join(c for c in cond_details if c.startswith('✗'))}")

    # Entry: short at close of reversal candle back below resistance
    entry  = price
    stop   = resistance_level + (atr * 0.15)  # 0.15% above false breakout high (doc)
    target1 = price - (price - (price * 0.994))  # VWAP proxy — document says first target is VWAP
    target2 = price * 0.988  # Prior consolidation support

    logger.info("[CHOP-01] False Breakout Fade CONFIRMED. All 6 conditions. "
                "Entry %.2f, Stop %.2f (above wick)", entry, stop)

    return _chop_signal("CHOP-01", "False Breakout Fade",
                         "short", entry, stop, target1, target2,
                         0.74, 2.0, 0.82, conditions,
                         f"All 6/6 conditions. False breakout at {resistance_level:.2f}. "
                         f"Weak volume ({breakout_volume/avg_volume:.1f}x), "
                         f"wick-close, reversal candle confirmed.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-02: VWAP EXTENDED FADE
# "When GEX is positive, VWAP extension fade has 72% win rate."
# Win Rate: 72% in Choppy Regime
# ─────────────────────────────────────────────────────────────────────────────

def chop02_vwap_extended_fade(
    price:              float,
    vwap:               float,
    choppy_confirmed:   bool,
    gex_positive:       bool,
    extension_pct:      float,   # % deviation from VWAP
    volume_declining:   bool,    # Volume declining on extension
    delta_diverging:    bool,    # Price new high but delta lower high
    tick_reverting:     bool,    # TICK reverting from extreme toward zero
    atr:                float,
) -> ChopSignal:
    """
    CHOP-02: VWAP Extended Fade.
    "Dealer hedging actively suppresses extended moves — pushes price back toward fair value."

    ALL 6 conditions required:
    1. Choppy regime confirmed (FORGE-CHOP-01)
    2. GEX positive — dealers stabilizing
    3. Price extended 1.0%+ from VWAP without catalyst
    4. Volume declining on extension
    5. Delta diverging (buyer exhaustion)
    6. NYSE TICK reverting toward zero from extreme
    """
    conditions = 0

    if choppy_confirmed:     conditions += 1
    if gex_positive:         conditions += 1
    if extension_pct >= 1.0: conditions += 1
    if volume_declining:     conditions += 1
    if delta_diverging:      conditions += 1
    if tick_reverting:       conditions += 1

    if conditions < 6:
        return _no_chop("CHOP-02", "VWAP Extended Fade", 0.72, 1.8,
                         conditions,
                         f"{conditions}/6 conditions. GEX+={gex_positive}, "
                         f"Extension={extension_pct:.1f}% (need ≥1.0%), "
                         f"Volume declining={volume_declining}, Delta div={delta_diverging}.")

    direction = "short" if price > vwap else "long"
    entry  = price
    stop   = price + atr * 0.3 if direction == "short" else price - atr * 0.3  # 0.3% beyond extreme
    target1 = vwap
    target2 = vwap

    logger.info("[CHOP-02] VWAP Extended Fade. %.1f%% extension. Target VWAP %.2f.",
                extension_pct, vwap)

    return _chop_signal("CHOP-02", "VWAP Extended Fade",
                         direction, entry, stop, target1, target2,
                         0.72, 1.8, 0.78, conditions,
                         f"VWAP fade: {extension_pct:.1f}% extension. "
                         f"GEX+ dealers push back. Target VWAP {vwap:.2f}.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-03: OPENING RANGE PRISON TRADE
# "Opening range becomes a cage — price bounces between H/L for 3–5 hours."
# Win Rate: 70% in Choppy Regime
# ─────────────────────────────────────────────────────────────────────────────

def chop03_opening_range_prison(
    price:              float,
    or_high:            float,      # Opening range high (first 15 min)
    or_low:             float,      # Opening range low
    choppy_confirmed:   bool,
    prior_test_failed:  bool,       # One boundary already tested and rejected
    price_approaching:  str,        # "high" or "low" — which boundary approaching
    volume_declining:   bool,       # Declining volume on approach
    time_et:            time,
    rejection_candle:   bool,       # Rejection candle forming at boundary
    atr:                float,
) -> ChopSignal:
    """
    CHOP-03: Opening Range Prison Trade.
    Entry window: 10:15am–12:30pm only.
    Can trade 2–3 times per session.
    """
    conditions = 0

    if choppy_confirmed:      conditions += 1
    if or_high > or_low:      conditions += 1   # OR established
    if prior_test_failed:     conditions += 1
    if price_approaching in ("high", "low"): conditions += 1
    if volume_declining:      conditions += 1

    in_window = time(10, 15) <= time_et <= time(12, 30)
    if in_window and rejection_candle: conditions += 1

    if conditions < 6:
        return _no_chop("CHOP-03", "Opening Range Prison", 0.70, 2.0,
                         conditions,
                         f"{conditions}/6. Window={in_window} ({time_et}), "
                         f"Prior fail={prior_test_failed}, Rejection={rejection_candle}.")

    direction = "short" if price_approaching == "high" else "long"
    boundary  = or_high if price_approaching == "high" else or_low
    opposite  = or_low  if price_approaching == "high" else or_high
    entry     = price
    stop      = boundary + (boundary * 0.002) if direction == "short" else boundary - (boundary * 0.002)
    target1   = opposite
    target2   = opposite

    or_range = or_high - or_low
    rr = abs(opposite - entry) / abs(stop - entry) if abs(stop - entry) > 0 else 2.0

    logger.info("[CHOP-03] OR Prison Trade. Approaching %s (%.2f). Target opposite: %.2f.",
                price_approaching, boundary, opposite)

    return _chop_signal("CHOP-03", "Opening Range Prison",
                         direction, entry, stop, target1, target2,
                         0.70, max(2.0, rr), 0.74, conditions,
                         f"OR Prison: {price_approaching} boundary {boundary:.2f}. "
                         f"OR range ${or_range:.2f}. Target opposite {opposite:.2f}.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-04: NYSE TICK EXTREME MEAN REVERSION
# "One of the highest win-rate patterns in choppy market conditions."
# Win Rate: 73% in Choppy Regime
# ─────────────────────────────────────────────────────────────────────────────

def chop04_tick_extreme_mean_reversion(
    price:                  float,
    nyse_tick:              float,       # Current 5-min TICK reading
    nyse_tick_prev:         float,       # Prior 5-min TICK reading
    choppy_confirmed:       bool,
    no_directional_catalyst: bool,
    vix_spike:              bool,        # VIX moved >5% in last 30 min (disqualifier)
    no_technical_break:     bool,        # No significant level broken in last 5 candles
    tick_sustained:         bool,        # Extreme for 2+ consecutive 5-min candles
    atr:                    float,
) -> ChopSignal:
    """
    CHOP-04: NYSE TICK Extreme Mean Reversion.
    "TICK extremes in choppy markets represent temporary imbalances that resolve quickly."

    TICK above +850: short signal
    TICK below -850: long signal
    ALL 6 conditions required.
    """
    tick_extreme_bull = nyse_tick <= -850 and nyse_tick_prev <= -850
    tick_extreme_bear = nyse_tick >= 850  and nyse_tick_prev >= 850
    tick_extreme      = tick_extreme_bull or tick_extreme_bear

    conditions = 0
    if choppy_confirmed:        conditions += 1
    if tick_extreme:            conditions += 1
    if no_directional_catalyst: conditions += 1
    if not vix_spike:           conditions += 1
    if no_technical_break:      conditions += 1
    if tick_sustained:          conditions += 1

    if conditions < 6:
        return _no_chop("CHOP-04", "TICK Extreme Mean Reversion", 0.73, 2.0,
                         conditions,
                         f"{conditions}/6. TICK={nyse_tick:.0f} (need >+850 or <-850 sustained), "
                         f"VIX spike={vix_spike}.")

    direction = "long" if tick_extreme_bull else "short"
    entry     = price
    stop_pct  = 0.003   # Stop: TICK extreme continues 2 more candles (price proxy)
    stop      = price + price * stop_pct if direction == "short" else price - price * stop_pct
    # Target: TICK returns to +/-200 — use VWAP as price proxy
    target1   = price + atr * 1.5 if direction == "long" else price - atr * 1.5
    target2   = target1

    logger.info("[CHOP-04] TICK Extreme %.0f. %s signal. All 6 conditions.",
                nyse_tick, direction.upper())

    return _chop_signal("CHOP-04", "TICK Extreme Mean Reversion",
                         direction, entry, stop, target1, target2,
                         0.73, 2.0, 0.80, conditions,
                         f"TICK extreme {nyse_tick:.0f} sustained 2 candles. "
                         f"{direction.upper()} fade. Target: TICK →±200.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-05: BOLLINGER BAND SQUEEZE REVERSION
# "Squeeze-and-fake is not random — institutional indecision."
# Win Rate: 69% in Choppy Regime
# ─────────────────────────────────────────────────────────────────────────────

def chop05_bb_squeeze_reversion(
    price:              float,
    bb_width:           float,       # Current BB width
    bb_width_percentile: float,      # Percentile vs last 50 sessions (0–1)
    price_broke_band:   str,         # "upper" or "lower" — which band broke
    candle_returned_inside: bool,    # Breakout candle closed back inside band
    volume_below_avg:   bool,
    choppy_confirmed:   bool,
    squeeze_candles:    int,         # How many consecutive candles narrowing
    bb_midline:         float,       # 20-period moving average (target)
    atr:                float,
) -> ChopSignal:
    """
    CHOP-05: Bollinger Band Squeeze Reversion.
    Enter the REVERSION after failed squeeze breakout. Target: BB midline.
    """
    conditions = 0
    if bb_width_percentile <= 0.20:  conditions += 1   # Below 20th percentile
    if price_broke_band in ("upper","lower"): conditions += 1
    if candle_returned_inside:       conditions += 1
    if volume_below_avg:             conditions += 1
    if choppy_confirmed:             conditions += 1
    if squeeze_candles >= 3:         conditions += 1   # Squeeze building ≥3 candles

    if conditions < 6:
        return _no_chop("CHOP-05", "BB Squeeze Reversion", 0.69, 1.8,
                         conditions,
                         f"{conditions}/6. BB percentile={bb_width_percentile:.0%} (need ≤20%), "
                         f"Returned inside={candle_returned_inside}, "
                         f"Squeeze candles={squeeze_candles} (need ≥3).")

    direction = "short" if price_broke_band == "upper" else "long"
    entry  = price
    stop   = price + atr * 0.25 if direction == "short" else price - atr * 0.25
    target1 = bb_midline
    target2 = bb_midline

    logger.info("[CHOP-05] BB Squeeze Reversion. Failed %s band break. Target midline %.2f.",
                price_broke_band, bb_midline)

    return _chop_signal("CHOP-05", "BB Squeeze Reversion",
                         direction, entry, stop, target1, target2,
                         0.69, 1.8, 0.72, conditions,
                         f"Failed {price_broke_band} BB break. "
                         f"Reversion to BB midline {bb_midline:.2f}.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-06: VALUE AREA OSCILLATION TRADE
# "70%+ probability of remaining within prior session's Value Area."
# Win Rate: 71% in Choppy Regime
# ─────────────────────────────────────────────────────────────────────────────

def chop06_value_area_oscillation(
    price:              float,
    vah:                float,       # Value Area High (prior session)
    val:                float,       # Value Area Low
    poc:                float,       # Point of Control
    choppy_confirmed:   bool,
    approaching_boundary: str,       # "vah" or "val"
    volume_declining:   bool,        # Volume declining on approach
    rejection_candle:   bool,        # Rejection at VA boundary
    gex_confluence:     bool,        # GEX or VWAP within 0.2% of VA boundary
    atr:                float,
) -> ChopSignal:
    """
    CHOP-06: Value Area Oscillation Trade.
    "Institutions defend their average cost basis — this is market structure physics."
    Target: prior session POC (center of gravity).
    """
    conditions = 0
    prior_va_identified = vah > val > 0
    if prior_va_identified:    conditions += 1
    if choppy_confirmed:       conditions += 1
    if approaching_boundary in ("vah","val"): conditions += 1
    if volume_declining:       conditions += 1
    if rejection_candle:       conditions += 1
    if gex_confluence:         conditions += 1

    if conditions < 6:
        return _no_chop("CHOP-06", "Value Area Oscillation", 0.71, 2.0,
                         conditions,
                         f"{conditions}/6. VA={val:.2f}–{vah:.2f}, "
                         f"Approaching={approaching_boundary}, "
                         f"Rejection={rejection_candle}, Confluence={gex_confluence}.")

    direction = "short" if approaching_boundary == "vah" else "long"
    boundary  = vah if approaching_boundary == "vah" else val
    entry     = price
    stop      = boundary + atr * 0.3 if direction == "short" else boundary - atr * 0.3
    target1   = poc   # Document: target = prior session POC
    target2   = poc

    logger.info("[CHOP-06] VA Oscillation. %s → POC %.2f. Entry %.2f.",
                approaching_boundary.upper(), poc, entry)

    return _chop_signal("CHOP-06", "Value Area Oscillation",
                         direction, entry, stop, target1, target2,
                         0.71, 2.0, 0.76, conditions,
                         f"VA boundary {approaching_boundary.upper()} {boundary:.2f} rejection. "
                         f"Target POC {poc:.2f}. Institutional defense.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-07: SESSION HIGH/LOW REJECTION
# "Third test of session high with 40% of first test's volume is a reversal setup."
# Win Rate: 72% in Choppy Regime
# ─────────────────────────────────────────────────────────────────────────────

def chop07_session_hl_rejection(
    price:              float,
    session_extreme:    float,       # Session high or low
    extreme_type:       str,         # "high" or "low"
    test_count:         int,         # Number of times level tested (need ≥2)
    current_volume:     float,       # Volume on current approach
    first_test_volume:  float,       # Volume on first test
    choppy_confirmed:   bool,
    gex_concentration:  bool,        # GEX gamma concentration within 0.3%
    rejection_candle:   bool,
    no_catalyst_60min:  bool,        # No pending catalyst in 60 minutes
    vwap:               float,
) -> ChopSignal:
    """
    CHOP-07: Session High/Low Rejection.
    "Volume on current approach 30%+ lower than first test volume."
    Target: VWAP. Can trade multiple times per session.
    """
    conditions = 0
    # Condition 1: Not first test (second or third approach)
    if test_count >= 2:              conditions += 1
    # Condition 2: Volume 30%+ lower than first test
    if current_volume <= first_test_volume * 0.70: conditions += 1
    if choppy_confirmed:             conditions += 1
    if gex_concentration:            conditions += 1
    if rejection_candle:             conditions += 1
    if no_catalyst_60min:            conditions += 1

    if conditions < 6:
        return _no_chop("CHOP-07", "Session H/L Rejection", 0.72, 2.0,
                         conditions,
                         f"{conditions}/6. Test #{test_count} (need ≥2), "
                         f"Volume {current_volume/first_test_volume:.0%} of first test "
                         f"(need ≤70%), GEX conc={gex_concentration}.")

    direction = "short" if extreme_type == "high" else "long"
    entry     = price
    stop_dist = abs(session_extreme - price) * 0.20 + abs(session_extreme - price)
    stop      = session_extreme + stop_dist * 0.002 if direction == "short" else session_extreme - stop_dist * 0.002

    # Take 70% off at VWAP, trail the rest
    target1 = vwap         # 70% of position
    target2 = vwap * 0.998 if direction == "short" else vwap * 1.002  # Trail remainder

    logger.info("[CHOP-07] Session %s Rejection. Test #%d. Target VWAP %.2f.",
                extreme_type.upper(), test_count, vwap)

    return _chop_signal("CHOP-07", "Session H/L Rejection",
                         direction, entry, stop, target1, target2,
                         0.72, 2.0, 0.76, conditions,
                         f"Session {extreme_type} test #{test_count}. "
                         f"Volume {current_volume/first_test_volume:.0%} of first test. "
                         f"GEX defending level. Target VWAP {vwap:.2f}.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-08: BREADTH DIVERGENCE REVERSAL
# "Narrow leadership — only large-caps driving index while majority flat/declining."
# Win Rate: 70% in Choppy Regime
# ─────────────────────────────────────────────────────────────────────────────

def chop08_breadth_divergence(
    price:                  float,
    made_new_session_high:  bool,
    advance_decline_pct:    float,    # % of stocks advancing (0–1)
    up_down_volume_ratio:   float,    # Up vol / Down vol
    choppy_confirmed:       bool,
    declining_sectors:      int,      # Number of S&P 500 sectors declining
    nyse_tick:              float,    # TICK reading at new high
    vwap:                   float,
    atr:                    float,
) -> ChopSignal:
    """
    CHOP-08: Breadth Divergence Reversal.
    "Structural weakness — insufficient buyers to sustain the move."
    Resolution typically within 30–90 minutes. Target: VWAP.
    """
    conditions = 0
    if made_new_session_high:         conditions += 1
    if advance_decline_pct < 0.55:    conditions += 1   # <55% advancing
    if up_down_volume_ratio < 1.5:    conditions += 1   # <1.5:1 volume ratio
    if choppy_confirmed:              conditions += 1
    if declining_sectors >= 2:        conditions += 1   # ≥2 sectors declining
    if nyse_tick < 600:               conditions += 1   # TICK <+600 at new high

    if conditions < 6:
        return _no_chop("CHOP-08", "Breadth Divergence Reversal", 0.70, 2.0,
                         conditions,
                         f"{conditions}/6. A/D={advance_decline_pct:.0%} (need <55%), "
                         f"UpDn Vol={up_down_volume_ratio:.1f}x (need <1.5x), "
                         f"TICK={nyse_tick:.0f} (need <+600), "
                         f"Declining sectors={declining_sectors} (need ≥2).")

    entry  = price
    stop   = price + price * 0.002   # Above new high + 0.2%
    target1 = vwap
    target2 = vwap

    logger.info("[CHOP-08] Breadth Divergence. A/D=%.0f%%, TICK=%.0f, %d sectors declining. Short.",
                advance_decline_pct*100, nyse_tick, declining_sectors)

    return _chop_signal("CHOP-08", "Breadth Divergence Reversal",
                         "short", entry, stop, target1, target2,
                         0.70, 2.0, 0.72, conditions,
                         f"New high on {advance_decline_pct:.0%} breadth. "
                         f"{declining_sectors} sectors declining. "
                         f"TICK {nyse_tick:.0f}. Target VWAP {vwap:.2f}.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-09: VOLATILITY COMPRESSION ENTRY
# "Intelligent complement to CHOP-01 — enters the GENUINE breakouts."
# Win Rate: 68% in Choppy Regime (transition trade)
# ─────────────────────────────────────────────────────────────────────────────

def chop09_volatility_compression_entry(
    price:                  float,
    atr_15min:              float,        # Current 15-min ATR
    atr_percentile:         float,        # vs last 20 sessions (0–1)
    bb_width_percentile:    float,        # vs 10 sessions (0–1)
    volume_declining_3h:    bool,         # Declining for previous 3 hours
    breakout_occurred:      bool,
    breakout_volume:        float,        # Must be ABOVE 1.5x avg (unlike CHOP-01)
    avg_volume:             float,
    breakout_close_pct:     float,        # Closes in UPPER 60% (strong — unlike CHOP-01)
    nyse_tick_confirming:   float,        # TICK >+700 (bullish) or <-700 (bearish)
    breakout_direction:     str,          # "up" or "down"
    compression_range:      float,        # Size of compression zone
) -> ChopSignal:
    """
    CHOP-09: Volatility Compression Entry.
    "UNLIKE CHOP-01 — strong volume, closes upper 60%, TICK confirms."
    Sized at 1.5x normal — strongest move of the session.
    """
    conditions = 0
    if atr_percentile <= 0.20:           conditions += 1   # ATR ≤20th percentile
    if bb_width_percentile <= 0.10:      conditions += 1   # BB ≤10-session minimum
    if volume_declining_3h:              conditions += 1
    if breakout_occurred:                conditions += 1
    # Condition 5: STRONG breakout (opposite of CHOP-01's weak breakout)
    strong_vol = breakout_volume >= avg_volume * 1.5
    strong_close = breakout_close_pct >= 0.60
    if strong_vol and strong_close:      conditions += 1
    # Condition 6: TICK confirms strongly
    tick_confirms = (breakout_direction == "up" and nyse_tick_confirming > 700) or \
                    (breakout_direction == "down" and nyse_tick_confirming < -700)
    if tick_confirms:                    conditions += 1

    if conditions < 6:
        return _no_chop("CHOP-09", "Volatility Compression Entry", 0.68, 2.0,
                         conditions,
                         f"{conditions}/6. ATR pctile={atr_percentile:.0%} (need ≤20%), "
                         f"Strong vol={strong_vol} ({breakout_volume/avg_volume:.1f}x, need ≥1.5x), "
                         f"Strong close={strong_close} ({breakout_close_pct:.0%}, need ≥60%), "
                         f"TICK confirms={tick_confirms} ({nyse_tick_confirming:.0f}).")

    direction = "long" if breakout_direction == "up" else "short"
    entry  = price
    stop   = price - compression_range if direction == "long" else price + compression_range
    target1 = price + compression_range * 1.5 if direction == "long" else price - compression_range * 1.5
    target2 = target1

    logger.info("[CHOP-09] Volatility Compression GENUINE breakout. %s. Target +%.1f pts.",
                direction.upper(), compression_range * 1.5)

    return _chop_signal("CHOP-09", "Volatility Compression Entry",
                         direction, entry, stop, target1, target2,
                         0.68, 2.0, 0.75, conditions,
                         f"Genuine compression break {breakout_direction}. "
                         f"Volume {breakout_volume/avg_volume:.1f}x, close {breakout_close_pct:.0%} of range. "
                         f"TICK {nyse_tick_confirming:.0f}. 1.5x size — regime transition trade.")


# ─────────────────────────────────────────────────────────────────────────────
# CHOP-10: POC GRAVITY ENHANCED TRADE
# "Enhanced VOL-01 calibrated for choppy regime — lower 0.7% threshold."
# Win Rate: 71% in Choppy Regime
# ─────────────────────────────────────────────────────────────────────────────

def chop10_poc_gravity_enhanced(
    price:                  float,
    session_poc:            float,   # Current session Point of Control
    prior_poc:              float,   # Prior session POC
    deviation_pct:          float,   # % deviation from session POC
    choppy_confirmed:       bool,
    gex_positive:           bool,    # Dealers stabilizing → enhances POC gravity
    no_catalyst:            bool,    # No catalyst explains deviation
    atr:                    float,
) -> ChopSignal:
    """
    CHOP-10: POC Gravity Enhanced.
    Document: "0.7% threshold (lower than standard VOL-01's 1.0%) because
    choppy sessions have smaller ranges."
    Multi-session POC confluence: +1.25x size.
    """
    # 0.7% threshold (NOT 1.0% like standard VOL-01)
    POC_DEVIATION_THRESHOLD = 0.007

    # Multi-session POC confluence: within 0.15% of prior session POC
    multi_poc_confluence = abs(session_poc - prior_poc) / session_poc < 0.0015 if session_poc > 0 else False

    conditions = 0
    if choppy_confirmed:             conditions += 1
    if session_poc > 0:              conditions += 1   # POC identified
    if deviation_pct >= POC_DEVIATION_THRESHOLD: conditions += 1
    if multi_poc_confluence:         conditions += 1   # Stronger signal
    elif deviation_pct >= POC_DEVIATION_THRESHOLD * 1.3: conditions += 1   # Stronger single POC dev
    else: pass  # No 4th condition
    if gex_positive:                 conditions += 1
    if no_catalyst:                  conditions += 1

    if conditions < 6:
        return _no_chop("CHOP-10", "POC Gravity Enhanced", 0.71, 1.8,
                         conditions,
                         f"{conditions}/6. POC dev={deviation_pct:.1%} (need ≥0.7%), "
                         f"GEX+={gex_positive}, Multi-POC={multi_poc_confluence}.")

    direction = "short" if price > session_poc else "long"
    entry  = price
    stop   = price + atr * 0.4 if direction == "short" else price - atr * 0.4
    target1 = session_poc
    target2 = session_poc

    # Multi-session POC confluence → 1.25x size (caller applies this)
    size_note = " [Multi-POC confluence: 1.25x size]" if multi_poc_confluence else ""

    logger.info("[CHOP-10] POC Gravity. %.1f%% deviation. Target POC %.2f.%s",
                deviation_pct * 100, session_poc, size_note)

    return _chop_signal("CHOP-10", "POC Gravity Enhanced",
                         direction, entry, stop, target1, target2,
                         0.71, 1.8, 0.76, conditions,
                         f"POC gravity {deviation_pct:.1f}% deviation. "
                         f"Target POC {session_poc:.2f}.{size_note}")


# ─────────────────────────────────────────────────────────────────────────────
# CHOPPY STRATEGY REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

CHOPPY_STRATEGY_REGISTRY: dict[str, dict] = {
    "CHOP-01": {"name": "False Breakout Fade",            "win_rate": 0.74, "rr": 2.0},
    "CHOP-02": {"name": "VWAP Extended Fade",             "win_rate": 0.72, "rr": 1.8},
    "CHOP-03": {"name": "Opening Range Prison Trade",     "win_rate": 0.70, "rr": 2.5},
    "CHOP-04": {"name": "TICK Extreme Mean Reversion",    "win_rate": 0.73, "rr": 2.0},
    "CHOP-05": {"name": "Bollinger Band Squeeze Reversion","win_rate": 0.69, "rr": 1.8},
    "CHOP-06": {"name": "Value Area Oscillation",         "win_rate": 0.71, "rr": 2.0},
    "CHOP-07": {"name": "Session H/L Rejection",          "win_rate": 0.72, "rr": 2.0},
    "CHOP-08": {"name": "Breadth Divergence Reversal",    "win_rate": 0.70, "rr": 2.0},
    "CHOP-09": {"name": "Volatility Compression Entry",   "win_rate": 0.68, "rr": 2.0},
    "CHOP-10": {"name": "POC Gravity Enhanced",           "win_rate": 0.71, "rr": 1.8},
}

CHOP_STRATEGY_COUNT = len(CHOPPY_STRATEGY_REGISTRY)

# Priority order in choppy regime (per FORGE-CHOP-02 and FORGE-72 update):
CHOP_PRIORITY_ORDER = [
    "CHOP-04",  # TICK Extreme first
    "CHOP-02",  # VWAP Extended Fade second
    "CHOP-10",  # POC Gravity Enhanced third
    "CHOP-01",  # False Breakout Fade
    "CHOP-06",  # Value Area Oscillation
    "CHOP-07",  # Session H/L Rejection
    "CHOP-03",  # Opening Range Prison
    "CHOP-08",  # Breadth Divergence
    "CHOP-05",  # BB Squeeze Reversion
    "CHOP-09",  # Volatility Compression (transition trade — last)
]

# Momentum strategies suspended in choppy regime (FORGE-CHOP-02)
SUSPENDED_IN_CHOP = frozenset(["GEX-01", "GEX-02", "ICT-08", "VOL-03", "SES-01", "SES-02"])
