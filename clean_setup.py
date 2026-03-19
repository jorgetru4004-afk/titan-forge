"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   clean_setup.py — FORGE-10 — Layer 1                       ║
║                                                                              ║
║  CLEAN SETUP REQUIREMENT                                                     ║
║  No chasing. No extended entries. No counter-trend.                          ║
║  Waits for setup to come to it. Consistency over explosiveness.              ║
║                                                                              ║
║  Three hard rules. All must pass. One failure = dirty = no trade.            ║
║                                                                              ║
║  Rule 1 — NO CHASING                                                         ║
║    Entry must be within acceptable distance from the setup trigger.          ║
║    If price has moved more than 2× ATR from the intended entry               ║
║    (or setup-specific threshold), the setup is stale. Wait for next.         ║
║                                                                              ║
║  Rule 2 — NO EXTENDED ENTRIES                                                ║
║    Price must not be extended beyond institutional reference points.         ║
║    VWAP distance, distance from session open, ATR-normalized extension.      ║
║    Extended entries have poor R:R because the mean is far below.             ║
║                                                                              ║
║  Rule 3 — NO COUNTER-TREND                                                   ║
║    Entry direction must align with the identified session bias.              ║
║    GEX direction, market structure, prior swing confirmation.                ║
║    During evaluation: counter-trend entries require explicit override        ║
║    justification (mean reversion setups are exempt by design).               ║
║                                                                              ║
║  Setup type exemptions:                                                      ║
║    Mean reversion setups (GEX-05, VOL-01/02, SES-03) are by design          ║
║    counter-trend — they receive a counter-trend pass.                        ║
║    Liquidity sweep setups (ICT-02/05) are designed to enter after a          ║
║    move — they receive a reduced chase threshold.                             ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.clean_setup")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# No-Chase Rule: max ATR multiples from intended entry before setup is stale
CHASE_THRESHOLD_STANDARD:      float = 0.50   # 0.5× ATR — standard setups
CHASE_THRESHOLD_MOMENTUM:      float = 1.00   # 1.0× ATR — momentum/breakout setups
CHASE_THRESHOLD_SWEEP:         float = 1.50   # 1.5× ATR — liquidity sweep setups
CHASE_THRESHOLD_RANGE:         float = 0.75   # 0.75× ATR — range/reversion setups

# No-Extension Rule: max ATR multiples from VWAP before price is "extended"
EXTENSION_THRESHOLD_STANDARD:  float = 2.00   # 2× ATR from VWAP
EXTENSION_THRESHOLD_MOMENTUM:  float = 3.00   # Momentum can extend further
EXTENSION_THRESHOLD_REVERSION: float = 1.50   # Reversion setups need to be close to mean

# Trend alignment: minimum trend score (0–1) required for trend-following entries
TREND_ALIGNMENT_MINIMUM:       float = 0.60   # 60%+ aligned
TREND_ALIGNMENT_STRICT:        float = 0.75   # Used during evaluation Phase 1

# Setup types that are intrinsically counter-trend (exempt from Rule 3)
COUNTER_TREND_EXEMPT_SETUPS: frozenset[str] = frozenset({
    "GEX-05",   # Charm Decay Fade — mean reversion
    "GEX-03",   # GEX Pin — mean reversion when GEX positive
    "VOL-01",   # POC Magnetic Revert
    "VOL-02",   # Value Area Edge Fade
    "SES-03",   # First Hour Reversal
    "ICT-06",   # Premium/Discount Zone Filter — can be counter-trend
    "ICT-07",   # FVG Inversion Play
})

# Liquidity sweep setups (relaxed chase threshold — designed to enter after a move)
SWEEP_SETUPS: frozenset[str] = frozenset({
    "ICT-02",   # Liquidity Sweep and Reverse
    "ICT-05",   # Asian Range Raid and Reverse
    "ICT-04",   # Breaker Block Retest
})

# Momentum/breakout setups (relaxed extension tolerance)
MOMENTUM_SETUPS: frozenset[str] = frozenset({
    "GEX-01",   # Gamma Flip Breakout
    "GEX-02",   # Dealer Hedging Cascade
    "ICT-08",   # Market Structure Break + OTE
    "ORD-03",   # Order Block Stacking Breakout
    "ORD-04",   # Bid/Ask Imbalance Cascade
})


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — ENTRY PROPOSAL
# All the data needed to validate a proposed setup entry.
# ─────────────────────────────────────────────────────────────────────────────

class SessionBias(Enum):
    """The identified session directional bias."""
    STRONGLY_BULLISH = "strongly_bullish"
    BULLISH          = "bullish"
    NEUTRAL          = "neutral"
    BEARISH          = "bearish"
    STRONGLY_BEARISH = "strongly_bearish"


@dataclass
class EntryProposal:
    """
    A proposed trade entry awaiting clean-setup validation.
    All fields needed to evaluate the three clean setup rules.
    """
    setup_id:           str            # e.g. "GEX-01"
    direction:          str            # "long" or "short"
    # Price context
    current_price:      float          # Current market price
    intended_entry:     float          # Price where setup was identified
    atr:                float          # Average True Range for this instrument
    vwap:               float          # Session VWAP
    session_open:       float          # Price at session open
    # Trend context
    session_bias:       SessionBias    # Identified session directional bias
    trend_score:        float          # 0.0–1.0: how aligned price action is with bias
    # GEX context
    gex_confirms_direction: bool       # True = GEX regime supports this direction
    # Setup-specific
    is_evaluation:      bool = True
    is_retrace_entry:   bool = False   # True = entering on pullback (better R:R)
    time_since_trigger_minutes: float = 0.0  # How long ago the setup triggered


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — RULE RESULTS
# ─────────────────────────────────────────────────────────────────────────────

class CleanRuleVerdict(Enum):
    PASS    = "PASS"
    FAIL    = "FAIL"
    EXEMPT  = "EXEMPT"   # Setup type is exempt from this specific rule


@dataclass
class RuleResult:
    """Result of a single clean-setup rule check."""
    rule_name:    str
    verdict:      CleanRuleVerdict
    value:        float          # The measured value (e.g., chase distance in ATR)
    threshold:    float          # The threshold applied
    reason:       str


@dataclass
class CleanSetupResult:
    """
    Complete clean-setup validation result.
    All three rules must PASS or EXEMPT for the setup to be clean.
    """
    setup_id:               str
    direction:              str
    is_clean:               bool
    # Rule results
    no_chase_rule:          RuleResult
    no_extension_rule:      RuleResult
    no_counter_trend_rule:  RuleResult
    # Summary
    failing_rules:          list[str]
    reason:                 str

    @property
    def all_rules_pass(self) -> bool:
        return self.is_clean

    @property
    def failure_count(self) -> int:
        return len(self.failing_rules)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — THE CLEAN SETUP FILTER
# FORGE-10. Validates setup quality before execution.
# ─────────────────────────────────────────────────────────────────────────────

class CleanSetupFilter:
    """
    FORGE-10: Clean Setup Requirement.

    Validates three rules before any setup is executed:
        1. No Chasing — entry within acceptable ATR distance from trigger
        2. No Extended — price not too far from institutional reference (VWAP)
        3. No Counter-Trend — direction aligned with session bias (with exemptions)

    All three must pass. One failure = no trade. Wait for the next setup.

    Usage:
        filt = CleanSetupFilter()

        result = filt.validate(EntryProposal(
            setup_id="GEX-01", direction="long",
            current_price=4810.0, intended_entry=4805.0,
            atr=15.0, vwap=4800.0, session_open=4795.0,
            session_bias=SessionBias.BULLISH, trend_score=0.72,
            gex_confirms_direction=True,
        ))

        if not result.is_clean:
            # Don't trade. Log the reason.
            logger.warning(result.reason)
            return
    """

    def __init__(
        self,
        chase_multiplier:    float = 1.0,   # Scale all chase thresholds
        extension_multiplier: float = 1.0,  # Scale all extension thresholds
        strict_trend:        bool  = False,  # Use stricter trend alignment
    ):
        self._chase_scale = chase_multiplier
        self._ext_scale   = extension_multiplier
        self._strict      = strict_trend
        self._validated_count = 0
        self._clean_count     = 0

    # ── MAIN VALIDATOR ────────────────────────────────────────────────────────

    def validate(self, proposal: EntryProposal) -> CleanSetupResult:
        """
        Validate a proposed entry against all three clean-setup rules.

        Args:
            proposal: Complete entry proposal with price and context data.

        Returns:
            CleanSetupResult — check .is_clean before executing.
        """
        self._validated_count += 1

        r1 = self._check_no_chase(proposal)
        r2 = self._check_no_extension(proposal)
        r3 = self._check_no_counter_trend(proposal)

        failing = []
        for r in [r1, r2, r3]:
            if r.verdict == CleanRuleVerdict.FAIL:
                failing.append(r.rule_name)

        is_clean = len(failing) == 0
        if is_clean:
            self._clean_count += 1

        # Build summary reason
        if is_clean:
            reason = (
                f"✅ CLEAN: {proposal.setup_id} {proposal.direction} | "
                f"Chase: {r1.value:.2f}/{r1.threshold:.2f}ATR ✓ | "
                f"Extension: {r2.value:.2f}/{r2.threshold:.2f}ATR ✓ | "
                f"Trend: {r3.verdict.value}"
            )
        else:
            failed_details = " | ".join(
                f"{r.rule_name}: {r.reason}"
                for r in [r1, r2, r3]
                if r.verdict == CleanRuleVerdict.FAIL
            )
            reason = f"❌ DIRTY: {proposal.setup_id} | {failed_details}"

        log_fn = logger.info if is_clean else logger.warning
        log_fn("[FORGE-10] %s", reason)

        return CleanSetupResult(
            setup_id=proposal.setup_id,
            direction=proposal.direction,
            is_clean=is_clean,
            no_chase_rule=r1,
            no_extension_rule=r2,
            no_counter_trend_rule=r3,
            failing_rules=failing,
            reason=reason,
        )

    # ── RULE 1: NO CHASING ────────────────────────────────────────────────────

    def _check_no_chase(self, p: EntryProposal) -> RuleResult:
        """
        Rule 1: Price must not have moved too far from the intended entry.

        Measures: |current_price - intended_entry| / ATR
        Liquidity sweep setups get a larger threshold (designed to enter after a move).
        Retrace entries always pass (entering on pullback is optimal).
        """
        rule_name = "NO_CHASE"

        # Retrace entries are inherently non-chasing
        if p.is_retrace_entry:
            return RuleResult(
                rule_name=rule_name, verdict=CleanRuleVerdict.PASS,
                value=0.0, threshold=0.0,
                reason="Retrace entry — by definition not chasing."
            )

        if p.atr <= 0:
            return RuleResult(
                rule_name=rule_name, verdict=CleanRuleVerdict.PASS,
                value=0.0, threshold=0.0,
                reason="ATR not available — chase rule skipped."
            )

        # Distance from intended entry in ATR units
        chase_distance = abs(p.current_price - p.intended_entry) / p.atr

        # Select threshold based on setup type
        if p.setup_id in SWEEP_SETUPS:
            threshold = CHASE_THRESHOLD_SWEEP * self._chase_scale
        elif p.setup_id in MOMENTUM_SETUPS:
            threshold = CHASE_THRESHOLD_MOMENTUM * self._chase_scale
        elif p.setup_id in COUNTER_TREND_EXEMPT_SETUPS:
            threshold = CHASE_THRESHOLD_RANGE * self._chase_scale
        else:
            threshold = CHASE_THRESHOLD_STANDARD * self._chase_scale

        verdict = CleanRuleVerdict.PASS if chase_distance <= threshold else CleanRuleVerdict.FAIL
        reason = (
            f"Chase: {chase_distance:.2f}ATR from intended entry "
            f"({'within' if verdict == CleanRuleVerdict.PASS else 'exceeds'} {threshold:.2f}ATR limit)"
        )

        return RuleResult(
            rule_name=rule_name, verdict=verdict,
            value=round(chase_distance, 4),
            threshold=threshold,
            reason=reason,
        )

    # ── RULE 2: NO EXTENDED ENTRIES ───────────────────────────────────────────

    def _check_no_extension(self, p: EntryProposal) -> RuleResult:
        """
        Rule 2: Price must not be extended from VWAP.

        Measures: |current_price - vwap| / ATR
        Extended entries have poor R:R — mean is far away, stop must be wide.
        Momentum setups get more tolerance (breakouts extend away from VWAP).
        Mean-reversion setups need to be CLOSE to VWAP for optimal entry.
        """
        rule_name = "NO_EXTENSION"

        if p.atr <= 0 or p.vwap <= 0:
            return RuleResult(
                rule_name=rule_name, verdict=CleanRuleVerdict.PASS,
                value=0.0, threshold=0.0,
                reason="VWAP or ATR not available — extension rule skipped."
            )

        vwap_distance = abs(p.current_price - p.vwap) / p.atr

        # Select threshold based on setup type
        if p.setup_id in MOMENTUM_SETUPS:
            threshold = EXTENSION_THRESHOLD_MOMENTUM * self._ext_scale
        elif p.setup_id in COUNTER_TREND_EXEMPT_SETUPS:
            # Mean reversion: must be close to an extreme (AWAY from VWAP is good!)
            # Invert: we want it to be extended — that's the signal
            # Return EXEMPT for mean reversion setups on extension rule
            return RuleResult(
                rule_name=rule_name, verdict=CleanRuleVerdict.EXEMPT,
                value=round(vwap_distance, 4), threshold=0.0,
                reason=f"Mean reversion setup — extension from VWAP is the setup condition. EXEMPT."
            )
        else:
            threshold = EXTENSION_THRESHOLD_STANDARD * self._ext_scale

        verdict = CleanRuleVerdict.PASS if vwap_distance <= threshold else CleanRuleVerdict.FAIL
        reason = (
            f"Extension: {vwap_distance:.2f}ATR from VWAP "
            f"({'within' if verdict == CleanRuleVerdict.PASS else 'exceeds'} {threshold:.2f}ATR limit)"
        )

        return RuleResult(
            rule_name=rule_name, verdict=verdict,
            value=round(vwap_distance, 4),
            threshold=threshold,
            reason=reason,
        )

    # ── RULE 3: NO COUNTER-TREND ──────────────────────────────────────────────

    def _check_no_counter_trend(self, p: EntryProposal) -> RuleResult:
        """
        Rule 3: Direction must align with the session bias.

        Counter-trend exempt setups (mean reversion) receive EXEMPT verdict.
        For all other setups: direction must be aligned with identified bias.
        Strict mode (evaluation) requires 0.75+ alignment score.
        """
        rule_name = "NO_COUNTER_TREND"

        # Exempt setups are counter-trend by design
        if p.setup_id in COUNTER_TREND_EXEMPT_SETUPS:
            return RuleResult(
                rule_name=rule_name, verdict=CleanRuleVerdict.EXEMPT,
                value=p.trend_score, threshold=0.0,
                reason=f"{p.setup_id} is a mean-reversion setup — counter-trend EXEMPT."
            )

        # Determine if direction aligns with session bias
        bias_aligned = self._direction_aligns_with_bias(p.direction, p.session_bias)

        # Neutral bias — neither long nor short is counter-trend
        if p.session_bias == SessionBias.NEUTRAL:
            return RuleResult(
                rule_name=rule_name, verdict=CleanRuleVerdict.PASS,
                value=p.trend_score, threshold=0.0,
                reason="Session bias is NEUTRAL — any direction permitted."
            )

        if not bias_aligned:
            return RuleResult(
                rule_name=rule_name, verdict=CleanRuleVerdict.FAIL,
                value=p.trend_score, threshold=TREND_ALIGNMENT_MINIMUM,
                reason=(
                    f"Counter-trend: {p.direction} against {p.session_bias.value} bias. "
                    f"Wait for alignment or use an exempt mean-reversion setup."
                )
            )

        # Aligned but check trend score threshold
        threshold = TREND_ALIGNMENT_STRICT if (p.is_evaluation and self._strict) else TREND_ALIGNMENT_MINIMUM
        verdict = CleanRuleVerdict.PASS if p.trend_score >= threshold else CleanRuleVerdict.FAIL

        reason = (
            f"Direction aligned with {p.session_bias.value}. "
            f"Trend score: {p.trend_score:.2f} "
            f"({'≥' if verdict == CleanRuleVerdict.PASS else '<'} {threshold:.2f} threshold)."
        )

        # GEX confirmation bonus note
        if p.gex_confirms_direction and verdict == CleanRuleVerdict.PASS:
            reason += " GEX confirms direction ✓"

        return RuleResult(
            rule_name=rule_name, verdict=verdict,
            value=round(p.trend_score, 4),
            threshold=threshold,
            reason=reason,
        )

    # ── UTILITIES ────────────────────────────────────────────────────────────

    @staticmethod
    def _direction_aligns_with_bias(direction: str, bias: SessionBias) -> bool:
        """True if the proposed direction is consistent with the session bias."""
        d = direction.lower()
        if bias in (SessionBias.BULLISH, SessionBias.STRONGLY_BULLISH):
            return d == "long"
        elif bias in (SessionBias.BEARISH, SessionBias.STRONGLY_BEARISH):
            return d == "short"
        return True  # NEUTRAL — both directions are fine

    @property
    def clean_rate(self) -> float:
        """Fraction of proposals that passed as clean setups."""
        if self._validated_count == 0:
            return 0.0
        return self._clean_count / self._validated_count

    def summary(self) -> dict:
        """Summary statistics for ARCHITECT dashboard."""
        return {
            "validated":   self._validated_count,
            "clean":       self._clean_count,
            "dirty":       self._validated_count - self._clean_count,
            "clean_rate":  round(self.clean_rate, 4),
        }

    def validate_batch(
        self, proposals: list[EntryProposal]
    ) -> list[CleanSetupResult]:
        """Validate multiple proposals at once."""
        return [self.validate(p) for p in proposals]

    def get_clean_proposals(
        self, proposals: list[EntryProposal]
    ) -> list[EntryProposal]:
        """Return only proposals that pass all clean-setup rules."""
        return [
            p for p, r in zip(proposals, self.validate_batch(proposals))
            if r.is_clean
        ]


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — CONVENIENCE BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def make_entry_proposal(
    setup_id:           str,
    direction:          str,
    current_price:      float,
    intended_entry:     float,
    atr:                float,
    vwap:               float,
    session_bias:       SessionBias = SessionBias.NEUTRAL,
    trend_score:        float       = 0.70,
    session_open:       float       = 0.0,
    gex_confirms:       bool        = True,
    is_evaluation:      bool        = True,
    is_retrace:         bool        = False,
    minutes_since_trigger: float    = 0.0,
) -> EntryProposal:
    return EntryProposal(
        setup_id=setup_id,
        direction=direction,
        current_price=current_price,
        intended_entry=intended_entry,
        atr=atr,
        vwap=vwap,
        session_open=session_open or current_price - atr,
        session_bias=session_bias,
        trend_score=trend_score,
        gex_confirms_direction=gex_confirms,
        is_evaluation=is_evaluation,
        is_retrace_entry=is_retrace,
        time_since_trigger_minutes=minutes_since_trigger,
    )
