"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              triple_layer_loss.py — FORGE-11 + FORGE-67 — Layer 1           ║
║                                                                              ║
║  TRIPLE LAYER LOSS PROTECTION + REAL-TIME P&L MONITOR                       ║
║  Level 3 RISK MANAGEMENT — overrides all strategy signals.                  ║
║                                                                              ║
║  FORGE-11: Three layers of loss protection.                                  ║
║  Any layer triggering modifies or blocks trading. All three run always.      ║
║                                                                              ║
║  Layer 1 — Per-Trade Stop                                                    ║
║    Every trade must have a stop before entry.                                ║
║    No stop = no entry. Non-negotiable.                                       ║
║    Stop must be at a technically valid level within allowed range.           ║
║                                                                              ║
║  Layer 2 — Daily Circuit Breaker                                             ║
║    If session drawdown reaches TITAN_DAILY_LIMIT, stop trading for the day. ║
║    Set 20% tighter than the firm's hard limit — leave buffer.                ║
║    Apex: $1,200 TITAN limit (firm's $1,500 limit - 20% buffer).             ║
║                                                                              ║
║  Layer 3 — Drawdown Guardian                                                 ║
║    Monitors total evaluation drawdown continuously.                          ║
║    At 60%: warning — size reduction begins (connects to safety margin).      ║
║    At 70%: orange — minimum position size only.                              ║
║    At 85%: red — close all, no new entries.                                  ║
║                                                                              ║
║  FORGE-67: Real-Time P&L Monitor                                             ║
║    30-second updates. Provides the drawdown readings Layer 3 uses.           ║
║    Yellow 50% | Orange 70% | Red 85% → maps to size adjustments.            ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from firm_rules import FirmID, MultiFirmRuleEngine, DrawdownType

logger = logging.getLogger("titan_forge.triple_layer_loss")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Layer 3: Drawdown guardian thresholds (fraction of firm's total drawdown limit)
GUARDIAN_WARNING_PCT:   float = 0.60   # 60% → size reduction begins
GUARDIAN_ORANGE_PCT:    float = 0.70   # 70% → minimum size only
GUARDIAN_RED_PCT:       float = 0.85   # 85% → close all, no new entries

# Layer 2: TITAN FORGE daily limit = firm limit × (1 - DAILY_BUFFER)
# We trip our own circuit breaker before the firm does
TITAN_DAILY_BUFFER:     float = 0.20   # 20% safety buffer vs firm limit

# Layer 1: Per-trade stop validation bounds
MAX_STOP_PCT_OF_ACCOUNT:  float = 0.02   # Stop can't risk more than 2% of account
MIN_STOP_PCT_OF_ATR:      float = 0.30   # Stop must be at least 0.3 ATR wide

# FORGE-67: P&L monitor size modifications
YELLOW_SIZE_MODIFIER:   float = 0.75   # 50%+ drawdown → -25% size
ORANGE_SIZE_MODIFIER:   float = 0.01   # 70%+ → minimum (effectively min size)
RED_SIZE_MODIFIER:      float = 0.00   # 85%+ → no entries


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — LAYER STATES AND RESULTS
# ─────────────────────────────────────────────────────────────────────────────

class ProtectionLevel(Enum):
    """Current protection state across all three layers."""
    CLEAR    = auto()   # All layers green — normal trading
    CAUTION  = auto()   # Layer 3 at 60%+ — size reduction
    WARNING  = auto()   # Layer 3 at 70%+ — minimum size
    CRITICAL = auto()   # Layer 3 at 85%+ — close all
    DAILY_STOPPED = auto()   # Layer 2 triggered — no trading today
    BLOCKED  = auto()   # Layer 1 failed — this trade has no valid stop


@dataclass
class Layer1Result:
    """Per-trade stop validation result."""
    has_valid_stop:     bool
    stop_level:         Optional[float]     # Price of stop loss
    stop_distance_atr:  float               # Stop distance in ATR units
    risk_pct_account:   float               # Risk as % of account (0.0–1.0)
    is_within_bounds:   bool                # Within min/max bounds
    reason:             str


@dataclass
class Layer2Result:
    """Daily circuit breaker state."""
    circuit_broken:         bool
    daily_loss_dollars:     float           # Today's realized + unrealized losses
    titan_daily_limit:      float           # TITAN's threshold (firm limit × 0.80)
    firm_daily_limit:       float           # Firm's actual hard limit
    daily_pct_consumed:     float           # 0.0–1.0 of TITAN limit
    approaching_limit:      bool            # Within 80% of TITAN limit
    reason:                 str


@dataclass
class Layer3Result:
    """Drawdown guardian state."""
    protection_level:       ProtectionLevel
    drawdown_pct_used:      float           # 0.0–1.0 of total drawdown budget
    drawdown_dollars_used:  float
    drawdown_dollars_remaining: float
    size_modifier:          float           # Multiplier for position size
    new_entries_permitted:  bool
    reason:                 str


@dataclass
class TripleLayerResult:
    """
    Combined result from all three protection layers.
    The most restrictive layer's modifier is applied.
    """
    layer1:             Layer1Result
    layer2:             Layer2Result
    layer3:             Layer3Result
    # Combined outcome
    overall_level:      ProtectionLevel
    entry_permitted:    bool             # False if any layer blocks
    size_modifier:      float            # Most restrictive modifier
    reason:             str

    @property
    def is_blocked(self) -> bool:
        return not self.entry_permitted

    @property
    def should_close_all(self) -> bool:
        return self.overall_level == ProtectionLevel.CRITICAL

    @property
    def daily_stopped(self) -> bool:
        return self.layer2.circuit_broken


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — P&L MONITOR (FORGE-67)
# Real-time drawdown readings that Layer 3 consumes.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PnLSnapshot:
    """
    FORGE-67: Real-Time P&L Monitor snapshot.
    Updated every 30 seconds (or on every tick for critical levels).
    """
    account_id:                 str
    firm_id:                    str
    # Account balances
    starting_balance:           float
    current_balance:            float       # Realized balance
    current_equity:             float       # Balance + open P&L
    open_pnl:                   float       # Unrealized P&L
    # Session (daily) metrics
    session_starting_equity:    float       # Equity at session open
    session_pnl:                float       # Today's P&L (realized + unrealized)
    # Drawdown calculations
    total_drawdown_budget:      float       # Total allowed drawdown in $
    drawdown_used_dollars:      float       # Total drawdown consumed
    drawdown_used_pct:          float       # 0.0–1.0
    daily_drawdown_budget:      float       # Today's daily limit in $
    daily_loss_dollars:         float       # Today's loss so far
    daily_loss_pct:             float       # 0.0–1.0 of daily budget
    # Alert flags
    at_yellow:                  bool        # ≥ 50%
    at_orange:                  bool        # ≥ 70%
    at_red:                     bool        # ≥ 85%
    at_daily_circuit:           bool        # ≥ TITAN daily limit
    # Recommended action
    size_modifier:              float
    action:                     str         # "NORMAL" / "REDUCE" / "MINIMUM" / "CLOSE_ALL"


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — TRIPLE LAYER PROTECTION ENGINE
# FORGE-11 + FORGE-67
# ─────────────────────────────────────────────────────────────────────────────

class TripleLayerProtection:
    """
    FORGE-11 + FORGE-67: Triple Layer Loss Protection + Real-Time P&L Monitor.

    Level 3 RISK MANAGEMENT — overrides all strategy signals.
    All three layers run on every trade. Most restrictive wins.

    Usage:
        tlp = TripleLayerProtection(rule_engine)

        # Before opening a trade:
        result = tlp.check(
            firm_id=FirmID.FTMO,
            account_size=100_000.0,
            current_equity=97_000.0,
            starting_balance=100_000.0,
            session_starting_equity=97_500.0,
            daily_loss_dollars=500.0,
            total_drawdown_used_pct=0.30,
            atr=10.0,
            proposed_stop_price=4790.0,
            current_price=4800.0,
            is_evaluation=True,
        )

        if result.is_blocked:
            # Do not trade — log reason
            return

        position_size *= result.size_modifier

        # Every 30 seconds — update the P&L monitor:
        snapshot = tlp.snapshot(...)
        if snapshot.at_red:
            # CLOSE ALL POSITIONS
    """

    def __init__(self, rule_engine: MultiFirmRuleEngine):
        self._rule_engine = rule_engine

    # ── MAIN CHECK ────────────────────────────────────────────────────────────

    def check(
        self,
        firm_id:                    str,
        account_size:               float,
        current_equity:             float,
        starting_balance:           float,
        session_starting_equity:    float,
        daily_loss_dollars:         float,
        total_drawdown_used_pct:    float,    # 0.0–1.0
        # Layer 1 inputs
        atr:                        float,
        proposed_stop_price:        Optional[float],   # None = no stop defined
        current_price:              float,
        direction:                  str = "long",       # "long" or "short"
        is_evaluation:              bool = True,
        # Optional pre-computed values
        total_drawdown_dollars:     Optional[float] = None,
    ) -> TripleLayerResult:
        """
        Run all three protection layers. Returns the combined result.

        Args:
            firm_id:                  Active firm.
            account_size:             Account size (starting balance).
            current_equity:           Current account equity.
            starting_balance:         Balance at evaluation start.
            session_starting_equity:  Equity at start of today's session.
            daily_loss_dollars:       Today's loss in dollars (positive number).
            total_drawdown_used_pct:  Fraction of total drawdown budget consumed.
            atr:                      Average True Range of the instrument.
            proposed_stop_price:      Proposed stop-loss price (None = no stop set).
            current_price:            Current market price.
            direction:                Trade direction ("long" or "short").
            is_evaluation:            True = evaluation mode.
            total_drawdown_dollars:   Override for total drawdown amount (optional).

        Returns:
            TripleLayerResult — check .entry_permitted and .size_modifier.
        """
        rules = self._rule_engine.get_firm_rules(firm_id)

        # Total drawdown budget
        total_dd_budget = (
            total_drawdown_dollars
            if total_drawdown_dollars is not None
            else starting_balance * rules.total_drawdown_pct
        )

        # Firm daily limit in dollars
        if rules.daily_drawdown_pct:
            firm_daily_limit  = session_starting_equity * rules.daily_drawdown_pct
            titan_daily_limit = firm_daily_limit * (1.0 - TITAN_DAILY_BUFFER)
        else:
            firm_daily_limit  = float("inf")
            titan_daily_limit = float("inf")

        # ── Layer 1: Per-trade stop ────────────────────────────────────────────
        l1 = self._check_layer1(
            proposed_stop_price, current_price, direction,
            atr, account_size, current_equity,
        )

        # ── Layer 2: Daily circuit breaker ────────────────────────────────────
        l2 = self._check_layer2(
            daily_loss_dollars, titan_daily_limit, firm_daily_limit,
        )

        # ── Layer 3: Drawdown guardian ────────────────────────────────────────
        drawdown_used_dollars = total_drawdown_used_pct * total_dd_budget
        drawdown_remaining    = max(0.0, total_dd_budget - drawdown_used_dollars)
        l3 = self._check_layer3(
            total_drawdown_used_pct, drawdown_used_dollars, drawdown_remaining,
        )

        # ── Combine results ───────────────────────────────────────────────────
        return self._combine(l1, l2, l3)

    # ── LAYER 1: PER-TRADE STOP ───────────────────────────────────────────────

    def _check_layer1(
        self,
        stop_price:     Optional[float],
        current_price:  float,
        direction:      str,
        atr:            float,
        account_size:   float,
        current_equity: float,
    ) -> Layer1Result:
        """
        Layer 1: Every trade must have a stop-loss before entry.
        No stop = no entry. Non-negotiable.
        """
        if stop_price is None:
            return Layer1Result(
                has_valid_stop=False,
                stop_level=None,
                stop_distance_atr=0.0,
                risk_pct_account=0.0,
                is_within_bounds=False,
                reason="NO STOP DEFINED. Every trade requires a stop-loss before entry. "
                       "Non-negotiable. Define the stop at a technically valid level."
            )

        # Stop must be on the correct side
        if direction.lower() == "long" and stop_price >= current_price:
            return Layer1Result(
                has_valid_stop=False, stop_level=stop_price,
                stop_distance_atr=0.0, risk_pct_account=0.0, is_within_bounds=False,
                reason=f"INVALID STOP: Long entry stop {stop_price} >= current price {current_price}."
            )
        if direction.lower() == "short" and stop_price <= current_price:
            return Layer1Result(
                has_valid_stop=False, stop_level=stop_price,
                stop_distance_atr=0.0, risk_pct_account=0.0, is_within_bounds=False,
                reason=f"INVALID STOP: Short entry stop {stop_price} <= current price {current_price}."
            )

        stop_distance  = abs(current_price - stop_price)
        stop_dist_atr  = stop_distance / atr if atr > 0 else 0.0
        risk_dollars   = stop_distance  # Per unit — actual risk depends on position size
        risk_pct       = risk_dollars / current_equity if current_equity > 0 else 0.0

        # Minimum: stop must be at least 0.3 ATR wide (not a micro-stop to game system)
        if stop_dist_atr < MIN_STOP_PCT_OF_ATR and atr > 0:
            return Layer1Result(
                has_valid_stop=False, stop_level=stop_price,
                stop_distance_atr=stop_dist_atr, risk_pct_account=risk_pct,
                is_within_bounds=False,
                reason=f"STOP TOO TIGHT: {stop_dist_atr:.2f} ATR < "
                       f"{MIN_STOP_PCT_OF_ATR} ATR minimum. "
                       f"Stop must be at a technically valid level."
            )

        return Layer1Result(
            has_valid_stop=True,
            stop_level=stop_price,
            stop_distance_atr=round(stop_dist_atr, 4),
            risk_pct_account=round(risk_pct, 6),
            is_within_bounds=True,
            reason=f"Valid stop at {stop_price}. "
                   f"Distance: {stop_dist_atr:.2f} ATR."
        )

    # ── LAYER 2: DAILY CIRCUIT BREAKER ────────────────────────────────────────

    def _check_layer2(
        self,
        daily_loss_dollars: float,
        titan_daily_limit:  float,
        firm_daily_limit:   float,
    ) -> Layer2Result:
        """
        Layer 2: Daily circuit breaker — TITAN triggers before the firm does.
        """
        if titan_daily_limit == float("inf"):
            return Layer2Result(
                circuit_broken=False,
                daily_loss_dollars=daily_loss_dollars,
                titan_daily_limit=float("inf"),
                firm_daily_limit=float("inf"),
                daily_pct_consumed=0.0,
                approaching_limit=False,
                reason="No daily drawdown limit for this firm/account type."
            )

        pct_consumed = daily_loss_dollars / titan_daily_limit if titan_daily_limit > 0 else 0.0
        circuit_broken = daily_loss_dollars >= titan_daily_limit
        approaching    = pct_consumed >= 0.80 and not circuit_broken

        if circuit_broken:
            reason = (
                f"⚡ DAILY CIRCUIT BREAKER TRIGGERED: "
                f"Loss ${daily_loss_dollars:,.2f} ≥ TITAN limit ${titan_daily_limit:,.2f}. "
                f"No more trades today. Firm limit: ${firm_daily_limit:,.2f}. "
                f"Buffer maintained: ${firm_daily_limit - daily_loss_dollars:,.2f}."
            )
            logger.error(
                "[FORGE-11][L2] CIRCUIT BREAKER: $%.2f loss ≥ $%.2f TITAN limit.",
                daily_loss_dollars, titan_daily_limit,
            )
        elif approaching:
            reason = (
                f"⚠ Approaching daily limit: ${daily_loss_dollars:,.2f} / "
                f"${titan_daily_limit:,.2f} ({pct_consumed:.1%}). "
                f"Only high-conviction setups from here."
            )
        else:
            reason = (
                f"Daily limit clear: ${daily_loss_dollars:,.2f} / "
                f"${titan_daily_limit:,.2f} ({pct_consumed:.1%})."
            )

        return Layer2Result(
            circuit_broken=circuit_broken,
            daily_loss_dollars=daily_loss_dollars,
            titan_daily_limit=titan_daily_limit,
            firm_daily_limit=firm_daily_limit,
            daily_pct_consumed=round(pct_consumed, 4),
            approaching_limit=approaching,
            reason=reason,
        )

    # ── LAYER 3: DRAWDOWN GUARDIAN ────────────────────────────────────────────

    def _check_layer3(
        self,
        drawdown_pct_used:      float,
        drawdown_used_dollars:  float,
        drawdown_remaining:     float,
    ) -> Layer3Result:
        """
        Layer 3: Total drawdown guardian — protection escalates with consumption.
        60% → caution, 70% → orange, 85% → red.
        """
        if drawdown_pct_used >= GUARDIAN_RED_PCT:
            level      = ProtectionLevel.CRITICAL
            size_mod   = RED_SIZE_MODIFIER
            permitted  = False
            reason = (
                f"🔴 RED: {drawdown_pct_used:.1%} drawdown used. "
                f"CLOSE ALL POSITIONS. No new entries. "
                f"${drawdown_remaining:,.2f} remaining to firm floor."
            )
            logger.critical(
                "[FORGE-11][L3] 🔴 RED %.1f%% used. $%.2f remaining.",
                drawdown_pct_used * 100, drawdown_remaining,
            )

        elif drawdown_pct_used >= GUARDIAN_ORANGE_PCT:
            level      = ProtectionLevel.WARNING
            size_mod   = ORANGE_SIZE_MODIFIER
            permitted  = True
            reason = (
                f"🟠 ORANGE: {drawdown_pct_used:.1%} drawdown used. "
                f"Minimum position size only. "
                f"${drawdown_remaining:,.2f} remaining."
            )
            logger.error(
                "[FORGE-11][L3] 🟠 ORANGE %.1f%% used.", drawdown_pct_used * 100,
            )

        elif drawdown_pct_used >= GUARDIAN_WARNING_PCT:
            level      = ProtectionLevel.CAUTION
            size_mod   = YELLOW_SIZE_MODIFIER
            permitted  = True
            reason = (
                f"🟡 CAUTION: {drawdown_pct_used:.1%} drawdown used. "
                f"-25% position size. "
                f"${drawdown_remaining:,.2f} remaining."
            )
            logger.warning(
                "[FORGE-11][L3] 🟡 CAUTION %.1f%% used.", drawdown_pct_used * 100,
            )

        else:
            level      = ProtectionLevel.CLEAR
            size_mod   = 1.0
            permitted  = True
            reason = (
                f"✅ Clear: {drawdown_pct_used:.1%} drawdown used. "
                f"${drawdown_remaining:,.2f} remaining."
            )

        return Layer3Result(
            protection_level=level,
            drawdown_pct_used=drawdown_pct_used,
            drawdown_dollars_used=drawdown_used_dollars,
            drawdown_dollars_remaining=drawdown_remaining,
            size_modifier=size_mod,
            new_entries_permitted=permitted,
            reason=reason,
        )

    # ── COMBINATION ──────────────────────────────────────────────────────────

    def _combine(
        self, l1: Layer1Result, l2: Layer2Result, l3: Layer3Result
    ) -> TripleLayerResult:
        """Combine all three layers. Most restrictive wins."""
        # Determine overall entry permission
        entry_permitted = (
            l1.has_valid_stop and
            not l2.circuit_broken and
            l3.new_entries_permitted
        )

        # Most restrictive size modifier
        if not l1.has_valid_stop or not entry_permitted:
            size_modifier = 0.0
        else:
            size_modifier = min(1.0, l3.size_modifier)   # L3 drives size

        # Overall protection level
        if not l1.has_valid_stop:
            overall = ProtectionLevel.BLOCKED
        elif l2.circuit_broken:
            overall = ProtectionLevel.DAILY_STOPPED
        else:
            overall = l3.protection_level

        # Reason
        if not l1.has_valid_stop:
            reason = f"[L1] {l1.reason}"
        elif l2.circuit_broken:
            reason = f"[L2] {l2.reason}"
        elif overall == ProtectionLevel.CRITICAL:
            reason = f"[L3] {l3.reason}"
        elif overall in (ProtectionLevel.WARNING, ProtectionLevel.CAUTION):
            reason = f"[L3] {l3.reason}"
        else:
            reason = "All three layers: CLEAR."

        return TripleLayerResult(
            layer1=l1, layer2=l2, layer3=l3,
            overall_level=overall,
            entry_permitted=entry_permitted,
            size_modifier=round(size_modifier, 4),
            reason=reason,
        )

    # ── FORGE-67: P&L SNAPSHOT ────────────────────────────────────────────────

    def snapshot(
        self,
        account_id:             str,
        firm_id:                str,
        starting_balance:       float,
        current_balance:        float,
        current_equity:         float,
        session_starting_equity: float,
        daily_loss_dollars:     float,
        peak_unrealized:        float = 0.0,   # Apex trailing
    ) -> PnLSnapshot:
        """
        FORGE-67: Build a real-time P&L snapshot.
        Called every 30 seconds (or on every significant tick).
        """
        rules = self._rule_engine.get_firm_rules(firm_id)

        # Total drawdown budget
        total_dd_budget = starting_balance * rules.total_drawdown_pct

        # Apex: trailing drawdown floor rises with peak unrealized
        if rules.drawdown_type == DrawdownType.TRAILING_UNREALIZED:
            dd_base = starting_balance - total_dd_budget
            effective_floor = dd_base + peak_unrealized
            drawdown_used = max(0.0, starting_balance - current_equity + peak_unrealized)
        else:
            drawdown_used = max(0.0, starting_balance - current_equity)

        drawdown_pct = min(1.0, drawdown_used / total_dd_budget) if total_dd_budget > 0 else 0.0
        drawdown_remaining = max(0.0, current_equity - (starting_balance - total_dd_budget))

        # Daily metrics
        if rules.daily_drawdown_pct:
            daily_budget = session_starting_equity * rules.daily_drawdown_pct
            titan_daily  = daily_budget * (1.0 - TITAN_DAILY_BUFFER)
            daily_pct    = daily_loss_dollars / titan_daily if titan_daily > 0 else 0.0
        else:
            daily_budget = float("inf")
            titan_daily  = float("inf")
            daily_pct    = 0.0

        at_yellow  = drawdown_pct >= 0.50
        at_orange  = drawdown_pct >= GUARDIAN_ORANGE_PCT
        at_red     = drawdown_pct >= GUARDIAN_RED_PCT
        at_circuit = daily_loss_dollars >= titan_daily

        if at_red or at_circuit:
            action     = "CLOSE_ALL"
            size_mod   = 0.0
        elif at_orange:
            action     = "MINIMUM"
            size_mod   = ORANGE_SIZE_MODIFIER
        elif at_yellow:
            action     = "REDUCE"
            size_mod   = YELLOW_SIZE_MODIFIER
        else:
            action     = "NORMAL"
            size_mod   = 1.0

        return PnLSnapshot(
            account_id=account_id,
            firm_id=firm_id,
            starting_balance=starting_balance,
            current_balance=current_balance,
            current_equity=current_equity,
            open_pnl=current_equity - current_balance,
            session_starting_equity=session_starting_equity,
            session_pnl=current_equity - session_starting_equity,
            total_drawdown_budget=total_dd_budget,
            drawdown_used_dollars=drawdown_used,
            drawdown_used_pct=round(drawdown_pct, 4),
            daily_drawdown_budget=daily_budget,
            daily_loss_dollars=daily_loss_dollars,
            daily_loss_pct=round(daily_pct, 4),
            at_yellow=at_yellow,
            at_orange=at_orange,
            at_red=at_red,
            at_daily_circuit=at_circuit,
            size_modifier=size_mod,
            action=action,
        )
