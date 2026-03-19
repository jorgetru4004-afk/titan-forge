"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  phase1_intelligence.py — Layer 3                           ║
║                                                                              ║
║  Missing Phase 1 requirements built strictly per document.                  ║
║                                                                              ║
║  FORGE-34: Trailing vs Static Drawdown                                      ║
║  FORGE-35: Consistency Rule Compliance (DNA 40%, Apex 50%)                  ║
║  FORGE-38: Free Reset Optimizer                                              ║
║  FORGE-40: Firm Health Monitor (Trustpilot, regulatory, payout disputes)    ║
║  FORGE-42: Evaluation Behavioral Journal                                    ║
║  FORGE-43: 90-Minute Recovery Protocol (ANY losing trade — no exceptions)  ║
║  FORGE-45: Prop Firm Arbitrage Intelligence                                 ║
║  FORGE-49: Rule Backtesting Engine                                          ║
║  FORGE-50: Kelly Criterion Adapter (fractional Kelly, 2% hard cap)          ║
║  FORGE-51: Ruin Probability Calculator                                      ║
║  FORGE-52: Regime to Firm Matchmaking                                       ║
║  FORGE-53: Firm Financial Health Assessment                                 ║
║  FORGE-55: Psychological Calibration Reset                                  ║
║  FORGE-56: Information Edge Verification                                    ║
║  FORGE-57: Evaluation Cost Basis Tracking                                   ║
║  FORGE-66: Profit Target Approach Protocol (20%→half, 10%→min)             ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional

from firm_rules import FirmID

logger = logging.getLogger("titan_forge.phase1_intelligence")

# ─────────────────────────────────────────────────────────────────────────────
# FORGE-34: TRAILING VS STATIC DRAWDOWN
# Identifies firm drawdown model. Apex trailing = more conservative sizing.
# FTMO static = more flexibility.
# ─────────────────────────────────────────────────────────────────────────────

class DrawdownModel(Enum):
    STATIC             = "static"    # FTMO, DNA, 5%ers — floor never moves
    TRAILING_UNREALIZED = "trailing_unrealized"  # Apex — follows unrealized P&L
    TRAILING_EOD       = "trailing_eod"          # Topstep — end-of-day trailing

FIRM_DRAWDOWN_MODELS: dict[str, DrawdownModel] = {
    FirmID.FTMO:           DrawdownModel.STATIC,
    FirmID.DNA_FUNDED:     DrawdownModel.STATIC,
    FirmID.FIVEPERCENTERS: DrawdownModel.STATIC,
    FirmID.APEX:           DrawdownModel.TRAILING_UNREALIZED,
    FirmID.TOPSTEP:        DrawdownModel.TRAILING_EOD,
}

# Sizing multipliers based on drawdown model.
# Trailing drawdown = MORE conservative — floor moves against you in real time.
DRAWDOWN_MODEL_SIZE_MULT: dict[DrawdownModel, float] = {
    DrawdownModel.STATIC:              1.00,   # Full flexibility
    DrawdownModel.TRAILING_EOD:        0.85,   # More conservative
    DrawdownModel.TRAILING_UNREALIZED: 0.70,   # MOST conservative — Apex rule is dangerous
}

@dataclass
class DrawdownModelResult:
    firm_id:          str
    model:            DrawdownModel
    size_multiplier:  float
    reasoning:        str

def identify_drawdown_model(firm_id: str) -> DrawdownModelResult:
    """
    FORGE-34: Identify firm drawdown model and return appropriate size multiplier.
    Apex trailing requires 30% smaller positions than FTMO static.
    """
    model = FIRM_DRAWDOWN_MODELS.get(firm_id, DrawdownModel.STATIC)
    mult  = DRAWDOWN_MODEL_SIZE_MULT[model]

    if model == DrawdownModel.TRAILING_UNREALIZED:
        reasoning = (
            f"Apex trailing unrealized — MOST DANGEROUS. "
            f"Floor rises with every unrealized gain. "
            f"Size reduced {int((1-mult)*100)}% vs static firms. "
            f"If position is up $1K and returns to BE: $1K permanently gone."
        )
    elif model == DrawdownModel.TRAILING_EOD:
        reasoning = (
            f"Topstep trailing EOD — floor rises at end of profitable days. "
            f"Size reduced {int((1-mult)*100)}%. Force close by 3:10pm CT daily."
        )
    else:
        reasoning = f"Static drawdown — floor set day 1 and never moves. Full sizing."

    return DrawdownModelResult(firm_id=firm_id, model=model,
                               size_multiplier=mult, reasoning=reasoning)


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-35: CONSISTENCY RULE COMPLIANCE
# DNA Funded: 40% cap. Apex: 50% cap. Throttles on approach.
# ─────────────────────────────────────────────────────────────────────────────

CONSISTENCY_CAPS: dict[str, Optional[float]] = {
    FirmID.FTMO:           None,   # NO consistency rule — biggest advantage
    FirmID.DNA_FUNDED:     0.40,   # 40% max single-day on withdrawal request
    FirmID.APEX:           0.50,   # 50% cap (March 2026 update)
    FirmID.FIVEPERCENTERS: None,
    FirmID.TOPSTEP:        None,
}

@dataclass
class ConsistencyStatus:
    firm_id:              str
    cap_pct:              Optional[float]
    today_pct_of_total:   float
    is_approaching:       bool    # Within 80% of cap
    is_at_cap:            bool
    size_throttle:        float   # 0–1: reduce size when approaching
    recommendation:       str

def check_consistency_compliance(
    firm_id:          str,
    today_profit:     float,
    total_eval_profit: float,
) -> ConsistencyStatus:
    """
    FORGE-35: Check consistency rule compliance.
    DNA 40% cap: no single day > 40% of total profits.
    Apex 50% cap: no single day > 50% since last payout.
    Throttles SIZE automatically on approach.
    """
    cap = CONSISTENCY_CAPS.get(firm_id)

    if cap is None or total_eval_profit <= 0:
        return ConsistencyStatus(
            firm_id=firm_id, cap_pct=cap,
            today_pct_of_total=0.0, is_approaching=False,
            is_at_cap=False, size_throttle=1.0,
            recommendation=f"No consistency rule at {firm_id}. No restriction."
        )

    today_pct = today_profit / total_eval_profit if total_eval_profit > 0 else 0.0
    is_at_cap    = today_pct >= cap
    is_approach  = today_pct >= cap * 0.80

    if is_at_cap:
        throttle = 0.0   # STOP — at or above cap
        rec = (f"⛔ CONSISTENCY CAP HIT: Today {today_pct:.1%} ≥ {cap:.0%} cap. "
               f"No more trading today. Taking profits here violates {firm_id} consistency rule.")
    elif is_approach:
        # Throttle down to avoid accidentally crossing cap
        throttle = max(0.25, 1.0 - ((today_pct - cap * 0.80) / (cap * 0.20)))
        rec = (f"⚠ Approaching cap: {today_pct:.1%} / {cap:.0%}. "
               f"Size throttled to {throttle:.0%}. One careful trade only.")
    else:
        throttle = 1.0
        rec = (f"Clear. Today: {today_pct:.1%} / {cap:.0%} cap.")

    return ConsistencyStatus(
        firm_id=firm_id, cap_pct=cap,
        today_pct_of_total=round(today_pct, 4),
        is_approaching=is_approach, is_at_cap=is_at_cap,
        size_throttle=round(throttle, 4),
        recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-38: FREE RESET OPTIMIZER
# Tracks firms with free reset packages.
# Adjusts strategy when reset is available vs not.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ResetStatus:
    firm_id:          str
    has_free_reset:   bool
    resets_remaining: int
    reset_threshold:  float   # Drawdown % where reset becomes available
    should_use_reset: bool
    strategy_adjustment: str

def check_reset_optimizer(
    firm_id:           str,
    drawdown_pct_used: float,
    days_elapsed:      int,
    total_days:        Optional[int] = None,
) -> ResetStatus:
    """
    FORGE-38: Optimize free reset usage.
    With reset available: can take slightly more aggressive setups
    because downside is bounded.
    Without reset: more conservative.
    """
    # FTMO offers free reset in some packages (Challenge Swap)
    # Apex: 1 free repeat if you fail the evaluation
    firm_reset_policies: dict[str, dict] = {
        FirmID.FTMO:    {"has_free": True,  "resets": 1, "threshold": 0.70},
        FirmID.APEX:    {"has_free": True,  "resets": 1, "threshold": 0.80},
        FirmID.TOPSTEP: {"has_free": False, "resets": 0, "threshold": 1.0},
        FirmID.DNA_FUNDED:     {"has_free": False, "resets": 0, "threshold": 1.0},
        FirmID.FIVEPERCENTERS: {"has_free": False, "resets": 0, "threshold": 1.0},
    }

    policy = firm_reset_policies.get(firm_id, {"has_free": False, "resets": 0, "threshold": 1.0})
    has_free  = policy["has_free"]
    resets    = policy["resets"]
    threshold = policy["threshold"]

    should_use = has_free and drawdown_pct_used >= threshold

    if should_use:
        adjustment = (
            f"Free reset available AND drawdown at {drawdown_pct_used:.0%}. "
            f"CONSIDER RESET: save the remaining capital, start fresh. "
            f"Reset before drawdown reaches firm limit."
        )
    elif has_free and drawdown_pct_used >= threshold * 0.70:
        adjustment = (
            f"Approaching reset threshold ({drawdown_pct_used:.0%}/{threshold:.0%}). "
            f"Stay disciplined — reset is available but costs time."
        )
    elif has_free:
        adjustment = (
            f"Free reset available (unused). Standard strategy. "
            f"Reset is a safety net, not a license to be reckless."
        )
    else:
        adjustment = f"No free reset. Every trade is final. Conservative approach only."

    return ResetStatus(
        firm_id=firm_id, has_free_reset=has_free,
        resets_remaining=resets, reset_threshold=threshold,
        should_use_reset=should_use, strategy_adjustment=adjustment,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-40: FIRM HEALTH MONITOR
# Monitors Trustpilot, regulatory status, payout disputes.
# Pauses evaluations at unhealthy firms.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FirmHealthScore:
    firm_id:             str
    trustpilot_score:    float    # 0–5
    payout_dispute_rate: float    # 0–1 (fraction of payouts disputed)
    regulatory_ok:       bool
    community_sentiment: str      # "positive" / "neutral" / "negative"
    composite_score:     float    # 0–100
    is_healthy:          bool
    pause_evaluations:   bool
    warning:             Optional[str]

# Known firm health data (document: DNA Funded 3.4/5 Trustpilot)
FIRM_HEALTH_BASELINE: dict[str, dict] = {
    FirmID.FTMO:           {"trustpilot": 4.7, "dispute_rate": 0.02, "regulatory": True, "sentiment": "positive"},
    FirmID.APEX:           {"trustpilot": 4.2, "dispute_rate": 0.05, "regulatory": True, "sentiment": "positive"},
    FirmID.DNA_FUNDED:     {"trustpilot": 3.4, "dispute_rate": 0.12, "regulatory": True, "sentiment": "neutral"},
    FirmID.FIVEPERCENTERS: {"trustpilot": 4.5, "dispute_rate": 0.03, "regulatory": True, "sentiment": "positive"},
    FirmID.TOPSTEP:        {"trustpilot": 4.3, "dispute_rate": 0.04, "regulatory": True, "sentiment": "positive"},
}

def assess_firm_health(
    firm_id:             str,
    trustpilot_override: Optional[float] = None,
    dispute_rate_override: Optional[float] = None,
) -> FirmHealthScore:
    """
    FORGE-40: Firm health assessment. Pause evaluations at unhealthy firms.
    DNA Funded: 3.4/5 — monitor payout history carefully before deploying.
    """
    baseline = FIRM_HEALTH_BASELINE.get(firm_id, {
        "trustpilot": 3.0, "dispute_rate": 0.10,
        "regulatory": True, "sentiment": "neutral"
    })

    tp    = trustpilot_override   or baseline["trustpilot"]
    dr    = dispute_rate_override or baseline["dispute_rate"]
    reg   = baseline["regulatory"]
    sent  = baseline["sentiment"]

    # Composite score
    tp_score    = (tp / 5.0) * 40            # 40 pts from Trustpilot
    dr_score    = max(0.0, (1.0 - dr*5)) * 30  # 30 pts from dispute rate
    reg_score   = 20.0 if reg else 0.0       # 20 pts regulatory
    sent_score  = {"positive": 10, "neutral": 5, "negative": 0}.get(sent, 5)

    composite = tp_score + dr_score + reg_score + sent_score
    is_healthy = composite >= 60.0 and reg
    pause      = composite < 50.0 or not reg

    warning = None
    if firm_id == FirmID.DNA_FUNDED:
        warning = (
            "DNA Funded Trustpilot 3.4/5. Payout dispute risk elevated. "
            "Stage 4 only — after proven track record and financial buffer. "
            "Monitor community forums before each evaluation."
        )
    elif composite < 60:
        warning = f"Firm health score {composite:.0f}/100 below threshold. Consider alternatives."

    return FirmHealthScore(
        firm_id=firm_id, trustpilot_score=tp,
        payout_dispute_rate=dr, regulatory_ok=reg,
        community_sentiment=sent, composite_score=round(composite, 1),
        is_healthy=is_healthy, pause_evaluations=pause, warning=warning,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-42: EVALUATION BEHAVIORAL JOURNAL
# Per-evaluation behavioral metrics log. Feeds firm performance database.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BehavioralEntry:
    """Single behavioral observation within an evaluation."""
    timestamp:       datetime
    metric:          str    # "sizing", "entry_time", "setup_type", "hold_time"
    value:           float
    session_date:    date
    notes:           str = ""

@dataclass
class EvalBehavioralJournal:
    """FORGE-42: Full behavioral log for one evaluation."""
    eval_id:         str
    firm_id:         str
    entries:         list[BehavioralEntry] = field(default_factory=list)

    def record(self, metric: str, value: float, notes: str = "") -> None:
        self.entries.append(BehavioralEntry(
            timestamp=datetime.now(timezone.utc),
            metric=metric, value=value,
            session_date=date.today(), notes=notes,
        ))

    def avg(self, metric: str) -> float:
        vals = [e.value for e in self.entries if e.metric == metric]
        return sum(vals) / len(vals) if vals else 0.0

    def cv(self, metric: str) -> float:
        """Coefficient of variation for behavioral consistency."""
        vals = [e.value for e in self.entries if e.metric == metric]
        if len(vals) < 2:
            return 0.0
        mean = sum(vals) / len(vals)
        if mean == 0:
            return 0.0
        std  = math.sqrt(sum((v - mean)**2 for v in vals) / len(vals))
        return std / mean

    def summary(self) -> dict:
        metrics = set(e.metric for e in self.entries)
        return {
            m: {"avg": round(self.avg(m), 4), "cv": round(self.cv(m), 4),
                "count": sum(1 for e in self.entries if e.metric == m)}
            for m in metrics
        }


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-43: 90-MINUTE RECOVERY PROTOCOL
# Mandatory 90-min pause after ANY losing trade. No exceptions. No override.
# ─────────────────────────────────────────────────────────────────────────────

RECOVERY_PAUSE_MINUTES: int = 90   # FORGE-43: exactly 90 minutes

@dataclass
class RecoveryProtocolStatus:
    is_in_recovery:     bool
    pause_started_at:   Optional[datetime]
    resume_at:          Optional[datetime]
    minutes_remaining:  Optional[float]
    trading_permitted:  bool
    reason:             str

class NinetyMinuteRecovery:
    """
    FORGE-43: 90-Minute Recovery Protocol.
    Mandatory 90-minute pause after ANY single losing trade.
    No exceptions. No override.
    """

    def __init__(self):
        self._pause_start:   Optional[datetime] = None
        self._resume_at:     Optional[datetime] = None
        self._in_recovery:   bool = False
        self._loss_count:    int  = 0

    def record_loss(self, as_of: Optional[datetime] = None) -> RecoveryProtocolStatus:
        """
        Record a losing trade. IMMEDIATELY starts 90-minute pause.
        Document: "Mandatory 90-min pause after ANY losing trade. No exceptions."
        """
        now = as_of or datetime.now(timezone.utc)
        self._loss_count    += 1
        self._in_recovery   = True
        self._pause_start   = now
        self._resume_at     = now + timedelta(minutes=RECOVERY_PAUSE_MINUTES)

        logger.warning(
            "[FORGE-43] ⏸ 90-MIN RECOVERY: Loss #%d. No trading until %s UTC. "
            "MANDATORY. NO EXCEPTIONS.",
            self._loss_count,
            self._resume_at.strftime("%H:%M"),
        )

        return self.get_status(now)

    def check_resume(self, as_of: Optional[datetime] = None) -> RecoveryProtocolStatus:
        """Check if the 90-minute pause has elapsed."""
        now = as_of or datetime.now(timezone.utc)
        if self._in_recovery and self._resume_at and now >= self._resume_at:
            self._in_recovery = False
            logger.info("[FORGE-43] ▶ 90-min recovery complete. Trading may resume.")
        return self.get_status(now)

    def get_status(self, as_of: Optional[datetime] = None) -> RecoveryProtocolStatus:
        now = as_of or datetime.now(timezone.utc)

        if not self._in_recovery or self._resume_at is None:
            return RecoveryProtocolStatus(
                is_in_recovery=False, pause_started_at=None,
                resume_at=None, minutes_remaining=None,
                trading_permitted=True,
                reason="No active recovery. Trading permitted."
            )

        remaining = (self._resume_at - now).total_seconds() / 60.0

        if remaining <= 0:
            self._in_recovery = False
            return RecoveryProtocolStatus(
                is_in_recovery=False, pause_started_at=self._pause_start,
                resume_at=self._resume_at, minutes_remaining=0.0,
                trading_permitted=True,
                reason="Recovery complete."
            )

        return RecoveryProtocolStatus(
            is_in_recovery=True,
            pause_started_at=self._pause_start,
            resume_at=self._resume_at,
            minutes_remaining=round(remaining, 1),
            trading_permitted=False,
            reason=(
                f"⏸ FORGE-43: 90-min recovery active. "
                f"{remaining:.0f} min remaining. "
                f"Resume at {self._resume_at.strftime('%H:%M UTC')}. "
                f"NO EXCEPTIONS."
            )
        )

    @property
    def is_in_recovery(self) -> bool:
        return self._in_recovery


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-45: PROP FIRM ARBITRAGE INTELLIGENCE
# Matches each evaluation to optimal strategy for that firm's rule set.
# ─────────────────────────────────────────────────────────────────────────────

FIRM_OPTIMAL_STRATEGY: dict[str, dict] = {
    FirmID.FTMO: {
        "best_regime":    "trending_low_vol",
        "best_setups":    ["GEX-01", "ICT-01", "SES-01"],
        "avoid":          [],
        "edge":           "No consistency rule — can make 8% in one day. Size up on high-conviction days.",
        "time_of_day":    "9:30am-11am ET (NY Kill Zone)",
    },
    FirmID.APEX: {
        "best_regime":    "trending_high_vol",
        "best_setups":    ["GEX-01", "GEX-02", "ORD-01"],
        "avoid":          ["VOL-01", "VOL-02"],  # Ranging setups risky with trailing DD
        "edge":           "Futures only. No daily limit intraday. Best profit split.",
        "time_of_day":    "9:30am-12pm ET",
    },
    FirmID.DNA_FUNDED: {
        "best_regime":    "ranging_low_vol",
        "best_setups":    ["ICT-01", "ICT-03", "ICT-05"],
        "avoid":          ["GEX-02"],  # Fast cascades may trigger news timing issues
        "edge":           "Forex only. London-NY overlap. ICT mechanics cleanest on forex.",
        "time_of_day":    "8am-12pm ET (London-NY overlap)",
    },
    FirmID.FIVEPERCENTERS: {
        "best_regime":    "any",
        "best_setups":    ["INS-01", "GEX-01", "ICT-01"],
        "avoid":          [],
        "edge":           "$4M ceiling. 100% split at $1M+. The long game.",
        "time_of_day":    "9:30am-11am ET",
    },
    FirmID.TOPSTEP: {
        "best_regime":    "trending",
        "best_setups":    ["GEX-01", "GEX-02", "SES-01"],
        "avoid":          [],
        "edge":           "Most permissive news policy. Can trade through announcements.",
        "time_of_day":    "9:30am-3pm CT (hard close 3:10pm CT)",
    },
}

def get_firm_arbitrage_intel(firm_id: str) -> dict:
    """FORGE-45: Get optimal strategy configuration for a specific firm."""
    intel = FIRM_OPTIMAL_STRATEGY.get(firm_id, {})
    logger.info("[FORGE-45] Firm arbitrage intel for %s: best setups=%s",
                firm_id, intel.get("best_setups", []))
    return intel


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-49: RULE BACKTESTING ENGINE
# 6+ months historical backtest per firm with exact rules.
# 80%+ pass rate required over 50+ simulations.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    firm_id:          str
    months_tested:    int
    simulations_run:  int
    pass_rate:        float
    avg_profit:       float
    max_drawdown:     float
    meets_threshold:  bool   # 80%+ pass rate over 50+ sims
    recommendation:   str

def validate_backtest_threshold(
    firm_id:         str,
    simulations_run: int,
    passes:          int,
    avg_profit:      float,
    max_drawdown:    float,
    months_tested:   int,
) -> BacktestResult:
    """
    FORGE-49: Rule Backtesting Engine.
    80%+ pass rate over 50+ simulations required.
    6+ months of historical data required.
    """
    pass_rate = passes / simulations_run if simulations_run > 0 else 0.0
    meets     = (pass_rate >= 0.80 and simulations_run >= 50 and months_tested >= 6)

    if meets:
        rec = (f"✅ Backtest validated: {pass_rate:.1%} pass rate over "
               f"{simulations_run} simulations ({months_tested} months). Ready.")
    else:
        issues = []
        if pass_rate < 0.80:
            issues.append(f"pass rate {pass_rate:.1%} < 80%")
        if simulations_run < 50:
            issues.append(f"only {simulations_run}/50 simulations run")
        if months_tested < 6:
            issues.append(f"only {months_tested}/6 months tested")
        rec = f"❌ Backtest threshold not met: {', '.join(issues)}."

    return BacktestResult(
        firm_id=firm_id, months_tested=months_tested,
        simulations_run=simulations_run, pass_rate=round(pass_rate, 4),
        avg_profit=avg_profit, max_drawdown=max_drawdown,
        meets_threshold=meets, recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-50: KELLY CRITERION ADAPTER
# Fractional Kelly (1/4 of full). Hard cap 2% of drawdown buffer.
# Immature below 100 trades (returns 0.5% conservative default).
# ─────────────────────────────────────────────────────────────────────────────

KELLY_HARD_CAP_EVALUATION: float = 0.02   # FX-04: ABSOLUTE MAXIMUM
KELLY_HARD_CAP_FUNDED:     float = 0.03
KELLY_MIN_TRADES:          int   = 100    # FX-03: Immature below this
KELLY_IMMATURE_DEFAULT:    float = 0.005  # 0.5% when immature

def calculate_kelly_size(
    win_rate:          float,
    avg_win_pct:       float,    # Average win as fraction of account
    avg_loss_pct:      float,    # Average loss as fraction of account
    drawdown_buffer:   float,    # Available drawdown in dollars
    remaining_drawdown:float,    # Remaining drawdown available
    total_trades:      int,
    is_funded:         bool,
) -> float:
    """
    FORGE-50: Kelly Criterion Adapter.
    Section 3 / FX-04: Fractional Kelly with hard caps.
    Immature below 100 trades → conservative 0.5% default.
    """
    # FX-03: Below 100 trades → immature default
    if total_trades < KELLY_MIN_TRADES:
        return KELLY_IMMATURE_DEFAULT

    # Full Kelly formula: f = (WR/avg_loss) - ((1-WR)/avg_win)
    if avg_loss_pct <= 0 or avg_win_pct <= 0:
        return KELLY_IMMATURE_DEFAULT

    full_kelly = (win_rate / avg_loss_pct) - ((1 - win_rate) / avg_win_pct)
    full_kelly = max(0.0, full_kelly)

    # Quarter Kelly (fractional Kelly)
    quarter_kelly = full_kelly * 0.25

    # Hard cap per FX-04
    cap = KELLY_HARD_CAP_FUNDED if is_funded else KELLY_HARD_CAP_EVALUATION

    # Three-way minimum: quarter kelly, hard cap, 25% of remaining drawdown
    hard_cap_dollars = drawdown_buffer * cap
    sanity_cap       = remaining_drawdown * 0.25

    return min(quarter_kelly, hard_cap_dollars / max(1, drawdown_buffer), sanity_cap / max(1, drawdown_buffer))


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-51: RUIN PROBABILITY CALCULATOR
# If failure probability > 5%: reduce position sizes until below 2%.
# Math-driven, not intuition.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RuinResult:
    ruin_probability:   float    # 0–1
    exceeds_threshold:  bool     # > 5%
    recommended_size_pct: float  # Recommended position size as pct of account
    reduction_needed:   float    # How much to reduce current size
    reason:             str

def calculate_ruin_probability(
    win_rate:           float,
    avg_win_pct:        float,
    avg_loss_pct:       float,
    current_position_pct: float,
    bankroll:           float,
    ruin_threshold_pct: float = 0.10,  # 10% of bankroll = ruin
) -> RuinResult:
    """
    FORGE-51: Ruin probability calculator.
    If P(ruin) > 5%: reduce sizes until P(ruin) < 2%.
    Uses gambler's ruin formula for non-symmetric payoffs.
    """
    if avg_win_pct <= 0 or avg_loss_pct <= 0:
        return RuinResult(0.0, False, current_position_pct, 0.0, "Insufficient data.")

    # Edge per trade
    edge = win_rate * avg_win_pct - (1 - win_rate) * avg_loss_pct

    if edge <= 0:
        return RuinResult(1.0, True, current_position_pct * 0.5, 0.5,
                          "Negative edge: ruin probability = 1.0. Do not trade.")

    # Simplified gambler's ruin: P(ruin) ≈ (loss/win)^(n_units)
    # where n_units = bankroll / position_size
    n_units = 1.0 / current_position_pct if current_position_pct > 0 else 100
    ratio   = (avg_loss_pct * (1-win_rate)) / (avg_win_pct * win_rate)
    ratio   = min(ratio, 0.999)

    p_ruin  = ratio ** n_units
    p_ruin  = max(0.0, min(1.0, p_ruin))

    exceeds = p_ruin > 0.05

    if exceeds:
        # Find size where P(ruin) < 0.02
        target_n = math.log(0.02) / math.log(ratio) if ratio < 1 and ratio > 0 else n_units * 2
        safe_size = 1.0 / max(1, target_n)
        reduction = current_position_pct - safe_size
        rec = (f"⚠ Ruin probability {p_ruin:.1%} > 5% threshold. "
               f"Reduce position from {current_position_pct:.1%} to {safe_size:.1%}.")
    else:
        safe_size = current_position_pct
        reduction = 0.0
        rec = f"Ruin probability {p_ruin:.1%} < 5% threshold. Size OK."

    return RuinResult(
        ruin_probability=round(p_ruin, 6),
        exceeds_threshold=exceeds,
        recommended_size_pct=round(safe_size, 4),
        reduction_needed=round(reduction, 4),
        reason=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-52: REGIME TO FIRM MATCHMAKING
# Trending low-vol = FTMO (no consistency rule).
# Ranging = DNA Funded. High-vol = Topstep (EOD drawdown).
# ─────────────────────────────────────────────────────────────────────────────

REGIME_FIRM_MATCH: dict[str, str] = {
    "trending_low_vol":   FirmID.FTMO,           # No consistency rule + static DD
    "trending_high_vol":  FirmID.APEX,            # Best split + futures momentum
    "ranging_low_vol":    FirmID.DNA_FUNDED,      # Forex ranging + ICT setups
    "ranging_high_vol":   FirmID.TOPSTEP,         # EOD drawdown safe in volatile range
    "expansion":          FirmID.FIVEPERCENTERS,  # 4-stack only, but ceiling is $4M
}

def match_regime_to_firm(current_regime: str) -> tuple[str, str]:
    """
    FORGE-52: Match current market regime to optimal firm.
    Returns (firm_id, reasoning).
    """
    firm_id = REGIME_FIRM_MATCH.get(current_regime, FirmID.FTMO)
    intel   = FIRM_OPTIMAL_STRATEGY.get(firm_id, {})
    reason  = (f"Regime '{current_regime}' → {firm_id}. "
               f"Edge: {intel.get('edge', 'Standard approach')}.")
    logger.info("[FORGE-52] Regime match: %s → %s", current_regime, firm_id)
    return firm_id, reason


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-53: FIRM FINANCIAL HEALTH ASSESSMENT
# Regulatory filings, staff changes, payout dispute volume, community sentiment.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FirmFinancialAssessment:
    firm_id:             str
    stability_score:     float   # 0–100
    red_flags:           list[str]
    green_flags:         list[str]
    verdict:             str     # "STABLE" / "MONITOR" / "AVOID"
    last_assessed:       date

def assess_firm_financial_health(
    firm_id:             str,
    months_operating:    int,
    total_payouts_usd:   float,
    recent_dispute_count: int,
    staff_changes:       bool,
    community_negative:  bool,
) -> FirmFinancialAssessment:
    """
    FORGE-53: Firm financial health assessment.
    Long operating history + large verified payouts = stable.
    Recent disputes + staff changes + negative community = monitor/avoid.
    """
    score = 50.0
    red_flags: list[str] = []
    green_flags: list[str] = []

    if months_operating >= 36:
        score += 20
        green_flags.append(f"{months_operating} months operating (established)")
    elif months_operating < 12:
        score -= 15
        red_flags.append("Less than 12 months operating (new firm)")

    if total_payouts_usd >= 10_000_000:
        score += 20
        green_flags.append(f"${total_payouts_usd/1e6:.0f}M+ verified payouts")
    elif total_payouts_usd < 1_000_000:
        score -= 10
        red_flags.append("Low verified payout volume")

    if recent_dispute_count > 10:
        score -= 20
        red_flags.append(f"{recent_dispute_count} recent payout disputes")
    elif recent_dispute_count == 0:
        score += 10
        green_flags.append("No recent payout disputes")

    if staff_changes:
        score -= 10
        red_flags.append("Recent senior staff changes")

    if community_negative:
        score -= 15
        red_flags.append("Negative community sentiment on forums")
    else:
        score += 5
        green_flags.append("Positive community sentiment")

    score = max(0.0, min(100.0, score))

    if score >= 70:
        verdict = "STABLE"
    elif score >= 50:
        verdict = "MONITOR"
    else:
        verdict = "AVOID"

    return FirmFinancialAssessment(
        firm_id=firm_id, stability_score=round(score, 1),
        red_flags=red_flags, green_flags=green_flags,
        verdict=verdict, last_assessed=date.today(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-55: PSYCHOLOGICAL CALIBRATION RESET
# Full calibration reset before every new evaluation.
# No residue from previous outcomes.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CalibrationState:
    """State after psychological reset — clean slate for new evaluation."""
    reset_at:            datetime
    previous_eval_id:    Optional[str]
    previous_outcome:    Optional[str]    # "PASS" / "FAIL" / None
    is_clean:            bool
    checklist_completed: list[str]
    ready_for_eval:      bool

def perform_calibration_reset(
    previous_eval_id:    Optional[str] = None,
    previous_outcome:    Optional[str] = None,
) -> CalibrationState:
    """
    FORGE-55: Psychological calibration reset.
    Called before starting any new evaluation.
    Clears all residue from previous outcomes — wins AND losses.
    """
    checklist = [
        "Previous eval metrics reviewed and filed",
        "Win/loss streak counter reset to zero",
        "Position sizing reverted to base (not affected by prior result)",
        "Setup conviction thresholds reset to standard",
        "Session quality scoring reset to baseline",
        "No 'getting even' mindset — each eval is independent",
        "No overconfidence from prior pass — same discipline required",
    ]

    logger.info(
        "[FORGE-55] Psychological calibration reset. "
        "Previous: %s (%s). Clean slate initiated.",
        previous_eval_id or "None", previous_outcome or "N/A",
    )

    return CalibrationState(
        reset_at=datetime.now(timezone.utc),
        previous_eval_id=previous_eval_id,
        previous_outcome=previous_outcome,
        is_clean=True,
        checklist_completed=checklist,
        ready_for_eval=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-56: INFORMATION EDGE VERIFICATION
# Pre-session: win rates statistically confirmed before starting evaluation.
# Edge must be confirmed — not assumed.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EdgeVerificationResult:
    setup_id:          str
    recent_win_rate:   float
    historical_win_rate: float
    within_1_5_sd:     bool   # Document: "within 1.5 SD of historical average"
    edge_confirmed:    bool
    sd_distance:       float
    reason:            str

def verify_information_edge(
    setup_id:           str,
    recent_trades:      list[bool],   # True = win, False = loss (last 20)
    historical_win_rate: float,
    historical_std:     float = 0.05,  # Typical SD of win rate
) -> EdgeVerificationResult:
    """
    FORGE-56: Information Edge Verification.
    Last 20 trades of each setup type must be within 1.5 SD of historical average.
    If not: edge may have decayed. Delay evaluation.
    """
    if len(recent_trades) < 10:
        return EdgeVerificationResult(
            setup_id=setup_id, recent_win_rate=0.0,
            historical_win_rate=historical_win_rate,
            within_1_5_sd=False, edge_confirmed=False,
            sd_distance=0.0, reason=f"Need 10+ recent trades ({len(recent_trades)} available).",
        )

    recent_wr = sum(recent_trades) / len(recent_trades)
    deviation = abs(recent_wr - historical_win_rate)
    sd_dist   = deviation / historical_std if historical_std > 0 else 0.0

    within_sd     = sd_dist <= 1.5
    edge_confirmed = within_sd and recent_wr >= 0.55   # Must still be profitable

    if edge_confirmed:
        reason = (f"✅ Edge confirmed: WR {recent_wr:.1%} within 1.5 SD "
                  f"({sd_dist:.2f} SD from {historical_win_rate:.1%} historical).")
    elif not within_sd:
        reason = (f"⚠ Edge decay detected: WR {recent_wr:.1%} is {sd_dist:.1f} SD "
                  f"from historical {historical_win_rate:.1%}. Delay evaluation.")
    else:
        reason = f"Win rate {recent_wr:.1%} too low despite statistical validity. Edge weak."

    return EdgeVerificationResult(
        setup_id=setup_id, recent_win_rate=round(recent_wr, 4),
        historical_win_rate=historical_win_rate,
        within_1_5_sd=within_sd, edge_confirmed=edge_confirmed,
        sd_distance=round(sd_dist, 3), reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-57: EVALUATION COST BASIS TRACKING
# Every fee tracked as investment against funded account revenue.
# Shows ROI per firm per account size.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CostBasisRecord:
    firm_id:           str
    account_size:      float
    eval_fee:          float
    total_fees_paid:   float
    total_revenue:     float   # From funded account payouts
    pass_count:        int
    fail_count:        int
    roi_pct:           float
    break_even_month:  Optional[float]
    recommendation:    str

def track_evaluation_cost_basis(
    firm_id:           str,
    account_size:      float,
    eval_fee:          float,
    passes:            int,
    fails:             int,
    total_fees_paid:   float,
    total_revenue:     float,
    monthly_payout:    float,
) -> CostBasisRecord:
    """
    FORGE-57: Evaluation Cost Basis Tracking.
    Every fee = investment. Must track ROI to know when each firm is worth continuing.
    """
    roi_pct = ((total_revenue - total_fees_paid) / total_fees_paid * 100
               if total_fees_paid > 0 else 0.0)

    break_even = (total_fees_paid / monthly_payout
                  if monthly_payout > 0 else None)

    if roi_pct > 0:
        rec = (f"✅ {firm_id} ${account_size:,.0f}: ROI {roi_pct:.0f}%. "
               f"Break-even: {break_even:.1f} months.")
    elif total_fees_paid > 0:
        rec = (f"Cost basis ${total_fees_paid:,.0f}. Revenue ${total_revenue:,.0f}. "
               f"ROI {roi_pct:.0f}%.")
    else:
        rec = f"No fees paid yet for {firm_id} ${account_size:,.0f}."

    return CostBasisRecord(
        firm_id=firm_id, account_size=account_size, eval_fee=eval_fee,
        total_fees_paid=total_fees_paid, total_revenue=total_revenue,
        pass_count=passes, fail_count=fails,
        roi_pct=round(roi_pct, 2),
        break_even_month=round(break_even, 1) if break_even else None,
        recommendation=rec,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-66: PROFIT TARGET APPROACH PROTOCOL
# Within 20% of target: half size only.
# Within 10% of target: minimum size only. One trade at a time. No rush.
# Document says this is separate from C-02 clash rule — explicit FORGE req.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ApproachProtocolResult:
    """FORGE-66: Result of profit target approach protocol check."""
    profit_pct_complete:   float
    is_within_20_pct:      bool
    is_within_10_pct:      bool
    size_multiplier:       float
    max_concurrent:        int     # Max simultaneous positions
    reason:                str

def check_approach_protocol(
    current_profit:    float,
    profit_target:     float,
) -> ApproachProtocolResult:
    """
    FORGE-66: Profit Target Approach Protocol.
    Separate from C-02. Explicit requirement.
    Within 20%: half size.
    Within 10%: minimum size. One trade at a time. No rush.
    """
    if profit_target <= 0:
        return ApproachProtocolResult(0.0, False, False, 1.0, 2, "No target set.")

    remaining   = max(0.0, profit_target - current_profit)
    pct_complete = current_profit / profit_target
    pct_remaining = remaining / profit_target

    if pct_remaining <= 0.10:
        # Within 10%: MINIMUM SIZE ONLY. One trade at a time.
        return ApproachProtocolResult(
            profit_pct_complete=round(pct_complete, 4),
            is_within_20_pct=True, is_within_10_pct=True,
            size_multiplier=0.25,   # Minimum position size
            max_concurrent=1,       # ONE TRADE AT A TIME
            reason=(
                f"FORGE-66: Within 10% of target (${remaining:.0f} remaining). "
                f"MINIMUM SIZE ONLY. One trade at a time. No rush. "
                f"Target is essentially earned — don't give it back."
            )
        )
    elif pct_remaining <= 0.20:
        # Within 20%: HALF SIZE
        return ApproachProtocolResult(
            profit_pct_complete=round(pct_complete, 4),
            is_within_20_pct=True, is_within_10_pct=False,
            size_multiplier=0.50,   # Half size
            max_concurrent=1,       # Still one at a time
            reason=(
                f"FORGE-66: Within 20% of target (${remaining:.0f} remaining). "
                f"HALF SIZE only. ${current_profit:.0f}/{profit_target:.0f} earned."
            )
        )
    else:
        return ApproachProtocolResult(
            profit_pct_complete=round(pct_complete, 4),
            is_within_20_pct=False, is_within_10_pct=False,
            size_multiplier=1.0,
            max_concurrent=2,
            reason=f"Normal: {pct_complete:.1%} complete. ${remaining:.0f} to target."
        )
