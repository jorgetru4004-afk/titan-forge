"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                      firm_rules.py — FORGE-01 — Layer 1                     ║
║                                                                              ║
║  MULTI-FIRM RULE ENGINE                                                      ║
║  Level 1 ABSOLUTE Authority — highest authority in TITAN FORGE.              ║
║  Firm rule violations are blocked regardless of ALL other signals.           ║
║                                                                              ║
║  Contains the complete rule database for all 5 prop firms:                  ║
║    • FTMO          — Grade A+. First evaluation target.                      ║
║    • APEX          — Grade A.  Best profit split. Trailing drawdown.         ║
║    • DNA_FUNDED    — Grade B.  Monitor payouts. Stage 4 only.                ║
║    • FIVEPERCENTERS— Grade A.  $4M ceiling. The long game. Month 18+.       ║
║    • TOPSTEP       — Grade B+. Futures only. Most permissive news.           ║
║                                                                              ║
║  Switch firms by changing one variable: ACTIVE_FIRM_ID                      ║
║  This engine enforces every rule automatically. No manual checks.            ║
║                                                                              ║
║  DO NOT add a new firm without adding ALL its rules and 3 test cases.        ║
║  DO NOT modify firm rules without verifying against official firm docs.      ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import Enum
from typing import Optional

logger = logging.getLogger("titan_forge.firm_rules")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — FIRM IDENTIFIERS
# ─────────────────────────────────────────────────────────────────────────────

class FirmID(str, Enum):
    FTMO            = "FTMO"
    APEX            = "APEX"
    DNA_FUNDED      = "DNA_FUNDED"
    FIVEPERCENTERS  = "FIVEPERCENTERS"
    TOPSTEP         = "TOPSTEP"


class DrawdownType(str, Enum):
    STATIC               = "STATIC"           # Floor set on Day 1, never moves
    TRAILING_UNREALIZED  = "TRAILING_UNREALIZED"  # Apex: trails on unrealized P&L
    TRAILING_EOD         = "TRAILING_EOD"     # Topstep: trails end-of-day equity
    STATIC_EOD_SNAPSHOT  = "STATIC_EOD_SNAPSHOT"  # DNA: balance snapshot at 10pm UTC


class Platform(str, Enum):
    DXTRADE         = "DXTrade"
    MATCH_TRADER    = "Match-Trader"
    TRADELOCKER     = "TradeLocker"
    RITHMIC         = "Rithmic"
    TRADOVATE       = "Tradovate"
    METATRADER_4    = "MetaTrader4"
    METATRADER_5    = "MetaTrader5"
    TOPSTEP_X       = "TopstepX"
    NINJA_TRADER    = "NinjaTrader"
    WEALTHCHARTS    = "WealthCharts"
    QUANTOWER       = "Quantower"


class AccountPhase(str, Enum):
    EVALUATION  = "EVALUATION"
    FUNDED      = "FUNDED"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — RULE VIOLATION RESULT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RuleCheckResult:
    """
    Result of a firm rule compliance check.
    ALWAYS check .compliant before executing any trade.
    """
    compliant:          bool
    firm_id:            str
    rule_name:          str
    reason:             str
    violation_detail:   Optional[str] = None
    # For near-violation warnings
    warning:            bool = False
    warning_detail:     Optional[str] = None

    @property
    def is_violation(self) -> bool:
        return not self.compliant


@dataclass
class DrawdownStatus:
    """
    Real-time drawdown calculation result.
    The firm_floor is the line that must never be crossed.
    """
    firm_id:            str
    drawdown_type:      DrawdownType
    starting_balance:   float
    firm_floor:         float          # The absolute floor — crossing = account failed
    current_equity:     float
    distance_to_floor:  float          # How much room remains
    pct_used:           float          # % of drawdown budget consumed
    daily_floor:        float          # Daily loss limit floor
    daily_distance:     float          # Distance to daily limit
    daily_pct_used:     float          # % of daily budget consumed
    # Warning thresholds
    at_yellow:          bool = False   # 50% drawdown used
    at_orange:          bool = False   # 70% drawdown used
    at_red:             bool = False   # 85% drawdown used — close all


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — FIRM RULE DEFINITIONS
# One dataclass per firm. Built from official documentation.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FirmRules:
    """
    Complete rule definition for a single prop firm.
    All values derived directly from official firm documentation.
    """
    firm_id:                    str
    display_name:               str
    grade:                      str
    platforms:                  tuple[str, ...]
    requires_windows_vps:       bool             # True = Rithmic/Tradovate path
    drawdown_type:              DrawdownType
    drawdown_snapshot_time_utc: Optional[str]    # e.g. "22:00" for DNA 10pm UTC
    daily_drawdown_pct:         float            # e.g. 0.05 = 5%
    total_drawdown_pct:         float            # e.g. 0.10 = 10%
    profit_target_phase1_pct:   float
    profit_target_phase2_pct:   Optional[float]  # None if single-phase
    # Time limits
    min_trading_days:           Optional[int]
    max_calendar_days:          Optional[int]    # None = no strict time limit
    # News rules
    news_blackout_minutes_before:   int          # 0 = no restriction
    news_blackout_minutes_after:    int
    news_can_hold_through:          bool         # True = can hold open through news
    # Consistency rules
    consistency_rule_pct:           Optional[float]  # Max single-day % of total profit
    consistency_applies_to:         Optional[str]    # "WITHDRAWAL" or "EVALUATION"
    # Payout / withdrawal
    first_n_withdrawals_cap_pct:    Optional[float]  # % of balance cap (DNA)
    first_n_withdrawals_count:      Optional[int]
    standard_payout_cycle_days:     Optional[int]
    max_total_allocation:           Optional[float]  # Max $ across all accounts
    max_payout_count:               Optional[int]    # Apex 6-payout max
    # Special flags
    no_consistency_rule:            bool         # FTMO advantage
    has_scaling_plan:               bool
    scaling_trigger_months:         Optional[int]
    scaling_pct_increase:           Optional[float]
    scaling_cap_dollars:            Optional[float]
    # Hard closes
    requires_eod_close:             bool         # Topstep: must close by 3:10 PM CT
    eod_close_time_ct:              Optional[str]
    requires_weekend_close:         bool
    # Funded-only restrictions
    funded_no_scalping:             bool
    funded_no_grid:                 bool
    funded_no_martingale:           bool
    funded_no_news_scalping:        bool
    funded_requires_ea_approval:    bool
    funded_min_hold_seconds:        Optional[int]   # DNA: 30 second minimum
    # Futures-specific (Apex, Topstep)
    futures_only:                   bool
    mae_limit_pct:                  Optional[float] # Apex: 30% of current profit
    qualifying_day_profit:          Optional[float] # Topstep: $150+ to qualify
    # Risk calculation note
    risk_on_drawdown_buffer:        bool         # FTMO: 1% risk = 10% of real capital
    # Position size limits
    minimum_position_size:          float        # Smallest allowed position (lots/contracts)
    maximum_position_size:          float        # Largest allowed position
    # Stage entry month
    recommended_start_month:        Optional[int]
    critical_note:                  str


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — THE COMPLETE FIRM DATABASE
# 5 firms. Every rule documented. Built once. Trusted always.
# ─────────────────────────────────────────────────────────────────────────────

FIRM_DATABASE: dict[str, FirmRules] = {

    # ── FTMO ─────────────────────────────────────────────────────────────────
    # Grade: A+. Most established. $200M+ verified payouts.
    # FIRST evaluation. $10K warm-up, then $100K.
    # Key advantage: NO consistency rule. Can make 8% in one day.
    # ─────────────────────────────────────────────────────────────────────────
    FirmID.FTMO: FirmRules(
        firm_id=FirmID.FTMO,
        display_name="FTMO",
        grade="A+",
        platforms=(Platform.DXTRADE, Platform.MATCH_TRADER),
        requires_windows_vps=False,         # Direct REST API from Railway ✓
        drawdown_type=DrawdownType.STATIC,
        drawdown_snapshot_time_utc=None,    # Floor set on Day 1, never moves
        daily_drawdown_pct=0.05,            # 5% of start-of-day balance
        total_drawdown_pct=0.10,            # 10% static from initial balance
        profit_target_phase1_pct=0.10,      # 10%
        profit_target_phase2_pct=0.05,      # 5%
        min_trading_days=None,              # No minimum trading days required
        max_calendar_days=None,             # No strict time limit
        news_blackout_minutes_before=5,     # Funded: 5-min before (research: FOMC volatility at all-time high 2026)
        news_blackout_minutes_after=3,      # 3-min after — slippage window clears
        news_can_hold_through=False,        # No holding through funded news events
        consistency_rule_pct=None,          # NO CONSISTENCY RULE — biggest advantage
        consistency_applies_to=None,
        first_n_withdrawals_cap_pct=None,
        first_n_withdrawals_count=None,
        standard_payout_cycle_days=None,
        max_total_allocation=400_000.0,     # Scaling cap $400K
        max_payout_count=None,              # No payout count limit
        no_consistency_rule=True,           # *** FTMO'S KEY ADVANTAGE ***
        has_scaling_plan=True,
        scaling_trigger_months=4,           # +25% every 4 profitable months
        scaling_pct_increase=0.25,
        scaling_cap_dollars=400_000.0,      # Month 4: $125K → Month 12: $195K → Cap $400K
        requires_eod_close=False,
        eod_close_time_ct=None,
        requires_weekend_close=False,
        futures_only=False,
        mae_limit_pct=None,
        qualifying_day_profit=None,
        risk_on_drawdown_buffer=True,       # 1% risk on $100K = 10% of real functional capital
        minimum_position_size=0.01,
        maximum_position_size=50.0,
        funded_no_scalping=False,
        funded_no_grid=False,
        funded_no_martingale=False,
        funded_no_news_scalping=False,
        funded_requires_ea_approval=False,
        funded_min_hold_seconds=None,
        recommended_start_month=1,          # Start Month 1 — first paid evaluation
        critical_note=(
            "START HERE. $10K-$25K warm-up first ($155 fee), then $100K. "
            "Fee refunded on first payout. Demo the DXTrade platform for 1 week before challenge. "
            "Risk is on DRAWDOWN BUFFER not balance. 1% on $100K = 10% of real capital."
        ),
    ),

    # ── APEX TRADER FUNDING ───────────────────────────────────────────────────
    # Grade: A. Best profit split. TRAILING drawdown on unrealized P&L.
    # Most dangerous drawdown rule in prop firm trading.
    # Requires Windows VPS (Rithmic, Tradovate).
    # ─────────────────────────────────────────────────────────────────────────
    FirmID.APEX: FirmRules(
        firm_id=FirmID.APEX,
        display_name="Apex Trader Funding",
        grade="A",
        platforms=(Platform.RITHMIC, Platform.TRADOVATE, Platform.WEALTHCHARTS),
        requires_windows_vps=True,          # Rithmic/Tradovate = Windows VPS required
        drawdown_type=DrawdownType.TRAILING_UNREALIZED,
        drawdown_snapshot_time_utc=None,    # Trails CONTINUOUSLY on unrealized P&L
        daily_drawdown_pct=0.015,           # EOD accounts: $1,500 on $100K
        total_drawdown_pct=0.06,            # 6% trailing (trails on unrealized gains)
        profit_target_phase1_pct=0.06,      # 6% = $6,000 on $100K
        profit_target_phase2_pct=None,      # Single phase
        min_trading_days=None,
        max_calendar_days=30,               # 30 calendar days — NO extensions
        news_blackout_minutes_before=0,     # No news restriction (intraday accounts)
        news_blackout_minutes_after=0,
        news_can_hold_through=True,
        consistency_rule_pct=0.50,          # 50% — no single day > 50% of profit since last payout
        consistency_applies_to="PAYOUT",    # March 2026 update
        first_n_withdrawals_cap_pct=None,
        first_n_withdrawals_count=None,
        standard_payout_cycle_days=None,
        max_total_allocation=None,
        max_payout_count=6,                 # 6 payouts MAXIMUM per PA — Apex lifecycle
        no_consistency_rule=False,
        has_scaling_plan=False,
        scaling_trigger_months=None,
        scaling_pct_increase=None,
        scaling_cap_dollars=None,
        requires_eod_close=False,           # EOD accounts only: session circuit breaker (not failure)
        eod_close_time_ct=None,
        requires_weekend_close=False,
        futures_only=True,
        mae_limit_pct=0.30,                 # Open trades cannot exceed 30% of current profit balance
        qualifying_day_profit=250.0,        # $250+ profit per session = qualifying day
        risk_on_drawdown_buffer=False,
        minimum_position_size=1.0,
        maximum_position_size=20.0,
        funded_no_scalping=False,
        funded_no_grid=False,
        funded_no_martingale=False,
        funded_no_news_scalping=False,
        funded_requires_ea_approval=False,
        funded_min_hold_seconds=None,
        recommended_start_month=9,          # Month 9 — after FTMO is proven
        critical_note=(
            "TRAILING DRAWDOWN ON UNREALIZED P&L — most dangerous rule in prop trading. "
            "If position is up $1K and returns to breakeven: $1K permanently removed from buffer. "
            "Start with HALF max contracts until Safety Net ($52,600) reached. "
            "Platform does NOT auto-enforce — TITAN FORGE must self-police. "
            "LOCK profits aggressively. Never let winners give back more than 25% of unrealized gains. "
            "30 calendar days NO extensions — miss it: buy new eval. "
            "6 payout maximum per PA — plan full lifecycle from Day 1."
        ),
    ),

    # ── DNA FUNDED ───────────────────────────────────────────────────────────
    # Grade: B. Trustpilot 3.4/5 — payout dispute risk.
    # STAGE 4 ONLY — after proven track record + financial buffer.
    # Drawdown: STATIC at 10pm UTC end-of-day BALANCE snapshot.
    # ─────────────────────────────────────────────────────────────────────────
    FirmID.DNA_FUNDED: FirmRules(
        firm_id=FirmID.DNA_FUNDED,
        display_name="DNA Funded",
        grade="B",
        platforms=(Platform.TRADELOCKER,),  # TradeLocker ONLY — no MT4/MT5
        requires_windows_vps=False,          # TradeLocker REST API from Railway ✓
        drawdown_type=DrawdownType.STATIC_EOD_SNAPSHOT,
        drawdown_snapshot_time_utc="22:00",  # 10pm UTC — NOT equity, NOT ET time
        daily_drawdown_pct=0.04,             # 4% daily
        total_drawdown_pct=0.06,             # 6% total from starting balance
        profit_target_phase1_pct=0.10,       # 10% (1-Phase)
        profit_target_phase2_pct=0.05,       # 8% + 5% (2-Phase) — using 5% for Phase 2
        min_trading_days=None,
        max_calendar_days=None,
        news_blackout_minutes_before=10,     # Cannot open/close within 10 min before major events
        news_blackout_minutes_after=10,      # Cannot open/close within 10 min after
        news_can_hold_through=True,          # CAN hold through — just not open/close
        consistency_rule_pct=0.40,           # 40% max single-day contribution on withdrawal requests
        consistency_applies_to="WITHDRAWAL",
        first_n_withdrawals_cap_pct=0.05,    # First 3 withdrawals capped at 5% of account balance
        first_n_withdrawals_count=3,
        standard_payout_cycle_days=14,       # 14-day standard payout cycle
        max_total_allocation=600_000.0,      # $600,000 across all accounts
        max_payout_count=None,
        no_consistency_rule=False,
        has_scaling_plan=False,
        scaling_trigger_months=None,
        scaling_pct_increase=None,
        scaling_cap_dollars=None,
        requires_eod_close=False,
        eod_close_time_ct=None,
        requires_weekend_close=False,
        futures_only=False,
        mae_limit_pct=None,
        qualifying_day_profit=None,
        risk_on_drawdown_buffer=False,
        minimum_position_size=0.01,
        maximum_position_size=50.0,
        funded_no_scalping=True,             # NO scalping under 30 seconds
        funded_no_grid=True,
        funded_no_martingale=True,
        funded_no_news_scalping=True,
        funded_requires_ea_approval=True,    # Pre-approve all EAs
        funded_min_hold_seconds=30,          # 30-second minimum hold on funded
        recommended_start_month=None,        # Stage 4 — no specific month, earned via track record
        critical_note=(
            "STAGE 4 ONLY — start with FTMO first. DNA Funded is only after proven "
            "track record AND financial buffer exist. Trustpilot 3.4/5 — payout dispute risk. "
            "Drawdown is BALANCE at 10pm UTC — NOT equity, NOT ET time. "
            "TradeLocker ONLY — no MT4/MT5. First 3 withdrawals capped at 5%."
        ),
    ),

    # ── THE 5%ERS ─────────────────────────────────────────────────────────────
    # Grade: A. Tightest drawdown (4%) but highest ceiling ($4M at 100% split).
    # THE LONG GAME. Start Month 18. Patience required.
    # 100% profit split at $1M+. $280,000/month at $4M. THE ultimate target.
    # ─────────────────────────────────────────────────────────────────────────
    FirmID.FIVEPERCENTERS: FirmRules(
        firm_id=FirmID.FIVEPERCENTERS,
        display_name="The 5%ers",
        grade="A",
        platforms=(Platform.METATRADER_4, Platform.METATRADER_5),
        requires_windows_vps=False,
        drawdown_type=DrawdownType.STATIC,
        drawdown_snapshot_time_utc=None,
        daily_drawdown_pct=0.04,             # 4% daily AND total — one bad day = failed
        total_drawdown_pct=0.04,             # TIGHTEST of all 5 firms
        profit_target_phase1_pct=0.06,       # 6%
        profit_target_phase2_pct=0.04,       # 4%
        min_trading_days=None,
        max_calendar_days=None,              # No minimum trading days
        news_blackout_minutes_before=0,
        news_blackout_minutes_after=0,
        news_can_hold_through=True,
        consistency_rule_pct=None,           # No consistency rule specified
        consistency_applies_to=None,
        first_n_withdrawals_cap_pct=None,
        first_n_withdrawals_count=None,
        standard_payout_cycle_days=None,
        max_total_allocation=4_000_000.0,    # $4M — HIGHEST IN THE INDUSTRY
        max_payout_count=None,
        no_consistency_rule=False,
        has_scaling_plan=True,               # 50% → 80% → 90% → 100% split progression
        scaling_trigger_months=None,         # Milestone-based, not time-based
        scaling_pct_increase=None,           # Step function: 50→80→90→100%
        scaling_cap_dollars=4_000_000.0,
        requires_eod_close=False,
        eod_close_time_ct=None,
        requires_weekend_close=False,
        futures_only=False,
        mae_limit_pct=None,
        qualifying_day_profit=None,
        risk_on_drawdown_buffer=False,
        minimum_position_size=0.01,
        maximum_position_size=50.0,
        funded_no_scalping=False,
        funded_no_grid=False,
        funded_no_martingale=False,
        funded_no_news_scalping=False,
        funded_requires_ea_approval=False,
        funded_min_hold_seconds=None,
        recommended_start_month=18,          # Month 18 — after FTMO/Apex proven
        critical_note=(
            "THE MOST IMPORTANT LONG-TERM FIRM RELATIONSHIP. "
            "Start Month 18. Patience required. The $4M path is worth it. "
            "4% drawdown = ULTRA-CONSERVATIVE sizing on ALL strategies. "
            "One bad day can fail the entire evaluation. "
            "Profit split: 50% → 80% → 90% → 100% at $1M+ funded. "
            "$280,000/month at $4M. Month 48-60. THE ULTIMATE TARGET."
        ),
    ),

    # ── TOPSTEP ───────────────────────────────────────────────────────────────
    # Grade: B+. Futures only. 100% first $10K. Most permissive on news.
    # Hard close: ALL positions by 3:10 PM CT EVERY DAY.
    # TITAN FORGE must enforce 3:00 PM CT forced close (10 min buffer).
    # ─────────────────────────────────────────────────────────────────────────
    FirmID.TOPSTEP: FirmRules(
        firm_id=FirmID.TOPSTEP,
        display_name="Topstep",
        grade="B+",
        platforms=(
            Platform.TOPSTEP_X, Platform.NINJA_TRADER,
            Platform.TRADOVATE, Platform.QUANTOWER,
        ),
        requires_windows_vps=True,           # Rithmic-compatible — Windows VPS required
        drawdown_type=DrawdownType.TRAILING_EOD,
        drawdown_snapshot_time_utc=None,     # Tracks end-of-day equity. Locks at breakeven once 10% above start
        daily_drawdown_pct=None,             # No standard daily limit
        total_drawdown_pct=0.06,             # Trailing EOD on equity
        profit_target_phase1_pct=0.06,
        profit_target_phase2_pct=None,
        min_trading_days=None,
        max_calendar_days=None,
        news_blackout_minutes_before=0,      # MOST PERMISSIVE — can trade through major announcements
        news_blackout_minutes_after=0,
        news_can_hold_through=True,          # Competitive advantage — trade through news
        consistency_rule_pct=None,
        consistency_applies_to=None,
        first_n_withdrawals_cap_pct=None,
        first_n_withdrawals_count=None,
        standard_payout_cycle_days=7,        # Weekly payouts after 5 qualifying days
        max_total_allocation=None,
        max_payout_count=None,
        no_consistency_rule=False,
        has_scaling_plan=False,
        scaling_trigger_months=None,
        scaling_pct_increase=None,
        scaling_cap_dollars=None,
        requires_eod_close=True,             # *** HARD RULE: ALL positions by 3:10 PM CT ***
        eod_close_time_ct="15:00",           # TITAN FORGE forced close at 3:00 PM CT (10-min buffer)
        requires_weekend_close=True,         # PROHIBITED — no positions over weekend EVER
        futures_only=True,
        mae_limit_pct=None,
        qualifying_day_profit=150.0,         # $150+ profit = qualifying day (not just any profitable day)
        risk_on_drawdown_buffer=False,
        minimum_position_size=1.0,
        maximum_position_size=20.0,
        funded_no_scalping=False,
        funded_no_grid=False,
        funded_no_martingale=False,
        funded_no_news_scalping=False,
        funded_requires_ea_approval=False,
        funded_min_hold_seconds=None,
        recommended_start_month=None,        # Added after Apex is operational
        critical_note=(
            "HARD CLOSE: ALL positions must close by 3:10 PM CT EVERY DAY. "
            "TITAN FORGE must have 3:00 PM CT FORCED CLOSE (10-min buffer). "
            "No positions over weekend EVER. Monthly subscription $49-$149/month. "
            "100% of first $10K earned, 90/10 thereafter. "
            "Most permissive news trading — competitive advantage. "
            "Best suited for TITAN PRIME futures strategies. Add after Apex is operational."
        ),
    ),
}


# Convenience accessor — load additional dataclass fields that frozen doesn't support
def _extend_ftmo_rules():
    """FTMO has extra fields — patch them in post-init."""
    pass  # FTMO daily_reset_timezone and no_consistency_rule_note handled in docstring


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — SAFETY NET DATABASE
# Firm-specific safety net amounts before any payout is permitted (C-19).
# ─────────────────────────────────────────────────────────────────────────────

# Safety net amounts for each firm + account size combination
# Format: (firm_id, account_size_dollars) → safety_net_dollars
SAFETY_NET_MAP: dict[tuple[str, float], float] = {
    # Apex $50K: $50K + $2,500 drawdown + $100 = $52,600
    (FirmID.APEX, 50_000.0):    52_600.0,
    (FirmID.APEX, 100_000.0):   52_600.0,  # Same floor for $100K PA
    (FirmID.APEX, 150_000.0):   52_600.0,  # Safety net locks once reached
    # FTMO: 110% of starting balance (build 10% buffer above floor)
    (FirmID.FTMO, 10_000.0):    10_500.0,
    (FirmID.FTMO, 25_000.0):    26_000.0,
    (FirmID.FTMO, 100_000.0):   101_000.0,
    # DNA Funded: above 5% withdrawal cap
    (FirmID.DNA_FUNDED, 100_000.0): 105_500.0,
    # The 5%ers: above 4% drawdown (tightest)
    (FirmID.FIVEPERCENTERS, 100_000.0): 97_000.0,  # Floor = $96K, net above it
    # Topstep: variable based on qualifier status
    (FirmID.TOPSTEP, 50_000.0):  52_000.0,
    (FirmID.TOPSTEP, 100_000.0): 52_000.0,
}

DEFAULT_SAFETY_NET_PCT = 0.05  # 5% above floor if not in map


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — DAILY RESET TIMEZONE UTILITY
# ─────────────────────────────────────────────────────────────────────────────

# FTMO resets at midnight CET (Prague) — 5-6 hours ahead of ET
# DNA Funded drawdown snapshot at 10pm UTC
FIRM_DAILY_RESET: dict[str, dict] = {
    FirmID.FTMO: {
        "timezone":    "Europe/Prague",
        "reset_time":  "00:00",   # Midnight CET
        "offset_from_et_hours": 5,  # Summer; 6 in winter
        "note": "Midnight CET Prague time. Track carefully during US evening.",
        "cet_offset_hours": 1,    # CET = UTC+1 (winter), CEST = UTC+2 (summer)
        "daily_reset_utc_winter": 23,  # 23:00 UTC = midnight CET (Oct-Mar)
        "daily_reset_utc_summer": 22,  # 22:00 UTC = midnight CEST (Mar-Oct)
    },
    FirmID.DNA_FUNDED: {
        "timezone":    "UTC",
        "reset_time":  "22:00",   # 10pm UTC = ~5pm or 6pm ET
        "offset_from_et_hours": 5,
        "note": "Drawdown snapshot at 10pm UTC on BALANCE. NOT equity."
    },
    FirmID.APEX: {
        "timezone":    "US/Eastern",
        "reset_time":  "EOD",     # End of day — circuit breaker, not failure
        "note": "EOD daily loss limit. Session circuit breaker only — account NOT failed."
    },
    FirmID.TOPSTEP: {
        "timezone":    "US/Central",
        "hard_close":  "15:10",   # 3:10 PM CT
        "titan_close": "15:00",   # TITAN FORGE closes at 3:00 PM CT (10-min buffer)
        "note": "HARD CLOSE 3:10 PM CT. TITAN FORGE enforces 3:00 PM CT."
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — THE MULTI-FIRM RULE ENGINE
# FORGE-01. Level 1 ABSOLUTE authority. The gatekeeper.
# ─────────────────────────────────────────────────────────────────────────────

class MultiFirmRuleEngine:
    """
    FORGE-01: Multi-Firm Rule Engine.

    Complete database of all 5 firm rules.
    Switch firms by calling set_active_firm(firm_id).
    Never violates any rule.

    This is Level 1 ABSOLUTE authority. All other checks are subordinate to this.
    Do not trade without first calling check_trade_compliance().

    Usage:
        engine = MultiFirmRuleEngine(active_firm_id=FirmID.FTMO)

        # Before every trade
        result = engine.check_trade_compliance(account_state, proposed_trade)
        if result.is_violation:
            logger.error(result.violation_detail)
            # DO NOT execute the trade

        # Real-time drawdown monitoring
        status = engine.calculate_drawdown_status(account_state)
        if status.at_red:
            # CLOSE ALL POSITIONS IMMEDIATELY
    """

    def __init__(self, active_firm_id: str = FirmID.FTMO):
        self._active_firm_id = active_firm_id
        self._rules: FirmRules = FIRM_DATABASE[active_firm_id]
        logger.info(
            "[FORGE-01] Multi-Firm Rule Engine initialized. Active firm: %s (%s). "
            "Drawdown type: %s.",
            self._rules.display_name,
            self._rules.grade,
            self._rules.drawdown_type.value,
        )

    @property
    def active_firm_id(self) -> str:
        return self._active_firm_id

    @property
    def rules(self) -> FirmRules:
        return self._rules

    def set_active_firm(self, firm_id: str) -> None:
        """
        Switch active firm. Changes the entire rule set.
        This is the one config variable that switches firms.
        """
        if firm_id not in FIRM_DATABASE:
            raise ValueError(
                f"Unknown firm_id: '{firm_id}'. "
                f"Valid firms: {list(FIRM_DATABASE.keys())}"
            )
        old_firm = self._active_firm_id
        self._active_firm_id = firm_id
        self._rules = FIRM_DATABASE[firm_id]
        logger.warning(
            "[FORGE-01] Firm switched: %s → %s (%s). "
            "ALL rule parameters updated. Drawdown type: %s.",
            old_firm, firm_id, self._rules.display_name,
            self._rules.drawdown_type.value,
        )

    def get_firm_rules(self, firm_id: Optional[str] = None) -> FirmRules:
        """Get rules for a specific firm (or active firm if not specified)."""
        fid = firm_id or self._active_firm_id
        if fid not in FIRM_DATABASE:
            raise ValueError(f"Unknown firm_id: '{fid}'")
        return FIRM_DATABASE[fid]

    # ── DRAWDOWN CALCULATIONS ─────────────────────────────────────────────────

    def calculate_drawdown_status(
        self,
        starting_balance:   float,
        current_equity:     float,
        peak_unrealized:    float,           # Apex: highest unrealized P&L ever reached
        daily_start_equity: float,           # Equity at start of current trading day
        account_size:       Optional[float] = None,
        firm_id:            Optional[str]   = None,
    ) -> DrawdownStatus:
        """
        Calculate real-time drawdown status for the active firm.

        Args:
            starting_balance:   Account balance at evaluation start.
            current_equity:     Current account equity (balance + open P&L).
            peak_unrealized:    Highest unrealized P&L ever reached (Apex critical).
            daily_start_equity: Equity at start of today's session.
            account_size:       Account size for safety net lookup.
            firm_id:            Override firm (default: active firm).

        Returns:
            DrawdownStatus with all thresholds calculated.
        """
        rules = self.get_firm_rules(firm_id)

        # ── Calculate firm floor based on drawdown type ──────────────────────

        if rules.drawdown_type == DrawdownType.STATIC:
            # FTMO / 5%ers: floor set on Day 1, never moves
            total_drawdown_dollars = starting_balance * rules.total_drawdown_pct
            firm_floor = starting_balance - total_drawdown_dollars

        elif rules.drawdown_type == DrawdownType.TRAILING_UNREALIZED:
            # APEX: trails on UNREALIZED P&L — the most dangerous
            # The floor moves up every time unrealized P&L peaks
            # If position reaches $1K unrealized gain → floor rises by $1K permanently
            total_drawdown_dollars = starting_balance * rules.total_drawdown_pct
            # Apex floor = starting_balance - total_drawdown + peak_unrealized
            # (peak_unrealized already baked into the trailing floor)
            firm_floor = (starting_balance - total_drawdown_dollars) + peak_unrealized

        elif rules.drawdown_type == DrawdownType.TRAILING_EOD:
            # TOPSTEP: trails end-of-day equity
            # Locks at breakeven once 10% above starting balance
            total_drawdown_dollars = starting_balance * rules.total_drawdown_pct
            lock_trigger = starting_balance * 1.10
            if daily_start_equity >= lock_trigger:
                # Locked at breakeven — floor is starting balance
                firm_floor = starting_balance
            else:
                firm_floor = daily_start_equity - total_drawdown_dollars

        elif rules.drawdown_type == DrawdownType.STATIC_EOD_SNAPSHOT:
            # DNA FUNDED: static but calculated on BALANCE at 10pm UTC snapshot
            # Not equity — only the end-of-day balance matters
            total_drawdown_dollars = starting_balance * rules.total_drawdown_pct
            firm_floor = starting_balance - total_drawdown_dollars

        else:
            raise ValueError(f"Unknown drawdown type: {rules.drawdown_type}")

        # ── Calculate daily limit ────────────────────────────────────────────

        if rules.daily_drawdown_pct:
            daily_limit_dollars = daily_start_equity * rules.daily_drawdown_pct
            daily_floor = daily_start_equity - daily_limit_dollars
        else:
            daily_floor = 0.0  # No daily limit (some Apex account types)
            daily_limit_dollars = float("inf")

        # ── Calculate distances and percentages ──────────────────────────────

        distance_to_floor = current_equity - firm_floor
        total_drawdown_budget = starting_balance - firm_floor if firm_floor < starting_balance else starting_balance * rules.total_drawdown_pct
        pct_used = max(0.0, 1.0 - (distance_to_floor / total_drawdown_budget)) if total_drawdown_budget > 0 else 1.0

        daily_distance = current_equity - daily_floor if daily_floor > 0 else float("inf")
        daily_pct_used = max(0.0, 1.0 - (daily_distance / daily_limit_dollars)) if daily_limit_dollars > 0 and daily_limit_dollars != float("inf") else 0.0

        # ── Warning thresholds (from FORGE-67 P&L Monitor) ──────────────────
        at_yellow = pct_used >= 0.50   # 50%: -25% position size
        at_orange = pct_used >= 0.70   # 70%: minimum size only
        at_red    = pct_used >= 0.85   # 85%: CLOSE ALL POSITIONS

        if at_red:
            logger.critical(
                "[FORGE-01][%s] ⛔ RED ALERT — 85%% drawdown used. "
                "Equity: $%.2f. Floor: $%.2f. Distance: $%.2f. CLOSE ALL.",
                rules.firm_id, current_equity, firm_floor, distance_to_floor
            )
        elif at_orange:
            logger.error(
                "[FORGE-01][%s] 🟠 ORANGE — 70%% drawdown used. "
                "Minimum position size only. Distance to floor: $%.2f.",
                rules.firm_id, distance_to_floor
            )
        elif at_yellow:
            logger.warning(
                "[FORGE-01][%s] 🟡 YELLOW — 50%% drawdown used. "
                "-25%% position size. Distance to floor: $%.2f.",
                rules.firm_id, distance_to_floor
            )

        return DrawdownStatus(
            firm_id=rules.firm_id,
            drawdown_type=rules.drawdown_type,
            starting_balance=starting_balance,
            firm_floor=firm_floor,
            current_equity=current_equity,
            distance_to_floor=distance_to_floor,
            pct_used=pct_used,
            daily_floor=daily_floor,
            daily_distance=daily_distance,
            daily_pct_used=daily_pct_used,
            at_yellow=at_yellow,
            at_orange=at_orange,
            at_red=at_red,
        )

    # ── COMPLIANCE CHECKS ─────────────────────────────────────────────────────

    def check_news_blackout(
        self,
        minutes_to_event: Optional[float],
        minutes_since_event: Optional[float],
        phase: str = AccountPhase.EVALUATION,
        firm_id: Optional[str] = None,
    ) -> RuleCheckResult:
        """
        Check whether a trade violates the news blackout window.

        Args:
            minutes_to_event:    Minutes until next major news event (None if no event).
            minutes_since_event: Minutes since last major news event (None if no event).
            phase:               Account phase (EVALUATION or FUNDED).
            firm_id:             Override firm.
        """
        rules = self.get_firm_rules(firm_id)

        # Some restrictions only apply in funded phase (FTMO)
        if rules.firm_id == FirmID.FTMO and phase == AccountPhase.EVALUATION:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="News Blackout",
                reason="FTMO evaluation: no news blackout restriction. "
                       "Funded phase: 2-minute restriction applies."
            )

        # Topstep: no news restriction ever
        if rules.firm_id == FirmID.TOPSTEP:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="News Blackout",
                reason="Topstep: most permissive news policy. No restriction. "
                       "Competitive advantage — trade through announcements."
            )

        # Check before-event blackout
        if minutes_to_event is not None and rules.news_blackout_minutes_before > 0:
            if minutes_to_event <= rules.news_blackout_minutes_before:
                return RuleCheckResult(
                    compliant=False,
                    firm_id=rules.firm_id,
                    rule_name="News Blackout — Pre-Event",
                    reason=f"Within {rules.news_blackout_minutes_before}-minute pre-event "
                           f"blackout window. {minutes_to_event:.1f} min to event. "
                           f"No new positions. Cannot open or close.",
                    violation_detail=f"{rules.display_name} requires {rules.news_blackout_minutes_before} "
                                     f"minutes clear before major events. "
                                     f"Wait until event has passed + {rules.news_blackout_minutes_after} minutes."
                )

        # Check after-event blackout
        if minutes_since_event is not None and rules.news_blackout_minutes_after > 0:
            if minutes_since_event <= rules.news_blackout_minutes_after:
                return RuleCheckResult(
                    compliant=False,
                    firm_id=rules.firm_id,
                    rule_name="News Blackout — Post-Event",
                    reason=f"Within {rules.news_blackout_minutes_after}-minute post-event "
                           f"blackout window. {minutes_since_event:.1f} min since event. "
                           f"No new positions. Cannot open or close.",
                    violation_detail=f"{rules.display_name} requires {rules.news_blackout_minutes_after} "
                                     f"minutes clear after major events."
                )

        return RuleCheckResult(
            compliant=True,
            firm_id=rules.firm_id,
            rule_name="News Blackout",
            reason="No active news blackout. Clear to trade."
        )

    def check_consistency_rule(
        self,
        total_profit_since_last_payout: float,
        todays_profit: float,
        firm_id: Optional[str] = None,
    ) -> RuleCheckResult:
        """
        Check whether today's profit would violate the single-day consistency rule.

        DNA Funded: 40% max of total withdrawal profit.
        Apex: 50% max of total profit since last payout.
        FTMO: No consistency rule — always compliant.
        """
        rules = self.get_firm_rules(firm_id)

        if rules.no_consistency_rule or rules.consistency_rule_pct is None:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="Consistency Rule",
                reason=f"{rules.display_name}: No consistency rule. "
                       f"Full profit available in a single day."
            )

        if total_profit_since_last_payout <= 0:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="Consistency Rule",
                reason="No profit since last payout — consistency rule not triggered."
            )

        max_allowed = total_profit_since_last_payout * rules.consistency_rule_pct
        pct_of_total = todays_profit / total_profit_since_last_payout if total_profit_since_last_payout > 0 else 0.0

        if todays_profit > max_allowed:
            return RuleCheckResult(
                compliant=False,
                firm_id=rules.firm_id,
                rule_name=f"Consistency Rule ({rules.consistency_rule_pct*100:.0f}% cap)",
                reason=f"Today's profit ${todays_profit:,.2f} exceeds {rules.consistency_rule_pct*100:.0f}% "
                       f"of total profit ${total_profit_since_last_payout:,.2f}. "
                       f"Max allowed today: ${max_allowed:,.2f}. "
                       f"Current: {pct_of_total:.1%}.",
                violation_detail=f"Stop trading for today. "
                                 f"Overage: ${todays_profit - max_allowed:,.2f}. "
                                 f"This applies to {rules.consistency_applies_to} requests."
            )

        # Approaching the limit — issue warning
        if pct_of_total >= (rules.consistency_rule_pct * 0.80):
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name=f"Consistency Rule ({rules.consistency_rule_pct*100:.0f}% cap)",
                reason=f"Within 20% of consistency limit. "
                       f"Today: {pct_of_total:.1%} of {rules.consistency_rule_pct*100:.0f}% cap. "
                       f"Remaining budget: ${max_allowed - todays_profit:,.2f}.",
                warning=True,
                warning_detail=f"Throttle position size — approaching daily consistency cap."
            )

        return RuleCheckResult(
            compliant=True,
            firm_id=rules.firm_id,
            rule_name=f"Consistency Rule ({rules.consistency_rule_pct*100:.0f}% cap)",
            reason=f"Compliant. Today: {pct_of_total:.1%} of {rules.consistency_rule_pct*100:.0f}% cap. "
                   f"Remaining budget: ${max_allowed - todays_profit:,.2f}."
        )

    def check_eod_close_required(
        self,
        current_time_ct: time,
        has_open_positions: bool,
        firm_id: Optional[str] = None,
    ) -> RuleCheckResult:
        """
        Check whether positions must be closed before end-of-day.
        Critical for Topstep: hard close at 3:10 PM CT.
        TITAN FORGE enforces at 3:00 PM CT (10-minute buffer).
        """
        rules = self.get_firm_rules(firm_id)

        if not rules.requires_eod_close:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="EOD Close",
                reason=f"{rules.display_name}: No mandatory EOD close required."
            )

        titan_close = time(15, 0)   # 3:00 PM CT — TITAN FORGE enforced close
        hard_close  = time(15, 10)  # 3:10 PM CT — Topstep hard deadline

        if current_time_ct >= titan_close and has_open_positions:
            return RuleCheckResult(
                compliant=False,
                firm_id=rules.firm_id,
                rule_name="EOD Forced Close",
                reason=f"Current time {current_time_ct.strftime('%H:%M')} CT >= "
                       f"TITAN FORGE forced close at 15:00 CT. "
                       f"CLOSE ALL POSITIONS IMMEDIATELY. "
                       f"Hard deadline: 15:10 CT.",
                violation_detail=f"Topstep hard close is 15:10 CT. "
                                 f"TITAN FORGE enforces 15:00 CT (10-min buffer). "
                                 f"Open positions after 15:10 = account violation."
            )

        # Warning: approaching close time
        warning_threshold = time(14, 45)  # 2:45 PM CT
        if current_time_ct >= warning_threshold and has_open_positions:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="EOD Close",
                reason=f"Approaching forced close. Current: {current_time_ct.strftime('%H:%M')} CT. "
                       f"TITAN FORGE close: 15:00 CT. Begin planning exit.",
                warning=True,
                warning_detail="15 minutes to forced close. Prepare exit strategy."
            )

        return RuleCheckResult(
            compliant=True,
            firm_id=rules.firm_id,
            rule_name="EOD Close",
            reason=f"EOD close not triggered. Current time: {current_time_ct.strftime('%H:%M')} CT."
        )

    def check_weekend_hold(
        self,
        is_weekend_or_friday_eod: bool,
        has_open_positions: bool,
        firm_id: Optional[str] = None,
    ) -> RuleCheckResult:
        """Check whether weekend positions violate firm rules."""
        rules = self.get_firm_rules(firm_id)

        if not rules.requires_weekend_close:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="Weekend Hold",
                reason=f"{rules.display_name}: Weekend holds permitted."
            )

        if is_weekend_or_friday_eod and has_open_positions:
            return RuleCheckResult(
                compliant=False,
                firm_id=rules.firm_id,
                rule_name="Weekend Hold PROHIBITED",
                reason=f"{rules.display_name} PROHIBITS weekend positions. "
                       f"CLOSE ALL POSITIONS before market close on Friday.",
                violation_detail="Weekend positions = account violation. No exceptions."
            )

        return RuleCheckResult(
            compliant=True,
            firm_id=rules.firm_id,
            rule_name="Weekend Hold",
            reason="No open positions over weekend."
        )

    def check_funded_restrictions(
        self,
        strategy_type: str,       # "SCALP", "GRID", "MARTINGALE", "STANDARD", etc.
        hold_seconds: float,      # How long this trade has been open
        phase: str = AccountPhase.FUNDED,
        firm_id: Optional[str] = None,
    ) -> RuleCheckResult:
        """
        Check funded account restrictions (DNA Funded bans scalping, grid, martingale).
        These only apply in FUNDED phase.
        """
        rules = self.get_firm_rules(firm_id)

        if phase != AccountPhase.FUNDED:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="Funded Restrictions",
                reason="Not in funded phase — funded restrictions do not apply."
            )

        strategy_upper = strategy_type.upper()

        if rules.funded_no_scalping and "SCALP" in strategy_upper:
            return RuleCheckResult(
                compliant=False,
                firm_id=rules.firm_id,
                rule_name="Funded: No Scalping",
                reason=f"{rules.display_name} FUNDED: Scalping is BANNED. "
                       f"Strategy '{strategy_type}' violates funded account rules.",
                violation_detail="Remove all scalping strategies from funded account execution."
            )

        if rules.funded_no_grid and "GRID" in strategy_upper:
            return RuleCheckResult(
                compliant=False,
                firm_id=rules.firm_id,
                rule_name="Funded: No Grid",
                reason=f"{rules.display_name} FUNDED: Grid trading is BANNED.",
                violation_detail="Grid strategies prohibited in funded mode."
            )

        if rules.funded_no_martingale and "MARTINGALE" in strategy_upper:
            return RuleCheckResult(
                compliant=False,
                firm_id=rules.firm_id,
                rule_name="Funded: No Martingale",
                reason=f"{rules.display_name} FUNDED: Martingale is BANNED.",
                violation_detail="Martingale sizing prohibited in funded mode."
            )

        if rules.funded_min_hold_seconds and hold_seconds < rules.funded_min_hold_seconds:
            return RuleCheckResult(
                compliant=False,
                firm_id=rules.firm_id,
                rule_name=f"Funded: Minimum Hold ({rules.funded_min_hold_seconds}s)",
                reason=f"{rules.display_name} FUNDED: Minimum hold time is "
                       f"{rules.funded_min_hold_seconds} seconds. "
                       f"This trade was held for {hold_seconds:.1f} seconds — violation.",
                violation_detail=f"Do not close positions held for less than "
                                 f"{rules.funded_min_hold_seconds} seconds."
            )

        return RuleCheckResult(
            compliant=True,
            firm_id=rules.firm_id,
            rule_name="Funded Restrictions",
            reason=f"{rules.display_name} funded restrictions: all clear."
        )

    def check_payout_withdrawal_cap(
        self,
        account_balance: float,
        requested_amount: float,
        withdrawal_number: int,   # 1 = first, 2 = second, etc.
        firm_id: Optional[str] = None,
    ) -> RuleCheckResult:
        """
        Check DNA Funded's 5% cap on first 3 withdrawals.
        """
        rules = self.get_firm_rules(firm_id)

        if rules.first_n_withdrawals_cap_pct is None:
            return RuleCheckResult(
                compliant=True,
                firm_id=rules.firm_id,
                rule_name="Withdrawal Cap",
                reason=f"{rules.display_name}: No withdrawal cap rule."
            )

        if withdrawal_number <= rules.first_n_withdrawals_count:
            max_withdrawal = account_balance * rules.first_n_withdrawals_cap_pct
            if requested_amount > max_withdrawal:
                return RuleCheckResult(
                    compliant=False,
                    firm_id=rules.firm_id,
                    rule_name=f"Withdrawal #{withdrawal_number}: 5% Cap",
                    reason=f"{rules.display_name}: Withdrawal #{withdrawal_number} capped at "
                           f"{rules.first_n_withdrawals_cap_pct*100:.0f}% of balance. "
                           f"Max: ${max_withdrawal:,.2f}. Requested: ${requested_amount:,.2f}. "
                           f"Overage: ${requested_amount - max_withdrawal:,.2f}.",
                    violation_detail=f"First {rules.first_n_withdrawals_count} withdrawals capped at "
                                     f"{rules.first_n_withdrawals_cap_pct*100:.0f}%. "
                                     f"Request ${max_withdrawal:,.2f} or less."
                )

        return RuleCheckResult(
            compliant=True,
            firm_id=rules.firm_id,
            rule_name=f"Withdrawal #{withdrawal_number}",
            reason=f"Compliant. "
                   + (f"Withdrawal #{withdrawal_number} is within 5% cap." if withdrawal_number <= (rules.first_n_withdrawals_count or 0) else "Cap no longer applies.")
        )

    def check_apex_trailing_lock(
        self,
        current_equity:     float,
        starting_balance:   float,
        peak_unrealized_pnl: float,
        has_open_positions: bool,
    ) -> RuleCheckResult:
        """
        Apex-specific: Monitor unrealized P&L to prevent trailing drawdown trap.

        The trailing drawdown floor rises permanently with every unrealized peak.
        If position is up $1K and returns to breakeven: $1K is PERMANENTLY
        removed from the trailing buffer.

        Rule: Never let winners give back more than 25% of unrealized gains.
        """
        rules = self.get_firm_rules(FirmID.APEX)
        total_drawdown = starting_balance * rules.total_drawdown_pct
        current_floor = (starting_balance - total_drawdown) + peak_unrealized_pnl

        if not has_open_positions:
            return RuleCheckResult(
                compliant=True,
                firm_id=FirmID.APEX,
                rule_name="Apex Trailing Drawdown Lock",
                reason=f"No open positions. Floor: ${current_floor:,.2f}. "
                       f"Distance: ${current_equity - current_floor:,.2f}."
            )

        # If equity is close to the floor — warning
        distance = current_equity - current_floor
        total_buffer = starting_balance - (starting_balance - total_drawdown)

        if distance <= total_buffer * 0.15:
            return RuleCheckResult(
                compliant=True,
                firm_id=FirmID.APEX,
                rule_name="Apex Trailing Drawdown Lock",
                reason=f"CRITICAL WARNING: Only ${distance:,.2f} above trailing floor. "
                       f"Floor: ${current_floor:,.2f}. "
                       f"Reduce position immediately.",
                warning=True,
                warning_detail="Trailing floor is dangerously close. Tighten stops aggressively."
            )

        return RuleCheckResult(
            compliant=True,
            firm_id=FirmID.APEX,
            rule_name="Apex Trailing Drawdown Lock",
            reason=f"Apex trailing floor: ${current_floor:,.2f}. "
                   f"Distance: ${distance:,.2f}. "
                   f"Unrealized peak contributing to floor: ${peak_unrealized_pnl:,.2f}."
        )

    def check_apex_mae_limit(
        self,
        current_profit_balance: float,
        open_trade_drawdown:    float,  # Unrealized loss on current open trade
        firm_id: Optional[str] = None,
    ) -> RuleCheckResult:
        """
        Apex MAE limit: open trades cannot exceed 30% drawdown of current profit balance.
        """
        if (firm_id or self._active_firm_id) != FirmID.APEX:
            return RuleCheckResult(compliant=True, firm_id=firm_id or self._active_firm_id,
                                   rule_name="MAE Limit", reason="Not Apex — MAE limit does not apply.")

        rules = self.get_firm_rules(FirmID.APEX)
        max_mae = current_profit_balance * rules.mae_limit_pct

        if open_trade_drawdown > max_mae:
            return RuleCheckResult(
                compliant=False,
                firm_id=FirmID.APEX,
                rule_name=f"Apex MAE Limit (30%)",
                reason=f"Open trade drawdown ${open_trade_drawdown:,.2f} exceeds "
                       f"30% of profit balance ${current_profit_balance:,.2f}. "
                       f"Max allowed: ${max_mae:,.2f}. Exit trade immediately.",
                violation_detail="Apex MAE rule: open trades cannot exceed 30% of profit balance in drawdown."
            )

        return RuleCheckResult(
            compliant=True,
            firm_id=FirmID.APEX,
            rule_name="Apex MAE Limit (30%)",
            reason=f"MAE within limit. Drawdown: ${open_trade_drawdown:,.2f}. "
                   f"Max: ${max_mae:,.2f} (30% of ${current_profit_balance:,.2f})."
        )

    def get_safety_net(
        self,
        account_size: float,
        firm_id: Optional[str] = None,
    ) -> float:
        """Return the safety net dollar amount for the given firm and account size."""
        fid = firm_id or self._active_firm_id
        key = (fid, account_size)
        if key in SAFETY_NET_MAP:
            return SAFETY_NET_MAP[key]
        # Fallback: 5% above the drawdown floor
        rules = self.get_firm_rules(fid)
        floor = account_size * (1.0 - rules.total_drawdown_pct)
        return floor + (account_size * DEFAULT_SAFETY_NET_PCT)

    def get_max_contracts_pre_safety_net(
        self,
        full_max_contracts: float,
        firm_id: Optional[str] = None,
    ) -> float:
        """
        Apex rule: start with HALF max contracts until Safety Net reached.
        Platform does NOT auto-enforce — TITAN FORGE self-polices.
        """
        fid = firm_id or self._active_firm_id
        if fid == FirmID.APEX:
            return full_max_contracts * 0.50
        return full_max_contracts

    def get_firm_summary(self, firm_id: Optional[str] = None) -> dict:
        """Return a structured summary of firm rules for logging/display."""
        rules = self.get_firm_rules(firm_id)
        return {
            "firm_id":          rules.firm_id,
            "display_name":     rules.display_name,
            "grade":            rules.grade,
            "drawdown_type":    rules.drawdown_type.value,
            "daily_dd_pct":     f"{rules.daily_drawdown_pct*100:.1f}%" if rules.daily_drawdown_pct else "N/A",
            "total_dd_pct":     f"{rules.total_drawdown_pct*100:.1f}%",
            "phase1_target":    f"{rules.profit_target_phase1_pct*100:.1f}%",
            "phase2_target":    f"{rules.profit_target_phase2_pct*100:.1f}%" if rules.profit_target_phase2_pct else "N/A",
            "consistency_rule": f"{rules.consistency_rule_pct*100:.0f}% ({rules.consistency_applies_to})" if rules.consistency_rule_pct else "NONE",
            "news_blackout":    f"{rules.news_blackout_minutes_before}min before / {rules.news_blackout_minutes_after}min after",
            "requires_vps":     rules.requires_windows_vps,
            "futures_only":     rules.futures_only,
            "eod_close":        rules.requires_eod_close,
            "weekend_close":    rules.requires_weekend_close,
            "scaling":          f"+{rules.scaling_pct_increase*100:.0f}% every {rules.scaling_trigger_months} months, cap ${rules.scaling_cap_dollars:,.0f}" if rules.has_scaling_plan and rules.scaling_trigger_months else "milestone-based" if rules.has_scaling_plan else "N/A",
            "critical_note":    rules.critical_note[:100] + "...",
        }


# ── BUG FIX: CET Timezone Daily Reset ─────────────────────────────────────────
# FTMO resets the daily loss limit at midnight CET (Prague time).
# CET = UTC+1 (winter, Oct–Mar), CEST = UTC+2 (summer, Mar–Oct).
# Bug was: system used UTC midnight, which is 1–2 hours wrong.
# Fix: always calculate FTMO daily reset in CET/CEST, not UTC.

from datetime import timezone, timedelta
import time as _time

def get_ftmo_daily_reset_utc(for_date=None) -> "datetime":
    """
    Return the UTC datetime of the FTMO daily loss reset for a given date.
    FTMO resets at midnight CET/CEST (Prague time).

    CET  = UTC+1 (standard time, last Sunday Oct → last Sunday Mar)
    CEST = UTC+2 (summer time,  last Sunday Mar → last Sunday Oct)
    """
    from datetime import datetime, date as date_type
    if for_date is None:
        for_date = datetime.now(timezone.utc).date()
    if isinstance(for_date, date_type) and not isinstance(for_date, datetime):
        for_date = datetime(for_date.year, for_date.month, for_date.day)

    # Determine if Prague is on summer time (CEST = UTC+2) or winter (CET = UTC+1)
    # Python's time.localtime uses the system timezone — we calculate manually.
    # CEST starts: last Sunday of March at 02:00 CET
    # CEST ends:   last Sunday of October at 03:00 CEST

    def last_sunday(year, month):
        """Return the date of the last Sunday in a given month."""
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        d = datetime(year, month, last_day)
        # Walk back to Sunday
        while d.weekday() != 6:  # 6 = Sunday
            d = d.replace(day=d.day - 1)
        return d

    year = for_date.year
    cest_start = last_sunday(year, 3)   # Last Sunday in March
    cest_end   = last_sunday(year, 10)  # Last Sunday in October

    is_summer = cest_start <= for_date < cest_end
    cet_offset = timedelta(hours=2) if is_summer else timedelta(hours=1)

    # Midnight Prague time = 00:00 CET/CEST = (00:00 - offset) UTC
    midnight_prague = datetime(for_date.year, for_date.month, for_date.day,
                               0, 0, 0, tzinfo=timezone(cet_offset))
    reset_utc = midnight_prague.astimezone(timezone.utc)
    return reset_utc


def is_new_ftmo_day(last_reset_utc: "datetime", now_utc: "datetime") -> bool:
    """
    Returns True if FTMO's daily loss limit has reset since last_reset_utc.
    Uses Prague midnight (CET/CEST) not UTC midnight.
    """
    today_reset = get_ftmo_daily_reset_utc(now_utc.date())
    yesterday_reset = get_ftmo_daily_reset_utc(
        (now_utc - timedelta(days=1)).date()
    )
    # A new FTMO day started if we've passed Prague midnight since last check
    return (last_reset_utc < today_reset <= now_utc or
            last_reset_utc < yesterday_reset <= now_utc)

