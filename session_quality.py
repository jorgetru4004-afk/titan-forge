"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              session_quality.py — FORGE-08 + FORGE-61 — Layer 1             ║
║                                                                              ║
║  SESSION QUALITY FILTER + PRE-SESSION CLASSIFIER                             ║
║                                                                              ║
║  FORGE-08: Session Quality Filter                                            ║
║    Gates entry to the trading session. Runs BEFORE the first trade.         ║
║    Below 6.0 → skip session. Below 4.0 → no trading at all.                 ║
║    The threshold is dynamically adjusted by the Pacing Engine (FORGE-04).   ║
║                                                                              ║
║  FORGE-61: Session Quality Classifier                                        ║
║    Scores the session using 5 pre-market components:                         ║
║    1. Overnight Futures Bias   — ES/NQ overnight direction & magnitude       ║
║    2. VIX / Volatility Context — level, term structure, trending             ║
║    3. GEX Regime Signal        — dealer gamma exposure direction             ║
║    4. Economic Event Calendar  — news impact, proximity, firm blackouts      ║
║    5. Market Breadth           — advance/decline, new highs/lows, trend      ║
║                                                                              ║
║  Score: 0–10. Composite of 5 weighted components.                           ║
║    ≥ 8.0 = EXCELLENT — high conviction, full size                            ║
║    ≥ 6.0 = GOOD — proceed normally                                           ║
║    ≥ 4.0 = MARGINAL — skip unless pacing requires                            ║
║    < 4.0 = POOR — no trading under any circumstance                          ║
║                                                                              ║
║  Integration:                                                                ║
║    • FORGE-04 Pacing Engine supplies adjusted conviction threshold          ║
║    • FORGE-06 Setup Filter feeds into session score (setup availability)    ║
║    • FORGE-07 Consistency Score — session score affects setup confidence    ║
║    • FORGE-46 Evaluation Timing Intelligence — MFI components               ║
║    • FORGE-15 Streak Detector — adds penalty to session score after losses  ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum, auto
from typing import Optional

from behavioral_arch import check_behavioral_consistency, BehavioralConsistencyCheck

logger = logging.getLogger("titan_forge.session_quality")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Session quality thresholds (0–10 scale)
SCORE_EXCELLENT:       float = 8.0   # Full conviction, full size
SCORE_GOOD:            float = 6.0   # Proceed normally — default gate
SCORE_MARGINAL:        float = 4.0   # Session skip threshold
SCORE_POOR:            float = 4.0   # Hard floor — no trading ever

# Component weights (must sum to 1.0)
WEIGHT_FUTURES_BIAS:    float = 0.25
WEIGHT_VIX_CONTEXT:     float = 0.20
WEIGHT_GEX_REGIME:      float = 0.20
WEIGHT_EVENT_CALENDAR:  float = 0.20
WEIGHT_MARKET_BREADTH:  float = 0.15

# VIX thresholds
VIX_CALM:       float = 15.0   # Below = calm, structured market
VIX_ELEVATED:   float = 20.0   # Caution zone
VIX_HIGH:       float = 30.0   # High volatility — reduces score
VIX_EXTREME:    float = 45.0   # Extreme — sessions often chaotic

# Futures magnitude thresholds (% gap from prior close)
FUTURES_STRONG_DIRECTIONAL: float = 0.005  # 0.5%+ overnight gap
FUTURES_MODERATE:           float = 0.002  # 0.2%–0.5%

# Breadth thresholds
BREADTH_STRONG:   float = 0.65  # 65%+ advancing = strong breadth
BREADTH_NEUTRAL:  float = 0.50
BREADTH_WEAK:     float = 0.40  # <40% = bearish breadth


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — MARKET CONDITIONS INPUT
# Data gathered in pre-market before session scoring runs.
# ─────────────────────────────────────────────────────────────────────────────

class GEXRegime(Enum):
    """Gamma Exposure (GEX) regime from dealer hedging analysis."""
    STRONGLY_NEGATIVE  = "strongly_negative"  # Strong trend day likely
    NEGATIVE           = "negative"           # Trend day likely
    NEUTRAL            = "neutral"            # Mixed signals
    POSITIVE           = "positive"           # Ranging day likely
    STRONGLY_POSITIVE  = "strongly_positive"  # Pinned, low volatility


class EventImpact(Enum):
    """Impact level of upcoming economic event."""
    NONE    = "none"
    LOW     = "low"
    MEDIUM  = "medium"
    HIGH    = "high"         # FOMC, CPI, NFP, etc.
    EXTREME = "extreme"      # Major central bank decisions, crisis events


@dataclass
class FuturesBias:
    """Pre-market futures data (ES/NQ)."""
    instrument:         str          # "ES", "NQ", "both"
    overnight_pct:      float        # % change from prior close (positive = up)
    direction:          str          # "bullish", "bearish", "flat"
    is_above_vwap:      bool         # Futures trading above overnight VWAP
    volume_vs_avg:      float        # Volume as multiple of average (1.0 = normal)
    gap_fills_expected: bool         # Does price need to fill a gap?


@dataclass
class VIXContext:
    """Volatility context from VIX and term structure."""
    current_level:      float        # Spot VIX
    one_month_avg:      float        # 30-day average VIX
    is_rising:          bool         # VIX trending up (10-day trend)
    term_structure:     str          # "contango", "backwardation", "flat"
    vix_percentile_1yr: float        # 0.0–1.0: where VIX sits in last-year range


@dataclass
class EventCalendar:
    """Today's economic event risk."""
    has_high_impact_today:  bool
    events_today:           list[str]    # e.g. ["CPI 8:30am", "FOMC 2pm"]
    next_event_minutes:     Optional[float]   # Minutes to next event (None = none today)
    highest_impact:         EventImpact
    firm_blackout_active:   bool         # Current firm's blackout rule is active
    blackout_ends_minutes:  Optional[float]


@dataclass
class MarketBreadth:
    """Broad market health indicators."""
    advance_decline_ratio: float    # Advancing / (Advancing + Declining) 0–1
    pct_above_20ma:        float    # % of S&P 500 stocks above 20-day MA
    new_highs:             int
    new_lows:              int
    spy_above_vwap:        bool     # SPY trading above intraday VWAP
    trend_strength:        float    # 0–1 scale (ADX proxy)


@dataclass
class PreSessionData:
    """Complete pre-session market data. Assembled before session score runs."""
    session_date:   date
    futures:        FuturesBias
    vix:            VIXContext
    gex:            GEXRegime
    events:         EventCalendar
    breadth:        MarketBreadth
    consecutive_losses:         int    # From FORGE-15 streak detector
    firm_id:                    str
    is_evaluation:              bool


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — SCORING OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

class SessionDecision(Enum):
    """Go / No-Go decision for the trading session."""
    TRADE_FULL       = auto()   # ≥ 8.0 — excellent conditions, full size
    TRADE_STANDARD   = auto()   # 6.0–7.9 — normal conditions
    TRADE_REDUCED    = auto()   # 4.0–5.9 — marginal, only if pacing requires
    SKIP_SESSION     = auto()   # 4.0–5.9 — skip unless pacing threshold allows
    NO_TRADING       = auto()   # < 4.0 — hard block regardless of pacing


@dataclass
class SessionQualityScore:
    """
    Complete session quality assessment.
    Produced before the first trade of the day.
    """
    session_date:           date
    firm_id:                str
    # Core score
    composite_score:        float          # 0–10 weighted composite
    decision:               SessionDecision
    # Sub-scores
    futures_score:          float
    vix_score:              float
    gex_score:              float
    event_score:            float
    breadth_score:          float
    # Threshold context
    base_threshold:         float          # SCORE_GOOD = 6.0
    pacing_adjusted_threshold: float      # After pacing engine adjustment
    consecutive_loss_penalty: float       # From streak detector
    final_threshold:        float         # base + penalty = actual gate
    # Flags
    hard_blocked:           bool          # Score < 4.0 — no override possible
    event_blackout:         bool          # Firm blackout currently active
    streak_penalty_active:  bool
    # Explanation
    reason:                 str
    best_setups_for_today:  list[str]     # Which setups work in this regime/breadth
    size_recommendation:    str           # "FULL", "STANDARD", "REDUCED", "NONE"

    @property
    def is_tradeable(self) -> bool:
        return self.decision not in (
            SessionDecision.SKIP_SESSION,
            SessionDecision.NO_TRADING,
        )

    @property
    def exceeded_threshold(self) -> bool:
        return self.composite_score >= self.final_threshold


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — THE SESSION QUALITY FILTER
# FORGE-08 + FORGE-61. Runs before the first trade. Gates the session.
# ─────────────────────────────────────────────────────────────────────────────

class SessionQualityFilter:
    """
    FORGE-08 + FORGE-61: Session Quality Filter + Pre-Session Classifier.

    Call score_session() before the first trade of each day.
    Only proceed if result.is_tradeable is True.

    The threshold is dynamic:
        Base:       6.0 (SCORE_GOOD)
        Adjusted:   pacing engine conviction threshold (may be 4.5–7.0)
        Final:      adjusted - streak_penalty

    Usage:
        sqf = SessionQualityFilter()

        score = sqf.score_session(
            data=pre_session_data,
            pacing_threshold=6.0,    # From FORGE-04 PacingEngine
        )

        if score.hard_blocked:
            # Absolute no-go — don't even check other conditions
            return

        if not score.is_tradeable:
            logger.info("Skipping session: %s", score.reason)
            return

        # Proceed with trading using score.best_setups_for_today
    """

    def __init__(self):
        self._session_history: list[SessionQualityScore] = []

    # ── MAIN SCORER ──────────────────────────────────────────────────────────

    def score_session(
        self,
        data:                   PreSessionData,
        pacing_threshold:       float = SCORE_GOOD,   # From FORGE-04 PacingEngine
        # FORGE-56: Behavioral consistency inputs (Bug 6)
        position_sizes:         Optional[list[float]] = None,
        entry_hours:            Optional[list[int]]   = None,
        baseline_win_rate:      float = 0.60,
        recent_win_rate:        float = 0.60,
    ) -> SessionQualityScore:
        """
        Score the pre-session market conditions.

        Args:
            data:               Complete pre-session market data.
            pacing_threshold:   Minimum session score from pacing engine.
                                Ranges from 3.0 (urgent) to 7.0 (ahead of pace).
            position_sizes:     Recent position sizes for FORGE-56 consistency check.
            entry_hours:        Recent entry hours (0-23) for FORGE-56 check.
            baseline_win_rate:  Lifetime win rate baseline for drift detection.
            recent_win_rate:    Recent win rate (last 10-20 trades) for drift detection.

        Returns:
            SessionQualityScore with decision and all component scores.
        """
        # ── FORGE-56: Behavioral Consistency Check (Bug 6) ───────────────────
        # Auto-runs at session start before any scoring or trading decisions.
        behavioral_check: BehavioralConsistencyCheck = check_behavioral_consistency(
            position_sizes=position_sizes or [1.0],
            entry_hours=entry_hours or [9],
            baseline_win_rate=baseline_win_rate,
            recent_win_rate=recent_win_rate,
        )
        if behavioral_check.severity == "FLAGGED":
            logger.warning(
                "[FORGE-56][%s] Behavioral consistency FLAGGED: %s",
                data.firm_id, " | ".join(behavioral_check.flags),
            )
        elif behavioral_check.severity == "CAUTION":
            logger.info(
                "[FORGE-56][%s] Behavioral consistency CAUTION: %s",
                data.firm_id, " | ".join(behavioral_check.flags),
            )
        else:
            logger.info("[FORGE-56][%s] Behavioral profile: CLEAN.", data.firm_id)

        # ── Component scores ─────────────────────────────────────────────────
        s_futures = self._score_futures(data.futures)
        s_vix     = self._score_vix(data.vix)
        s_gex     = self._score_gex(data.gex)
        s_event   = self._score_events(data.events, data.firm_id, data.is_evaluation)
        s_breadth = self._score_breadth(data.breadth)

        # ── Composite ────────────────────────────────────────────────────────
        composite = (
            s_futures * WEIGHT_FUTURES_BIAS +
            s_vix     * WEIGHT_VIX_CONTEXT +
            s_gex     * WEIGHT_GEX_REGIME +
            s_event   * WEIGHT_EVENT_CALENDAR +
            s_breadth * WEIGHT_MARKET_BREADTH
        )
        composite = round(max(0.0, min(10.0, composite)), 2)

        # ── Streak penalty (FORGE-15 integration) ─────────────────────────────
        streak_penalty = self._streak_penalty(data.consecutive_losses)

        # ── Behavioral penalty (FORGE-56 integration) ──────────────────────
        behavioral_penalty = 0.5 if behavioral_check.severity == "FLAGGED" else 0.0

        composite_with_penalty = max(0.0, composite - streak_penalty - behavioral_penalty)

        # ── Threshold calculation ─────────────────────────────────────────────
        # Pacing threshold is already adjusted by FORGE-04.
        # Apply streak penalty to the threshold AS WELL — a good session in a
        # bad streak still warrants caution.
        final_threshold = max(SCORE_POOR, pacing_threshold)  # Never below 4.0

        # ── Decision ─────────────────────────────────────────────────────────
        # Hard block: even composite doesn't matter if score < 4.0
        hard_blocked = composite_with_penalty < SCORE_POOR

        if hard_blocked:
            decision = SessionDecision.NO_TRADING
        elif data.events.firm_blackout_active:
            decision = SessionDecision.NO_TRADING
        elif composite_with_penalty >= SCORE_EXCELLENT:
            decision = SessionDecision.TRADE_FULL
        elif composite_with_penalty >= SCORE_GOOD:
            decision = SessionDecision.TRADE_STANDARD
        elif composite_with_penalty >= final_threshold:
            # Between marginal (4.0) and good (6.0) — only trade if pacing threshold allows
            decision = SessionDecision.TRADE_REDUCED
        else:
            decision = SessionDecision.SKIP_SESSION

        # ── Best setups for today's regime ────────────────────────────────────
        best_setups = self._recommend_setups(data.gex, data.breadth)

        # ── Size recommendation ───────────────────────────────────────────────
        size_rec = {
            SessionDecision.TRADE_FULL:     "FULL",
            SessionDecision.TRADE_STANDARD: "STANDARD",
            SessionDecision.TRADE_REDUCED:  "REDUCED (50%)",
            SessionDecision.SKIP_SESSION:   "NONE",
            SessionDecision.NO_TRADING:     "NONE",
        }[decision]

        # ── Log ───────────────────────────────────────────────────────────────
        log_fn = (
            logger.critical if hard_blocked else
            logger.warning  if decision in (SessionDecision.SKIP_SESSION, SessionDecision.NO_TRADING) else
            logger.info
        )
        log_fn(
            "[FORGE-08][%s] Session %s: %.1f/10 → %s | "
            "Threshold: %.1f | Futures: %.1f | VIX: %.1f | GEX: %.1f | "
            "Events: %.1f | Breadth: %.1f",
            data.firm_id, data.session_date, composite_with_penalty,
            decision.name, final_threshold,
            s_futures, s_vix, s_gex, s_event, s_breadth,
        )

        reason = self._build_reason(
            composite, composite_with_penalty, decision, final_threshold,
            streak_penalty, data, s_futures, s_vix, s_gex, s_event, s_breadth,
        )

        score = SessionQualityScore(
            session_date=data.session_date,
            firm_id=data.firm_id,
            composite_score=composite_with_penalty,
            decision=decision,
            futures_score=s_futures,
            vix_score=s_vix,
            gex_score=s_gex,
            event_score=s_event,
            breadth_score=s_breadth,
            base_threshold=SCORE_GOOD,
            pacing_adjusted_threshold=pacing_threshold,
            consecutive_loss_penalty=streak_penalty,
            final_threshold=final_threshold,
            hard_blocked=hard_blocked,
            event_blackout=data.events.firm_blackout_active,
            streak_penalty_active=streak_penalty > 0,
            reason=reason,
            best_setups_for_today=best_setups,
            size_recommendation=size_rec,
        )

        self._session_history.append(score)
        return score

    # ── COMPONENT SCORERS ────────────────────────────────────────────────────

    def _score_futures(self, f: FuturesBias) -> float:
        """
        Score overnight futures bias.
        Strong directional overnight gap with volume = higher quality.
        Flat, directionless overnight = lower quality session.
        """
        score = 5.0  # Start neutral

        # Directional clarity
        mag = abs(f.overnight_pct)
        if mag >= FUTURES_STRONG_DIRECTIONAL:
            score += 3.0   # Strong directional bias = clear setup
        elif mag >= FUTURES_MODERATE:
            score += 1.5
        # else flat — no bonus

        # Volume confirmation
        if f.volume_vs_avg >= 1.3:
            score += 1.0   # High volume = institutional participation
        elif f.volume_vs_avg < 0.7:
            score -= 1.0   # Low volume = no conviction

        # VWAP position
        if f.is_above_vwap and f.direction == "bullish":
            score += 0.5   # Aligned: bullish and above VWAP
        elif not f.is_above_vwap and f.direction == "bearish":
            score += 0.5   # Aligned: bearish and below VWAP

        # Gap fill risk
        if f.gap_fills_expected:
            score -= 1.0   # Competing forces reduce clarity

        return round(max(0.0, min(10.0, score)), 2)

    def _score_vix(self, v: VIXContext) -> float:
        """
        Score VIX context.
        Calm structured VIX = better session quality.
        Extreme or spiking VIX = chaotic conditions.
        """
        score = 8.0  # Start optimistic

        # Level scoring
        if v.current_level <= VIX_CALM:
            score += 1.0    # Very calm
        elif v.current_level <= VIX_ELEVATED:
            score -= 0.0    # Normal range — no penalty
        elif v.current_level <= VIX_HIGH:
            score -= 2.0    # Elevated — strategy adjustments needed
        elif v.current_level <= VIX_EXTREME:
            score -= 5.0    # High vol — regime changes erratic
        else:
            score -= 7.0    # Extreme VIX — session likely chaotic

        # Trending direction
        if v.is_rising:
            score -= 1.5    # Rising VIX = increasing uncertainty

        # Term structure
        if v.term_structure == "backwardation":
            score -= 1.0    # Backwardation = fear/stress in market
        elif v.term_structure == "contango":
            score += 0.5    # Normal contango = calm

        # Percentile context
        if v.vix_percentile_1yr >= 0.90:
            score -= 1.0    # Near 1-year highs
        elif v.vix_percentile_1yr <= 0.20:
            score += 0.5    # Near 1-year lows = very calm

        return round(max(0.0, min(10.0, score)), 2)

    def _score_gex(self, gex: GEXRegime) -> float:
        """
        Score GEX regime signal.
        Clear regime signal = better quality session.
        Neutral/mixed GEX = lower quality (unclear dealer behavior).
        """
        return {
            GEXRegime.STRONGLY_NEGATIVE: 9.0,   # Clear trend day — high conviction
            GEXRegime.NEGATIVE:          8.0,   # Trend day likely
            GEXRegime.NEUTRAL:           5.0,   # Mixed — unclear
            GEXRegime.POSITIVE:          7.5,   # Ranging — mean reversion works
            GEXRegime.STRONGLY_POSITIVE: 6.5,   # Pinned — lower range of motion
        }[gex]

    def _score_events(
        self,
        events:       EventCalendar,
        firm_id:      str,
        is_evaluation: bool,
    ) -> float:
        """
        Score economic event calendar impact.
        High-impact events reduce session quality.
        Firm blackouts = hard reduction.
        """
        score = 9.0  # Start high — no events = best

        # Active firm blackout
        if events.firm_blackout_active:
            return 0.0   # Hard zero — blackout means no trading

        # Impact of today's events
        impact_penalties = {
            EventImpact.NONE:    0.0,
            EventImpact.LOW:     0.5,
            EventImpact.MEDIUM:  2.0,
            EventImpact.HIGH:    4.0,
            EventImpact.EXTREME: 7.0,
        }
        score -= impact_penalties.get(events.highest_impact, 0.0)

        # Proximity to next event
        if events.next_event_minutes is not None:
            if events.next_event_minutes <= 10:
                score -= 3.0   # Within 10 minutes — high risk window
            elif events.next_event_minutes <= 30:
                score -= 1.5
            elif events.next_event_minutes <= 60:
                score -= 0.5

        # Multiple events multiply risk
        if len(events.events_today) >= 3:
            score -= 1.0
        elif len(events.events_today) >= 2:
            score -= 0.5

        return round(max(0.0, min(10.0, score)), 2)

    def _score_breadth(self, breadth: MarketBreadth) -> float:
        """
        Score market breadth.
        Strong broad market participation = better quality conditions.
        """
        score = 5.0  # Neutral start

        # Advance/decline ratio
        adr = breadth.advance_decline_ratio
        if adr >= BREADTH_STRONG:
            score += 3.0
        elif adr >= BREADTH_NEUTRAL:
            score += 1.0
        elif adr <= BREADTH_WEAK:
            score -= 1.5
        else:
            score += 0.0

        # % above MA
        ma_pct = breadth.pct_above_20ma
        if ma_pct >= 0.65:
            score += 1.5
        elif ma_pct >= 0.50:
            score += 0.5
        elif ma_pct <= 0.30:
            score -= 2.0

        # New highs vs new lows
        nh_nl_ratio = (
            breadth.new_highs / (breadth.new_highs + breadth.new_lows)
            if (breadth.new_highs + breadth.new_lows) > 0 else 0.5
        )
        if nh_nl_ratio >= 0.70:
            score += 1.0
        elif nh_nl_ratio <= 0.30:
            score -= 1.0

        # VWAP
        if breadth.spy_above_vwap:
            score += 0.5

        # Trend strength
        if breadth.trend_strength >= 0.70:
            score += 0.5
        elif breadth.trend_strength <= 0.30:
            score -= 0.5

        return round(max(0.0, min(10.0, score)), 2)

    # ── STREAK PENALTY ────────────────────────────────────────────────────────

    def _streak_penalty(self, consecutive_losses: int) -> float:
        """
        Apply penalty to session score based on consecutive losses.
        Integrates with FORGE-15 Streak Detector.
        Not a hard block — just makes the threshold harder to clear.
        """
        if consecutive_losses == 0:
            return 0.0
        elif consecutive_losses == 1:
            return 0.5    # Minor — slight caution
        elif consecutive_losses == 2:
            return 1.0    # Meaningful — setup quality now matters more
        elif consecutive_losses == 3:
            return 1.5    # Streak detector threshold — 2-hour pause
        elif consecutive_losses >= 5:
            return 2.5    # Day stop territory
        else:
            return 2.0    # 4 consecutive losses

    # ── SETUP RECOMMENDATIONS ─────────────────────────────────────────────────

    def _recommend_setups(
        self, gex: GEXRegime, breadth: MarketBreadth
    ) -> list[str]:
        """
        Recommend setup types for today's market regime.
        Based on GEX direction and breadth strength.
        """
        setups = []

        if gex in (GEXRegime.STRONGLY_NEGATIVE, GEXRegime.NEGATIVE):
            # Trend day — momentum setups
            setups += ["GEX-01", "GEX-02", "ICT-01", "ICT-08", "ORD-01"]
            if breadth.advance_decline_ratio >= BREADTH_STRONG:
                setups += ["ICT-03", "SES-01"]  # Kill zone with bullish trend
        elif gex in (GEXRegime.POSITIVE, GEXRegime.STRONGLY_POSITIVE):
            # Ranging day — mean reversion setups
            setups += ["GEX-05", "VOL-01", "VOL-02", "ICT-06", "SES-03"]
        else:
            # Neutral — confluence required
            setups += ["ICT-01", "VOL-03", "VOL-05", "ORD-02"]

        # Session-based additions
        setups += ["SES-01"]   # NY Kill Zone always valid if score permits

        return list(dict.fromkeys(setups))  # Deduplicate preserving order

    # ── REASON BUILDER ────────────────────────────────────────────────────────

    def _build_reason(
        self,
        raw_composite: float,
        adjusted_composite: float,
        decision: SessionDecision,
        final_threshold: float,
        streak_penalty: float,
        data: PreSessionData,
        sf: float, sv: float, sg: float, se: float, sb: float,
    ) -> str:
        parts = []

        if data.events.firm_blackout_active:
            parts.append(
                f"FIRM BLACKOUT ACTIVE — no trading until blackout clears "
                f"(ends in {data.events.blackout_ends_minutes:.0f} min)"
                if data.events.blackout_ends_minutes else
                "FIRM BLACKOUT ACTIVE — no trading"
            )
        elif adjusted_composite < SCORE_POOR:
            parts.append(
                f"HARD BLOCK: Score {adjusted_composite:.1f} below minimum {SCORE_POOR:.1f}. "
                f"No trading under any circumstance."
            )
        else:
            parts.append(
                f"Score: {adjusted_composite:.1f}/10 "
                f"(raw: {raw_composite:.1f}, streak penalty: -{streak_penalty:.1f}) | "
                f"Threshold: {final_threshold:.1f} | Decision: {decision.name}"
            )

        # Weakest component callout
        subs = {
            "Futures": sf, "VIX": sv, "GEX": sg, "Events": se, "Breadth": sb
        }
        weakest = min(subs, key=subs.get)
        if subs[weakest] < 5.0:
            parts.append(f"Weakest component: {weakest} ({subs[weakest]:.1f}/10)")

        if streak_penalty > 0:
            parts.append(
                f"Streak penalty: -{streak_penalty:.1f} "
                f"({data.consecutive_losses} consecutive losses)"
            )

        return " | ".join(parts)

    # ── HISTORY / STATS ───────────────────────────────────────────────────────

    @property
    def session_history(self) -> list[SessionQualityScore]:
        return list(self._session_history)

    def recent_avg_score(self, n: int = 5) -> float:
        """Average composite score over last n sessions."""
        recent = self._session_history[-n:]
        if not recent:
            return 0.0
        return sum(s.composite_score for s in recent) / len(recent)

    def skip_rate(self) -> float:
        """Fraction of sessions skipped or blocked."""
        if not self._session_history:
            return 0.0
        blocked = sum(
            1 for s in self._session_history
            if not s.is_tradeable
        )
        return blocked / len(self._session_history)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — DATA BUILDERS
# Convenience functions for building PreSessionData from raw market data.
# ─────────────────────────────────────────────────────────────────────────────

def build_pre_session_data(
    session_date:           date,
    firm_id:                str,
    is_evaluation:          bool,
    # Futures
    overnight_pct:          float,
    futures_direction:      str,
    futures_above_vwap:     bool         = True,
    futures_volume_ratio:   float        = 1.0,
    gap_fill_expected:      bool         = False,
    # VIX
    vix_level:              float        = 15.0,
    vix_30d_avg:            float        = 15.0,
    vix_rising:             bool         = False,
    vix_term_structure:     str          = "contango",
    vix_percentile:         float        = 0.40,
    # GEX
    gex_regime:             GEXRegime    = GEXRegime.NEUTRAL,
    # Events
    high_impact_today:      bool         = False,
    events_today:           Optional[list[str]] = None,
    next_event_minutes:     Optional[float]     = None,
    highest_event_impact:   EventImpact  = EventImpact.NONE,
    firm_blackout_active:   bool         = False,
    blackout_ends_minutes:  Optional[float]     = None,
    # Breadth
    advance_decline:        float        = 0.55,
    pct_above_20ma:         float        = 0.55,
    new_highs:              int          = 100,
    new_lows:               int          = 50,
    spy_above_vwap:         bool         = True,
    trend_strength:         float        = 0.50,
    # Context
    consecutive_losses:     int          = 0,
) -> PreSessionData:
    """Build a complete PreSessionData from individual parameters."""
    return PreSessionData(
        session_date=session_date,
        futures=FuturesBias(
            instrument="ES",
            overnight_pct=overnight_pct,
            direction=futures_direction,
            is_above_vwap=futures_above_vwap,
            volume_vs_avg=futures_volume_ratio,
            gap_fills_expected=gap_fill_expected,
        ),
        vix=VIXContext(
            current_level=vix_level,
            one_month_avg=vix_30d_avg,
            is_rising=vix_rising,
            term_structure=vix_term_structure,
            vix_percentile_1yr=vix_percentile,
        ),
        gex=gex_regime,
        events=EventCalendar(
            has_high_impact_today=high_impact_today,
            events_today=events_today or [],
            next_event_minutes=next_event_minutes,
            highest_impact=highest_event_impact,
            firm_blackout_active=firm_blackout_active,
            blackout_ends_minutes=blackout_ends_minutes,
        ),
        breadth=MarketBreadth(
            advance_decline_ratio=advance_decline,
            pct_above_20ma=pct_above_20ma,
            new_highs=new_highs,
            new_lows=new_lows,
            spy_above_vwap=spy_above_vwap,
            trend_strength=trend_strength,
        ),
        consecutive_losses=consecutive_losses,
        firm_id=firm_id,
        is_evaluation=is_evaluation,
    )
