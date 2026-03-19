"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  consistency_score.py — FORGE-07 — Layer 1                  ║
║                                                                              ║
║  CONSISTENCY SCORE                                                           ║
║  Rates each setup type by how PREDICTABLE its outcomes are.                  ║
║  Separate from profit potential. Consistency over explosiveness.             ║
║                                                                              ║
║  A setup with 65% win rate and tight variance beats a setup with 70%        ║
║  win rate that alternates between 90% and 50% streaks.                      ║
║  TITAN FORGE needs predictability — prop firms reward it.                    ║
║                                                                              ║
║  Consistency Score = composite of five sub-scores (0–10 each):              ║
║    1. Win Rate Stability    — how stable is the win rate over time?          ║
║    2. P&L Variance Control  — how tight are wins and losses?                 ║
║    3. Outcome Predictability— does each trade behave like the last?          ║
║    4. Regime Robustness     — does it work across multiple regimes?          ║
║    5. Temporal Stability    — does it work across different times/sessions?  ║
║                                                                              ║
║  Thresholds:                                                                 ║
║    8.0+ = High Consistency — preferred for evaluation trading               ║
║    6.0–7.9 = Acceptable — use with standard sizing                           ║
║    4.0–5.9 = Caution — reduce size, monitor closely                          ║
║    < 4.0 = Block — inconsistent, do not trade during evaluation              ║
║                                                                              ║
║  FORGE-36 / FORGE-71 integration:                                            ║
║  This also tracks the ACCOUNT-LEVEL behavioral consistency that FTMO's AI    ║
║  monitors. Consistent sizing variance, consistent time-of-day patterns,      ║
║  consistent win rate profile. Self-policing before the firm flags it.        ║
║                                                                              ║
║  Integrates with:                                                            ║
║    • FORGE-06 Setup Filter — post-win-rate secondary gate                   ║
║    • FORGE-08 Session Quality Filter — consistency feeds session scoring     ║
║    • FORGE-36 Behavioral Signature Management — account-level consistency   ║
║    • FORGE-71 Consistency Score Tracker — FTMO AI shadow monitoring          ║
║    • FX-03 Data Maturity — immature below 50 trades                          ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.consistency_score")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Consistency score thresholds (0–10 scale)
CONSISTENCY_HIGH:       float = 8.0   # Preferred for evaluation
CONSISTENCY_ACCEPTABLE: float = 6.0   # Standard sizing
CONSISTENCY_CAUTION:    float = 4.0   # Reduce size
CONSISTENCY_BLOCK:      float = 4.0   # Below this = blocked

# Sub-score weights (must sum to 1.0)
WEIGHT_WIN_RATE_STABILITY:    float = 0.30
WEIGHT_PNL_VARIANCE:          float = 0.25
WEIGHT_OUTCOME_PREDICTABILITY:float = 0.20
WEIGHT_REGIME_ROBUSTNESS:     float = 0.15
WEIGHT_TEMPORAL_STABILITY:    float = 0.10

# FX-03 maturity thresholds
MIN_TRADES_FOR_FULL_SCORE:    int = 50
MIN_TRADES_FOR_PARTIAL_SCORE: int = 20   # Below this = IMMATURE_DEFAULT
IMMATURE_CONSISTENCY_DEFAULT: float = 6.0  # Conservative default

# Win rate variance: rolling window for stability calculation
STABILITY_WINDOW_SIZE: int = 20   # Compare win rates in chunks of 20 trades

# P&L variance: target coefficient of variation (lower = more consistent)
PNL_CV_IDEAL:   float = 0.30   # CV ≤ 0.30 = score 10
PNL_CV_POOR:    float = 1.50   # CV ≥ 1.50 = score 0

# Account-level behavioral consistency thresholds (FORGE-71)
# FTMO AI monitors these — self-police before firm flags
BEHAVIORAL_SIZE_VARIANCE_MAX:    float = 0.25  # Max ±25% sizing variance
BEHAVIORAL_WINRATE_DRIFT_MAX:    float = 0.10  # Max 10% drift from baseline
BEHAVIORAL_SESSION_TIMING_VAR:   float = 0.15  # Max 15% timing variance


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — INPUT DATA
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TradeOutcome:
    """Single trade outcome for consistency calculation."""
    pnl:            float       # Realized P&L
    is_win:         bool
    regime:         str         # Market regime this trade occurred in
    session_hour:   int         # 0–23, hour of trade entry (ET)
    position_size:  float       # Lot/contract size used
    hold_minutes:   float       # Duration of trade in minutes


@dataclass
class SetupHistoryBuffer:
    """
    Rolling buffer of trade outcomes for a single setup type.
    Used to calculate consistency scores over time.
    """
    setup_id:           str
    outcomes:           list[TradeOutcome] = field(default_factory=list)
    # Regime breakdown
    regime_wins:        dict[str, int]   = field(default_factory=dict)
    regime_totals:      dict[str, int]   = field(default_factory=dict)
    # Session timing breakdown
    hour_wins:          dict[int, int]   = field(default_factory=dict)
    hour_totals:        dict[int, int]   = field(default_factory=dict)

    def add(self, outcome: TradeOutcome) -> None:
        """Append a new trade outcome to the buffer."""
        self.outcomes.append(outcome)
        # Track regime breakdown
        r = outcome.regime
        self.regime_totals[r] = self.regime_totals.get(r, 0) + 1
        if outcome.is_win:
            self.regime_wins[r] = self.regime_wins.get(r, 0) + 1
        # Track session timing
        h = outcome.session_hour
        self.hour_totals[h] = self.hour_totals.get(h, 0) + 1
        if outcome.is_win:
            self.hour_wins[h] = self.hour_wins.get(h, 0) + 1

    @property
    def total_trades(self) -> int:
        return len(self.outcomes)

    @property
    def wins(self) -> int:
        return sum(1 for o in self.outcomes if o.is_win)

    @property
    def win_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return self.wins / self.total_trades


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — SCORE OUTPUTS
# ─────────────────────────────────────────────────────────────────────────────

class ConsistencyGrade(Enum):
    HIGH       = "HIGH"        # 8.0+ — preferred for evaluation
    ACCEPTABLE = "ACCEPTABLE"  # 6.0–7.9 — standard sizing
    CAUTION    = "CAUTION"     # 4.0–5.9 — reduce size, monitor
    BLOCKED    = "BLOCKED"     # < 4.0 — do not trade during evaluation
    IMMATURE   = "IMMATURE"    # Insufficient data — use conservative default


@dataclass
class ConsistencyScore:
    """
    Complete consistency assessment for a single setup type.
    Composite score from 5 sub-scores. Each sub-score is 0–10.
    """
    setup_id:               str
    composite_score:        float          # 0–10 weighted composite
    grade:                  ConsistencyGrade
    # Sub-scores
    win_rate_stability:     float          # How stable is win rate over time?
    pnl_variance_control:   float          # How tight are wins/losses?
    outcome_predictability: float          # Per-trade predictability
    regime_robustness:      float          # Works across regimes?
    temporal_stability:     float          # Works across sessions/times?
    # Supporting data
    total_trades:           int
    is_mature:              bool
    win_rate:               float
    # Behavioral signature flags (FORGE-71)
    sizing_consistent:      bool           # Size variance within threshold?
    timing_consistent:      bool           # Session timing consistent?
    winrate_drift_ok:        bool          # Win rate drift within threshold?
    behavioral_flags:       list[str]      # Any FTMO AI behavioral concerns
    # Sizing recommendation
    size_multiplier:        float          # Based on grade: HIGH=1.0, ACCEPTABLE=0.85, CAUTION=0.60, BLOCKED=0.0
    reason:                 str

    @property
    def is_tradeable(self) -> bool:
        return self.grade != ConsistencyGrade.BLOCKED

    @property
    def should_reduce_size(self) -> bool:
        return self.grade in (ConsistencyGrade.CAUTION, ConsistencyGrade.IMMATURE)


@dataclass
class AccountBehavioralProfile:
    """
    Account-level behavioral consistency tracking (FORGE-36 / FORGE-71).
    This is what FTMO's AI monitors. We monitor it first.
    """
    account_id:               str
    firm_id:                  str
    # Position sizing consistency
    avg_position_size:        float
    size_std_dev:             float
    size_cv:                  float          # Coefficient of variation
    size_consistent:          bool           # CV ≤ BEHAVIORAL_SIZE_VARIANCE_MAX?
    # Win rate profile consistency
    baseline_win_rate:        float          # First 30 trades baseline
    recent_win_rate:          float          # Last 20 trades
    win_rate_drift:           float          # |recent - baseline|
    win_rate_consistent:      bool           # Drift ≤ BEHAVIORAL_WINRATE_DRIFT_MAX?
    # Session timing consistency
    session_hours_used:       list[int]      # Which hours are traded
    timing_variance:          float          # How spread out are entry times?
    timing_consistent:        bool
    # Overall behavioral score
    behavioral_score:         float          # 0–10 aggregate
    is_flagged:               bool           # True = likely to trigger firm monitoring
    flags:                    list[str]
    recommendation:           str


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — THE CONSISTENCY SCORER
# FORGE-07. Rates setup predictability. Separate from profit potential.
# ─────────────────────────────────────────────────────────────────────────────

class ConsistencyScorer:
    """
    FORGE-07: Consistency Score.

    Rates setup types by outcome predictability.
    Feeds into FORGE-08 Session Quality Filter.
    Also tracks account-level behavioral consistency (FORGE-36/71).

    Usage:
        scorer = ConsistencyScorer()

        # After each trade, record outcome:
        scorer.record_outcome("GEX-01", TradeOutcome(...))

        # Get consistency score:
        score = scorer.score("GEX-01")
        size_multiplier = score.size_multiplier

        # Account behavioral profile (FORGE-71):
        profile = scorer.behavioral_profile(account_id, firm_id, trade_history)
    """

    def __init__(self):
        self._buffers: dict[str, SetupHistoryBuffer] = {}

    # ── DATA INGESTION ────────────────────────────────────────────────────────

    def record_outcome(self, setup_id: str, outcome: TradeOutcome) -> None:
        """Record a completed trade outcome for a setup type."""
        if setup_id not in self._buffers:
            self._buffers[setup_id] = SetupHistoryBuffer(setup_id=setup_id)
        self._buffers[setup_id].add(outcome)

    def get_buffer(self, setup_id: str) -> Optional[SetupHistoryBuffer]:
        return self._buffers.get(setup_id)

    # ── MAIN SCORER ───────────────────────────────────────────────────────────

    def score(self, setup_id: str) -> ConsistencyScore:
        """
        Calculate the full consistency score for a setup type.

        If insufficient data: returns IMMATURE with conservative default.
        If sufficient data: calculates all 5 sub-scores and composites.
        """
        buf = self._buffers.get(setup_id)

        # No data at all
        if buf is None or buf.total_trades < MIN_TRADES_FOR_PARTIAL_SCORE:
            return self._immature_score(setup_id, buf)

        # Partial data (20–49 trades) — limited scoring
        if buf.total_trades < MIN_TRADES_FOR_FULL_SCORE:
            return self._partial_score(setup_id, buf)

        # Full data (50+ trades) — complete scoring
        return self._full_score(setup_id, buf)

    def score_from_stats(
        self,
        setup_id:               str,
        total_trades:           int,
        win_rate:               float,
        win_rate_std_dev:       float,     # Std dev of win rate across windows
        avg_win_pct:            float,     # Average win as % of account
        avg_loss_pct:           float,     # Average loss as % of account
        pnl_std_dev:            float,     # Std dev of P&L outcomes
        regime_win_rates:       Optional[dict[str, float]] = None,
        hour_win_rates:         Optional[dict[int, float]] = None,
    ) -> ConsistencyScore:
        """
        Calculate consistency score from pre-computed statistics.
        Used when live trade history isn't in the buffer (e.g., backtest data).

        Args:
            total_trades:       Total historical trade count.
            win_rate:           Lifetime win rate.
            win_rate_std_dev:   Std deviation of rolling window win rates.
            avg_win_pct:        Avg win size as fraction of account.
            avg_loss_pct:       Avg loss size as fraction (positive number).
            pnl_std_dev:        Std deviation of P&L outcomes.
            regime_win_rates:   Win rates per regime {regime: win_rate}.
            hour_win_rates:     Win rates per session hour {hour: win_rate}.
        """
        is_mature = total_trades >= MIN_TRADES_FOR_FULL_SCORE

        if not is_mature:
            return self._immature_score(setup_id, None)

        # Sub-score 1: Win rate stability
        s1 = self._score_win_rate_stability_from_stats(win_rate_std_dev, win_rate)

        # Sub-score 2: P&L variance control
        s2 = self._score_pnl_variance_from_stats(avg_win_pct, avg_loss_pct, pnl_std_dev)

        # Sub-score 3: Outcome predictability (proxy from win rate variance)
        s3 = self._score_predictability_from_stats(win_rate_std_dev)

        # Sub-score 4: Regime robustness
        s4 = self._score_regime_robustness_from_dict(regime_win_rates or {}, win_rate)

        # Sub-score 5: Temporal stability
        s5 = self._score_temporal_stability_from_dict(hour_win_rates or {}, win_rate)

        composite = (
            s1 * WEIGHT_WIN_RATE_STABILITY +
            s2 * WEIGHT_PNL_VARIANCE +
            s3 * WEIGHT_OUTCOME_PREDICTABILITY +
            s4 * WEIGHT_REGIME_ROBUSTNESS +
            s5 * WEIGHT_TEMPORAL_STABILITY
        )

        return self._build_score(
            setup_id, composite, total_trades, True, win_rate,
            s1, s2, s3, s4, s5, [], []
        )

    # ── INTERNAL SCORING METHODS ──────────────────────────────────────────────

    def _immature_score(
        self, setup_id: str, buf: Optional[SetupHistoryBuffer]
    ) -> ConsistencyScore:
        """Return conservative IMMATURE score when data is insufficient."""
        trades = buf.total_trades if buf else 0
        return ConsistencyScore(
            setup_id=setup_id,
            composite_score=IMMATURE_CONSISTENCY_DEFAULT,
            grade=ConsistencyGrade.IMMATURE,
            win_rate_stability=IMMATURE_CONSISTENCY_DEFAULT,
            pnl_variance_control=IMMATURE_CONSISTENCY_DEFAULT,
            outcome_predictability=IMMATURE_CONSISTENCY_DEFAULT,
            regime_robustness=IMMATURE_CONSISTENCY_DEFAULT,
            temporal_stability=IMMATURE_CONSISTENCY_DEFAULT,
            total_trades=trades,
            is_mature=False,
            win_rate=buf.win_rate if buf else 0.0,
            sizing_consistent=True,
            timing_consistent=True,
            winrate_drift_ok=True,
            behavioral_flags=[],
            size_multiplier=0.75,   # Reduced size for immature setups
            reason=(
                f"IMMATURE: {trades}/{MIN_TRADES_FOR_FULL_SCORE} trades. "
                f"Using conservative default score {IMMATURE_CONSISTENCY_DEFAULT:.1f}. "
                f"Reduce position size until {MIN_TRADES_FOR_FULL_SCORE} trades recorded."
            )
        )

    def _partial_score(
        self, setup_id: str, buf: SetupHistoryBuffer
    ) -> ConsistencyScore:
        """Partial scoring for 20–49 trades — limited but directional."""
        trades = buf.total_trades
        wr = buf.win_rate
        pnls = [o.pnl for o in buf.outcomes]

        # Basic variance check with limited data
        s1 = self._score_win_rate_chunks(buf.outcomes)
        s2 = self._score_pnl_variance(pnls)
        s3 = s1 * 0.9   # Proxy
        s4 = self._score_regime_robustness_from_buffer(buf)
        s5 = self._score_temporal_stability_from_buffer(buf)

        composite = (
            s1 * WEIGHT_WIN_RATE_STABILITY +
            s2 * WEIGHT_PNL_VARIANCE +
            s3 * WEIGHT_OUTCOME_PREDICTABILITY +
            s4 * WEIGHT_REGIME_ROBUSTNESS +
            s5 * WEIGHT_TEMPORAL_STABILITY
        )
        # Partial data penalty — bring score toward immature default
        composite = composite * 0.85 + IMMATURE_CONSISTENCY_DEFAULT * 0.15

        return self._build_score(
            setup_id, composite, trades, False, wr,
            s1, s2, s3, s4, s5, [], []
        )

    def _full_score(
        self, setup_id: str, buf: SetupHistoryBuffer
    ) -> ConsistencyScore:
        """Full scoring for 50+ trades."""
        trades  = buf.total_trades
        wr      = buf.win_rate
        pnls    = [o.pnl for o in buf.outcomes]
        sizes   = [o.position_size for o in buf.outcomes]
        flags:  list[str] = []

        # Sub-score 1: Win rate stability over rolling windows
        s1 = self._score_win_rate_chunks(buf.outcomes)

        # Sub-score 2: P&L variance
        s2 = self._score_pnl_variance(pnls)

        # Sub-score 3: Outcome predictability (how consistent is the loss?)
        s3 = self._score_outcome_predictability(buf.outcomes)

        # Sub-score 4: Regime robustness
        s4 = self._score_regime_robustness_from_buffer(buf)

        # Sub-score 5: Temporal stability (consistent across session hours)
        s5 = self._score_temporal_stability_from_buffer(buf)

        # Behavioral flags (FORGE-71: check BEFORE FTMO AI does)
        sizing_consistent, timing_consistent, wr_drift_ok, bflags = (
            self._check_behavioral_flags(buf, wr)
        )
        flags.extend(bflags)

        composite = (
            s1 * WEIGHT_WIN_RATE_STABILITY +
            s2 * WEIGHT_PNL_VARIANCE +
            s3 * WEIGHT_OUTCOME_PREDICTABILITY +
            s4 * WEIGHT_REGIME_ROBUSTNESS +
            s5 * WEIGHT_TEMPORAL_STABILITY
        )

        return self._build_score(
            setup_id, composite, trades, True, wr,
            s1, s2, s3, s4, s5,
            flags,
            [sizing_consistent, timing_consistent, wr_drift_ok],
        )

    # ── SUB-SCORE CALCULATORS ─────────────────────────────────────────────────

    def _score_win_rate_chunks(self, outcomes: list[TradeOutcome]) -> float:
        """
        Sub-score 1: Win rate stability.
        Split outcomes into windows of STABILITY_WINDOW_SIZE.
        Calculate win rate per window, then score based on variance.
        Lower variance = higher score.
        """
        if len(outcomes) < STABILITY_WINDOW_SIZE:
            return IMMATURE_CONSISTENCY_DEFAULT

        window = STABILITY_WINDOW_SIZE
        chunk_rates = []
        for i in range(0, len(outcomes) - window + 1, window // 2):
            chunk = outcomes[i: i + window]
            if len(chunk) >= window // 2:
                rate = sum(1 for o in chunk if o.is_win) / len(chunk)
                chunk_rates.append(rate)

        if len(chunk_rates) < 2:
            return IMMATURE_CONSISTENCY_DEFAULT

        mean_rate = sum(chunk_rates) / len(chunk_rates)
        variance = sum((r - mean_rate) ** 2 for r in chunk_rates) / len(chunk_rates)
        std_dev = math.sqrt(variance)

        # Score: std_dev of 0.0 → 10, std_dev of 0.25 → 0
        score = max(0.0, 10.0 * (1.0 - std_dev / 0.25))
        return round(min(10.0, score), 2)

    def _score_pnl_variance(self, pnls: list[float]) -> float:
        """
        Sub-score 2: P&L variance control.
        Scores how consistent wins are AND how consistent losses are, separately.
        Mixed-sign CV is meaningless — wins and losses are evaluated independently.
        Lower variance within wins (and within losses) = more consistent = higher score.
        """
        if len(pnls) < 5:
            return IMMATURE_CONSISTENCY_DEFAULT

        wins  = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]

        def cv_score(values: list[float]) -> float:
            if len(values) < 3:
                return 7.0  # Insufficient data — neutral
            mean = sum(values) / len(values)
            if mean <= 0:
                return 5.0
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            import math
            std = math.sqrt(variance)
            cv = std / mean
            return max(0.0, 10.0 * (1.0 - (cv - PNL_CV_IDEAL) / (PNL_CV_POOR - PNL_CV_IDEAL)))

        win_score  = cv_score(wins)
        loss_score = cv_score(losses)
        combined   = (win_score + loss_score) / 2.0
        return round(min(10.0, max(0.0, combined)), 2)

    def _score_outcome_predictability(self, outcomes: list[TradeOutcome]) -> float:
        """
        Sub-score 3: Outcome predictability.
        Measures how consistent each individual outcome is.
        Consecutive win-loss alternation is more predictable than long streaks.
        """
        if len(outcomes) < 10:
            return IMMATURE_CONSISTENCY_DEFAULT

        # Count transitions (win→loss or loss→win)
        transitions = sum(
            1 for i in range(1, len(outcomes))
            if outcomes[i].is_win != outcomes[i - 1].is_win
        )
        transition_rate = transitions / (len(outcomes) - 1)

        # Ideal transition rate is ~0.50 (random walk is most predictable)
        # Both high and low streakiness reduce predictability for evaluation
        deviation = abs(transition_rate - 0.50)
        score = max(0.0, 10.0 * (1.0 - deviation / 0.45))
        return round(min(10.0, score), 2)

    def _score_regime_robustness_from_buffer(self, buf: SetupHistoryBuffer) -> float:
        """Sub-score 4: Works across multiple regimes?"""
        return self._score_regime_robustness_from_dict(
            {r: (buf.regime_wins.get(r, 0) / buf.regime_totals[r])
             for r in buf.regime_totals if buf.regime_totals[r] >= 5},
            buf.win_rate
        )

    def _score_regime_robustness_from_dict(
        self,
        regime_win_rates: dict[str, float],
        overall_wr: float,
    ) -> float:
        """Score regime robustness from a pre-computed dict."""
        if not regime_win_rates:
            return 7.0  # No regime data — assume moderate robustness

        wr_values = list(regime_win_rates.values())
        if len(wr_values) < 2:
            return 7.0  # Only one regime — can't assess robustness

        variance = sum((r - overall_wr) ** 2 for r in wr_values) / len(wr_values)
        std_dev = math.sqrt(variance)

        # Score: std_dev 0.0 → 10, std_dev 0.20 → 0
        score = max(0.0, 10.0 * (1.0 - std_dev / 0.20))
        return round(min(10.0, score), 2)

    def _score_temporal_stability_from_buffer(self, buf: SetupHistoryBuffer) -> float:
        """Sub-score 5: Consistent across session hours?"""
        return self._score_temporal_stability_from_dict(
            {h: (buf.hour_wins.get(h, 0) / buf.hour_totals[h])
             for h in buf.hour_totals if buf.hour_totals[h] >= 3},
            buf.win_rate
        )

    def _score_temporal_stability_from_dict(
        self,
        hour_win_rates: dict[int, float],
        overall_wr: float,
    ) -> float:
        """Score temporal stability from a pre-computed dict."""
        if not hour_win_rates:
            return 7.0

        wr_values = list(hour_win_rates.values())
        if len(wr_values) < 2:
            return 7.5

        variance = sum((r - overall_wr) ** 2 for r in wr_values) / len(wr_values)
        std_dev = math.sqrt(variance)

        score = max(0.0, 10.0 * (1.0 - std_dev / 0.20))
        return round(min(10.0, score), 2)

    def _score_win_rate_stability_from_stats(
        self, win_rate_std_dev: float, overall_wr: float
    ) -> float:
        """Score win rate stability from pre-computed std dev."""
        score = max(0.0, 10.0 * (1.0 - win_rate_std_dev / 0.25))
        return round(min(10.0, score), 2)

    def _score_pnl_variance_from_stats(
        self,
        avg_win_pct: float,
        avg_loss_pct: float,
        pnl_std_dev: float,
    ) -> float:
        """Score P&L variance from pre-computed statistics."""
        if avg_win_pct <= 0:
            return 5.0
        mean_abs = (avg_win_pct + avg_loss_pct) / 2.0
        cv = pnl_std_dev / mean_abs if mean_abs > 0 else 1.0
        score = max(0.0, 10.0 * (1.0 - (cv - PNL_CV_IDEAL) / (PNL_CV_POOR - PNL_CV_IDEAL)))
        return round(min(10.0, score), 2)

    def _score_predictability_from_stats(self, win_rate_std_dev: float) -> float:
        """Proxy predictability from win rate std dev when individual trades unavailable."""
        score = max(0.0, 10.0 * (1.0 - win_rate_std_dev / 0.30))
        return round(min(10.0, score), 2)

    # ── BEHAVIORAL FLAGS (FORGE-71) ───────────────────────────────────────────

    def _check_behavioral_flags(
        self,
        buf: SetupHistoryBuffer,
        overall_wr: float,
    ) -> tuple[bool, bool, bool, list[str]]:
        """
        Check account-level behavioral consistency (FORGE-71).
        FTMO's AI monitors these same metrics — we check first.

        Returns: (sizing_consistent, timing_consistent, wr_drift_ok, flags)
        """
        flags: list[str] = []

        # Sizing consistency
        sizes = [o.position_size for o in buf.outcomes]
        sizing_ok = True
        if len(sizes) >= 10:
            mean_size = sum(sizes) / len(sizes)
            if mean_size > 0:
                size_variance = sum((s - mean_size) ** 2 for s in sizes) / len(sizes)
                size_std = math.sqrt(size_variance)
                size_cv = size_std / mean_size
                if size_cv > BEHAVIORAL_SIZE_VARIANCE_MAX:
                    sizing_ok = False
                    flags.append(
                        f"SIZING_VARIANCE: CV={size_cv:.2f} > "
                        f"{BEHAVIORAL_SIZE_VARIANCE_MAX:.2f} — FTMO AI may flag inconsistent sizing."
                    )

        # Timing consistency
        hours = [o.session_hour for o in buf.outcomes]
        timing_ok = True
        if len(hours) >= 10:
            mean_hour = sum(hours) / len(hours)
            hour_variance = sum((h - mean_hour) ** 2 for h in hours) / len(hours)
            hour_std = math.sqrt(hour_variance)
            timing_var = hour_std / 12.0  # Normalize to 0–1
            if timing_var > BEHAVIORAL_SESSION_TIMING_VAR:
                timing_ok = False
                flags.append(
                    f"TIMING_VARIANCE: {timing_var:.2f} > {BEHAVIORAL_SESSION_TIMING_VAR:.2f} "
                    f"— session timing inconsistency detected."
                )

        # Win rate drift
        wr_ok = True
        if len(buf.outcomes) >= 40:
            baseline_outcomes = buf.outcomes[:20]
            recent_outcomes   = buf.outcomes[-20:]
            baseline_wr = sum(1 for o in baseline_outcomes if o.is_win) / len(baseline_outcomes)
            recent_wr   = sum(1 for o in recent_outcomes   if o.is_win) / len(recent_outcomes)
            drift = abs(recent_wr - baseline_wr)
            if drift > BEHAVIORAL_WINRATE_DRIFT_MAX:
                wr_ok = False
                flags.append(
                    f"WINRATE_DRIFT: {drift:.1%} drift from baseline. "
                    f"Max {BEHAVIORAL_WINRATE_DRIFT_MAX:.0%}. "
                    f"FTMO AI monitors behavioral consistency."
                )

        return sizing_ok, timing_ok, wr_ok, flags

    # ── ACCOUNT BEHAVIORAL PROFILE (FORGE-36) ─────────────────────────────────

    def behavioral_profile(
        self,
        account_id:  str,
        firm_id:     str,
        outcomes:    list[TradeOutcome],
    ) -> AccountBehavioralProfile:
        """
        FORGE-36/71: Build the account-level behavioral profile.
        This is what FTMO's AI monitors. Self-police before the firm flags it.

        Args:
            account_id:  Account identifier.
            firm_id:     Firm being evaluated/traded at.
            outcomes:    All trade outcomes on this account.
        """
        flags: list[str] = []

        if len(outcomes) < 10:
            return AccountBehavioralProfile(
                account_id=account_id, firm_id=firm_id,
                avg_position_size=0.0, size_std_dev=0.0, size_cv=0.0,
                size_consistent=True, baseline_win_rate=0.0,
                recent_win_rate=0.0, win_rate_drift=0.0, win_rate_consistent=True,
                session_hours_used=[], timing_variance=0.0, timing_consistent=True,
                behavioral_score=7.0,
                is_flagged=False, flags=[],
                recommendation="Insufficient data for behavioral profiling."
            )

        sizes   = [o.position_size for o in outcomes]
        hours   = [o.session_hour for o in outcomes]

        # Sizing profile
        avg_size = sum(sizes) / len(sizes)
        size_var = sum((s - avg_size) ** 2 for s in sizes) / len(sizes)
        size_std = math.sqrt(size_var)
        size_cv  = size_std / avg_size if avg_size > 0 else 0.0
        size_ok  = size_cv <= BEHAVIORAL_SIZE_VARIANCE_MAX
        if not size_ok:
            flags.append(f"SIZING: CV={size_cv:.2f} exceeds {BEHAVIORAL_SIZE_VARIANCE_MAX:.2f}")

        # Win rate profile
        half = len(outcomes) // 2
        baseline_outcomes = outcomes[:min(30, half)]
        recent_outcomes   = outcomes[-min(20, half):]
        baseline_wr = sum(1 for o in baseline_outcomes if o.is_win) / len(baseline_outcomes)
        recent_wr   = sum(1 for o in recent_outcomes   if o.is_win) / len(recent_outcomes)
        drift = abs(recent_wr - baseline_wr)
        wr_ok = drift <= BEHAVIORAL_WINRATE_DRIFT_MAX
        if not wr_ok:
            flags.append(f"WIN_RATE_DRIFT: {drift:.1%} exceeds {BEHAVIORAL_WINRATE_DRIFT_MAX:.0%}")

        # Session timing profile
        unique_hours = sorted(set(hours))
        mean_hour = sum(hours) / len(hours)
        hour_var  = sum((h - mean_hour) ** 2 for h in hours) / len(hours)
        timing_var = math.sqrt(hour_var) / 12.0
        timing_ok  = timing_var <= BEHAVIORAL_SESSION_TIMING_VAR
        if not timing_ok:
            flags.append(f"TIMING: variance={timing_var:.2f} exceeds {BEHAVIORAL_SESSION_TIMING_VAR:.2f}")

        # Aggregate behavioral score
        scores = []
        scores.append(10.0 if size_ok else max(0.0, 10.0 * (1.0 - size_cv / 0.50)))
        scores.append(10.0 if wr_ok else max(0.0, 10.0 * (1.0 - drift / 0.30)))
        scores.append(10.0 if timing_ok else max(0.0, 10.0 * (1.0 - timing_var / 0.40)))
        behavioral_score = sum(scores) / len(scores)

        is_flagged = len(flags) > 0

        rec = (
            "Behavioral profile CLEAN — maintain current patterns."
            if not is_flagged else
            f"⚠ {len(flags)} behavioral concern(s) detected. "
            f"Correct before FTMO AI flags the account: " + "; ".join(flags)
        )

        return AccountBehavioralProfile(
            account_id=account_id, firm_id=firm_id,
            avg_position_size=avg_size, size_std_dev=size_std, size_cv=size_cv,
            size_consistent=size_ok,
            baseline_win_rate=baseline_wr, recent_win_rate=recent_wr,
            win_rate_drift=drift, win_rate_consistent=wr_ok,
            session_hours_used=unique_hours, timing_variance=timing_var,
            timing_consistent=timing_ok,
            behavioral_score=round(behavioral_score, 2),
            is_flagged=is_flagged, flags=flags,
            recommendation=rec,
        )

    # ── BUILD HELPER ─────────────────────────────────────────────────────────

    def _build_score(
        self,
        setup_id:       str,
        composite:      float,
        total_trades:   int,
        is_mature:      bool,
        win_rate:       float,
        s1: float, s2: float, s3: float, s4: float, s5: float,
        flags:          list,
        behavioral_bools: list,
    ) -> ConsistencyScore:
        """Assemble the final ConsistencyScore from computed sub-scores."""
        composite = round(max(0.0, min(10.0, composite)), 2)

        if not is_mature:
            grade = ConsistencyGrade.IMMATURE
            size_mult = 0.75
        elif composite >= CONSISTENCY_HIGH:
            grade = ConsistencyGrade.HIGH
            size_mult = 1.00
        elif composite >= CONSISTENCY_ACCEPTABLE:
            grade = ConsistencyGrade.ACCEPTABLE
            size_mult = 0.85
        elif composite >= CONSISTENCY_CAUTION:
            grade = ConsistencyGrade.CAUTION
            size_mult = 0.60
        else:
            grade = ConsistencyGrade.BLOCKED
            size_mult = 0.00

        sizing_ok  = behavioral_bools[0] if behavioral_bools else True
        timing_ok  = behavioral_bools[1] if len(behavioral_bools) > 1 else True
        wr_drift_ok = behavioral_bools[2] if len(behavioral_bools) > 2 else True

        reason = (
            f"{grade.value} (score {composite:.1f}/10) | "
            f"WR Stability: {s1:.1f} | P&L Variance: {s2:.1f} | "
            f"Predictability: {s3:.1f} | Regime: {s4:.1f} | Temporal: {s5:.1f}"
        )
        if flags:
            reason += f" | ⚠ Behavioral: {'; '.join(flags[:2])}"

        logger.debug(
            "[FORGE-07] %s → %s (%.1f) | %d trades",
            setup_id, grade.name, composite, total_trades,
        )

        return ConsistencyScore(
            setup_id=setup_id,
            composite_score=composite,
            grade=grade,
            win_rate_stability=s1,
            pnl_variance_control=s2,
            outcome_predictability=s3,
            regime_robustness=s4,
            temporal_stability=s5,
            total_trades=total_trades,
            is_mature=is_mature,
            win_rate=win_rate,
            sizing_consistent=sizing_ok,
            timing_consistent=timing_ok,
            winrate_drift_ok=wr_drift_ok,
            behavioral_flags=flags,
            size_multiplier=size_mult,
            reason=reason,
        )
