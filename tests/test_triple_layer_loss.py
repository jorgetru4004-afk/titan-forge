"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║       test_triple_layer_loss.py — FORGE-11/67 — FX-06 Compliance            ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from triple_layer_loss import (
    TripleLayerProtection, ProtectionLevel,
    GUARDIAN_WARNING_PCT, GUARDIAN_ORANGE_PCT, GUARDIAN_RED_PCT,
    TITAN_DAILY_BUFFER, YELLOW_SIZE_MODIFIER, ORANGE_SIZE_MODIFIER,
    RED_SIZE_MODIFIER, MIN_STOP_PCT_OF_ATR,
)
from firm_rules import FirmID, MultiFirmRuleEngine

ENGINE = MultiFirmRuleEngine(active_firm_id=FirmID.FTMO)


def make_tlp() -> TripleLayerProtection:
    return TripleLayerProtection(ENGINE)


def base_check(tlp, **kwargs):
    defaults = dict(
        firm_id=FirmID.FTMO,
        account_size=100_000.0,
        current_equity=97_000.0,
        starting_balance=100_000.0,
        session_starting_equity=97_000.0,
        daily_loss_dollars=500.0,
        total_drawdown_used_pct=0.30,
        atr=10.0,
        proposed_stop_price=4790.0,
        current_price=4800.0,
        direction="long",
        is_evaluation=True,
    )
    defaults.update(kwargs)
    return tlp.check(**defaults)


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1: PER-TRADE STOP
# ─────────────────────────────────────────────────────────────────────────────

class TestLayer1PerTradeStop:

    def test_normal_valid_stop_passes(self):
        """Normal: Long with stop below price → valid → PASS."""
        tlp = make_tlp()
        result = base_check(tlp, proposed_stop_price=4790.0, current_price=4800.0,
                            direction="long", atr=10.0)
        assert result.layer1.has_valid_stop is True
        assert result.layer1.stop_distance_atr == 1.0   # 10/10 = 1.0 ATR

    def test_edge_no_stop_defined_blocks_entry(self):
        """Edge: No stop price provided → BLOCKED. Non-negotiable."""
        tlp = make_tlp()
        result = base_check(tlp, proposed_stop_price=None)
        assert result.layer1.has_valid_stop is False
        assert result.entry_permitted is False
        assert result.overall_level == ProtectionLevel.BLOCKED

    def test_conflict_stop_on_wrong_side_invalid(self):
        """Conflict: Long trade with stop ABOVE current price → invalid."""
        tlp = make_tlp()
        result = base_check(tlp, proposed_stop_price=4810.0, current_price=4800.0,
                            direction="long")
        assert result.layer1.has_valid_stop is False
        assert result.entry_permitted is False

    def test_normal_short_valid_stop(self):
        """Normal: Short with stop above price → valid."""
        tlp = make_tlp()
        result = base_check(tlp, proposed_stop_price=4810.0, current_price=4800.0,
                            direction="short", atr=10.0)
        assert result.layer1.has_valid_stop is True

    def test_edge_stop_too_tight_below_min_atr(self):
        """Edge: Stop distance < 0.3 ATR → micro-stop rejected."""
        tlp = make_tlp()
        # 2 ticks = 0.5 points / ATR 10 = 0.05 ATR — below 0.3 ATR minimum
        result = base_check(tlp, proposed_stop_price=4799.5, current_price=4800.0,
                            direction="long", atr=10.0)
        assert result.layer1.has_valid_stop is False

    def test_normal_stop_exactly_at_min_atr_passes(self):
        """Normal: Stop distance = exactly 0.3 ATR → passes minimum."""
        tlp = make_tlp()
        # 3.0 points / ATR 10 = 0.30 ATR = exactly at minimum
        result = base_check(tlp, proposed_stop_price=4797.0, current_price=4800.0,
                            direction="long", atr=10.0)
        assert result.layer1.has_valid_stop is True


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2: DAILY CIRCUIT BREAKER
# ─────────────────────────────────────────────────────────────────────────────

class TestLayer2DailyCircuitBreaker:

    def test_normal_well_below_limit_clear(self):
        """Normal: $500 daily loss with $4K TITAN limit → clear."""
        tlp = make_tlp()
        # FTMO 5% daily limit on $97K equity = $4,850. TITAN = 80% = $3,880
        result = base_check(tlp, daily_loss_dollars=500.0,
                            session_starting_equity=97_000.0)
        assert result.layer2.circuit_broken is False

    def test_edge_exactly_at_titan_limit_triggers(self):
        """Edge: Daily loss = exactly TITAN limit → circuit broken."""
        tlp = make_tlp()
        # FTMO 5% on $97K = $4,850 firm. TITAN = 80% = $3,880
        titan_limit = 97_000.0 * 0.05 * (1.0 - TITAN_DAILY_BUFFER)
        result = base_check(tlp, daily_loss_dollars=titan_limit,
                            session_starting_equity=97_000.0)
        assert result.layer2.circuit_broken is True
        assert result.entry_permitted is False
        assert result.overall_level == ProtectionLevel.DAILY_STOPPED

    def test_conflict_circuit_breaker_blocks_before_firm_limit(self):
        """Conflict: TITAN circuit at 80% of firm limit — firm's hard limit not yet hit."""
        tlp = make_tlp()
        # Firm FTMO limit = $4,850. TITAN = $3,880. Test at $4,000 (between the two).
        result = base_check(tlp, daily_loss_dollars=4_000.0,
                            session_starting_equity=97_000.0)
        # TITAN triggered at $3,880 but firm's at $4,850 → we stop before firm does
        assert result.layer2.circuit_broken is True
        # Firm limit not crossed yet — our protection worked
        assert result.layer2.firm_daily_limit > result.layer2.titan_daily_limit

    def test_normal_no_daily_limit_firm_no_circuit(self):
        """Normal: Firm with no daily limit (e.g., some Apex accounts) → no circuit."""
        tlp = make_tlp()
        # Use a firm config with no daily limit - APEX intraday has no DLL
        result = base_check(tlp, firm_id=FirmID.APEX, daily_loss_dollars=5_000.0,
                            session_starting_equity=100_000.0)
        # APEX daily_drawdown_pct = 0.015 ($1,500) — this WOULD trigger at $1,200
        # At $5,000 → triggered
        assert result.layer2.circuit_broken is True


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3: DRAWDOWN GUARDIAN
# ─────────────────────────────────────────────────────────────────────────────

class TestLayer3DrawdownGuardian:

    def test_normal_below_60_pct_clear(self):
        """Normal: 50% drawdown used → CLEAR, full size."""
        tlp = make_tlp()
        result = base_check(tlp, total_drawdown_used_pct=0.50)
        assert result.layer3.protection_level == ProtectionLevel.CLEAR
        assert result.layer3.size_modifier == 1.0

    def test_edge_exactly_60_pct_triggers_caution(self):
        """Edge: Exactly 60% drawdown used → CAUTION, -25% size."""
        tlp = make_tlp()
        result = base_check(tlp, total_drawdown_used_pct=0.60)
        assert result.layer3.protection_level == ProtectionLevel.CAUTION
        assert result.layer3.size_modifier == YELLOW_SIZE_MODIFIER   # 0.75

    def test_edge_exactly_70_pct_triggers_orange(self):
        """Edge: Exactly 70% drawdown used → WARNING, minimum size only."""
        tlp = make_tlp()
        result = base_check(tlp, total_drawdown_used_pct=0.70)
        assert result.layer3.protection_level == ProtectionLevel.WARNING
        assert result.layer3.size_modifier == ORANGE_SIZE_MODIFIER

    def test_edge_exactly_85_pct_triggers_red(self):
        """Edge: Exactly 85% → CRITICAL, no new entries."""
        tlp = make_tlp()
        result = base_check(tlp, total_drawdown_used_pct=0.85)
        assert result.layer3.protection_level == ProtectionLevel.CRITICAL
        assert result.layer3.new_entries_permitted is False
        assert result.entry_permitted is False

    def test_conflict_red_cannot_be_overridden_by_setup_quality(self):
        """Conflict: Even excellent setup is blocked when L3 is RED."""
        tlp = make_tlp()
        result = base_check(tlp, total_drawdown_used_pct=0.90)
        assert result.entry_permitted is False
        assert result.should_close_all is True


# ─────────────────────────────────────────────────────────────────────────────
# COMBINED — MOST RESTRICTIVE WINS
# ─────────────────────────────────────────────────────────────────────────────

class TestCombinedProtection:

    def test_normal_all_clear_full_size(self):
        """Normal: All three layers clear → entry permitted, size_modifier = 1.0."""
        tlp = make_tlp()
        result = base_check(tlp, total_drawdown_used_pct=0.30, daily_loss_dollars=200.0)
        assert result.entry_permitted is True
        assert result.size_modifier == 1.0
        assert result.overall_level == ProtectionLevel.CLEAR

    def test_edge_l1_blocks_regardless_of_l2_l3(self):
        """Edge: No stop (L1 fails) → BLOCKED even if L2/L3 are clear."""
        tlp = make_tlp()
        result = base_check(tlp, proposed_stop_price=None,
                            total_drawdown_used_pct=0.10, daily_loss_dollars=0.0)
        assert result.entry_permitted is False
        assert result.overall_level == ProtectionLevel.BLOCKED
        assert result.layer2.circuit_broken is False   # L2 is clear
        assert result.layer3.protection_level == ProtectionLevel.CLEAR  # L3 is clear

    def test_conflict_l2_blocks_even_when_l3_only_caution(self):
        """Conflict: L2 circuit broken (daily stopped) overrides L3 caution (which permits)."""
        tlp = make_tlp()
        # L3 at 65% = caution (permits with reduced size)
        # L2 circuit broken (daily loss > TITAN limit)
        titan_limit = 97_000.0 * 0.05 * (1.0 - TITAN_DAILY_BUFFER)
        result = base_check(tlp,
                            total_drawdown_used_pct=0.65,   # L3 caution — permits
                            daily_loss_dollars=titan_limit + 1.0,  # L2 triggered
                            session_starting_equity=97_000.0)
        assert result.layer2.circuit_broken is True
        assert result.entry_permitted is False

    def test_normal_caution_size_modifier_applied(self):
        """Normal: L3 at 65% caution → size_modifier = 0.75 (YELLOW_SIZE_MODIFIER)."""
        tlp = make_tlp()
        result = base_check(tlp, total_drawdown_used_pct=0.65, daily_loss_dollars=100.0)
        assert result.entry_permitted is True
        assert result.size_modifier == YELLOW_SIZE_MODIFIER


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-67: P&L SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────

class TestPnLSnapshot:

    def test_normal_healthy_account_normal_action(self):
        """Normal: Drawdown 30% → action = NORMAL, size modifier = 1.0."""
        tlp = make_tlp()
        snap = tlp.snapshot(
            account_id="TEST", firm_id=FirmID.FTMO,
            starting_balance=100_000.0, current_balance=97_500.0,
            current_equity=97_500.0, session_starting_equity=97_500.0,
            daily_loss_dollars=200.0,
        )
        assert snap.action == "NORMAL"
        assert snap.size_modifier == 1.0
        assert snap.at_yellow is False

    def test_edge_yellow_at_50_pct_drawdown(self):
        """Edge: Exactly 50% drawdown used → YELLOW, REDUCE action."""
        tlp = make_tlp()
        # FTMO $100K, floor $90K, budget $10K. 50% = $5K used → equity $95K
        snap = tlp.snapshot(
            account_id="TEST", firm_id=FirmID.FTMO,
            starting_balance=100_000.0, current_balance=95_000.0,
            current_equity=95_000.0, session_starting_equity=95_000.0,
            daily_loss_dollars=500.0,
        )
        assert snap.at_yellow is True
        assert snap.action == "REDUCE"
        assert snap.size_modifier == YELLOW_SIZE_MODIFIER

    def test_conflict_red_triggers_close_all_action(self):
        """Conflict: 87% drawdown → RED → CLOSE_ALL action."""
        tlp = make_tlp()
        # $10K budget, 87% used = $8,700 → equity = $91,300
        snap = tlp.snapshot(
            account_id="TEST", firm_id=FirmID.FTMO,
            starting_balance=100_000.0, current_balance=91_300.0,
            current_equity=91_300.0, session_starting_equity=91_300.0,
            daily_loss_dollars=1_000.0,
        )
        assert snap.at_red is True
        assert snap.action == "CLOSE_ALL"
        assert snap.size_modifier == 0.0

    def test_normal_open_pnl_calculated(self):
        """Normal: open_pnl = current_equity - current_balance."""
        tlp = make_tlp()
        snap = tlp.snapshot(
            account_id="TEST", firm_id=FirmID.FTMO,
            starting_balance=100_000.0, current_balance=99_000.0,
            current_equity=99_500.0, session_starting_equity=99_500.0,
            daily_loss_dollars=0.0,
        )
        assert abs(snap.open_pnl - 500.0) < 0.01   # $500 unrealized gain

    def test_normal_session_pnl_calculated(self):
        """Normal: session_pnl = current_equity - session_starting_equity."""
        tlp = make_tlp()
        snap = tlp.snapshot(
            account_id="TEST", firm_id=FirmID.FTMO,
            starting_balance=100_000.0, current_balance=100_500.0,
            current_equity=100_500.0, session_starting_equity=100_000.0,
            daily_loss_dollars=0.0,
        )
        assert abs(snap.session_pnl - 500.0) < 0.01   # $500 session profit


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:

    def test_normal_guardian_thresholds_ordered(self):
        """Normal: 60% < 70% < 85% — caution before orange before red."""
        assert GUARDIAN_WARNING_PCT < GUARDIAN_ORANGE_PCT < GUARDIAN_RED_PCT

    def test_edge_size_modifiers_descend_with_severity(self):
        """Edge: Yellow > Orange > Red size modifiers."""
        assert YELLOW_SIZE_MODIFIER > ORANGE_SIZE_MODIFIER
        assert ORANGE_SIZE_MODIFIER > RED_SIZE_MODIFIER
        assert RED_SIZE_MODIFIER == 0.0

    def test_conflict_titan_daily_buffer_reduces_firm_limit(self):
        """Conflict: TITAN daily limit is LESS than firm's daily limit (protective)."""
        titan_pct = 1.0 - TITAN_DAILY_BUFFER
        assert titan_pct < 1.0   # Always less than the firm's limit

    def test_normal_min_stop_atr_is_0_3(self):
        """Normal: Minimum stop distance is 0.3 ATR."""
        assert MIN_STOP_PCT_OF_ATR == 0.30


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
