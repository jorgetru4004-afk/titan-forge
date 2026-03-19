"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                    choppy_intelligence.py — Layer 3                         ║
║                                                                              ║
║  12 NEW FORGE-CHOP REQUIREMENTS                                             ║
║  + 11 EXISTING REQUIREMENT UPDATES FOR CHOPPY REGIME                        ║
║                                                                              ║
║  FORGE-CHOP-01: Regime Fingerprint Library (15 params, 7/15 = Choppy)      ║
║  FORGE-CHOP-02: Enhanced Session Quality Filter (3rd state: Choppy Mode)   ║
║  FORGE-CHOP-03: False Breakout Detection Engine (0–100 probability score)  ║
║  FORGE-CHOP-04: Adaptive Stop Width Protocol (Chop Noise Factor)           ║
║  FORGE-CHOP-05: Choppy Market Position Sizing (0.6x Kelly base)            ║
║  FORGE-CHOP-06: Maximum Trade Duration Protocol (45-min, 30-min extension) ║
║  FORGE-CHOP-07: Choppy Qualifying Day Protocol (4–6 trades, 2pm backup)    ║
║  FORGE-CHOP-08: Chop-to-Trend Transition Detector (8 signals, 5/8 = fire) ║
║  FORGE-CHOP-09: Correlation Collapse Monitor (below 0.5 from 0.85 baseline)║
║  FORGE-CHOP-10: Choppy Simulation Requirements (500 sessions, 5 thresholds)║
║  FORGE-CHOP-11: Choppy Behavioral Consistency Profile (gradual 2–3 sessions)║
║  FORGE-CHOP-12: Choppy Performance Attribution (per-session report)        ║
║                                                                              ║
║  UPDATED REQUIREMENTS (choppy-regime variants):                             ║
║  FORGE-08, 11, 12, 15, 43, 58, 61, 62, 63, 65, 72, 78                     ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 2026                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional

from firm_rules import FirmID

logger = logging.getLogger("titan_forge.choppy_intelligence")


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-01: REGIME FINGERPRINT LIBRARY
# "15+ parameters. 7/15 simultaneously indicating choppy → Choppy Regime 4."
# "Updates every 15 minutes. Identifies chop by 9:50am in most sessions."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RegimeFingerprint:
    """Snapshot of 15 market parameters evaluated for choppiness."""
    timestamp:                  datetime
    # 15 parameters (from document)
    adx:                        float    # Below 20 = no directional movement
    gex_dollars:                float    # Positive >$500M = stabilizing
    vix:                        float    # 18–30 = choppy range
    bb_width_percentile:        float    # Below 50th percentile
    advance_decline_pct:        float    # Between 40–60%
    atr_vs_20day_avg:           float    # Ratio: <1.0 = below average
    opening_range_percentile:   float    # Below 20th percentile = small OR
    vwap_deviation_by_10am:     float    # Less than 0.3% by 10am
    sector_correlation:         float    # Below 0.6 = correlation breakdown
    # Derived indicator flags (from document — 15+ parameters total)
    tick_oscillating:           bool     # TICK oscillating ±500 vs sustained
    volume_below_avg:           bool     # Overall session volume below average
    directional_reversals:      int      # Count of intraday direction changes
    bb_width_narrowing:         bool     # BB width decreasing (compression)
    prior_day_range_pct:        float    # Prior day range as % of price
    market_structure_unclear:   bool     # No clear higher highs/lower lows

    @property
    def choppy_signals(self) -> int:
        """Count how many of 15 parameters indicate choppy conditions."""
        count = 0
        if self.adx < 20:                              count += 1
        if self.gex_dollars > 500_000_000:             count += 1  # Positive >$500M
        if 18 <= self.vix <= 30:                       count += 1
        if self.bb_width_percentile < 0.50:            count += 1
        if 0.40 <= self.advance_decline_pct <= 0.60:   count += 1
        if self.atr_vs_20day_avg < 1.0:                count += 1
        if self.opening_range_percentile < 0.20:       count += 1
        if self.vwap_deviation_by_10am < 0.003:        count += 1  # < 0.3%
        if self.sector_correlation < 0.60:             count += 1
        if self.tick_oscillating:                      count += 1
        if self.volume_below_avg:                      count += 1
        if self.directional_reversals >= 4:            count += 1
        if self.bb_width_narrowing:                    count += 1
        if self.prior_day_range_pct < 0.008:           count += 1  # Tight prior day
        if self.market_structure_unclear:              count += 1
        return count

    @property
    def is_choppy(self) -> bool:
        """Document: 7 of 15 simultaneously = Choppy Regime 4."""
        return self.choppy_signals >= 7

    @property
    def choppy_score(self) -> float:
        """0–1 confidence score for choppiness."""
        return self.choppy_signals / 15.0


class RegimeFingerprintLibrary:
    """
    FORGE-CHOP-01: Regime Fingerprint Library.
    Stores snapshots, updates every 15 minutes, identifies chop by 9:50am.
    When 7+ of 15 parameters indicate choppy: Genesis Engine classifies as Regime 4.
    """

    CHOPPY_THRESHOLD: int = 7   # Document: 7 of 15

    def __init__(self):
        self._snapshots: list[RegimeFingerprint] = []
        self._current_regime: str = "unknown"

    def record_snapshot(self, fp: RegimeFingerprint) -> str:
        """
        Record a new fingerprint snapshot. Returns current regime classification.
        Document: updates every 15 minutes.
        """
        self._snapshots.append(fp)
        if len(self._snapshots) > 96:   # Keep 24 hours of 15-min snapshots
            self._snapshots.pop(0)

        regime = self._classify(fp)
        self._current_regime = regime

        logger.info(
            "[FORGE-CHOP-01] Fingerprint: %d/15 choppy signals → Regime: %s",
            fp.choppy_signals, regime,
        )
        return regime

    def _classify(self, fp: RegimeFingerprint) -> str:
        if fp.is_choppy:
            conf = fp.choppy_score
            return "Regime4_Choppy" if conf >= 0.60 else "Regime4_Choppy_Weak"
        return "Regime1_Trending" if fp.adx > 25 else "Regime2_Ranging"

    @property
    def current_regime(self) -> str:
        return self._current_regime

    @property
    def is_choppy_confirmed(self) -> bool:
        if not self._snapshots:
            return False
        # Need 2+ consecutive snapshots confirming chop (reduces false positives)
        if len(self._snapshots) < 2:
            return False
        recent = self._snapshots[-2:]
        return all(s.is_choppy for s in recent)

    def mean_reversion_probability(self) -> float:
        """
        FORGE-CHOP-01 + FORGE-58: Mean reversion probability for scoring engine.
        Higher choppy score = higher mean reversion probability.
        """
        if not self._snapshots:
            return 0.0
        latest = self._snapshots[-1]
        base_prob = latest.choppy_score
        # Boost if multiple consecutive choppy readings
        consecutive_choppy = sum(1 for s in self._snapshots[-4:] if s.is_choppy)
        return min(1.0, base_prob + consecutive_choppy * 0.05)


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-02: ENHANCED SESSION QUALITY FILTER
# Third state added to FORGE-08: "Choppy Mode Activated"
# When active: momentum suspended, CHOP strategies activated, 60% size, 1 pos max.
# ─────────────────────────────────────────────────────────────────────────────

class SessionMode(Enum):
    TRENDING_PLAYBOOK  = "trending"   # Original state 1: trading allowed
    SKIPPED            = "skipped"    # Original state 2: trading skipped
    CHOPPY_PLAYBOOK    = "choppy"     # NEW state 3: choppy mode activated


@dataclass
class EnhancedSessionState:
    """FORGE-CHOP-02: Output of enhanced session quality filter."""
    mode:                       SessionMode
    session_score:              float
    choppy_confirmed:           bool
    # When choppy mode activates:
    momentum_strategies_suspended: bool    # GEX-01, GEX-02, ICT-08, VOL-03
    chop_strategies_active:     bool       # CHOP-01 through CHOP-10
    position_size_pct:          float      # 0.60 in choppy (60% of standard)
    max_simultaneous_positions: int        # 1 in choppy
    max_trade_duration_min:     int        # 60 minutes in choppy
    reason:                     str


def classify_session_enhanced(
    session_score:          float,     # Existing FORGE-08 score (0–10)
    choppy_confirmed:       bool,      # From FORGE-CHOP-01
    trending_threshold:     float = 6.0,
    skip_threshold:         float = 4.0,
) -> EnhancedSessionState:
    """
    FORGE-CHOP-02: Enhanced session quality filter.
    Document: "The filter no longer says yes or no to trading.
    It says what kind of trading — trending playbook or choppy playbook."
    """
    if choppy_confirmed:
        return EnhancedSessionState(
            mode=SessionMode.CHOPPY_PLAYBOOK,
            session_score=session_score,
            choppy_confirmed=True,
            momentum_strategies_suspended=True,   # GEX-01, GEX-02, ICT-08, VOL-03 SUSPENDED
            chop_strategies_active=True,          # CHOP-01 through CHOP-10 ACTIVATED
            position_size_pct=0.60,               # 60% of standard
            max_simultaneous_positions=1,         # 1 position maximum
            max_trade_duration_min=60,            # 60-minute maximum hold
            reason=(
                "CHOPPY MODE ACTIVATED: Regime 4 confirmed. "
                "Momentum strategies suspended. CHOP-01–10 active. "
                "Size: 60%. Max 1 position. Max 60-minute hold."
            ),
        )
    elif session_score >= trending_threshold:
        return EnhancedSessionState(
            mode=SessionMode.TRENDING_PLAYBOOK,
            session_score=session_score,
            choppy_confirmed=False,
            momentum_strategies_suspended=False,
            chop_strategies_active=False,
            position_size_pct=1.0,
            max_simultaneous_positions=2,
            max_trade_duration_min=360,
            reason=f"Trending playbook: score {session_score:.1f}/10.",
        )
    else:
        return EnhancedSessionState(
            mode=SessionMode.SKIPPED,
            session_score=session_score,
            choppy_confirmed=False,
            momentum_strategies_suspended=True,
            chop_strategies_active=False,
            position_size_pct=0.0,
            max_simultaneous_positions=0,
            max_trade_duration_min=0,
            reason=f"Session skipped: score {session_score:.1f} < {skip_threshold:.1f}.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-03: FALSE BREAKOUT DETECTION ENGINE
# "Scores every breakout on a False Breakout Probability scale 0–100."
# ">65: CHOP-01 signal. <35: CHOP-09 signal. 35–65: no entry (ambiguous)."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FalseBreakoutScore:
    """FORGE-CHOP-03: False breakout probability score."""
    score:              float    # 0–100
    is_false:           bool     # score > 65
    is_genuine:         bool     # score < 35
    is_ambiguous:       bool     # 35–65
    signal:             str      # "CHOP-01" / "CHOP-09" / "NO_ENTRY"
    components:         dict     # Breakdown of what drove the score
    recommendation:     str


def score_false_breakout(
    volume_ratio:           float,    # Breakout vol / avg vol
    close_position:         float,    # 0=low, 1=high of candle range
    tick_confirmation:      float,    # TICK at breakout (+high = confirmed, low = not)
    gex_positive:           bool,     # Positive GEX = stabilizing (more likely false)
    prior_tests:            int,      # Times level was tested before (more = more likely false)
    delta_confirming:       bool,     # Delta direction confirms breakout
) -> FalseBreakoutScore:
    """
    FORGE-CHOP-03: False Breakout Detection Engine.
    6 monitored signals → 0–100 false probability score.
    """
    score = 50.0   # Start neutral
    components: dict[str, float] = {}

    # Volume: weak vol → more likely false
    if volume_ratio < 0.8:
        score += 20; components["volume_weak"] = +20
    elif volume_ratio < 1.2:
        score += 10; components["volume_average"] = +10
    elif volume_ratio > 1.5:
        score -= 20; components["volume_strong"] = -20
    else:
        score -= 5; components["volume_moderate"] = -5

    # Close position: wick (close in lower 40%) → more likely false
    if close_position <= 0.40:
        score += 18; components["wick_close"] = +18
    elif close_position >= 0.70:
        score -= 15; components["strong_close"] = -15
    else:
        components["neutral_close"] = 0

    # TICK: not confirming → false; confirming → genuine
    if tick_confirmation < 400:
        score += 15; components["tick_not_confirming"] = +15
    elif tick_confirmation > 700:
        score -= 15; components["tick_confirming"] = -15

    # GEX: positive → stabilizing → false more likely
    if gex_positive:
        score += 10; components["gex_stabilizing"] = +10
    else:
        score -= 10; components["gex_amplifying"] = -10

    # Prior tests: multiple previous tests → more likely false
    if prior_tests >= 3:
        score += 12; components["many_prior_tests"] = +12
    elif prior_tests >= 2:
        score += 6; components["prior_tests"] = +6

    # Delta: not confirming → false
    if not delta_confirming:
        score += 10; components["delta_not_confirming"] = +10
    else:
        score -= 10; components["delta_confirming"] = -10

    score = max(0.0, min(100.0, score))

    is_false    = score > 65
    is_genuine  = score < 35
    is_ambiguous = not is_false and not is_genuine

    if is_false:
        signal = "CHOP-01"
        rec = f"FALSE BREAKOUT ({score:.0f}/100). Fade it. Generate CHOP-01 entry."
    elif is_genuine:
        signal = "CHOP-09"
        rec = f"GENUINE BREAKOUT ({score:.0f}/100). Enter the break. Generate CHOP-09 entry."
    else:
        signal = "NO_ENTRY"
        rec = f"AMBIGUOUS ({score:.0f}/100). No entry. Wait for clarification."

    return FalseBreakoutScore(
        score=round(score, 1), is_false=is_false,
        is_genuine=is_genuine, is_ambiguous=is_ambiguous,
        signal=signal, components=components, recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-04: ADAPTIVE STOP WIDTH PROTOCOL
# "Chop Noise Factor = current ATR / 20-session average ATR."
# "Same dollar risk, completely different stop physics."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AdaptiveStopResult:
    """FORGE-CHOP-04: Adaptive stop calculation result."""
    standard_stop_distance:    float
    chop_noise_factor:         float   # current ATR / 20-session avg ATR
    adjusted_stop_distance:    float   # Wider stop
    size_multiplier:           float   # Smaller size to keep dollar risk constant
    dollar_risk_preserved:     bool    # Must stay the same
    explanation:               str


def calculate_adaptive_stop(
    standard_stop:      float,    # Normal stop distance in price
    current_atr:        float,    # Today's ATR
    avg_atr_20day:      float,    # 20-session average ATR
) -> AdaptiveStopResult:
    """
    FORGE-CHOP-04: Adaptive Stop Width Protocol.
    "If current ATR is 1.4x 20-session average: stops widen by 40%."
    "Position size reduces proportionally to keep dollar risk constant."
    """
    if avg_atr_20day <= 0:
        return AdaptiveStopResult(
            standard_stop, 1.0, standard_stop, 1.0, True,
            "No 20-day ATR data. Using standard stop."
        )

    noise_factor = current_atr / avg_atr_20day
    adjusted_stop = standard_stop * noise_factor
    # Size reduces proportionally: same $ risk = wider stop × smaller size
    size_multiplier = 1.0 / noise_factor if noise_factor > 0 else 1.0
    # Cap: don't let size go below 30% or above 100%
    size_multiplier = max(0.30, min(1.00, size_multiplier))

    explanation = (
        f"Chop Noise Factor: {noise_factor:.2f}x "
        f"({current_atr:.4f} / {avg_atr_20day:.4f} avg ATR). "
        f"Stop widens: {standard_stop:.4f} → {adjusted_stop:.4f} "
        f"(+{(noise_factor-1)*100:.0f}%). "
        f"Size reduces to {size_multiplier:.0%} to preserve dollar risk."
    )

    return AdaptiveStopResult(
        standard_stop_distance=standard_stop,
        chop_noise_factor=round(noise_factor, 4),
        adjusted_stop_distance=round(adjusted_stop, 6),
        size_multiplier=round(size_multiplier, 4),
        dollar_risk_preserved=True,
        explanation=explanation,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-05: CHOPPY MARKET POSITION SIZING
# "Choppy base size = 0.6x standard Kelly-calculated size."
# ─────────────────────────────────────────────────────────────────────────────

CHOP_SIZE_MULTIPLIER: float = 0.60   # Document: 0.6x Kelly base in choppy

def calculate_choppy_position_size(
    standard_kelly_size: float,
    correlation_collapse: bool = False,   # FORGE-CHOP-09: additional 20% reduction
) -> float:
    """
    FORGE-CHOP-05: Choppy Market Position Sizing.
    0.6x standard. Additional -20% if correlation collapse detected.
    """
    size = standard_kelly_size * CHOP_SIZE_MULTIPLIER
    if correlation_collapse:
        size *= 0.80   # FORGE-CHOP-09: correlation collapse → additional 20% cut
    return max(0.01, round(size, 4))


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-06: MAXIMUM TRADE DURATION PROTOCOL
# "45 minutes. If >0.8R profit at 45min mark: move stop to BE, 30-min extension."
# ─────────────────────────────────────────────────────────────────────────────

CHOP_MAX_HOLD_MIN: int      = 45
CHOP_EXTENSION_MIN: int     = 30
CHOP_EXTENSION_PROFIT_R: float = 0.8


@dataclass
class DurationCheck:
    """FORGE-CHOP-06: Trade duration check result."""
    minutes_open:       float
    profit_r:           float        # Current profit in R multiples
    action:             str          # "HOLD" / "MOVE_BE_EXTEND" / "CLOSE_NOW"
    reason:             str
    extension_granted:  bool


def check_trade_duration(
    entry_time:     datetime,
    current_time:   datetime,
    profit_r:       float,           # Current P&L as multiple of initial risk
    extension_used: bool = False,    # Has extension already been granted?
) -> DurationCheck:
    """
    FORGE-CHOP-06: Maximum Trade Duration Protocol.
    Choppy regime positions MUST be resolved within 45 minutes or closed.
    """
    minutes = (current_time - entry_time).total_seconds() / 60.0

    if minutes < CHOP_MAX_HOLD_MIN:
        return DurationCheck(
            minutes_open=round(minutes, 1),
            profit_r=profit_r,
            action="HOLD",
            reason=f"{minutes:.0f}/{CHOP_MAX_HOLD_MIN} min elapsed.",
            extension_granted=False,
        )

    extension_elapsed = minutes >= (CHOP_MAX_HOLD_MIN + CHOP_EXTENSION_MIN)

    if not extension_used and profit_r >= CHOP_EXTENSION_PROFIT_R:
        # ≥0.8R profit at 45min: move stop to BE, grant 30-min extension
        return DurationCheck(
            minutes_open=round(minutes, 1),
            profit_r=profit_r,
            action="MOVE_BE_EXTEND",
            reason=(
                f"45 min elapsed. Profit {profit_r:.2f}R ≥ {CHOP_EXTENSION_PROFIT_R}R threshold. "
                f"MOVE STOP TO BREAKEVEN. 30-minute extension granted."
            ),
            extension_granted=True,
        )
    elif extension_used and not extension_elapsed:
        return DurationCheck(
            minutes_open=round(minutes, 1),
            profit_r=profit_r,
            action="HOLD",
            reason=f"Extension active. {CHOP_MAX_HOLD_MIN + CHOP_EXTENSION_MIN - minutes:.0f} min remaining.",
            extension_granted=True,
        )
    else:
        return DurationCheck(
            minutes_open=round(minutes, 1),
            profit_r=profit_r,
            action="CLOSE_NOW",
            reason=(
                f"CLOSE AT MARKET: {minutes:.0f} min elapsed "
                f"({'extension expired' if extension_used else 'no extension — profit only ' + f'{profit_r:.2f}R'})."
            ),
            extension_granted=False,
        )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-07: CHOPPY QUALIFYING DAY PROTOCOL
# "4–6 smaller trades. $50–65 each at Topstep. $65–85 each at Apex."
# "At 2:00pm if below 60% of threshold: activate backup protocol."
# ─────────────────────────────────────────────────────────────────────────────

QUALIFYING_THRESHOLDS: dict[str, float] = {
    FirmID.TOPSTEP: 150.0,   # Document: Topstep $150 minimum
    FirmID.APEX:    250.0,   # Document: Apex $250 minimum
}

CHOP_TRADE_TARGET_PER_TRADE: dict[str, tuple[float, float]] = {
    FirmID.TOPSTEP: (50.0, 65.0),    # $50–65 per trade
    FirmID.APEX:    (65.0, 85.0),    # $65–85 per trade
}


@dataclass
class QualifyingDayStatus:
    """FORGE-CHOP-07: Qualifying day protocol status."""
    firm_id:                str
    threshold:              float
    current_pnl:            float
    pct_of_threshold:       float
    trades_needed:          int      # Estimated trades still needed
    backup_protocol:        bool     # 2pm backup activated
    recommended_setups:     list[str]
    recommendation:         str


def check_qualifying_day_protocol(
    firm_id:        str,
    current_pnl:    float,
    current_time_et: datetime,
    trades_taken:   int,
) -> QualifyingDayStatus:
    """
    FORGE-CHOP-07: Choppy Qualifying Day Protocol.
    At 2:00pm ET if <60% of threshold: activate CHOP-04 + CHOP-10 backup.
    """
    threshold = QUALIFYING_THRESHOLDS.get(firm_id, 150.0)
    pct = current_pnl / threshold if threshold > 0 else 0.0
    per_trade_range = CHOP_TRADE_TARGET_PER_TRADE.get(firm_id, (50.0, 65.0))
    avg_per_trade = sum(per_trade_range) / 2
    remaining = max(0.0, threshold - current_pnl)
    trades_needed = max(0, int(remaining / avg_per_trade) + 1) if remaining > 0 else 0

    # 2pm backup check
    is_after_2pm = current_time_et.hour >= 14
    backup = is_after_2pm and pct < 0.60

    if backup:
        rec_setups = ["CHOP-04", "CHOP-10"]  # Document: TICK Extreme + POC Gravity
        rec = (
            f"2PM BACKUP PROTOCOL ACTIVE: Only {pct:.0%} of ${threshold:.0f} threshold reached. "
            f"Deploy CHOP-04 (TICK Extreme) and CHOP-10 (POC Gravity) exclusively. "
            f"Need ~{trades_needed} more trades at ${avg_per_trade:.0f} avg."
        )
    elif pct >= 1.0:
        rec_setups = []
        rec = f"✅ Qualifying threshold met: ${current_pnl:.0f} ≥ ${threshold:.0f}. DONE for {firm_id}."
    else:
        rec_setups = ["CHOP-04", "CHOP-02", "CHOP-10"]
        rec = (
            f"On pace: {pct:.0%} complete. ${remaining:.0f} remaining. "
            f"~{trades_needed} more trades needed."
        )

    return QualifyingDayStatus(
        firm_id=firm_id, threshold=threshold,
        current_pnl=current_pnl, pct_of_threshold=round(pct, 4),
        trades_needed=trades_needed, backup_protocol=backup,
        recommended_setups=rec_setups, recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-08: CHOP-TO-TREND TRANSITION DETECTOR
# "8 signals. 5+ firing within 15-minute window = Regime Transition Alert."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TransitionDetectorResult:
    """FORGE-CHOP-08: Chop-to-trend transition detection result."""
    signals_firing:         int       # Out of 8
    transition_detected:    bool      # 5+ signals
    fired_signals:          list[str]
    action:                 str       # What TITAN FORGE must do
    reason:                 str


def detect_chop_to_trend_transition(
    adx_crossing_20_rising:     bool,   # Signal 1: ADX crosses >20 and rising
    atr_expanding:              bool,   # Signal 2: ATR expands above session avg
    bb_widening_after_squeeze:  bool,   # Signal 3: BB widening after squeeze
    tick_sustained_600:         bool,   # Signal 4: TICK >+600 for 3+ consecutive candles
    volume_expanding_1_5x:      bool,   # Signal 5: Volume expands >1.5x avg
    gex_going_negative:         bool,   # Signal 6: GEX transitions positive→negative
    breadth_above_65:           bool,   # Signal 7: Market breadth >65% advancing
    genuine_range_breakout:     bool,   # Signal 8: Genuine breakout from day range on strong vol
) -> TransitionDetectorResult:
    """
    FORGE-CHOP-08: Chop-to-Trend Transition Detector.
    "When 5+ of 8 signals fire within a 15-minute window: Regime Transition Alert."
    Document: "CHOP-09 Volatility Compression Entry is the first setup scanned for."
    """
    signal_map = {
        "ADX crossing 20 rising":      adx_crossing_20_rising,
        "ATR expanding":               atr_expanding,
        "BB widening post-squeeze":    bb_widening_after_squeeze,
        "TICK >+600 sustained":        tick_sustained_600,
        "Volume >1.5x":                volume_expanding_1_5x,
        "GEX going negative":          gex_going_negative,
        "Breadth >65% advancing":      breadth_above_65,
        "Genuine range breakout":      genuine_range_breakout,
    }
    fired = [name for name, val in signal_map.items() if val]
    count = len(fired)
    detected = count >= 5

    if detected:
        action = (
            "REGIME TRANSITION ALERT: Chop → Trend. "
            "Choppy strategy stack SUSPENDED. "
            "Trending stack ACTIVATING. "
            "Scan CHOP-09 Volatility Compression Entry FIRST. "
            "See FORGE-78 update: 15-minute pause before full activation."
        )
        logger.warning("[FORGE-CHOP-08] 🔄 TRANSITION: %d/8 signals. %s", count, fired)
    else:
        action = f"No transition. {count}/8 signals. Remain in choppy playbook."

    return TransitionDetectorResult(
        signals_firing=count,
        transition_detected=detected,
        fired_signals=fired,
        action=action,
        reason=f"{count}/8 signals firing. Threshold: 5. Fired: {fired}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-09: CORRELATION COLLAPSE MONITOR
# "Below 0.5 from normal baseline of 0.85+: Correlation Collapse Alert."
# "Additional 20% size reduction. CHOP-08 elevated to highest priority."
# ─────────────────────────────────────────────────────────────────────────────

CORRELATION_NORMAL_BASELINE: float  = 0.85
CORRELATION_COLLAPSE_THRESHOLD: float = 0.50

# Primary instrument pairs to monitor
MONITORED_PAIRS: list[tuple[str, str]] = [
    ("ES", "NQ"),          # S&P vs Nasdaq — normally 0.90+
    ("EURUSD", "GBPUSD"),  # EUR vs GBP — normally 0.85+
    ("ES", "RTY"),         # S&P vs Russell — normally 0.80+
]


@dataclass
class CorrelationCollapseStatus:
    """FORGE-CHOP-09: Correlation collapse monitor result."""
    is_collapsed:           bool
    collapsed_pairs:        list[tuple[str, str, float]]   # (asset1, asset2, correlation)
    additional_size_cut:    float     # 0.20 if collapsed
    chop08_elevated:        bool      # CHOP-08 moved to highest priority
    breadth_trades_blocked: bool      # No trades relying on breadth confirmation
    recommendation:         str


def check_correlation_collapse(
    pair_correlations: dict[tuple[str, str], float],   # pair → rolling 30-min correlation
) -> CorrelationCollapseStatus:
    """
    FORGE-CHOP-09: Correlation Collapse Monitor.
    Rolling 30-minute correlations. Below 0.5 from 0.85 baseline = alert.
    """
    collapsed: list[tuple[str, str, float]] = []
    for pair, corr in pair_correlations.items():
        if corr < CORRELATION_COLLAPSE_THRESHOLD:
            collapsed.append((pair[0], pair[1], corr))

    is_collapsed = len(collapsed) > 0

    if is_collapsed:
        rec = (
            f"⚠ CORRELATION COLLAPSE: {len(collapsed)} pair(s) below 0.5 threshold. "
            f"Collapsed: {collapsed}. "
            f"(1) Blocking breadth-confirmation trades. "
            f"(2) CHOP-08 Breadth Divergence elevated to HIGHEST priority. "
            f"(3) Additional 20% size cut applied."
        )
        logger.warning("[FORGE-CHOP-09] Correlation collapse detected: %s", collapsed)
    else:
        rec = "Correlations normal. No collapse detected."

    return CorrelationCollapseStatus(
        is_collapsed=is_collapsed,
        collapsed_pairs=collapsed,
        additional_size_cut=0.20 if is_collapsed else 0.0,
        chop08_elevated=is_collapsed,
        breadth_trades_blocked=is_collapsed,
        recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-10: CHOPPY SIMULATION REQUIREMENTS
# "5 thresholds. All must be met before live choppy regime trading begins."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChoppySimValidation:
    """FORGE-CHOP-10: All 5 choppy simulation thresholds."""
    sessions_simulated:         int        # Need ≥500
    chop_strategies_min_trades: dict[str, int]   # Each CHOP strategy ≥60 trades
    false_breakout_accuracy:    float      # FORGE-CHOP-03 ≥65% accuracy
    regime_id_accuracy:         float      # FORGE-CHOP-01 ≥80% by 10am
    transition_detection_pct:   float      # FORGE-CHOP-08 ≥70% within 2 candles
    # Results
    all_passed:                 bool
    failures:                   list[str]

    MIN_SESSIONS:           int   = 500
    MIN_TRADES_PER_CHOP:    int   = 60
    MIN_FB_ACCURACY:        float = 0.65
    MIN_REGIME_ACCURACY:    float = 0.80
    MIN_TRANSITION_PCT:     float = 0.70


def validate_choppy_simulation(
    sessions_run:               int,
    strategy_trade_counts:      dict[str, int],
    false_breakout_accuracy:    float,
    regime_id_accuracy:         float,
    transition_pct:             float,
) -> ChoppySimValidation:
    """
    FORGE-CHOP-10: Validate all 5 choppy simulation thresholds.
    ALL 5 must pass before live choppy trading begins.
    """
    CHOP_IDS = [f"CHOP-{i:02d}" for i in range(1, 11)]
    failures: list[str] = []

    # Threshold 1: 500+ sessions
    if sessions_run < 500:
        failures.append(f"Sessions: {sessions_run} < 500 required.")

    # Threshold 2: Each CHOP strategy ≥60 trades
    low_strategies = [
        f"{sid}: {strategy_trade_counts.get(sid, 0)} trades"
        for sid in CHOP_IDS
        if strategy_trade_counts.get(sid, 0) < 60
    ]
    if low_strategies:
        failures.append(f"Strategies below 60 trades: {low_strategies}")

    # Threshold 3: False breakout detection ≥65%
    if false_breakout_accuracy < 0.65:
        failures.append(f"False breakout accuracy {false_breakout_accuracy:.0%} < 65%.")

    # Threshold 4: Regime identification ≥80% by 10am
    if regime_id_accuracy < 0.80:
        failures.append(f"Regime ID accuracy {regime_id_accuracy:.0%} < 80%.")

    # Threshold 5: Transition detection ≥70% within 2 candles
    if transition_pct < 0.70:
        failures.append(f"Transition detection {transition_pct:.0%} < 70%.")

    return ChoppySimValidation(
        sessions_simulated=sessions_run,
        chop_strategies_min_trades=strategy_trade_counts,
        false_breakout_accuracy=false_breakout_accuracy,
        regime_id_accuracy=regime_id_accuracy,
        transition_detection_pct=transition_pct,
        all_passed=len(failures) == 0,
        failures=failures,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-11: CHOPPY BEHAVIORAL CONSISTENCY PROFILE
# "Gradual over 2–3 sessions not instantaneous."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BehavioralTransitionState:
    """FORGE-CHOP-11: Behavioral transition progress (0=trending, 1=full choppy)."""
    sessions_in_transition:     int      # 0, 1, 2, 3
    position_size_pct:          float    # Reducing progressively
    target_frequency_pct:       float    # Increasing progressively
    max_hold_minutes:           int      # Shortening progressively
    is_fully_choppy:            bool
    recommendation:             str


def get_behavioral_transition(
    sessions_since_chop_confirmed: int,
) -> BehavioralTransitionState:
    """
    FORGE-CHOP-11: Gradual behavioral transition.
    "Position sizes reduce progressively. Trade frequency increases progressively."
    "Hold times shorten progressively. Prop firm sees a professional trader adapting."
    """
    # Document: 2–3 sessions for full transition
    # Session 0 (just confirmed): 80% choppy profile
    # Session 1: 90% choppy
    # Session 2+: 100% choppy
    if sessions_since_chop_confirmed == 0:
        return BehavioralTransitionState(
            0, 0.80, 0.80, 52, False,
            "Day 1 of chop: 80% choppy profile. Gradual transition started."
        )
    elif sessions_since_chop_confirmed == 1:
        return BehavioralTransitionState(
            1, 0.70, 0.90, 48, False,
            "Day 2 of chop: 90% choppy profile. Nearly transitioned."
        )
    else:
        return BehavioralTransitionState(
            sessions_since_chop_confirmed, 0.60, 1.00, 45, True,
            "Full choppy profile active (Day 3+). 60% size, max freq, 45-min max hold."
        )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-CHOP-12: CHOPPY PERFORMANCE ATTRIBUTION
# "Every choppy session makes TITAN FORGE better at the next choppy session."
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ChoppySessionReport:
    """FORGE-CHOP-12: Per-session choppy performance attribution."""
    session_date:               date
    identified_choppy_before_10am: bool
    false_breakouts_identified: int
    false_breakout_fades_taken: int
    false_breakout_fade_wins:   int
    strategies_fired:           dict[str, dict]   # strategy → {"trades": n, "wins": m}
    transition_detected:        bool
    chop09_captured_transition: bool
    overall_pnl:                float
    regime_identification_time: str   # "9:45am", "10:00am", etc.

    @property
    def false_breakout_win_rate(self) -> float:
        if self.false_breakout_fades_taken == 0:
            return 0.0
        return self.false_breakout_fade_wins / self.false_breakout_fades_taken

    def to_dict(self) -> dict:
        return {
            "date": str(self.session_date),
            "identified_before_10am": self.identified_choppy_before_10am,
            "fb_identified": self.false_breakouts_identified,
            "fb_fades_taken": self.false_breakout_fades_taken,
            "fb_win_rate": round(self.false_breakout_win_rate, 4),
            "strategies": self.strategies_fired,
            "transition_detected": self.transition_detected,
            "chop09_captured": self.chop09_captured_transition,
            "pnl": self.overall_pnl,
            "regime_id_time": self.regime_identification_time,
        }


class ChoppyPerformanceLog:
    """FORGE-CHOP-12: Accumulates choppy session reports for library calibration."""

    def __init__(self):
        self._reports: list[ChoppySessionReport] = []

    def add_report(self, report: ChoppySessionReport) -> None:
        self._reports.append(report)
        logger.info(
            "[FORGE-CHOP-12] Session %s logged. Chop ID before 10am: %s. "
            "FB fades: %d/%d wins. PnL: $%.0f.",
            report.session_date,
            "✅" if report.identified_choppy_before_10am else "❌",
            report.false_breakout_fade_wins, report.false_breakout_fades_taken,
            report.overall_pnl,
        )

    @property
    def early_identification_rate(self) -> float:
        if not self._reports:
            return 0.0
        return sum(1 for r in self._reports if r.identified_choppy_before_10am) / len(self._reports)

    @property
    def avg_false_breakout_win_rate(self) -> float:
        rates = [r.false_breakout_win_rate for r in self._reports if r.false_breakout_fades_taken > 0]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def total_sessions(self) -> int:
        return len(self._reports)


# ─────────────────────────────────────────────────────────────────────────────
# UPDATED EXISTING REQUIREMENTS — CHOPPY REGIME VARIANTS
# These override standard behavior when is_choppy_regime=True
# ─────────────────────────────────────────────────────────────────────────────

# FORGE-11 UPDATE: Layer 3 tightens from 60% → 50% in choppy regime
FORGE11_LAYER3_THRESHOLD_TRENDING: float = 0.60
FORGE11_LAYER3_THRESHOLD_CHOPPY:   float = 0.50

def get_layer3_threshold(is_choppy: bool) -> float:
    """FORGE-11: Guardian threshold. 60% trending, 50% choppy."""
    return FORGE11_LAYER3_THRESHOLD_CHOPPY if is_choppy else FORGE11_LAYER3_THRESHOLD_TRENDING


# FORGE-12/62 UPDATE: Drawdown budget allocation
FORGE12_ALLOCATION_TRENDING = {"morning": 0.50, "afternoon": 0.30, "reserve": 0.20}
FORGE12_ALLOCATION_CHOPPY   = {"morning": 0.40, "afternoon": 0.30, "reserve": 0.30}

def get_drawdown_allocation(is_choppy: bool) -> dict:
    """FORGE-12/62: Daily drawdown budget allocation."""
    return FORGE12_ALLOCATION_CHOPPY if is_choppy else FORGE12_ALLOCATION_TRENDING


# FORGE-15 UPDATE: Streak thresholds tighter in choppy regime
# Trending: 3 losses → 90-min pause | 5 losses → stop day
# Choppy:   2 losses → 90-min pause | 4 losses → stop day
FORGE15_PAUSE_THRESHOLD  = {False: 3, True: 2}
FORGE15_STOP_THRESHOLD   = {False: 5, True: 4}

def get_streak_thresholds(is_choppy: bool) -> tuple[int, int]:
    """FORGE-15: (pause_after, stop_after). Tighter in choppy."""
    return FORGE15_PAUSE_THRESHOLD[is_choppy], FORGE15_STOP_THRESHOLD[is_choppy]


# FORGE-43 UPDATE: Recovery pause reduced to 45 minutes in choppy
FORGE43_PAUSE_TRENDING: int = 90
FORGE43_PAUSE_CHOPPY:   int = 45

def get_recovery_pause_minutes(is_choppy: bool) -> int:
    """FORGE-43: 90 min trending, 45 min choppy (higher trade frequency needed)."""
    return FORGE43_PAUSE_CHOPPY if is_choppy else FORGE43_PAUSE_TRENDING


# FORGE-58 UPDATE: Opportunity scoring weights in choppy regime
# Trending: profit_potential=50%, compliance=50%
# Choppy:   profit_potential=35%, compliance=50%, mean_reversion=15%
FORGE58_WEIGHTS_TRENDING = {"profit": 0.50, "compliance": 0.50, "mean_reversion": 0.0}
FORGE58_WEIGHTS_CHOPPY   = {"profit": 0.35, "compliance": 0.50, "mean_reversion": 0.15}

def get_scoring_weights(is_choppy: bool) -> dict:
    """FORGE-58: Opportunity scoring weights. Choppy adds mean reversion metric."""
    return FORGE58_WEIGHTS_CHOPPY if is_choppy else FORGE58_WEIGHTS_TRENDING


# FORGE-63 UPDATE: Setup hierarchy by firm in choppy regime
FORGE63_CHOPPY_HIERARCHY = {
    FirmID.FTMO:           ["CHOP-02", "CHOP-06"],       # VWAP Fade + VA Oscillation
    FirmID.APEX:           ["CHOP-04", "CHOP-07"],       # TICK Extreme + H/L Rejection
    FirmID.DNA_FUNDED:     ["CHOP-10"],                  # POC Gravity only
    FirmID.FIVEPERCENTERS: ["CHOP-02", "CHOP-03"],       # VWAP Fade + OR Prison only (4% DD)
    FirmID.TOPSTEP:        ["CHOP-04", "CHOP-07", "CHOP-10"],  # Multiple but conservative
}

def get_choppy_setup_hierarchy(firm_id: str) -> list[str]:
    """FORGE-63: Firm-specific choppy setup hierarchy."""
    return FORGE63_CHOPPY_HIERARCHY.get(firm_id, ["CHOP-04", "CHOP-10"])


# FORGE-65 UPDATE: Losing trade response more aggressive in choppy
# Trending: 1 loss=-25% size | 2 losses=90-min pause | 3 losses=48hr review
# Choppy:   1 loss=-30% size | 2 losses=45-min pause | 3 losses=STOP DAY
FORGE65_SIZE_CUT_TRENDING: float = 0.25
FORGE65_SIZE_CUT_CHOPPY:   float = 0.30

def get_loss_response(is_choppy: bool, consecutive_losses: int) -> dict:
    """FORGE-65: Loss response parameters."""
    cut = FORGE65_SIZE_CUT_CHOPPY if is_choppy else FORGE65_SIZE_CUT_TRENDING
    pause = FORGE43_PAUSE_CHOPPY  if is_choppy else FORGE43_PAUSE_TRENDING

    if consecutive_losses >= 3:
        action = "STOP_DAY" if is_choppy else "48HR_REVIEW"
    elif consecutive_losses == 2:
        action = f"PAUSE_{pause}MIN"
    elif consecutive_losses == 1:
        action = f"SIZE_CUT_{int(cut*100)}PCT"
    else:
        action = "NORMAL"

    return {
        "consecutive_losses": consecutive_losses,
        "is_choppy": is_choppy,
        "action": action,
        "size_cut_pct": cut if consecutive_losses == 1 else 0.0,
        "pause_minutes": pause if consecutive_losses == 2 else 0,
    }


# FORGE-72 UPDATE: Regime 4 strategy deployment
# Suspend: GEX-01, GEX-02, ICT-08, VOL-03, SES-01, SES-02
# Activate: CHOP-01–10 in priority order
FORGE72_SUSPENDED_IN_REGIME4 = frozenset(["GEX-01", "GEX-02", "ICT-08", "VOL-03", "SES-01", "SES-02"])
FORGE72_REGIME4_PRIORITY = ["CHOP-04", "CHOP-02", "CHOP-10",
                             "CHOP-01", "CHOP-06", "CHOP-07",
                             "CHOP-03", "CHOP-08", "CHOP-05", "CHOP-09"]


# FORGE-78 UPDATE: 15-minute pause before activating trending after chop-to-trend
FORGE78_TRANSITION_PAUSE_MIN: int = 15

def handle_regime_transition(
    transition_detected: bool,
    minutes_since_signal: float,
    still_confirmed_after_pause: bool,
) -> dict:
    """
    FORGE-78: Chop-to-trend transition guard.
    "15-minute pause before activating trending strategies."
    "If signal has faded: remain in choppy mode."
    """
    if not transition_detected:
        return {"action": "STAY_CHOPPY", "reason": "No transition signal."}

    if minutes_since_signal < FORGE78_TRANSITION_PAUSE_MIN:
        remaining = FORGE78_TRANSITION_PAUSE_MIN - minutes_since_signal
        return {
            "action": "WAIT",
            "reason": f"15-min pause: {remaining:.0f}min remaining. Confirming transition.",
        }

    if still_confirmed_after_pause:
        return {
            "action": "ACTIVATE_TRENDING",
            "reason": "Transition confirmed after 15-min pause. Activating trending playbook.",
        }
    else:
        return {
            "action": "STAY_CHOPPY",
            "reason": "Transition signal faded during 15-min pause. Remaining in choppy mode.",
        }


# FORGE-61 UPDATE: 4 new choppy-specific pre-session inputs
@dataclass
class ChoppyPreSessionScore:
    """FORGE-61: Additional choppy-specific pre-session scoring."""
    adx_prior_close:        float    # ADX from prior session close
    bb_width_vs_20avg:      float    # BB width relative to 20-session avg (ratio)
    directional_reversals:  int      # Count of directional changes in prior session
    vix_term_structure:     str      # "contango" / "backwardation" / "flat"
    choppy_score:           float    # 0–10 for choppy conditions
    trending_score:         float    # 0–10 for trending conditions (existing)
    recommendation:         str

def score_choppy_pre_session(
    adx_prior:              float,
    bb_width_ratio:         float,   # Current / 20-session avg (< 1.0 = compressed)
    reversals_prior:        int,
    vix_front_vs_3month:    float,   # Front-month VIX / 3-month VIX (>1 = backwardation)
) -> ChoppyPreSessionScore:
    """FORGE-61: Choppy pre-session score (separate from trending score)."""
    score = 0.0

    if adx_prior < 20:         score += 2.5
    elif adx_prior < 15:       score += 4.0

    if bb_width_ratio < 0.70:  score += 2.5
    elif bb_width_ratio < 0.85: score += 1.5

    if reversals_prior >= 6:   score += 2.0
    elif reversals_prior >= 4: score += 1.0

    if vix_front_vs_3month > 1.05:  score += 3.0  # Backwardation = elevated near-term fear
    elif vix_front_vs_3month < 0.95: score += 0.5  # Contango = calmer near-term

    score = min(10.0, score)
    trending_score = max(0.0, 10.0 - score)

    if score >= 7:
        rec = "High choppy probability. Deploy CHOP strategies from session open."
    elif score >= 4:
        rec = "Moderate choppy probability. Watch for regime confirmation by 9:50am."
    else:
        rec = "Low choppy probability. Trending playbook primary."

    vix_label = "backwardation" if vix_front_vs_3month > 1.05 else \
                "contango" if vix_front_vs_3month < 0.95 else "flat"

    return ChoppyPreSessionScore(
        adx_prior_close=adx_prior,
        bb_width_vs_20avg=bb_width_ratio,
        directional_reversals=reversals_prior,
        vix_term_structure=vix_label,
        choppy_score=round(score, 2),
        trending_score=round(trending_score, 2),
        recommendation=rec,
    )
