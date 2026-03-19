"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                  eval_funded_toggle.py — FORGE-05 — Layer 1                 ║
║                                                                              ║
║  EVALUATION VS FUNDED TOGGLE                                                 ║
║  Automatic rule set switch when evaluation passes and funded status          ║
║  is confirmed. Two completely separate rule databases. No half-states.       ║
║                                                                              ║
║  Critical distinction:                                                       ║
║    Evaluation PASSED ≠ FUNDED                                                ║
║    Evaluation passing is necessary but NOT sufficient.                       ║
║    The firm must confirm funding before funded rules activate.               ║
║    Operating on unconfirmed funded rules = catastrophic compliance risk.     ║
║                                                                              ║
║  Account lifecycle states:                                                   ║
║    EVALUATION → AWAITING_CONFIRMATION → FUNDED → [SCALING] → RETIRED        ║
║                                                                              ║
║  What changes at the transition:                                             ║
║    • Position sizing rules (drawdown buffer recalculates)                    ║
║    • News blackout windows (FTMO funded: 2-min restriction activates)        ║
║    • Funded-only restrictions (DNA: no scalping/grid/martingale)             ║
║    • Payout optimization (extract capital efficiently)                       ║
║    • Profit targets (no longer exist — now it's about preservation + payout) ║
║    • Risk parameters (funded mode uses funded-specific drawdown rules)       ║
║                                                                              ║
║  Integrates with:                                                            ║
║    • EvaluationStateMachine (FORGE-02) — detects PASSED state               ║
║    • MultiFirmRuleEngine (FORGE-01) — separate rule sets per phase           ║
║    • clash_rules.py AccountState — is_funded flag updates immediately        ║
║    • FORGE-93 Funded Rule Set Switcher — partner module for full lifecycle   ║
║    • FORGE-33 Payout Alert System — notified on funding confirmation         ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum, auto
from typing import Optional

from firm_rules import FirmID, FirmRules, MultiFirmRuleEngine, AccountPhase

logger = logging.getLogger("titan_forge.eval_funded_toggle")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — ACCOUNT LIFECYCLE STATES
# ─────────────────────────────────────────────────────────────────────────────

class AccountLifecycleState(Enum):
    """
    Full lifecycle of a prop firm account.
    Transitions are one-way — no going back.
    EVALUATION → AWAITING_CONFIRMATION → FUNDED → SCALING → RETIRED
    Failure is terminal: EVALUATION → FAILED
    """
    EVALUATION            = auto()   # Active challenge — evaluation rules apply
    AWAITING_CONFIRMATION = auto()   # Eval PASSED — waiting for firm to confirm funding
    FUNDED                = auto()   # Firm confirmed — funded rules now active
    SCALING               = auto()   # Hit scaling milestone — higher account, funded rules
    RETIRED               = auto()   # Account lifecycle complete (Apex 6-payout max, etc.)
    FAILED                = auto()   # Evaluation failed — terminal


class TransitionTrigger(Enum):
    """What caused the lifecycle state to change."""
    PROFIT_TARGET_HIT           = auto()   # Eval target reached
    FIRM_CONFIRMATION_RECEIVED  = auto()   # Firm confirmed funding via API/manual
    SCALING_MILESTONE_REACHED   = auto()   # Hit the scaling threshold
    ACCOUNT_RETIRED_NATURAL     = auto()   # Natural end of lifecycle (Apex 6 payouts)
    ACCOUNT_FAILED              = auto()   # Drawdown breached during evaluation
    MANUAL_OVERRIDE             = auto()   # Jorge manually triggered (emergency only)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — RULE SET SNAPSHOT
# What rule set is active RIGHT NOW. Rebuilt on every transition.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ActiveRuleSet:
    """
    The complete picture of which rules are active at this moment.
    Switches atomically on transition — never a partial state.
    """
    account_id:                 str
    firm_id:                    str
    lifecycle_state:            AccountLifecycleState
    account_phase:              str              # "EVALUATION" or "FUNDED"
    is_funded:                  bool             # Direct flag for clash_rules.py
    # Drawdown rules
    drawdown_type:              str
    total_drawdown_pct:         float
    daily_drawdown_pct:         Optional[float]
    firm_floor_dollars:         float            # Calculated firm floor
    # Position sizing limits
    minimum_position_size:      float
    maximum_position_size:      float
    # News blackout (changes between eval and funded for some firms)
    news_blackout_before_min:   int
    news_blackout_after_min:    int
    # Funded-only restrictions
    no_scalping:                bool
    no_grid:                    bool
    no_martingale:              bool
    min_hold_seconds:           Optional[int]
    requires_ea_approval:       bool
    # Profit / payout mode
    has_profit_target:          bool             # False in funded mode
    profit_target_pct:          Optional[float]  # None in funded mode
    payout_optimization_active: bool             # True in funded mode
    # Scaling
    scaling_eligible:           bool
    scaling_trigger_months:     Optional[int]
    # Timestamps
    activated_at:               datetime
    previous_state:             Optional[AccountLifecycleState]
    transition_trigger:         Optional[TransitionTrigger]
    # Notes
    rule_set_label:             str              # "EVALUATION" or "FUNDED - [firm]"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — TRANSITION RECORD
# Immutable audit trail of every rule set switch.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TransitionRecord:
    """Immutable audit record of a lifecycle state change."""
    account_id:         str
    firm_id:            str
    from_state:         AccountLifecycleState
    to_state:           AccountLifecycleState
    trigger:            TransitionTrigger
    timestamp:          datetime
    confirmed_by:       str          # "AUTO" or "JORGE" for manual overrides
    notes:              str
    # What changed
    is_funded_before:   bool
    is_funded_after:    bool
    account_phase_before: str
    account_phase_after:  str


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — THE TOGGLE ENGINE
# FORGE-05. The atomic switch between evaluation and funded rule sets.
# ─────────────────────────────────────────────────────────────────────────────

class EvalFundedToggle:
    """
    FORGE-05: Evaluation vs Funded Toggle.

    Manages the lifecycle of a single prop firm account.
    Switches the active rule set atomically on each transition.

    Evaluation PASSED ≠ FUNDED. Two separate steps:
        1. eval_passed()     → AWAITING_CONFIRMATION
        2. confirm_funded()  → FUNDED (firm has confirmed, rules now switch)

    Usage:
        toggle = EvalFundedToggle(
            account_id="FTMO-001",
            firm_id=FirmID.FTMO,
            account_size=100_000.0,
            rule_engine=rule_engine,
        )

        # When evaluation state machine reports PASSED:
        toggle.eval_passed()

        # When firm API confirms funding (or Jorge confirms manually):
        toggle.confirm_funded()

        # Check current rule set before every decision:
        rule_set = toggle.current_rule_set
        if rule_set.is_funded:
            # Apply funded rules — news blackout, no-scalping, etc.
        if not rule_set.has_profit_target:
            # Preservation + payout mode — no target to chase
    """

    def __init__(
        self,
        account_id:     str,
        firm_id:        str,
        account_size:   float,
        rule_engine:    MultiFirmRuleEngine,
        start_date:     Optional[date] = None,
    ):
        self.account_id     = account_id
        self.firm_id        = firm_id
        self.account_size   = account_size
        self._rule_engine   = rule_engine
        self._rules         = rule_engine.get_firm_rules(firm_id)
        self._start_date    = start_date or date.today()

        # Initialize in EVALUATION state
        self._state         = AccountLifecycleState.EVALUATION
        self._transitions:  list[TransitionRecord] = []
        self._rule_set      = self._build_evaluation_rule_set()

        logger.info(
            "[FORGE-05][%s] Toggle initialized. Firm: %s. Size: $%s. "
            "State: EVALUATION. Rules: %s.",
            account_id, firm_id, f"{account_size:,.0f}",
            self._rule_set.rule_set_label,
        )

    # ── PROPERTIES ────────────────────────────────────────────────────────────

    @property
    def state(self) -> AccountLifecycleState:
        return self._state

    @property
    def current_rule_set(self) -> ActiveRuleSet:
        return self._rule_set

    @property
    def is_funded(self) -> bool:
        return self._rule_set.is_funded

    @property
    def account_phase(self) -> str:
        return self._rule_set.account_phase

    @property
    def transitions(self) -> list[TransitionRecord]:
        return list(self._transitions)

    @property
    def is_terminal(self) -> bool:
        return self._state in (
            AccountLifecycleState.RETIRED,
            AccountLifecycleState.FAILED,
        )

    # ── LIFECYCLE TRANSITIONS ─────────────────────────────────────────────────

    def eval_passed(self, as_of: Optional[datetime] = None) -> TransitionRecord:
        """
        Called when EvaluationStateMachine reports PASSED.
        Transitions EVALUATION → AWAITING_CONFIRMATION.

        NOTE: Does NOT activate funded rules yet.
        Funded rules activate only after confirm_funded() is called.
        Operating on funded rules before confirmation = compliance violation.
        """
        if self._state != AccountLifecycleState.EVALUATION:
            raise RuntimeError(
                f"[FORGE-05][{self.account_id}] eval_passed() called from state "
                f"{self._state.name}. Expected EVALUATION."
            )

        now = as_of or datetime.now(timezone.utc)
        record = self._transition(
            to_state=AccountLifecycleState.AWAITING_CONFIRMATION,
            trigger=TransitionTrigger.PROFIT_TARGET_HIT,
            confirmed_by="AUTO",
            notes=(
                f"Evaluation PASSED at {now.isoformat()}. "
                f"Awaiting firm confirmation before funded rules activate. "
                f"Still operating on EVALUATION rules. Do NOT assume funded status."
            ),
            as_of=now,
        )

        logger.info(
            "[FORGE-05][%s] 🎯 Evaluation PASSED → AWAITING_CONFIRMATION. "
            "Rules remain EVALUATION until firm confirms. Waiting for Jorge/firm confirmation.",
            self.account_id,
        )
        return record

    def confirm_funded(
        self,
        confirmed_by:           str = "AUTO",
        confirmation_reference: str = "",
        as_of:                  Optional[datetime] = None,
    ) -> TransitionRecord:
        """
        Called when the firm confirms funding (via API response or Jorge manually).
        Transitions AWAITING_CONFIRMATION → FUNDED.

        THIS is the moment funded rules activate. Not before.

        Args:
            confirmed_by:           "AUTO" (API) or "JORGE" (manual confirmation).
            confirmation_reference: Firm's reference number or "manual".
            as_of:                  Timestamp of confirmation.
        """
        if self._state != AccountLifecycleState.AWAITING_CONFIRMATION:
            raise RuntimeError(
                f"[FORGE-05][{self.account_id}] confirm_funded() called from state "
                f"{self._state.name}. Expected AWAITING_CONFIRMATION."
            )

        now = as_of or datetime.now(timezone.utc)

        # ── ATOMIC SWITCH: evaluation rules → funded rules ────────────────────
        old_rule_set = self._rule_set
        self._rule_set = self._build_funded_rule_set(now)

        record = self._transition(
            to_state=AccountLifecycleState.FUNDED,
            trigger=TransitionTrigger.FIRM_CONFIRMATION_RECEIVED,
            confirmed_by=confirmed_by,
            notes=(
                f"Funding confirmed by {confirmed_by}. "
                f"Reference: {confirmation_reference or 'N/A'}. "
                f"FUNDED rules now active. "
                f"Key changes: is_funded=True, news_blackout={self._rules.news_blackout_minutes_before}min, "
                f"no_scalping={self._rule_set.no_scalping}, "
                f"payout_optimization=ACTIVE. "
                f"Profit target REMOVED — now preservation + payout mode."
            ),
            as_of=now,
        )

        logger.warning(
            "[FORGE-05][%s] ⚡ FUNDED CONFIRMED. Rule set switched: "
            "EVALUATION → FUNDED [%s]. "
            "is_funded=True. Payout optimization: ACTIVE. "
            "News blackout: %dmin. No scalping: %s.",
            self.account_id,
            self._rules.display_name,
            self._rules.news_blackout_minutes_before,
            self._rule_set.no_scalping,
        )

        # Verify the switch was clean — no half-state possible
        self._assert_clean_transition(old_rule_set)

        return record

    def mark_scaling_milestone(
        self,
        new_account_size:   float,
        as_of:              Optional[datetime] = None,
    ) -> TransitionRecord:
        """
        Called when a funded account hits a scaling milestone.
        FUNDED → SCALING. Updates the account size and recalculates floors.
        """
        if self._state not in (AccountLifecycleState.FUNDED, AccountLifecycleState.SCALING):
            raise RuntimeError(
                f"[FORGE-05][{self.account_id}] Scaling requires FUNDED or SCALING state. "
                f"Current: {self._state.name}"
            )

        now = as_of or datetime.now(timezone.utc)
        old_size = self.account_size
        self.account_size = new_account_size

        # Rebuild rule set with new account size
        self._rule_set = self._build_funded_rule_set(now)

        record = self._transition(
            to_state=AccountLifecycleState.SCALING,
            trigger=TransitionTrigger.SCALING_MILESTONE_REACHED,
            confirmed_by="AUTO",
            notes=(
                f"Scaling milestone reached. "
                f"Account size: ${old_size:,.0f} → ${new_account_size:,.0f}. "
                f"New floor: ${self._rule_set.firm_floor_dollars:,.2f}. "
                f"Rule set rebuilt with updated parameters."
            ),
            as_of=now,
        )

        logger.info(
            "[FORGE-05][%s] 📈 SCALING MILESTONE. Size: $%s → $%s. "
            "New floor: $%s.",
            self.account_id,
            f"{old_size:,.0f}", f"{new_account_size:,.0f}",
            f"{self._rule_set.firm_floor_dollars:,.2f}",
        )
        return record

    def retire(
        self,
        reason: str = "Natural lifecycle end",
        as_of:  Optional[datetime] = None,
    ) -> TransitionRecord:
        """
        Retire the account (Apex 6-payout max, FTMO cap reached, etc.).
        FUNDED/SCALING → RETIRED.
        """
        if self._state not in (
            AccountLifecycleState.FUNDED, AccountLifecycleState.SCALING
        ):
            raise RuntimeError(
                f"[FORGE-05][{self.account_id}] Cannot retire from state {self._state.name}."
            )

        now = as_of or datetime.now(timezone.utc)
        record = self._transition(
            to_state=AccountLifecycleState.RETIRED,
            trigger=TransitionTrigger.ACCOUNT_RETIRED_NATURAL,
            confirmed_by="AUTO",
            notes=reason,
            as_of=now,
        )

        logger.info(
            "[FORGE-05][%s] 🏁 Account RETIRED. Reason: %s. "
            "Start replacement evaluation before lifecycle gap.",
            self.account_id, reason,
        )
        return record

    def mark_failed(
        self,
        reason: str,
        as_of:  Optional[datetime] = None,
    ) -> TransitionRecord:
        """
        Mark account as failed (drawdown breached during evaluation).
        EVALUATION → FAILED. Terminal.
        """
        if self._state != AccountLifecycleState.EVALUATION:
            raise RuntimeError(
                f"[FORGE-05][{self.account_id}] mark_failed() requires EVALUATION state. "
                f"Current: {self._state.name}"
            )

        now = as_of or datetime.now(timezone.utc)
        record = self._transition(
            to_state=AccountLifecycleState.FAILED,
            trigger=TransitionTrigger.ACCOUNT_FAILED,
            confirmed_by="AUTO",
            notes=f"Evaluation failed: {reason}. "
                  f"Initiate Failure Fast-Forward protocol (FX-10): "
                  f"root cause in 4 hours, fix in 24, validated in 48, new eval in 72.",
            as_of=now,
        )

        logger.error(
            "[FORGE-05][%s] ❌ Evaluation FAILED: %s. "
            "FX-10: RCA in 4h, fix in 24h, validate in 48h, new eval in 72h.",
            self.account_id, reason,
        )
        return record

    # ── RULE SET BUILDERS ─────────────────────────────────────────────────────

    def _build_evaluation_rule_set(self) -> ActiveRuleSet:
        """Build the evaluation-mode rule set from firm rules."""
        rules = self._rules
        floor = self.account_size * (1.0 - rules.total_drawdown_pct)

        return ActiveRuleSet(
            account_id=self.account_id,
            firm_id=self.firm_id,
            lifecycle_state=AccountLifecycleState.EVALUATION,
            account_phase=AccountPhase.EVALUATION,
            is_funded=False,
            drawdown_type=rules.drawdown_type.value,
            total_drawdown_pct=rules.total_drawdown_pct,
            daily_drawdown_pct=rules.daily_drawdown_pct,
            firm_floor_dollars=floor,
            minimum_position_size=rules.minimum_position_size,
            maximum_position_size=rules.maximum_position_size,
            # Evaluation: news restriction minimal (FTMO eval = no restriction)
            news_blackout_before_min=0 if rules.firm_id == FirmID.FTMO else rules.news_blackout_minutes_before,
            news_blackout_after_min=0 if rules.firm_id == FirmID.FTMO else rules.news_blackout_minutes_after,
            # No funded restrictions during evaluation
            no_scalping=False,
            no_grid=False,
            no_martingale=False,
            min_hold_seconds=None,
            requires_ea_approval=False,
            # Evaluation: chasing the target
            has_profit_target=True,
            profit_target_pct=rules.profit_target_phase1_pct,
            payout_optimization_active=False,
            # Scaling info
            scaling_eligible=False,
            scaling_trigger_months=rules.scaling_trigger_months,
            activated_at=datetime.now(timezone.utc),
            previous_state=None,
            transition_trigger=None,
            rule_set_label=f"EVALUATION — {rules.display_name}",
        )

    def _build_funded_rule_set(self, activated_at: datetime) -> ActiveRuleSet:
        """Build the funded-mode rule set from firm rules."""
        rules = self._rules
        floor = self.account_size * (1.0 - rules.total_drawdown_pct)

        return ActiveRuleSet(
            account_id=self.account_id,
            firm_id=self.firm_id,
            lifecycle_state=AccountLifecycleState.FUNDED,
            account_phase=AccountPhase.FUNDED,
            is_funded=True,
            drawdown_type=rules.drawdown_type.value,
            total_drawdown_pct=rules.total_drawdown_pct,
            daily_drawdown_pct=rules.daily_drawdown_pct,
            firm_floor_dollars=floor,
            minimum_position_size=rules.minimum_position_size,
            maximum_position_size=rules.maximum_position_size,
            # FUNDED: full news restrictions now apply (FTMO 2-min rule activates)
            news_blackout_before_min=rules.news_blackout_minutes_before,
            news_blackout_after_min=rules.news_blackout_minutes_after,
            # FUNDED: firm-specific restrictions
            no_scalping=rules.funded_no_scalping,
            no_grid=rules.funded_no_grid,
            no_martingale=rules.funded_no_martingale,
            min_hold_seconds=rules.funded_min_hold_seconds,
            requires_ea_approval=rules.funded_requires_ea_approval,
            # FUNDED: no target — preservation + payout mode
            has_profit_target=False,
            profit_target_pct=None,
            payout_optimization_active=True,
            # Scaling eligibility
            scaling_eligible=rules.has_scaling_plan,
            scaling_trigger_months=rules.scaling_trigger_months,
            activated_at=activated_at,
            previous_state=AccountLifecycleState.AWAITING_CONFIRMATION,
            transition_trigger=TransitionTrigger.FIRM_CONFIRMATION_RECEIVED,
            rule_set_label=f"FUNDED — {rules.display_name}",
        )

    # ── TRANSITION RECORD ─────────────────────────────────────────────────────

    def _transition(
        self,
        to_state:       AccountLifecycleState,
        trigger:        TransitionTrigger,
        confirmed_by:   str,
        notes:          str,
        as_of:          datetime,
    ) -> TransitionRecord:
        """Execute a lifecycle state transition and record it immutably."""
        from_state = self._state
        was_funded = self._rule_set.is_funded
        was_phase  = self._rule_set.account_phase

        # Update state
        self._state = to_state

        # Build the record BEFORE rebuilding rule_set (captures the before values)
        record = TransitionRecord(
            account_id=self.account_id,
            firm_id=self.firm_id,
            from_state=from_state,
            to_state=to_state,
            trigger=trigger,
            timestamp=as_of,
            confirmed_by=confirmed_by,
            notes=notes,
            is_funded_before=was_funded,
            is_funded_after=self._rule_set.is_funded,   # May be same until confirm_funded
            account_phase_before=was_phase,
            account_phase_after=self._rule_set.account_phase,
        )
        self._transitions.append(record)
        return record

    def _assert_clean_transition(self, old_rule_set: ActiveRuleSet) -> None:
        """
        Safety assertion: verify the rule set switch was clean.
        No half-states allowed. Either fully EVALUATION or fully FUNDED.
        """
        new = self._rule_set
        # If we just confirmed funding, new must be is_funded=True
        if self._state == AccountLifecycleState.FUNDED:
            assert new.is_funded is True, "FUNDED state requires is_funded=True"
            assert new.account_phase == AccountPhase.FUNDED, "FUNDED requires FUNDED phase"
            assert new.has_profit_target is False, "FUNDED mode has no profit target"
            assert new.payout_optimization_active is True, "FUNDED requires payout optimization"
            assert old_rule_set.is_funded is False, "Pre-transition must have been EVALUATION"
            logger.debug(
                "[FORGE-05][%s] ✅ Clean transition verified. "
                "No half-states. Rule set fully FUNDED.",
                self.account_id,
            )

    # ── UTILITIES ─────────────────────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Compact summary for ARCHITECT and FORGE-31 Dashboard."""
        rs = self._rule_set
        return {
            "account_id":               self.account_id,
            "firm_id":                  self.firm_id,
            "account_size":             self.account_size,
            "lifecycle_state":          self._state.name,
            "account_phase":            rs.account_phase,
            "is_funded":                rs.is_funded,
            "rule_set":                 rs.rule_set_label,
            "has_profit_target":        rs.has_profit_target,
            "payout_optimization":      rs.payout_optimization_active,
            "news_blackout_before_min": rs.news_blackout_before_min,
            "no_scalping":              rs.no_scalping,
            "no_grid":                  rs.no_grid,
            "no_martingale":            rs.no_martingale,
            "scaling_eligible":         rs.scaling_eligible,
            "transition_count":         len(self._transitions),
            "is_terminal":              self.is_terminal,
        }

    def print_transition_log(self) -> None:
        """Print the full transition audit trail to the logger."""
        logger.info(
            "[FORGE-05][%s] Transition log (%d entries):",
            self.account_id, len(self._transitions),
        )
        for i, t in enumerate(self._transitions, 1):
            logger.info(
                "  [%d] %s → %s | Trigger: %s | By: %s | %s",
                i, t.from_state.name, t.to_state.name,
                t.trigger.name, t.confirmed_by,
                t.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC"),
            )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — TOGGLE REGISTRY
# Manages multiple toggles — one per active account across all firms.
# ─────────────────────────────────────────────────────────────────────────────

class ToggleRegistry:
    """
    Registry of all active EvalFundedToggle instances.
    One toggle per account. Thread-safe for read operations.

    Usage:
        registry = ToggleRegistry(rule_engine)
        account_id = registry.register("FTMO-001", FirmID.FTMO, 100_000.0)
        registry.eval_passed(account_id)
        registry.confirm_funded(account_id, confirmed_by="JORGE")
        summary = registry.get_summary(account_id)
    """

    def __init__(self, rule_engine: MultiFirmRuleEngine):
        self._rule_engine = rule_engine
        self._toggles: dict[str, EvalFundedToggle] = {}

    def register(
        self,
        account_id:   str,
        firm_id:      str,
        account_size: float,
        start_date:   Optional[date] = None,
    ) -> str:
        """Register a new account. Returns the account_id."""
        if account_id in self._toggles:
            raise ValueError(
                f"Account '{account_id}' already registered. "
                f"Use get(account_id) to access it."
            )
        toggle = EvalFundedToggle(
            account_id=account_id,
            firm_id=firm_id,
            account_size=account_size,
            rule_engine=self._rule_engine,
            start_date=start_date,
        )
        self._toggles[account_id] = toggle
        logger.info(
            "[FORGE-05][Registry] Registered: %s | %s | $%s",
            account_id, firm_id, f"{account_size:,.0f}"
        )
        return account_id

    def get(self, account_id: str) -> EvalFundedToggle:
        if account_id not in self._toggles:
            raise KeyError(f"Account '{account_id}' not found in registry.")
        return self._toggles[account_id]

    def eval_passed(self, account_id: str, **kwargs) -> TransitionRecord:
        return self.get(account_id).eval_passed(**kwargs)

    def confirm_funded(self, account_id: str, **kwargs) -> TransitionRecord:
        return self.get(account_id).confirm_funded(**kwargs)

    def mark_scaling_milestone(self, account_id: str, **kwargs) -> TransitionRecord:
        return self.get(account_id).mark_scaling_milestone(**kwargs)

    def retire(self, account_id: str, **kwargs) -> TransitionRecord:
        return self.get(account_id).retire(**kwargs)

    def get_summary(self, account_id: str) -> dict:
        return self.get(account_id).get_summary()

    def all_funded(self) -> list[EvalFundedToggle]:
        return [t for t in self._toggles.values()
                if t.state == AccountLifecycleState.FUNDED]

    def all_active(self) -> list[EvalFundedToggle]:
        return [t for t in self._toggles.values() if not t.is_terminal]

    def awaiting_confirmation(self) -> list[EvalFundedToggle]:
        """Accounts that have PASSED but not yet been confirmed funded — alert Jorge."""
        return [t for t in self._toggles.values()
                if t.state == AccountLifecycleState.AWAITING_CONFIRMATION]

    @property
    def funded_count(self) -> int:
        return len(self.all_funded())

    @property
    def evaluation_count(self) -> int:
        return sum(1 for t in self._toggles.values()
                   if t.state == AccountLifecycleState.EVALUATION)

    @property
    def awaiting_count(self) -> int:
        return len(self.awaiting_confirmation())

    def full_status(self) -> list[dict]:
        return [t.get_summary() for t in self._toggles.values()]
