"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                 opportunity_scoring.py — FORGE-58 — Layer 2                 ║
║  OPPORTUNITY SCORING ENGINE                                                  ║
║  Scores BOTH profit potential AND rule compliance.                           ║
║  Only executes if above threshold on BOTH.                                   ║
║  Level 5 STRATEGY — only fires when all higher levels permit.               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.opportunity_scoring")

# Minimum scores required to execute
MIN_PROFIT_SCORE:     float = 60.0   # Both must be above
MIN_COMPLIANCE_SCORE: float = 60.0
EXCELLENT_THRESHOLD:  float = 80.0
HIGH_THRESHOLD:       float = 70.0


@dataclass
class OpportunityScore:
    """
    FORGE-58: Dual-gate opportunity score.
    Both profit_score AND compliance_score must exceed threshold.
    """
    setup_id:           str
    firm_id:            str
    # Dual scores
    profit_score:       float   # 0–100: profit potential
    compliance_score:   float   # 0–100: rule compliance quality
    # Combined
    composite_score:    float   # Weighted combination
    execute_approved:   bool    # Both gates passed?
    # Components
    win_rate_score:     float
    rr_ratio_score:     float
    setup_quality_score:float
    catalyst_score:     float
    rule_risk_score:    float   # How safely this trade fits in the evaluation
    # Sizing
    conviction_level:   str   # "MAXIMUM", "HIGH", "STANDARD", "REDUCED"
    size_multiplier:    float
    reason:             str

    @property
    def is_high_conviction(self) -> bool:
        return self.composite_score >= HIGH_THRESHOLD and self.execute_approved


def score_opportunity(
    setup_id:               str,
    firm_id:                str,
    win_rate:               float,       # Historical win rate (0–1)
    avg_rr:                 float,       # Average reward:risk
    session_quality:        float,       # 0–10 from FORGE-08
    catalyst_stack:         int,         # Stack score from FORGE-22
    drawdown_pct_used:      float,       # How much drawdown is consumed (0–1)
    days_remaining:         Optional[int],
    profit_pct_complete:    float,       # Progress toward target (0–1)
    is_evaluation:          bool = True,
    rule_violation_risk:    float = 0.0, # 0–1: how close to firm rule limits
) -> OpportunityScore:
    """
    FORGE-58: Score a trading opportunity.
    Both profit potential AND rule compliance must score ≥ 60.
    """
    # ── Profit potential score ────────────────────────────────────────────────
    # Win rate contribution (40%)
    wr_score    = min(100.0, (win_rate - 0.50) / 0.30 * 100)
    wr_score    = max(0.0, wr_score)
    # R:R contribution (30%)
    rr_score    = min(100.0, (avg_rr / 3.0) * 100)
    # Session quality contribution (20%)
    sq_score    = session_quality * 10.0
    # Catalyst score (10%)
    cat_score   = min(100.0, (catalyst_stack / 4.0) * 100)

    profit_score = (
        wr_score  * 0.40 +
        rr_score  * 0.30 +
        sq_score  * 0.20 +
        cat_score * 0.10
    )

    # ── Rule compliance score ─────────────────────────────────────────────────
    # Drawdown buffer health (40%)
    dd_score    = max(0.0, (1.0 - drawdown_pct_used) * 100)
    # Days remaining cushion (30%)
    if days_remaining is not None:
        day_score = min(100.0, (days_remaining / 15.0) * 100)
    else:
        day_score = 80.0   # No deadline = generous

    # Progress toward target (20%) — don't approach target too aggressively
    target_dist = 1.0 - profit_pct_complete
    dist_score  = max(0.0, min(100.0, target_dist * 120))   # Penalize near target

    # Rule violation risk (10%) — inverse
    rule_score  = max(0.0, (1.0 - rule_violation_risk) * 100)

    compliance_score = (
        dd_score   * 0.40 +
        day_score  * 0.30 +
        dist_score * 0.20 +
        rule_score * 0.10
    )

    # ── Composite ─────────────────────────────────────────────────────────────
    composite = (profit_score * 0.55 + compliance_score * 0.45)

    # ── Execution gate ────────────────────────────────────────────────────────
    # BOTH gates must pass
    execute = (profit_score >= MIN_PROFIT_SCORE and
               compliance_score >= MIN_COMPLIANCE_SCORE)

    # ── Conviction level ──────────────────────────────────────────────────────
    if composite >= EXCELLENT_THRESHOLD and execute:
        conviction   = "MAXIMUM"
        size_mult    = 1.00
    elif composite >= HIGH_THRESHOLD and execute:
        conviction   = "HIGH"
        size_mult    = 0.85
    elif execute:
        conviction   = "STANDARD"
        size_mult    = 0.70
    else:
        conviction   = "REDUCED"
        size_mult    = 0.00   # Not approved

    # ── Reason ────────────────────────────────────────────────────────────────
    if execute:
        reason = (f"✅ {conviction}: Score {composite:.0f}/100 | "
                  f"Profit {profit_score:.0f} ✓ | Compliance {compliance_score:.0f} ✓")
    elif profit_score < MIN_PROFIT_SCORE:
        reason = (f"❌ Profit score {profit_score:.0f} < {MIN_PROFIT_SCORE:.0f} minimum. "
                  f"Setup not attractive enough.")
    else:
        reason = (f"❌ Compliance score {compliance_score:.0f} < {MIN_COMPLIANCE_SCORE:.0f}. "
                  f"Rule environment not safe for entry.")

    return OpportunityScore(
        setup_id=setup_id, firm_id=firm_id,
        profit_score=round(profit_score, 2),
        compliance_score=round(compliance_score, 2),
        composite_score=round(composite, 2),
        execute_approved=execute,
        win_rate_score=round(wr_score, 2),
        rr_ratio_score=round(rr_score, 2),
        setup_quality_score=round(sq_score, 2),
        catalyst_score=round(cat_score, 2),
        rule_risk_score=round(rule_score, 2),
        conviction_level=conviction,
        size_multiplier=size_mult,
        reason=reason,
    )
