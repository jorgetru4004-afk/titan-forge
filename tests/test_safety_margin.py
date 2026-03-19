"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║         test_safety_margin.py — FORGE-03 — FX-06 Compliance                 ║
║                                                                              ║
║  Three test cases per capability: normal, edge/boundary, conflict           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from safety_margin import (
    DynamicSafetyMargin, DailyBudgetAllocation, MarginTier,
    MarginResult, TIER_BOUNDARIES, TIER_MULTIPLIERS,
    DAILY_BUDGET_MULTIPLIERS,
)
from firm_rules import FirmID, MultiFirmRuleEngine

ENGINE  = MultiFirmRuleEngine(active_firm_id=FirmID.FTMO)
TODAY   = date(2026, 3, 19)


def make_margin(firm=FirmID.FTMO) -> DynamicSafetyMargin:
    return DynamicSafetyMargin(ENGINE)


def calc(
    dsm: DynamicSafetyMargin,
    pct_used: float,
    firm: str = FirmID.FTMO,
    base_size: float = 1.0,
    dd_remaining: float = 5_000.0,
    total_dd: float = 10_000.0,
) -> MarginResult:
    return dsm.calculate(
        firm_id=firm,
        drawdown_pct_used=pct_used,
        daily_pct_used=0.10,
        base_size=base_size,
        drawdown_remaining_dollars=dd_remaining,
        total_drawdown_dollars=total_dd,
    )


# ─────────────────────────────────────────────────────────────────────────────
# TIER CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class TestTierClassification:

    def test_normal_0_pct_is_clear(self):
        """Normal: 0% drawdown used = CLEAR tier, full size."""
        dsm = make_margin()
        assert dsm.get_tier(0.00) == MarginTier.CLEAR

    def test_normal_15_pct_is_clear(self):
        """Normal: 15% used = CLEAR tier."""
        dsm = make_margin()
        assert dsm.get_tier(0.15) == MarginTier.CLEAR

    def test_edge_exactly_30_pct_is_tier1(self):
        """Edge: Exactly 30% used = TIER_1 (boundary is inclusive at lower end)."""
        dsm = make_margin()
        assert dsm.get_tier(0.30) == MarginTier.TIER_1

    def test_edge_29_9_pct_still_clear(self):
        """Edge: 29.9% used = still CLEAR, not yet TIER_1."""
        dsm = make_margin()
        assert dsm.get_tier(0.299) == MarginTier.CLEAR

    def test_edge_exactly_50_pct_is_tier2(self):
        """Edge: Exactly 50% used = TIER_2 (Yellow)."""
        dsm = make_margin()
        assert dsm.get_tier(0.50) == MarginTier.TIER_2

    def test_edge_exactly_70_pct_is_tier3(self):
        """Edge: Exactly 70% used = TIER_3 (Orange)."""
        dsm = make_margin()
        assert dsm.get_tier(0.70) == MarginTier.TIER_3

    def test_edge_exactly_85_pct_is_red(self):
        """Edge: Exactly 85% used = RED. No new entries."""
        dsm = make_margin()
        assert dsm.get_tier(0.85) == MarginTier.RED

    def test_conflict_100_pct_is_red_not_tier3(self):
        """Conflict: 100% used must map to RED, not TIER_3."""
        dsm = make_margin()
        assert dsm.get_tier(1.00) == MarginTier.RED
        assert dsm.get_tier(1.50) == MarginTier.RED  # Over 100% also RED


# ─────────────────────────────────────────────────────────────────────────────
# SIZE MULTIPLIERS
# ─────────────────────────────────────────────────────────────────────────────

class TestSizeMultipliers:

    def test_normal_clear_full_size(self):
        """Normal: CLEAR tier — full 1.0 size multiplier."""
        dsm = make_margin()
        result = calc(dsm, 0.10, base_size=2.0)
        assert result.tier == MarginTier.CLEAR
        assert result.permitted_size == 2.0
        assert result.new_entries_permitted is True

    def test_normal_tier1_reduces_to_75_pct(self):
        """Normal: TIER_1 (35% used) — base 1.0 → 0.75."""
        dsm = make_margin()
        result = calc(dsm, 0.35, base_size=1.0)
        assert result.tier == MarginTier.TIER_1
        assert abs(result.permitted_size - 0.75) < 1e-6

    def test_normal_tier2_reduces_to_50_pct(self):
        """Normal: TIER_2 (55% used) — base 1.0 → 0.50."""
        dsm = make_margin()
        result = calc(dsm, 0.55, base_size=1.0)
        assert result.tier == MarginTier.TIER_2
        assert abs(result.permitted_size - 0.50) < 1e-6

    def test_normal_tier3_minimum_size_only(self):
        """Normal: TIER_3 (72% used) — permitted_size = firm minimum regardless of base."""
        dsm = make_margin()
        # Base size 5.0 lots → must reduce to firm minimum
        result = calc(dsm, 0.72, base_size=5.0)
        assert result.tier == MarginTier.TIER_3
        rules = ENGINE.get_firm_rules(FirmID.FTMO)
        assert result.permitted_size == rules.minimum_position_size

    def test_edge_red_zero_size_no_entries(self):
        """Edge: RED tier (90% used) — permitted_size = 0.0, no entries."""
        dsm = make_margin()
        result = calc(dsm, 0.90, base_size=1.0)
        assert result.tier == MarginTier.RED
        assert result.permitted_size == 0.0
        assert result.new_entries_permitted is False

    def test_conflict_large_base_size_always_capped_by_tier(self):
        """Conflict: Even 100-lot base size is capped to minimum at TIER_3."""
        dsm = make_margin()
        result = calc(dsm, 0.75, base_size=100.0)
        assert result.tier == MarginTier.TIER_3
        rules = ENGINE.get_firm_rules(FirmID.FTMO)
        assert result.permitted_size == rules.minimum_position_size
        assert result.permitted_size < 100.0

    def test_normal_minimum_size_floor_enforced(self):
        """Normal: Base size smaller than minimum — permitted_size is still minimum."""
        dsm = make_margin()
        # At TIER_1: 0.001 × 0.75 = 0.00075, below firm minimum 0.01
        result = calc(dsm, 0.35, base_size=0.001)
        rules = ENGINE.get_firm_rules(FirmID.FTMO)
        assert result.permitted_size >= rules.minimum_position_size


# ─────────────────────────────────────────────────────────────────────────────
# BOUNDARY INTEGRITY — NO GAP, NO OVERLAP
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundaryIntegrity:

    def test_normal_boundaries_contiguous(self):
        """Normal: All tier boundaries are contiguous — no gaps, no overlaps."""
        tier_order = [MarginTier.CLEAR, MarginTier.TIER_1,
                      MarginTier.TIER_2, MarginTier.TIER_3, MarginTier.RED]
        for i in range(len(tier_order) - 1):
            current = tier_order[i]
            next_t  = tier_order[i + 1]
            _, current_hi = TIER_BOUNDARIES[current]
            next_lo, _    = TIER_BOUNDARIES[next_t]
            assert abs(current_hi - next_lo) < 1e-9, (
                f"Gap between {current.name} and {next_t.name}: "
                f"hi={current_hi}, lo={next_lo}"
            )

    def test_edge_tier_multipliers_descend(self):
        """Edge: Size multipliers must descend from CLEAR to RED."""
        tier_order = [MarginTier.CLEAR, MarginTier.TIER_1, MarginTier.TIER_2]
        for i in range(len(tier_order) - 1):
            assert TIER_MULTIPLIERS[tier_order[i]] > TIER_MULTIPLIERS[tier_order[i + 1]], \
                f"{tier_order[i].name} multiplier must exceed {tier_order[i+1].name}"

    def test_conflict_red_and_tier3_both_zero_multiplier(self):
        """Conflict: TIER_3 and RED both have 0.0 multiplier — both force minimum/zero."""
        assert TIER_MULTIPLIERS[MarginTier.TIER_3] == 0.0
        assert TIER_MULTIPLIERS[MarginTier.RED]    == 0.0
        # But TIER_3 permits entries (minimum size), RED does not
        dsm = make_margin()
        t3 = calc(dsm, 0.72)
        rd = calc(dsm, 0.90)
        assert t3.new_entries_permitted is True
        assert rd.new_entries_permitted is False


# ─────────────────────────────────────────────────────────────────────────────
# DISTANCE TO NEXT TIER
# ─────────────────────────────────────────────────────────────────────────────

class TestDistanceMetrics:

    def test_normal_distance_from_clear_to_tier1(self):
        """Normal: At 10% used — 20% and $2K to next tier."""
        dsm = make_margin()
        result = calc(dsm, 0.10, dd_remaining=9_000.0, total_dd=10_000.0)
        assert result.pct_to_next_tier is not None
        assert abs(result.pct_to_next_tier - 0.20) < 1e-9
        assert abs(result.dollars_to_next_tier - 2_000.0) < 0.01

    def test_edge_at_tier_boundary_zero_distance(self):
        """Edge: Exactly at 30% boundary — 0% to next tier (already in TIER_1)."""
        dsm = make_margin()
        result = calc(dsm, 0.30, total_dd=10_000.0)
        # In TIER_1, next is TIER_2 at 50%, so 20% to next
        assert result.tier == MarginTier.TIER_1
        assert abs(result.pct_to_next_tier - 0.20) < 1e-9

    def test_conflict_red_has_no_next_tier(self):
        """Conflict: RED is the final tier — pct_to_next_tier must be None."""
        dsm = make_margin()
        result = calc(dsm, 0.90)
        assert result.pct_to_next_tier is None
        assert result.dollars_to_next_tier is None

    def test_normal_dollars_to_floor_always_set(self):
        """Normal: dollars_to_floor always populated regardless of tier."""
        dsm = make_margin()
        for pct, remaining in [(0.10, 9_000), (0.50, 5_000), (0.90, 1_000)]:
            result = calc(dsm, pct, dd_remaining=float(remaining))
            assert result.dollars_to_floor == float(remaining)


# ─────────────────────────────────────────────────────────────────────────────
# DAILY BUDGET ALLOCATION — FORGE-12
# ─────────────────────────────────────────────────────────────────────────────

class TestDailyBudgetAllocation:

    def test_normal_clear_tier_full_daily_budget(self):
        """Normal: CLEAR tier — full daily limit allocated."""
        dsm = make_margin()
        alloc = dsm.allocate_daily_budget(
            firm_id=FirmID.FTMO,
            daily_limit_dollars=5_000.0,
            margin_tier=MarginTier.CLEAR,
            session_date=TODAY,
        )
        assert alloc.session_budget == 5_000.0
        assert alloc.standard_budget == 4_000.0      # 80% of 5K
        assert alloc.high_conviction_budget == 1_000.0  # 20% of 5K

    def test_edge_red_tier_zero_budget(self):
        """Edge: RED tier — zero session budget allocated."""
        dsm = make_margin()
        alloc = dsm.allocate_daily_budget(
            firm_id=FirmID.FTMO,
            daily_limit_dollars=5_000.0,
            margin_tier=MarginTier.RED,
            session_date=TODAY,
        )
        assert alloc.session_budget == 0.0

    def test_conflict_tier2_reduces_budget_to_60_pct(self):
        """Conflict: TIER_2 daily budget = 60% of limit. Less than CLEAR (100%)."""
        dsm = make_margin()
        clear_alloc = dsm.allocate_daily_budget(FirmID.FTMO, 5_000.0, MarginTier.CLEAR, TODAY)
        tier2_alloc = dsm.allocate_daily_budget(FirmID.FTMO, 5_000.0, MarginTier.TIER_2, TODAY)
        assert tier2_alloc.session_budget < clear_alloc.session_budget
        assert abs(tier2_alloc.session_budget - 3_000.0) < 0.01  # 60% of 5K

    def test_normal_reserve_carryover_added_to_reserve_pool(self):
        """Normal: Prior session unspent budget carries forward into reserve pool."""
        dsm = make_margin()
        alloc = dsm.allocate_daily_budget(
            firm_id=FirmID.FTMO,
            daily_limit_dollars=5_000.0,
            margin_tier=MarginTier.CLEAR,
            session_date=TODAY,
            reserve_carryover=500.0,   # $500 unspent from yesterday
        )
        # Reserve pool = carryover $500 + 20% of today's $5K = $500 + $1K = $1,500
        assert abs(alloc.reserve_pool - 1_500.0) < 0.01

    def test_normal_consume_standard_budget(self):
        """Normal: Consuming standard budget reduces budget_remaining."""
        dsm = make_margin()
        alloc = dsm.allocate_daily_budget(FirmID.FTMO, 5_000.0, MarginTier.CLEAR, TODAY)
        success = alloc.consume(1_000.0, is_high_conviction=False)
        assert success is True
        assert abs(alloc.budget_remaining - 3_000.0) < 0.01  # 4K - 1K = 3K

    def test_edge_consume_over_budget_blocked(self):
        """Edge: Consuming more than available budget returns False — blocked."""
        dsm = make_margin()
        alloc = dsm.allocate_daily_budget(FirmID.FTMO, 1_000.0, MarginTier.CLEAR, TODAY)
        # Standard budget = 80% of $1K = $800
        result = alloc.consume(900.0, is_high_conviction=False)
        assert result is False  # Over the $800 standard budget
        assert alloc.budget_consumed == 0.0  # Nothing consumed

    def test_normal_unspent_returns_to_reserve(self):
        """Normal: Unspent standard budget is returned to the reserve pool."""
        dsm = make_margin()
        alloc = dsm.allocate_daily_budget(FirmID.FTMO, 5_000.0, MarginTier.CLEAR, TODAY)
        alloc.consume(1_000.0)   # Spend $1K of $4K standard
        unspent = alloc.return_unspent()
        assert abs(unspent - 3_000.0) < 0.01   # $4K - $1K = $3K returned


# ─────────────────────────────────────────────────────────────────────────────
# FULL SIZING STACK (Safety Margin + Kelly + Loss Response)
# ─────────────────────────────────────────────────────────────────────────────

class TestFullSizingStack:

    def test_normal_safety_margin_binding_at_tier2(self):
        """Normal: At TIER_2 (55% DD used), safety margin is binding over Kelly."""
        dsm = make_margin()
        result = dsm.apply_full_stack(
            firm_id=FirmID.FTMO,
            drawdown_pct_used=0.55,
            daily_pct_used=0.10,
            base_size=1.0,
            drawdown_remaining_dollars=4_500.0,
            total_drawdown_dollars=10_000.0,
            kelly_size=0.90,           # Kelly says 90% of base
            loss_response_modifier=1.0,  # No loss response active
        )
        # Safety margin at TIER_2 = 0.50, Kelly = 0.90 → safety margin wins
        assert abs(result["final_size"] - 0.50) < 1e-6
        assert "SAFETY_MARGIN" in result["binding_constraint"]
        assert result["new_entries_permitted"] is True

    def test_edge_kelly_binding_when_tighter_than_margin(self):
        """Edge: Kelly cap is tighter than safety margin — Kelly wins."""
        dsm = make_margin()
        result = dsm.apply_full_stack(
            firm_id=FirmID.FTMO,
            drawdown_pct_used=0.10,    # CLEAR tier — full 1.0 multiplier
            daily_pct_used=0.05,
            base_size=1.0,
            drawdown_remaining_dollars=9_000.0,
            total_drawdown_dollars=10_000.0,
            kelly_size=0.30,           # Kelly says 30% — tighter than safety margin
            loss_response_modifier=1.0,
        )
        assert abs(result["final_size"] - 0.30) < 1e-6
        assert result["binding_constraint"] == "KELLY_CAP_C06"

    def test_conflict_red_tier_blocks_regardless_of_kelly(self):
        """Conflict: RED tier blocks all entries — even if Kelly and Loss Response say go."""
        dsm = make_margin()
        result = dsm.apply_full_stack(
            firm_id=FirmID.FTMO,
            drawdown_pct_used=0.90,    # RED
            daily_pct_used=0.10,
            base_size=1.0,
            drawdown_remaining_dollars=1_000.0,
            total_drawdown_dollars=10_000.0,
            kelly_size=0.95,
            loss_response_modifier=1.0,
        )
        assert result["final_size"] == 0.0
        assert result["new_entries_permitted"] is False
        assert result["binding_constraint"] == "SAFETY_MARGIN_RED"

    def test_normal_loss_response_modifier_applied(self):
        """Normal: Loss response modifier further reduces size on top of margin."""
        dsm = make_margin()
        result = dsm.apply_full_stack(
            firm_id=FirmID.FTMO,
            drawdown_pct_used=0.10,    # CLEAR — full 1.0
            daily_pct_used=0.05,
            base_size=1.0,
            drawdown_remaining_dollars=9_000.0,
            total_drawdown_dollars=10_000.0,
            kelly_size=1.0,
            loss_response_modifier=0.75,   # C-08: 1 consecutive loss
        )
        # Margin: 1.0 × 1.0 = 1.0 → Loss response: 1.0 × 0.75 = 0.75
        assert abs(result["final_size"] - 0.75) < 1e-6


# ─────────────────────────────────────────────────────────────────────────────
# 5%ERS EXTRA BUFFER
# ─────────────────────────────────────────────────────────────────────────────

class TestFivePercentersBuffer:

    def test_normal_fivepercenters_extra_reduction(self):
        """Normal: 5%ers get additional 20% reduction on top of standard tier."""
        dsm = make_margin()
        # At 10% used: standard CLEAR = 1.0. With 5%ers buffer = 0.80
        std_result = dsm.calculate_with_firm_adjustment(
            FirmID.FTMO, 0.10, 0.05, 1.0, 9_000.0, 10_000.0
        )
        fpe_result = dsm.calculate_with_firm_adjustment(
            FirmID.FIVEPERCENTERS, 0.10, 0.05, 1.0, 9_000.0, 10_000.0
        )
        # 5%ers must be more conservative
        assert fpe_result.permitted_size <= std_result.permitted_size

    def test_edge_fivepercenters_at_20_pct_extra_caution(self):
        """Edge: 5%ers at 20%+ used — extra buffer increases to 30% additional reduction."""
        dsm = make_margin()
        result = dsm.apply_fivepercenters_extra_buffer(
            base_multiplier=1.0,
            drawdown_pct_used=0.20,
        )
        assert abs(result - 0.70) < 1e-9  # 30% additional reduction at 20%+

    def test_conflict_fivepercenters_below_20_pct_lighter_buffer(self):
        """Conflict: 5%ers below 20% used — only 20% extra buffer, not 30%."""
        dsm = make_margin()
        below_result = dsm.apply_fivepercenters_extra_buffer(1.0, 0.15)
        above_result = dsm.apply_fivepercenters_extra_buffer(1.0, 0.25)
        # Below 20%: 80% of base. Above 20%: 70% of base
        assert abs(below_result - 0.80) < 1e-9
        assert abs(above_result - 0.70) < 1e-9
        assert below_result > above_result  # Below is less restrictive


# ─────────────────────────────────────────────────────────────────────────────
# STATUS SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

class TestStatusSummary:

    def test_normal_summary_shows_correct_tier(self):
        """Normal: Status summary reflects the current active tier."""
        dsm = make_margin()
        summary = dsm.status_summary(FirmID.FTMO, 0.60, 4_000.0, 10_000.0)
        assert summary["active_tier"] == "TIER_2"
        assert summary["new_entries_permitted"] is True

    def test_edge_summary_red_shows_no_entries(self):
        """Edge: RED tier summary shows no entries permitted."""
        dsm = make_margin()
        summary = dsm.status_summary(FirmID.FTMO, 0.90, 1_000.0, 10_000.0)
        assert summary["active_tier"] == "RED"
        assert summary["new_entries_permitted"] is False
        assert summary["size_multiplier"] == 0.0

    def test_conflict_summary_contains_all_5_tiers(self):
        """Conflict: Summary must show all 5 tiers for ARCHITECT dashboard."""
        dsm = make_margin()
        summary = dsm.status_summary(FirmID.FTMO, 0.10, 9_000.0, 10_000.0)
        assert len(summary["tiers"]) == 5
        tier_names = [t["tier"] for t in summary["tiers"]]
        for expected in ["CLEAR", "TIER_1", "TIER_2", "TIER_3", "RED"]:
            assert expected in tier_names


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS INTEGRITY
# ─────────────────────────────────────────────────────────────────────────────

class TestConstantsIntegrity:

    def test_normal_tier_multipliers_match_spec(self):
        """Normal: Multipliers match the spec exactly."""
        assert TIER_MULTIPLIERS[MarginTier.CLEAR]  == 1.00
        assert TIER_MULTIPLIERS[MarginTier.TIER_1] == 0.75
        assert TIER_MULTIPLIERS[MarginTier.TIER_2] == 0.50
        assert TIER_MULTIPLIERS[MarginTier.TIER_3] == 0.00
        assert TIER_MULTIPLIERS[MarginTier.RED]    == 0.00

    def test_edge_boundaries_match_spec(self):
        """Edge: All tier boundaries match the documented 30/50/70/85 thresholds."""
        assert TIER_BOUNDARIES[MarginTier.TIER_1][0] == 0.30
        assert TIER_BOUNDARIES[MarginTier.TIER_2][0] == 0.50
        assert TIER_BOUNDARIES[MarginTier.TIER_3][0] == 0.70
        assert TIER_BOUNDARIES[MarginTier.RED][0]    == 0.85

    def test_conflict_daily_budget_descends_with_severity(self):
        """Conflict: Daily budget multipliers must decrease as tier severity increases."""
        multipliers = [
            DAILY_BUDGET_MULTIPLIERS[MarginTier.CLEAR],
            DAILY_BUDGET_MULTIPLIERS[MarginTier.TIER_1],
            DAILY_BUDGET_MULTIPLIERS[MarginTier.TIER_2],
            DAILY_BUDGET_MULTIPLIERS[MarginTier.TIER_3],
            DAILY_BUDGET_MULTIPLIERS[MarginTier.RED],
        ]
        for i in range(len(multipliers) - 1):
            assert multipliers[i] > multipliers[i + 1], \
                f"Budget multiplier must decrease from tier {i} to {i+1}"


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
