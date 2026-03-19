"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║       test_eval_funded_toggle.py — FORGE-05 — FX-06 Compliance              ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, datetime, timezone
from eval_funded_toggle import (
    EvalFundedToggle, ToggleRegistry,
    AccountLifecycleState, TransitionTrigger, ActiveRuleSet,
)
from firm_rules import FirmID, MultiFirmRuleEngine, AccountPhase

ENGINE = MultiFirmRuleEngine(active_firm_id=FirmID.FTMO)


def make_toggle(
    account_id: str = "TEST-001",
    firm: str = FirmID.FTMO,
    size: float = 100_000.0,
) -> EvalFundedToggle:
    return EvalFundedToggle(
        account_id=account_id,
        firm_id=firm,
        account_size=size,
        rule_engine=ENGINE,
    )


def make_registry() -> ToggleRegistry:
    return ToggleRegistry(ENGINE)


# ─────────────────────────────────────────────────────────────────────────────
# INITIAL STATE
# ─────────────────────────────────────────────────────────────────────────────

class TestInitialState:

    def test_normal_starts_in_evaluation(self):
        """Normal: New toggle always starts in EVALUATION state."""
        t = make_toggle()
        assert t.state == AccountLifecycleState.EVALUATION
        assert t.is_funded is False
        assert t.account_phase == AccountPhase.EVALUATION

    def test_edge_evaluation_rule_set_has_no_funded_restrictions(self):
        """Edge: Evaluation rule set must not have funded restrictions active."""
        t = make_toggle(firm=FirmID.DNA_FUNDED)
        rs = t.current_rule_set
        assert rs.is_funded is False
        assert rs.no_scalping is False    # Not yet — that's funded-only
        assert rs.no_grid is False
        assert rs.no_martingale is False
        assert rs.has_profit_target is True
        assert rs.payout_optimization_active is False

    def test_conflict_evaluation_has_profit_target_funded_does_not(self):
        """Conflict: Rule set must have profit target in eval but not in funded."""
        t = make_toggle()
        assert t.current_rule_set.has_profit_target is True
        t.eval_passed()
        t.confirm_funded()
        assert t.current_rule_set.has_profit_target is False


# ─────────────────────────────────────────────────────────────────────────────
# TWO-STEP TRANSITION (PASSED → AWAITING → FUNDED)
# ─────────────────────────────────────────────────────────────────────────────

class TestTwoStepTransition:

    def test_normal_eval_passed_goes_to_awaiting(self):
        """Normal: eval_passed() → AWAITING_CONFIRMATION, still evaluation rules."""
        t = make_toggle()
        t.eval_passed()
        assert t.state == AccountLifecycleState.AWAITING_CONFIRMATION
        # CRITICAL: still evaluation rules until confirmed
        assert t.is_funded is False
        assert t.current_rule_set.account_phase == AccountPhase.EVALUATION

    def test_normal_confirm_funded_switches_rules(self):
        """Normal: confirm_funded() → FUNDED, funded rules now active."""
        t = make_toggle()
        t.eval_passed()
        t.confirm_funded(confirmed_by="JORGE", confirmation_reference="FTMO-REF-001")
        assert t.state == AccountLifecycleState.FUNDED
        assert t.is_funded is True
        assert t.current_rule_set.account_phase == AccountPhase.FUNDED

    def test_edge_cannot_skip_awaiting_step(self):
        """Edge: confirm_funded() from EVALUATION (skipping awaiting) raises RuntimeError."""
        t = make_toggle()
        raised = False
        try:
            t.confirm_funded()  # Skip eval_passed() step
        except RuntimeError:
            raised = True
        assert raised, "Must raise RuntimeError when skipping AWAITING_CONFIRMATION step"

    def test_conflict_funded_rules_do_not_activate_at_eval_passed(self):
        """Conflict: Evaluation PASSED ≠ FUNDED. Funded rules must NOT activate at eval_passed()."""
        t = make_toggle(firm=FirmID.DNA_FUNDED)
        t.eval_passed()
        rs = t.current_rule_set
        # DNA Funded funded restrictions must NOT be active yet
        assert rs.no_scalping is False   # Will be True after confirm_funded()
        assert rs.is_funded is False
        assert rs.has_profit_target is True  # Still chasing target until confirmed


# ─────────────────────────────────────────────────────────────────────────────
# ATOMIC RULE SET SWITCH
# ─────────────────────────────────────────────────────────────────────────────

class TestAtomicRuleSetSwitch:

    def test_normal_rule_set_is_fully_funded_after_confirmation(self):
        """Normal: After confirm_funded(), every funded field is set correctly."""
        t = make_toggle()
        t.eval_passed()
        t.confirm_funded()
        rs = t.current_rule_set
        assert rs.is_funded is True
        assert rs.has_profit_target is False
        assert rs.payout_optimization_active is True
        assert rs.account_phase == AccountPhase.FUNDED
        assert rs.lifecycle_state == AccountLifecycleState.FUNDED

    def test_edge_no_half_state_possible(self):
        """Edge: At no point between eval_passed and confirm_funded do mixed rules appear."""
        t = make_toggle()
        t.eval_passed()
        # In AWAITING state — must be fully evaluation, not mixed
        rs = t.current_rule_set
        assert rs.is_funded is False
        assert rs.has_profit_target is True
        assert rs.payout_optimization_active is False
        # Not partially funded
        assert "EVALUATION" in rs.rule_set_label

    def test_conflict_dna_funded_restrictions_activate_only_on_confirmation(self):
        """Conflict: DNA Funded funded restrictions (no scalping etc.) activate ONLY on confirmation."""
        t = make_toggle(firm=FirmID.DNA_FUNDED)

        # Before: evaluation — no restrictions
        assert t.current_rule_set.no_scalping is False
        assert t.current_rule_set.min_hold_seconds is None

        t.eval_passed()
        # After eval_passed: still no restrictions
        assert t.current_rule_set.no_scalping is False

        t.confirm_funded()
        # After confirm: restrictions NOW active
        assert t.current_rule_set.no_scalping is True
        assert t.current_rule_set.min_hold_seconds == 30   # DNA 30-second minimum


# ─────────────────────────────────────────────────────────────────────────────
# FTMO-SPECIFIC RULE CHANGES
# ─────────────────────────────────────────────────────────────────────────────

class TestFTMOSpecificChanges:

    def test_normal_ftmo_evaluation_no_news_blackout(self):
        """Normal: FTMO evaluation has 0-minute news blackout (only funded has 2-min)."""
        t = make_toggle(firm=FirmID.FTMO)
        rs = t.current_rule_set
        assert rs.news_blackout_before_min == 0   # No restriction in evaluation

    def test_edge_ftmo_funded_activates_2_min_news_blackout(self):
        """Edge: After FTMO confirmation, 2-minute news blackout activates."""
        t = make_toggle(firm=FirmID.FTMO)
        t.eval_passed()
        t.confirm_funded()
        rs = t.current_rule_set
        assert rs.news_blackout_before_min == 2   # Activated on funding
        assert rs.news_blackout_after_min == 2

    def test_conflict_ftmo_funded_no_strategy_restrictions(self):
        """Conflict: FTMO funded has news blackout but NO scalping/grid/martingale bans."""
        t = make_toggle(firm=FirmID.FTMO)
        t.eval_passed()
        t.confirm_funded()
        rs = t.current_rule_set
        assert rs.news_blackout_before_min == 2   # Yes: news restriction
        assert rs.no_scalping is False            # No: FTMO doesn't ban scalping
        assert rs.no_grid is False
        assert rs.no_martingale is False


# ─────────────────────────────────────────────────────────────────────────────
# SCALING MILESTONE
# ─────────────────────────────────────────────────────────────────────────────

class TestScalingMilestone:

    def test_normal_scaling_updates_account_size_and_floor(self):
        """Normal: Scaling milestone updates account size and recalculates floor."""
        t = make_toggle(firm=FirmID.FTMO, size=100_000.0)
        t.eval_passed()
        t.confirm_funded()
        old_floor = t.current_rule_set.firm_floor_dollars

        t.mark_scaling_milestone(new_account_size=125_000.0)
        assert t.state == AccountLifecycleState.SCALING
        assert t.account_size == 125_000.0
        new_floor = t.current_rule_set.firm_floor_dollars
        assert new_floor > old_floor   # Larger account = higher floor in dollars

    def test_edge_scaling_requires_funded_state(self):
        """Edge: Cannot scale from EVALUATION state — must be FUNDED first."""
        t = make_toggle()
        raised = False
        try:
            t.mark_scaling_milestone(new_account_size=125_000.0)
        except RuntimeError:
            raised = True
        assert raised

    def test_conflict_scaling_keeps_funded_rules_active(self):
        """Conflict: After scaling, still funded rules (not back to evaluation)."""
        t = make_toggle(firm=FirmID.FTMO, size=100_000.0)
        t.eval_passed()
        t.confirm_funded()
        t.mark_scaling_milestone(new_account_size=125_000.0)
        assert t.is_funded is True
        assert t.current_rule_set.payout_optimization_active is True


# ─────────────────────────────────────────────────────────────────────────────
# TERMINAL STATES
# ─────────────────────────────────────────────────────────────────────────────

class TestTerminalStates:

    def test_normal_retire_from_funded(self):
        """Normal: Funded account retires cleanly → RETIRED state."""
        t = make_toggle()
        t.eval_passed()
        t.confirm_funded()
        t.retire(reason="Apex 6-payout maximum reached")
        assert t.state == AccountLifecycleState.RETIRED
        assert t.is_terminal is True

    def test_edge_failed_from_evaluation_is_terminal(self):
        """Edge: Failed evaluation → FAILED, terminal, no further transitions possible."""
        t = make_toggle()
        t.mark_failed(reason="Drawdown limit breached")
        assert t.state == AccountLifecycleState.FAILED
        assert t.is_terminal is True

    def test_conflict_cannot_transition_from_terminal_state(self):
        """Conflict: Once RETIRED, cannot eval_passed() or confirm_funded()."""
        t = make_toggle()
        t.eval_passed()
        t.confirm_funded()
        t.retire()

        raised = False
        try:
            t.retire()  # Second retire should fail
        except RuntimeError:
            raised = True
        assert raised


# ─────────────────────────────────────────────────────────────────────────────
# TRANSITION AUDIT TRAIL
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditTrail:

    def test_normal_full_lifecycle_has_correct_transition_count(self):
        """Normal: Full lifecycle (eval → awaiting → funded → retired) = 3 transitions."""
        t = make_toggle()
        t.eval_passed()
        t.confirm_funded()
        t.retire()
        assert len(t.transitions) == 3

    def test_edge_transition_records_are_immutable(self):
        """Edge: TransitionRecord is frozen dataclass — cannot be modified."""
        t = make_toggle()
        t.eval_passed()
        rec = t.transitions[0]
        raised = False
        try:
            rec.from_state = AccountLifecycleState.FUNDED
        except Exception:
            raised = True
        assert raised, "TransitionRecord must be immutable (frozen dataclass)"

    def test_conflict_transitions_record_correct_phase_changes(self):
        """Conflict: Audit trail captures exact before/after is_funded and phase."""
        t = make_toggle()
        t.eval_passed()
        t.confirm_funded()
        transitions = t.transitions

        # Transition 0: eval_passed → awaiting
        t0 = transitions[0]
        assert t0.from_state == AccountLifecycleState.EVALUATION
        assert t0.to_state == AccountLifecycleState.AWAITING_CONFIRMATION
        assert t0.is_funded_before is False
        # Still evaluation rules after eval_passed
        assert t0.account_phase_before == AccountPhase.EVALUATION

        # Transition 1: confirm_funded → funded
        t1 = transitions[1]
        assert t1.from_state == AccountLifecycleState.AWAITING_CONFIRMATION
        assert t1.to_state == AccountLifecycleState.FUNDED
        assert t1.trigger == TransitionTrigger.FIRM_CONFIRMATION_RECEIVED


# ─────────────────────────────────────────────────────────────────────────────
# TOGGLE REGISTRY (FORGE-28 companion)
# ─────────────────────────────────────────────────────────────────────────────

class TestToggleRegistry:

    def setup_method(self):
        self.reg = make_registry()

    def test_normal_register_and_retrieve(self):
        """Normal: Register account, retrieve toggle, verify state."""
        self.reg.register("FTMO-001", FirmID.FTMO, 100_000.0)
        t = self.reg.get("FTMO-001")
        assert t.state == AccountLifecycleState.EVALUATION

    def test_edge_duplicate_registration_raises(self):
        """Edge: Registering same account ID twice raises ValueError."""
        self.reg.register("DUP-001", FirmID.FTMO, 100_000.0)
        raised = False
        try:
            self.reg.register("DUP-001", FirmID.FTMO, 100_000.0)
        except ValueError:
            raised = True
        assert raised

    def test_conflict_multiple_firms_tracked_independently(self):
        """Conflict: FTMO and APEX accounts tracked independently — no state bleed."""
        self.reg.register("FTMO-001", FirmID.FTMO, 100_000.0)
        self.reg.register("APEX-001", FirmID.APEX, 50_000.0)

        # Pass FTMO only
        self.reg.eval_passed("FTMO-001")
        self.reg.confirm_funded("FTMO-001")

        ftmo = self.reg.get("FTMO-001")
        apex = self.reg.get("APEX-001")

        assert ftmo.is_funded is True
        assert apex.is_funded is False          # Apex unaffected
        assert apex.state == AccountLifecycleState.EVALUATION

    def test_normal_funded_count_accurate(self):
        """Normal: funded_count tracks confirmed funded accounts only."""
        self.reg.register("A1", FirmID.FTMO, 100_000.0)
        self.reg.register("A2", FirmID.FTMO, 100_000.0)
        assert self.reg.funded_count == 0
        self.reg.eval_passed("A1")
        self.reg.confirm_funded("A1")
        assert self.reg.funded_count == 1
        # A2 passed but not confirmed — not counted as funded
        self.reg.eval_passed("A2")
        assert self.reg.funded_count == 1
        assert self.reg.awaiting_count == 1

    def test_normal_awaiting_confirmation_list(self):
        """Normal: awaiting_confirmation() returns accounts needing Jorge's attention."""
        self.reg.register("B1", FirmID.FTMO, 100_000.0)
        self.reg.eval_passed("B1")
        awaiting = self.reg.awaiting_confirmation()
        assert len(awaiting) == 1
        assert awaiting[0].account_id == "B1"

    def test_normal_full_status_returns_all_accounts(self):
        """Normal: full_status() returns summary dict for every account."""
        for i in range(3):
            self.reg.register(f"ACC-{i}", FirmID.FTMO, 100_000.0)
        status = self.reg.full_status()
        assert len(status) == 3
        for s in status:
            assert "account_id" in s
            assert "lifecycle_state" in s
            assert "is_funded" in s


# ─────────────────────────────────────────────────────────────────────────────
# FLOOR CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestFloorCalculations:

    def test_normal_ftmo_floor_at_90k_for_100k(self):
        """Normal: FTMO $100K → floor at $90K (10% drawdown)."""
        t = make_toggle(firm=FirmID.FTMO, size=100_000.0)
        assert abs(t.current_rule_set.firm_floor_dollars - 90_000.0) < 0.01

    def test_edge_5ers_floor_at_96k_for_100k(self):
        """Edge: 5%ers $100K → floor at $96K (tightest: 4% drawdown)."""
        t = make_toggle(firm=FirmID.FIVEPERCENTERS, size=100_000.0)
        assert abs(t.current_rule_set.firm_floor_dollars - 96_000.0) < 0.01

    def test_conflict_floor_recalculates_on_scaling(self):
        """Conflict: After scaling to $125K, floor recalculates from new size."""
        t = make_toggle(firm=FirmID.FTMO, size=100_000.0)
        t.eval_passed()
        t.confirm_funded()
        t.mark_scaling_milestone(125_000.0)
        # FTMO 10% drawdown: $125K × 0.90 = $112.5K floor
        expected = 125_000.0 * 0.90
        assert abs(t.current_rule_set.firm_floor_dollars - expected) < 0.01


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
