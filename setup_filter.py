"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   setup_filter.py — FORGE-06 — Layer 1                      ║
║                                                                              ║
║  HIGH PROBABILITY SETUP FILTER                                               ║
║  Only setups with 60%+ historical win rate pass. Consistency over            ║
║  explosiveness. A 65% win rate 2:1 setup beats a 50% win rate 5:1 setup     ║
║  every single time in prop firm evaluation. Math always wins.                ║
║                                                                              ║
║  Filter rules:                                                               ║
║    1. Historical win rate must be ≥ 60% (FORGE-06 hard minimum)             ║
║    2. Minimum trade sample for statistical significance (50 trades)          ║
║    3. Setup must not be in a decay period (win rate dropping over last 20)   ║
║    4. Setup must match current market regime                                 ║
║    5. During evaluation: Catalyst Stack ≥ 4 for TITAN STOCK setups          ║
║       (FORGE-22 Catalyst Stack Lite integration)                             ║
║                                                                              ║
║  Pre-loaded with all 30 documented strategies (Section 10) and their        ║
║  verified win rates. New setups start IMMATURE until 50 trade threshold.     ║
║                                                                              ║
║  Principle: Consistency over explosiveness.                                  ║
║  TITAN FORGE would rather win 65% at 2:1 than try for 50% at 5:1.          ║
║                                                                              ║
║  Integrates with:                                                            ║
║    • FORGE-07 Consistency Score — secondary gate after win rate passes      ║
║    • FORGE-08 Session Quality Filter — setup filter feeds session scoring   ║
║    • FORGE-22 Catalyst Stack Lite — catalyst requirement for TITAN STOCK    ║
║    • FORGE-72 Regime Deployment — regime-setup matching                     ║
║    • FX-03 Data Maturity Thresholds — immature defaults below 50 trades     ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.setup_filter")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# FORGE-06: Hard minimum win rate for evaluation trading
WIN_RATE_MINIMUM_EVALUATION: float = 0.60   # 60% — non-negotiable
WIN_RATE_MINIMUM_FUNDED:     float = 0.60   # Same in funded mode
WIN_RATE_IMMATURE_DEFAULT:   float = 0.60   # Assume minimum until data proves otherwise

# FX-03: Minimum trades before win rate is statistically trusted
MIN_TRADES_MATURE: int = 50   # Below this = IMMATURE — use conservative defaults

# Edge decay detection: last N trades compared to lifetime average
EDGE_DECAY_SAMPLE_SIZE: int   = 20     # Compare last 20 trades
EDGE_DECAY_THRESHOLD:   float = 0.10   # 10% drop from lifetime average triggers decay flag

# Catalyst stack requirement for TITAN STOCK setups during evaluation (FORGE-22)
CATALYST_STACK_MINIMUM: int = 4   # 4+ stack score required

# Win rate warning zone (between 60% and 65%) — passes but with warning
WIN_RATE_WARNING_ZONE: float = 0.65


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — SETUP CATEGORIES
# From Section 10 of the Master Handoff Document.
# ─────────────────────────────────────────────────────────────────────────────

class SetupCategory(Enum):
    GEX_GAMMA         = "GEX/Gamma"
    ICT_SMART_MONEY   = "ICT/Smart Money"
    VOLUME_PROFILE    = "Volume Profile"
    ORDER_FLOW        = "Order Flow"
    SESSION_STATS     = "Session Statistics"
    INSTITUTIONAL     = "Institutional Footprint"
    CUSTOM            = "Custom"


class MarketRegime(Enum):
    """Five regimes from FORGE-72."""
    LOW_VOL_TRENDING   = "low_vol_trending"
    LOW_VOL_RANGING    = "low_vol_ranging"
    HIGH_VOL_TRENDING  = "high_vol_trending"
    HIGH_VOL_RANGING   = "high_vol_ranging"
    EXPANSION          = "expansion"
    ANY                = "any"   # Setup works across all regimes


class FilterVerdict(Enum):
    APPROVED        = auto()   # Passes all gates
    REJECTED        = auto()   # Failed at least one gate
    IMMATURE        = auto()   # Below 50-trade threshold — use with caution only
    EDGE_DECAY      = auto()   # Win rate declining — suspended pending review


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — SETUP RECORD
# Historical performance data for a single setup type.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SetupRecord:
    """
    Historical performance data for a single named setup type.
    Win rate is the primary gate. Consistency is the secondary gate.
    """
    setup_id:           str            # e.g. "GEX-01", "ICT-02"
    name:               str            # Human-readable name
    category:           SetupCategory
    # Performance metrics
    lifetime_win_rate:  float          # Across all historical trades
    total_trades:       int            # Total lifetime trade count
    avg_rr:             float          # Average reward:risk ratio
    # Recent performance (last 20 trades for edge decay detection)
    recent_win_rate:    float          # Win rate of last N trades
    recent_trade_count: int            # How many recent trades in sample
    # Regime compatibility
    best_regimes:       tuple[MarketRegime, ...] = field(default_factory=tuple)
    # Flags
    requires_catalyst_stack: bool = False   # TITAN STOCK setups need 4-stack
    is_futures_only:         bool = False
    is_forex_only:           bool = False
    notes:                   str  = ""

    @property
    def is_mature(self) -> bool:
        return self.total_trades >= MIN_TRADES_MATURE

    @property
    def effective_win_rate(self) -> float:
        """Win rate to use for filtering — immature setups use conservative default."""
        if not self.is_mature:
            return WIN_RATE_IMMATURE_DEFAULT
        return self.lifetime_win_rate

    @property
    def is_edge_decaying(self) -> bool:
        """True if recent win rate has dropped significantly from lifetime average."""
        if not self.is_mature or self.recent_trade_count < 10:
            return False
        drop = round(self.lifetime_win_rate - self.recent_win_rate, 10)
        return drop >= EDGE_DECAY_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — SETUP DATABASE
# All 30 strategies from Section 10, pre-loaded with documented win rates.
# ─────────────────────────────────────────────────────────────────────────────

def _build_setup_database() -> dict[str, SetupRecord]:
    """
    Build the initial setup database from Section 10 of the Master Handoff Document.
    All 30 strategies with their documented win rates and R:R ratios.
    These start with total_trades=0 — they will grow with live data (FORGE-83).
    Recent win rates default to lifetime until live data flows.
    """

    def rec(sid, name, category, win_rate, rr, regimes, *, futures=False,
            forex=False, catalyst=False, notes=""):
        return SetupRecord(
            setup_id=sid, name=name, category=category,
            lifetime_win_rate=win_rate, total_trades=0,
            avg_rr=rr, recent_win_rate=win_rate, recent_trade_count=0,
            best_regimes=regimes, requires_catalyst_stack=catalyst,
            is_futures_only=futures, is_forex_only=forex, notes=notes,
        )

    T   = (MarketRegime.LOW_VOL_TRENDING,)
    R   = (MarketRegime.LOW_VOL_RANGING,)
    HVT = (MarketRegime.HIGH_VOL_TRENDING,)
    HVR = (MarketRegime.HIGH_VOL_RANGING,)
    EXP = (MarketRegime.EXPANSION,)
    ANY = (MarketRegime.ANY,)

    db = {}
    for s in [
        # ── GEX / Gamma (5 strategies) ───────────────────────────────────────
        rec("GEX-01", "Gamma Flip Breakout",      SetupCategory.GEX_GAMMA,       0.75, 2.5, HVT,
            notes="GEX negative = trend day confirmed. Primary entry for trend days."),
        rec("GEX-02", "Dealer Hedging Cascade",   SetupCategory.GEX_GAMMA,       0.74, 3.0, HVT),
        rec("GEX-03", "GEX Pin and Break",        SetupCategory.GEX_GAMMA,       0.73, 2.0, ANY),
        rec("GEX-04", "Vanna Flow Drift",         SetupCategory.GEX_GAMMA,       0.70, 2.0, T,
            notes="IV falling post-event: dealers buy futures. Afternoon bias."),
        rec("GEX-05", "Charm Decay Fade",         SetupCategory.GEX_GAMMA,       0.68, 1.8, R,
            notes="Delta unwind in afternoon. Works in ranging high-positive GEX days."),
        # ── ICT / Smart Money (8 strategies) ─────────────────────────────────
        rec("ICT-01", "Order Block + FVG Confluence",   SetupCategory.ICT_SMART_MONEY, 0.76, 2.5, ANY,
            notes="Highest win rate in ICT category. Order block + imbalance confluence."),
        rec("ICT-02", "Liquidity Sweep and Reverse",    SetupCategory.ICT_SMART_MONEY, 0.74, 3.0, ANY,
            notes="Stop hunt + reversal. 68-74% mechanically driven by institutional need."),
        rec("ICT-03", "Kill Zone OTE Entry",            SetupCategory.ICT_SMART_MONEY, 0.73, 2.5, ANY,
            notes="NY Kill Zone 9:30-11am ET. Optimal Trade Entry in premium/discount."),
        rec("ICT-04", "Breaker Block Retest",           SetupCategory.ICT_SMART_MONEY, 0.72, 2.0, T+HVT),
        rec("ICT-05", "Asian Range Raid and Reverse",   SetupCategory.ICT_SMART_MONEY, 0.71, 2.5, ANY,
            notes="Asian session consolidation → London raid → NY reversal."),
        rec("ICT-06", "Premium/Discount Zone Filter",   SetupCategory.ICT_SMART_MONEY, 0.70, 2.5, ANY),
        rec("ICT-07", "FVG Inversion Play",             SetupCategory.ICT_SMART_MONEY, 0.69, 2.0, ANY),
        rec("ICT-08", "Market Structure Break + OTE",   SetupCategory.ICT_SMART_MONEY, 0.73, 3.0, T+HVT),
        # ── Volume Profile (5 strategies) ────────────────────────────────────
        rec("VOL-01", "Point of Control Magnetic Revert", SetupCategory.VOLUME_PROFILE, 0.74, 1.8, R+HVR,
            notes="POC acts as gravity. Mean reversion to volume node."),
        rec("VOL-02", "Value Area Edge Fade",             SetupCategory.VOLUME_PROFILE, 0.72, 2.0, R),
        rec("VOL-03", "Low Volume Node Express",          SetupCategory.VOLUME_PROFILE, 0.73, 2.5, ANY,
            notes="Price moves quickly through low-volume nodes. Express lane."),
        rec("VOL-04", "High Volume Node Cluster Trade",   SetupCategory.VOLUME_PROFILE, 0.70, 2.0, ANY),
        rec("VOL-05", "Anchored VWAP Confluence",         SetupCategory.VOLUME_PROFILE, 0.71, 2.0, ANY,
            notes="Institutional reference point. Multiple VWAP anchors = high conviction."),
        # ── Order Flow (4 strategies) ─────────────────────────────────────────
        rec("ORD-01", "Delta Divergence Reversal",   SetupCategory.ORDER_FLOW, 0.75, 2.5, ANY,
            notes="Footprint: price up but delta negative. Institutional absorption."),
        rec("ORD-02", "Footprint Absorption Entry",  SetupCategory.ORDER_FLOW, 0.73, 2.5, ANY,
            futures=True, notes="Requires ATAS or Quantower. Windows VPS."),
        rec("ORD-03", "Order Block Stacking Breakout",SetupCategory.ORDER_FLOW, 0.71, 2.0, T+HVT),
        rec("ORD-04", "Bid/Ask Imbalance Cascade",   SetupCategory.ORDER_FLOW, 0.70, 2.0, HVT+EXP),
        # ── Session Statistics (5 strategies) ────────────────────────────────
        rec("SES-01", "New York Kill Zone Power Hour", SetupCategory.SESSION_STATS, 0.74, 2.5, ANY,
            notes="9:30-11am ET. Institutional flow concentration. Same patterns daily."),
        rec("SES-02", "London-NY Overlap Momentum",  SetupCategory.SESSION_STATS, 0.73, 2.0, ANY,
            forex=True, notes="8am-12pm ET only. Major pairs. Not during evaluation outside window."),
        rec("SES-03", "First Hour Reversal Pattern",  SetupCategory.SESSION_STATS, 0.70, 2.0, R+HVR),
        rec("SES-04", "Pre-Close Institutional Positioning", SetupCategory.SESSION_STATS, 0.69, 1.8, ANY),
        rec("SES-05", "Monday Gap Fill Strategy",     SetupCategory.SESSION_STATS, 0.72, 2.0, ANY,
            notes="Monday only. Gap fill after weekend positioning."),
        # ── Institutional Footprint (3 strategies) ────────────────────────────
        rec("INS-01", "Unusual Options Flow Follow", SetupCategory.INSTITUTIONAL, 0.75, 3.0, ANY,
            catalyst=True, notes="Requires Unusual Whales API $25/month. Best for TITAN STOCK."),
        rec("INS-02", "Dark Pool Print Entry",       SetupCategory.INSTITUTIONAL, 0.73, 2.5, ANY,
            catalyst=True, notes="FINRA dark pool data. Free at finra.org daily."),
        rec("INS-03", "COT Extreme Reversal",        SetupCategory.INSTITUTIONAL, 0.71, 3.0, ANY,
            forex=True, catalyst=True, notes="CFTC COT data. Weekly. Macro positioning extremes."),
    ]:
        db[s.setup_id] = s

    return db


# Singleton — shared across all callers
_SETUP_DATABASE: dict[str, SetupRecord] = _build_setup_database()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — FILTER RESULT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SetupFilterResult:
    """
    Complete result of the high-probability setup filter for one setup.
    Always check .verdict before executing a trade.
    """
    setup_id:               str
    setup_name:             str
    verdict:                FilterVerdict
    # Gate results
    win_rate_gate:          bool           # ≥ 60%?
    maturity_gate:          bool           # ≥ 50 trades?
    edge_decay_gate:        bool           # Not in decay?
    regime_gate:            bool           # Matches current regime?
    catalyst_gate:          bool           # Catalyst stack if required?
    # Values
    effective_win_rate:     float
    win_rate_threshold:     float
    catalyst_stack_score:   Optional[int]
    catalyst_required:      bool
    # Regime
    current_regime:         Optional[MarketRegime]
    setup_best_regimes:     tuple
    # Warning
    in_warning_zone:        bool           # Passes but win rate 60–65%
    immature_note:          Optional[str]
    # Explanation
    reason:                 str
    recommendation:         str

    @property
    def is_approved(self) -> bool:
        return self.verdict == FilterVerdict.APPROVED

    @property
    def is_blocked(self) -> bool:
        return self.verdict in (
            FilterVerdict.REJECTED,
            FilterVerdict.EDGE_DECAY,
        )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — THE HIGH PROBABILITY SETUP FILTER
# FORGE-06. The quality gate before any trade is considered.
# ─────────────────────────────────────────────────────────────────────────────

class HighProbabilitySetupFilter:
    """
    FORGE-06: High Probability Setup Filter.

    Only setups with 60%+ historical win rate pass.
    Consistency over explosiveness — always.

    Checks in order:
        1. Win rate ≥ 60% (hard minimum — FORGE-06)
        2. Maturity ≥ 50 trades (FX-03 data maturity)
        3. No edge decay (FORGE-79 statistical edge verification)
        4. Regime compatibility (FORGE-72)
        5. Catalyst stack ≥ 4 for institutional setups (FORGE-22)

    Usage:
        filt = HighProbabilitySetupFilter()

        result = filt.check(
            setup_id="GEX-01",
            current_regime=MarketRegime.HIGH_VOL_TRENDING,
            is_evaluation=True,
            catalyst_stack_score=5,
        )

        if not result.is_approved:
            # Do not execute. Log result.reason.
            return
    """

    def __init__(
        self,
        setup_db:         Optional[dict[str, SetupRecord]] = None,
        win_rate_minimum: float = WIN_RATE_MINIMUM_EVALUATION,
    ):
        self._db              = dict(setup_db) if setup_db is not None else dict(_SETUP_DATABASE)
        self._win_rate_min    = win_rate_minimum
        self._filter_count    = 0
        self._reject_count    = 0

    # ── MAIN FILTER ──────────────────────────────────────────────────────────

    def check(
        self,
        setup_id:               str,
        current_regime:         Optional[MarketRegime] = None,
        is_evaluation:          bool = True,
        catalyst_stack_score:   Optional[int] = None,
    ) -> SetupFilterResult:
        """
        Run the full setup filter against all gates.

        Args:
            setup_id:             Setup identifier (e.g. "GEX-01").
            current_regime:       Active market regime from regime detection.
            is_evaluation:        True = evaluation mode (stricter catalyst rules).
            catalyst_stack_score: Multi-strategy confluence score (0–N).

        Returns:
            SetupFilterResult with verdict and per-gate breakdown.
        """
        self._filter_count += 1

        # ── Unknown setup — reject ────────────────────────────────────────────
        if setup_id not in self._db:
            self._reject_count += 1
            return SetupFilterResult(
                setup_id=setup_id, setup_name=f"UNKNOWN ({setup_id})",
                verdict=FilterVerdict.REJECTED,
                win_rate_gate=False, maturity_gate=False, edge_decay_gate=False,
                regime_gate=False, catalyst_gate=False,
                effective_win_rate=0.0, win_rate_threshold=self._win_rate_min,
                catalyst_stack_score=catalyst_stack_score,
                catalyst_required=False,
                current_regime=current_regime, setup_best_regimes=(),
                in_warning_zone=False, immature_note=None,
                reason=f"Setup '{setup_id}' not found in setup database. "
                       f"Cannot evaluate unknown setup.",
                recommendation="Add this setup to the database with documented win rate data.",
            )

        rec = self._db[setup_id]

        # ── Gate 1: Win rate ≥ 60% ────────────────────────────────────────────
        effective_wr = rec.effective_win_rate
        win_rate_gate = effective_wr >= self._win_rate_min
        in_warning_zone = win_rate_gate and effective_wr < WIN_RATE_WARNING_ZONE

        # ── Gate 2: Maturity ─────────────────────────────────────────────────
        maturity_gate = True   # Always passes — immature setups get IMMATURE verdict
        immature_note = None
        if not rec.is_mature:
            immature_note = (
                f"IMMATURE: Only {rec.total_trades}/{MIN_TRADES_MATURE} trades. "
                f"Using conservative default win rate {WIN_RATE_IMMATURE_DEFAULT:.0%}. "
                f"Win rate will be confirmed once {MIN_TRADES_MATURE} trades are recorded."
            )

        # ── Gate 3: Edge decay ────────────────────────────────────────────────
        edge_decay_gate = not rec.is_edge_decaying
        if not edge_decay_gate:
            logger.warning(
                "[FORGE-06] Edge decay detected on %s. "
                "Lifetime: %.1f%%. Recent: %.1f%%. Drop: %.1f%%.",
                setup_id,
                rec.lifetime_win_rate * 100,
                rec.recent_win_rate * 100,
                (rec.lifetime_win_rate - rec.recent_win_rate) * 100,
            )

        # ── Gate 4: Regime compatibility ──────────────────────────────────────
        if current_regime is None or MarketRegime.ANY in rec.best_regimes:
            regime_gate = True
        else:
            regime_gate = current_regime in rec.best_regimes

        # ── Gate 5: Catalyst stack (FORGE-22) ─────────────────────────────────
        catalyst_required = rec.requires_catalyst_stack and is_evaluation
        if catalyst_required:
            catalyst_gate = (
                catalyst_stack_score is not None
                and catalyst_stack_score >= CATALYST_STACK_MINIMUM
            )
        else:
            catalyst_gate = True

        # ── Determine verdict ─────────────────────────────────────────────────
        if not edge_decay_gate:
            verdict = FilterVerdict.EDGE_DECAY
        elif not win_rate_gate:
            verdict = FilterVerdict.REJECTED
        elif not catalyst_gate:
            # Catalyst failure blocks regardless of maturity
            verdict = FilterVerdict.REJECTED
        elif not rec.is_mature:
            verdict = FilterVerdict.IMMATURE
        elif not regime_gate:
            verdict = FilterVerdict.REJECTED
        else:
            verdict = FilterVerdict.APPROVED

        if verdict != FilterVerdict.APPROVED:
            self._reject_count += 1

        # ── Build reason string ───────────────────────────────────────────────
        reason = self._build_reason(
            rec, verdict, effective_wr, win_rate_gate, edge_decay_gate,
            regime_gate, catalyst_gate, current_regime, catalyst_stack_score,
            catalyst_required, immature_note,
        )
        recommendation = self._build_recommendation(verdict, rec, catalyst_stack_score)

        # ── Log ───────────────────────────────────────────────────────────────
        log_fn = logger.info if verdict == FilterVerdict.APPROVED else logger.warning
        log_fn(
            "[FORGE-06] %s | %s | WR: %.1f%% | Regime: %s | Catalyst: %s → %s",
            setup_id, rec.name, effective_wr * 100,
            current_regime.value if current_regime else "N/A",
            catalyst_stack_score if catalyst_stack_score is not None else "N/A",
            verdict.name,
        )

        return SetupFilterResult(
            setup_id=setup_id,
            setup_name=rec.name,
            verdict=verdict,
            win_rate_gate=win_rate_gate,
            maturity_gate=rec.is_mature,
            edge_decay_gate=edge_decay_gate,
            regime_gate=regime_gate,
            catalyst_gate=catalyst_gate,
            effective_win_rate=effective_wr,
            win_rate_threshold=self._win_rate_min,
            catalyst_stack_score=catalyst_stack_score,
            catalyst_required=catalyst_required,
            current_regime=current_regime,
            setup_best_regimes=rec.best_regimes,
            in_warning_zone=in_warning_zone,
            immature_note=immature_note,
            reason=reason,
            recommendation=recommendation,
        )

    def check_batch(
        self,
        setup_ids:            list[str],
        current_regime:       Optional[MarketRegime] = None,
        is_evaluation:        bool = True,
        catalyst_stack_score: Optional[int] = None,
    ) -> list[SetupFilterResult]:
        """Filter multiple setups at once. Returns all results (approved and rejected)."""
        return [
            self.check(sid, current_regime, is_evaluation, catalyst_stack_score)
            for sid in setup_ids
        ]

    def get_approved_setups(
        self,
        setup_ids:            list[str],
        current_regime:       Optional[MarketRegime] = None,
        is_evaluation:        bool = True,
        catalyst_stack_score: Optional[int] = None,
    ) -> list[SetupFilterResult]:
        """Return only approved setups from a list."""
        return [
            r for r in self.check_batch(
                setup_ids, current_regime, is_evaluation, catalyst_stack_score
            )
            if r.is_approved or r.verdict == FilterVerdict.IMMATURE
        ]

    # ── DATA MANAGEMENT ──────────────────────────────────────────────────────

    def update_win_rate(
        self,
        setup_id:          str,
        new_lifetime_rate: float,
        total_trades:      int,
        recent_rate:       float,
        recent_count:      int,
    ) -> None:
        """
        Update the historical win rate for a setup after new trade data.
        Called by FORGE-83 Evolutionary Setup Selection after each evaluation.
        """
        if setup_id not in self._db:
            logger.error("[FORGE-06] Cannot update unknown setup: %s", setup_id)
            return
        rec = self._db[setup_id]
        old_wr = rec.lifetime_win_rate
        # Update via object recreation (SetupRecord is mutable)
        rec.lifetime_win_rate  = new_lifetime_rate
        rec.total_trades       = total_trades
        rec.recent_win_rate    = recent_rate
        rec.recent_trade_count = recent_count
        logger.info(
            "[FORGE-06] Updated %s: WR %.1f%% → %.1f%% (%d trades). Recent: %.1f%%.",
            setup_id, old_wr * 100, new_lifetime_rate * 100, total_trades, recent_rate * 100,
        )

    def register_setup(self, record: SetupRecord) -> None:
        """Add a new setup to the database. Starts IMMATURE until 50 trades."""
        if record.setup_id in self._db:
            raise ValueError(
                f"Setup '{record.setup_id}' already registered. "
                f"Use update_win_rate() to modify existing records."
            )
        self._db[record.setup_id] = record
        logger.info(
            "[FORGE-06] New setup registered: %s (%s). "
            "Status: IMMATURE until %d trades.",
            record.setup_id, record.name, MIN_TRADES_MATURE,
        )

    def get_all_by_category(self, category: SetupCategory) -> list[SetupRecord]:
        """Return all setup records for a category."""
        return [r for r in self._db.values() if r.category == category]

    def get_above_win_rate(self, min_wr: float) -> list[SetupRecord]:
        """Return all setups with lifetime win rate >= min_wr."""
        return [r for r in self._db.values() if r.lifetime_win_rate >= min_wr]

    # ── STATISTICS ───────────────────────────────────────────────────────────

    @property
    def total_setups(self) -> int:
        return len(self._db)

    @property
    def mature_setups(self) -> int:
        return sum(1 for r in self._db.values() if r.is_mature)

    @property
    def filter_rejection_rate(self) -> float:
        if self._filter_count == 0:
            return 0.0
        return self._reject_count / self._filter_count

    def summary_stats(self) -> dict:
        """Summary for ARCHITECT dashboard."""
        avg_wr = (
            sum(r.lifetime_win_rate for r in self._db.values()) / len(self._db)
            if self._db else 0.0
        )
        decaying = [r.setup_id for r in self._db.values() if r.is_edge_decaying]
        return {
            "total_setups":        self.total_setups,
            "mature_setups":       self.mature_setups,
            "average_win_rate":    round(avg_wr, 4),
            "min_win_rate":        round(min((r.lifetime_win_rate for r in self._db.values()), default=0), 4),
            "max_win_rate":        round(max((r.lifetime_win_rate for r in self._db.values()), default=0), 4),
            "edge_decaying":       decaying,
            "filter_count":        self._filter_count,
            "rejection_rate":      round(self.filter_rejection_rate, 4),
        }

    # ── PRIVATE HELPERS ───────────────────────────────────────────────────────

    def _build_reason(
        self, rec, verdict, effective_wr, win_rate_gate, edge_decay_gate,
        regime_gate, catalyst_gate, current_regime, catalyst_score,
        catalyst_required, immature_note,
    ) -> str:
        parts = []
        if verdict == FilterVerdict.EDGE_DECAY:
            parts.append(
                f"EDGE DECAY: {rec.setup_id} win rate dropping. "
                f"Lifetime: {rec.lifetime_win_rate:.1%}. Recent: {rec.recent_win_rate:.1%}. "
                f"Setup suspended pending recovery."
            )
        elif not win_rate_gate:
            parts.append(
                f"WIN RATE FAILED: {effective_wr:.1%} < {self._win_rate_min:.0%} minimum. "
                f"FORGE-06: Only 60%+ historical win rate setups trade. "
                f"Consistency over explosiveness."
            )
        else:
            parts.append(
                f"Win rate: {effective_wr:.1%} ✓ (≥{self._win_rate_min:.0%})"
            )
        if immature_note:
            parts.append(immature_note)
        if not regime_gate and current_regime:
            best = ", ".join(r.value for r in rec.best_regimes) or "ANY"
            parts.append(
                f"REGIME MISMATCH: Current regime '{current_regime.value}' not in "
                f"setup's optimal regimes: [{best}]."
            )
        if not catalyst_gate:
            score_str = str(catalyst_score) if catalyst_score is not None else "N/A"
            parts.append(
                f"CATALYST STACK INSUFFICIENT: Score {score_str} < {CATALYST_STACK_MINIMUM} "
                f"required for institutional setup during evaluation. "
                f"(FORGE-22: 4+ stack required for TITAN STOCK tier-1 setups.)"
            )
        return " | ".join(parts)

    def _build_recommendation(
        self, verdict: FilterVerdict, rec: SetupRecord, catalyst_score: Optional[int]
    ) -> str:
        if verdict == FilterVerdict.APPROVED:
            return (
                f"Approved. Proceed with {rec.setup_id}. "
                f"Win rate {rec.lifetime_win_rate:.1%} at {rec.avg_rr:.1f}:1 R:R."
            )
        elif verdict == FilterVerdict.IMMATURE:
            return (
                f"Approved with caution — IMMATURE. Reduce position size. "
                f"Record every trade. Reassess at {MIN_TRADES_MATURE} trades."
            )
        elif verdict == FilterVerdict.EDGE_DECAY:
            return (
                f"Suspended. Wait for {rec.setup_id} win rate to recover "
                f"before re-activating. Review market regime conditions."
            )
        elif not catalyst_score or catalyst_score < CATALYST_STACK_MINIMUM:
            return (
                f"Wait for catalyst confluence. Need {CATALYST_STACK_MINIMUM}+ stack score. "
                f"Current: {catalyst_score or 0}. Add more confirming signals."
            )
        else:
            return (
                f"Rejected. Find an alternative setup with 60%+ win rate "
                f"that matches current market conditions."
            )
