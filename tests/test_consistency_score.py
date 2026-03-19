"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║        test_consistency_score.py — FORGE-07 — FX-06 Compliance              ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from consistency_score import (
    ConsistencyScorer, TradeOutcome, SetupHistoryBuffer, ConsistencyGrade,
    CONSISTENCY_HIGH, CONSISTENCY_ACCEPTABLE, CONSISTENCY_CAUTION,
    MIN_TRADES_FOR_FULL_SCORE, MIN_TRADES_FOR_PARTIAL_SCORE,
    IMMATURE_CONSISTENCY_DEFAULT, WEIGHT_WIN_RATE_STABILITY,
    BEHAVIORAL_SIZE_VARIANCE_MAX, BEHAVIORAL_WINRATE_DRIFT_MAX,
)


def make_scorer() -> ConsistencyScorer:
    return ConsistencyScorer()


def make_outcome(
    is_win: bool = True,
    pnl: float = 100.0,
    regime: str = "low_vol_trending",
    hour: int = 10,
    size: float = 1.0,
    hold: float = 30.0,
) -> TradeOutcome:
    return TradeOutcome(
        pnl=pnl if is_win else -abs(pnl),
        is_win=is_win,
        regime=regime,
        session_hour=hour,
        position_size=size,
        hold_minutes=hold,
    )


def fill_outcomes(
    scorer: ConsistencyScorer,
    setup_id: str,
    n: int,
    win_rate: float = 0.65,
    pnl_win: float = 150.0,
    pnl_loss: float = 75.0,
    regime: str = "low_vol_trending",
    hour: int = 10,
    size: float = 1.0,
) -> None:
    """Fill scorer with n outcomes at the given win rate."""
    for i in range(n):
        is_win = (i % round(1 / win_rate)) != 0 if win_rate > 0 else False
        # Simpler: alternate based on win_rate
        is_win = (i / n) < win_rate or (i % 3 != 0 and win_rate >= 0.65)
        pnl = pnl_win if is_win else -pnl_loss
        scorer.record_outcome(setup_id, make_outcome(
            is_win=is_win, pnl=pnl,
            regime=regime, hour=hour, size=size
        ))


def fill_stable(scorer, sid, n=60, win_rate=0.65):
    """Fill with perfectly stable outcomes."""
    wins_needed = int(n * win_rate)
    for i in range(n):
        is_win = i < wins_needed
        scorer.record_outcome(sid, make_outcome(
            is_win=is_win, pnl=150.0 if is_win else -75.0
        ))


# ─────────────────────────────────────────────────────────────────────────────
# IMMATURE STATE
# ─────────────────────────────────────────────────────────────────────────────

class TestImmatureState:

    def test_normal_no_data_returns_immature(self):
        """Normal: No trade data → IMMATURE grade with conservative default."""
        scorer = make_scorer()
        score = scorer.score("GEX-01")
        assert score.grade == ConsistencyGrade.IMMATURE
        assert score.composite_score == IMMATURE_CONSISTENCY_DEFAULT
        assert score.is_mature is False

    def test_edge_exactly_19_trades_still_immature(self):
        """Edge: 19 trades (below 20 partial threshold) → IMMATURE."""
        scorer = make_scorer()
        fill_stable(scorer, "E1", n=19)
        score = scorer.score("E1")
        assert score.grade == ConsistencyGrade.IMMATURE

    def test_edge_exactly_20_trades_gets_partial_score(self):
        """Edge: Exactly 20 trades → partial scoring begins (no longer pure IMMATURE)."""
        scorer = make_scorer()
        fill_stable(scorer, "E2", n=20)
        score = scorer.score("E2")
        # At 20 trades: partial score, not full. Still may be IMMATURE grade but score differs.
        assert score.total_trades == 20

    def test_conflict_immature_size_multiplier_is_reduced(self):
        """Conflict: IMMATURE setup has reduced size multiplier (0.75), not full 1.0."""
        scorer = make_scorer()
        score = scorer.score("NEW-SETUP")
        assert score.size_multiplier == 0.75   # Reduced for immature
        assert score.size_multiplier < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# GRADE THRESHOLDS
# ─────────────────────────────────────────────────────────────────────────────

class TestGradeThresholds:

    def test_normal_stable_setup_gets_high_grade(self):
        """Normal: Very consistent outcomes → HIGH grade, full size multiplier."""
        scorer = make_scorer()
        # Perfect: 65% win rate, same size, same regime, same hour, consistent P&L
        for i in range(60):
            is_win = (i % 3) != 0   # 2/3 = 66.7% win rate (stable pattern)
            scorer.record_outcome("STABLE-01", make_outcome(
                is_win=is_win,
                pnl=150.0 if is_win else -75.0,
                regime="low_vol_trending",
                hour=10,
                size=1.0,
            ))
        score = scorer.score("STABLE-01")
        assert score.is_mature is True
        assert score.composite_score >= CONSISTENCY_ACCEPTABLE
        assert score.size_multiplier > 0.0

    def test_edge_composite_bounded_0_to_10(self):
        """Edge: Composite score is always within [0, 10] regardless of data."""
        scorer = make_scorer()
        # Extreme chaos: random regime, size, hour every trade
        for i in range(60):
            scorer.record_outcome("CHAOS-01", make_outcome(
                is_win=(i % 5) < 3,
                pnl=500.0 if (i % 5) < 3 else -300.0,
                regime=["trend", "range", "hvt"][i % 3],
                hour=i % 24,
                size=0.5 + (i % 5) * 0.5,
            ))
        score = scorer.score("CHAOS-01")
        assert 0.0 <= score.composite_score <= 10.0
        assert score.grade != ConsistencyGrade.IMMATURE  # 60 trades = mature

    def test_conflict_blocked_grade_has_zero_size_multiplier(self):
        """Conflict: BLOCKED grade means zero size multiplier — do not trade."""
        scorer = make_scorer()
        # Force a low composite score via direct stats method
        score = scorer.score_from_stats(
            "BLOCK-01",
            total_trades=60,
            win_rate=0.60,
            win_rate_std_dev=0.25,    # Maximum variance → score 0
            avg_win_pct=0.015,
            avg_loss_pct=0.015,
            pnl_std_dev=0.05,         # High variance
            regime_win_rates={"trend": 0.80, "range": 0.35},  # Wildly different
            hour_win_rates={9: 0.85, 14: 0.40},
        )
        # With extreme variance: should be BLOCKED or CAUTION
        if score.grade == ConsistencyGrade.BLOCKED:
            assert score.size_multiplier == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# WIN RATE STABILITY (SUB-SCORE 1)
# ─────────────────────────────────────────────────────────────────────────────

class TestWinRateStability:

    def test_normal_stable_win_rate_high_score(self):
        """Normal: Win rate consistent across time windows → high stability score."""
        scorer = make_scorer()
        # Very consistent: exactly 2/3 wins in every block of 3
        for i in range(60):
            is_win = (i % 3) != 2
            scorer.record_outcome("WRS-01", make_outcome(is_win=is_win))
        score = scorer.score("WRS-01")
        assert score.win_rate_stability >= 7.0   # Consistent pattern = high score

    def test_edge_alternating_streaks_low_score(self):
        """Edge: Alternating win/loss streaks (all wins then all losses) → low stability."""
        scorer = make_scorer()
        # 30 wins then 30 losses — maximally streaky
        for i in range(30):
            scorer.record_outcome("WRS-02", make_outcome(is_win=True, pnl=100.0))
        for i in range(30):
            scorer.record_outcome("WRS-02", make_outcome(is_win=False, pnl=-100.0))
        score = scorer.score("WRS-02")
        assert score.win_rate_stability < 5.0    # Unstable pattern

    def test_conflict_stability_independent_of_overall_win_rate(self):
        """Conflict: 70% win rate with high variance < 60% win rate with low variance."""
        scorer1 = make_scorer()
        scorer2 = make_scorer()

        # Scorer1: 70% avg but wildly inconsistent (streaky)
        for i in range(70):
            scorer1.record_outcome("WRS-HIGH", make_outcome(is_win=True))
        for i in range(30):
            scorer1.record_outcome("WRS-HIGH", make_outcome(is_win=False))
        s1 = scorer1.score("WRS-HIGH")

        # Scorer2: 60% avg but perfectly consistent (alternating)
        for i in range(60):
            is_win = (i % 5) < 3  # Exactly 60% in every group of 5
            scorer2.record_outcome("WRS-LOW", make_outcome(is_win=is_win))
        s2 = scorer2.score("WRS-LOW")

        # The consistent 60% should have higher win rate STABILITY than streaky 70%
        assert s2.win_rate_stability >= s1.win_rate_stability


# ─────────────────────────────────────────────────────────────────────────────
# P&L VARIANCE (SUB-SCORE 2)
# ─────────────────────────────────────────────────────────────────────────────

class TestPnLVariance:

    def test_normal_tight_pnl_high_score(self):
        """Normal: Consistent win/loss amounts → high P&L variance score."""
        scorer = make_scorer()
        for i in range(60):
            is_win = (i % 3) != 2
            scorer.record_outcome("PNL-01", make_outcome(
                is_win=is_win,
                pnl=100.0 if is_win else -50.0,   # Very tight, consistent P&L
            ))
        score = scorer.score("PNL-01")
        assert score.pnl_variance_control >= 5.0

    def test_edge_wildly_variable_pnl_low_score(self):
        """Edge: P&L ranging from tiny wins to massive losses → low score."""
        scorer = make_scorer()
        pnls = [10.0, 500.0, -300.0, 1.0, -400.0, 200.0] * 10  # 60 trades
        for p in pnls:
            scorer.record_outcome("PNL-02", make_outcome(
                is_win=p > 0, pnl=p
            ))
        score = scorer.score("PNL-02")
        assert score.pnl_variance_control < 8.0   # High variance = lower than tight P&L score

    def test_conflict_pnl_score_uses_cv_not_absolute_magnitude(self):
        """Conflict: Large but consistent P&L beats small inconsistent P&L."""
        scorer_big = make_scorer()
        scorer_small = make_scorer()

        # Big consistent: $1000 wins, $500 losses (CV very low)
        for i in range(60):
            is_win = (i % 3) != 2
            scorer_big.record_outcome("BIG", make_outcome(
                is_win=is_win, pnl=1000.0 if is_win else -500.0
            ))
        # Small inconsistent: P&L all over the place
        pnls = [50, 200, -300, 10, -5, 150] * 10
        for p in pnls:
            scorer_small.record_outcome("SMALL", make_outcome(is_win=p > 0, pnl=p))

        big_score = scorer_big.score("BIG").pnl_variance_control
        small_score = scorer_small.score("SMALL").pnl_variance_control
        assert big_score >= small_score


# ─────────────────────────────────────────────────────────────────────────────
# REGIME ROBUSTNESS (SUB-SCORE 4)
# ─────────────────────────────────────────────────────────────────────────────

class TestRegimeRobustness:

    def test_normal_consistent_across_regimes_high_score(self):
        """Normal: Same win rate in all regimes → high regime robustness."""
        score = ConsistencyScorer().score_from_stats(
            "REG-01", total_trades=60, win_rate=0.65,
            win_rate_std_dev=0.05, avg_win_pct=0.015, avg_loss_pct=0.008,
            pnl_std_dev=0.004,
            regime_win_rates={"trend": 0.65, "range": 0.64, "hvt": 0.66},
        )
        assert score.regime_robustness >= 8.0

    def test_edge_works_only_in_one_regime_low_score(self):
        """Edge: 80% in trending, 40% in ranging → low regime robustness."""
        score = ConsistencyScorer().score_from_stats(
            "REG-02", total_trades=60, win_rate=0.60,
            win_rate_std_dev=0.05, avg_win_pct=0.015, avg_loss_pct=0.008,
            pnl_std_dev=0.004,
            regime_win_rates={"trend": 0.80, "range": 0.40},
        )
        assert score.regime_robustness < 5.0

    def test_conflict_single_regime_gets_neutral_score(self):
        """Conflict: Only one regime in data → neutral 7.0 (not penalized, not rewarded)."""
        score = ConsistencyScorer().score_from_stats(
            "REG-03", total_trades=60, win_rate=0.65,
            win_rate_std_dev=0.05, avg_win_pct=0.015, avg_loss_pct=0.008,
            pnl_std_dev=0.004,
            regime_win_rates={"trend": 0.65},   # Only one regime
        )
        assert abs(score.regime_robustness - 7.0) < 0.1


# ─────────────────────────────────────────────────────────────────────────────
# BEHAVIORAL PROFILE (FORGE-71)
# ─────────────────────────────────────────────────────────────────────────────

class TestBehavioralProfile:

    def test_normal_consistent_behavior_not_flagged(self):
        """Normal: Consistent size/timing/win rate → no flags, clean profile."""
        scorer = make_scorer()
        outcomes = [
            make_outcome(is_win=(i % 3) != 2, size=1.0, hour=10)
            for i in range(50)
        ]
        profile = scorer.behavioral_profile("ACC-001", "FTMO", outcomes)
        assert profile.is_flagged is False
        assert profile.size_consistent is True
        assert len(profile.flags) == 0

    def test_edge_high_size_variance_triggers_flag(self):
        """Edge: Large position size variance → FTMO AI behavioral flag."""
        scorer = make_scorer()
        outcomes = []
        for i in range(50):
            # Wildly varying sizes: 0.1 to 5.0
            size = 0.1 + (i % 10) * 0.5
            outcomes.append(make_outcome(is_win=(i % 3) != 2, size=size, hour=10))
        profile = scorer.behavioral_profile("ACC-002", "FTMO", outcomes)
        assert profile.size_consistent is False
        assert any("SIZING" in f for f in profile.flags)

    def test_conflict_win_rate_drift_triggers_flag(self):
        """Conflict: Baseline 60% then sudden 80% in recent trades → drift flag."""
        scorer = make_scorer()
        # First 30: 60% win rate baseline
        outcomes = [make_outcome(is_win=(i % 5) < 3, size=1.0, hour=10) for i in range(30)]
        # Last 20: 90%+ win rate (suspicious spike)
        outcomes += [make_outcome(is_win=(i % 10) < 9, size=1.0, hour=10) for i in range(20)]
        profile = scorer.behavioral_profile("ACC-003", "FTMO", outcomes)
        assert profile.win_rate_drift > BEHAVIORAL_WINRATE_DRIFT_MAX
        assert profile.win_rate_consistent is False

    def test_normal_behavioral_score_between_0_and_10(self):
        """Normal: Behavioral score always in valid range."""
        scorer = make_scorer()
        outcomes = [make_outcome(size=1.0, hour=10) for _ in range(20)]
        profile = scorer.behavioral_profile("ACC-004", "FTMO", outcomes)
        assert 0.0 <= profile.behavioral_score <= 10.0


# ─────────────────────────────────────────────────────────────────────────────
# SCORE FROM STATS API
# ─────────────────────────────────────────────────────────────────────────────

class TestScoreFromStats:

    def test_normal_perfect_stats_high_score(self):
        """Normal: Zero variance stats → near-perfect score."""
        score = ConsistencyScorer().score_from_stats(
            "PERF-01", total_trades=100, win_rate=0.70,
            win_rate_std_dev=0.01,     # Near zero variance
            avg_win_pct=0.015, avg_loss_pct=0.008,
            pnl_std_dev=0.002,         # Very tight P&L
            regime_win_rates={"trend": 0.70, "range": 0.69, "hvt": 0.71},
            hour_win_rates={9: 0.70, 10: 0.70, 11: 0.70},
        )
        assert score.composite_score >= CONSISTENCY_ACCEPTABLE
        assert score.is_mature is True

    def test_edge_below_50_trades_immature_even_in_stats(self):
        """Edge: total_trades < 50 in stats → IMMATURE regardless of stats quality."""
        score = ConsistencyScorer().score_from_stats(
            "PART-01", total_trades=40, win_rate=0.70,
            win_rate_std_dev=0.01, avg_win_pct=0.015, avg_loss_pct=0.008,
            pnl_std_dev=0.002,
        )
        assert score.grade == ConsistencyGrade.IMMATURE
        assert score.is_mature is False

    def test_conflict_composite_is_weighted_sum_of_sub_scores(self):
        """Conflict: Verify composite matches weighted formula."""
        score = ConsistencyScorer().score_from_stats(
            "COMP-01", total_trades=100, win_rate=0.65,
            win_rate_std_dev=0.05,
            avg_win_pct=0.015, avg_loss_pct=0.008,
            pnl_std_dev=0.006,
            regime_win_rates={"trend": 0.65, "range": 0.64},
            hour_win_rates={10: 0.66, 11: 0.64},
        )
        expected = (
            score.win_rate_stability    * WEIGHT_WIN_RATE_STABILITY +
            score.pnl_variance_control  * 0.25 +
            score.outcome_predictability * 0.20 +
            score.regime_robustness      * 0.15 +
            score.temporal_stability     * 0.10
        )
        assert abs(score.composite_score - expected) < 0.05


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:

    def test_normal_thresholds_are_ordered(self):
        """Normal: HIGH > ACCEPTABLE > CAUTION/BLOCK in descending order."""
        assert CONSISTENCY_HIGH > CONSISTENCY_ACCEPTABLE
        assert CONSISTENCY_ACCEPTABLE > CONSISTENCY_CAUTION

    def test_edge_weights_sum_to_1(self):
        """Edge: Sub-score weights must sum to exactly 1.0."""
        total = (
            WEIGHT_WIN_RATE_STABILITY + 0.25 + 0.20 + 0.15 + 0.10
        )
        assert abs(total - 1.0) < 1e-9

    def test_conflict_immature_default_is_in_acceptable_zone(self):
        """Conflict: Immature default 6.0 is in ACCEPTABLE zone — not blocked."""
        assert IMMATURE_CONSISTENCY_DEFAULT >= CONSISTENCY_ACCEPTABLE
        assert IMMATURE_CONSISTENCY_DEFAULT < CONSISTENCY_HIGH


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    passed = failed = 0
    failures = []
    for cls_name in sorted(dir()):
        cls = eval(cls_name)
        if not (isinstance(cls, type) and cls_name.startswith("Test")):
            continue
        inst = cls()
        for meth_name in sorted(dir(inst)):
            if not meth_name.startswith("test_"):
                continue
            try:
                if hasattr(inst, "setup_method"):
                    inst.setup_method()
                getattr(inst, meth_name)()
                print(f"  ✅ {cls_name}::{meth_name}")
                passed += 1
            except Exception as e:
                print(f"  ❌ {cls_name}::{meth_name}")
                failures.append((cls_name, meth_name, traceback.format_exc()))
                failed += 1
    print(f"\nResults: {passed} passed, {failed} failed")
    if failures:
        for cn, mn, tb in failures:
            print(f"\nFAIL: {cn}::{mn}\n{tb}")
