"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     behavioral_arch.py — Layer 3                            ║
║  FORGE-37: Multi-Stage Consistent Entry                                     ║
║  FORGE-38: Hot Hand Protocol (funded only, FTMO permanently disabled C-14) ║
║  FORGE-39: Win Streak Management                                            ║
║  FORGE-40: Anti-Tilt Architecture                                           ║
║  FORGE-56: Behavioral Consistency Monitor                                   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional
from firm_rules import FirmID

logger = logging.getLogger("titan_forge.behavioral_arch")

# ── FORGE-40: Anti-Tilt Architecture ─────────────────────────────────────────
# Emotional state monitor. Tilt = revenge trading, overtrading, deviation from plan.

class TiltLevel(Enum):
    CLEAR      = auto()   # Normal state
    MILD       = auto()   # Minor tilt signals
    MODERATE   = auto()   # Real concern — warning issued
    SEVERE     = auto()   # Trading suspended

@dataclass
class TiltAssessment:
    level:              TiltLevel
    trading_permitted:  bool
    score:              float      # 0.0 (clear) to 1.0 (severe tilt)
    triggers:           list[str]
    recommendation:     str
    cool_down_minutes:  int

def assess_tilt(
    consecutive_losses:     int,
    trades_last_hour:       int,    # Overtrading indicator
    avg_trades_per_hour:    float,
    deviated_from_plan:     bool,   # Outside approved setups
    profit_target_missed:   bool,   # Trading after session goal was missed
    time_since_last_loss_min: float,
) -> TiltAssessment:
    """FORGE-40: Assess tilt level from behavioral indicators."""
    score     = 0.0
    triggers  = []

    if consecutive_losses >= 3:
        score += 0.40
        triggers.append(f"3+ consecutive losses ({consecutive_losses})")
    elif consecutive_losses == 2:
        score += 0.20
        triggers.append("2 consecutive losses")

    if avg_trades_per_hour > 0:
        trade_ratio = trades_last_hour / max(1, avg_trades_per_hour)
        if trade_ratio > 2.5:
            score += 0.30
            triggers.append(f"Overtrading: {trades_last_hour} trades vs {avg_trades_per_hour:.1f} avg")
        elif trade_ratio > 1.8:
            score += 0.15
            triggers.append("Elevated trading frequency")

    if deviated_from_plan:
        score += 0.20
        triggers.append("Deviated from approved setup list")

    if profit_target_missed:
        score += 0.10
        triggers.append("Trading after session goal missed — chasing")

    if time_since_last_loss_min < 5 and consecutive_losses > 0:
        score += 0.15
        triggers.append(f"Re-entering {time_since_last_loss_min:.0f}min after loss (< 5 min)")

    score = min(1.0, score)

    if score >= 0.70:
        level = TiltLevel.SEVERE
        permitted = False
        rec = "🛑 TILT SEVERE: Trading suspended. 2-hour mandatory break. Root cause review."
        cool_down = 120
    elif score >= 0.40:
        level = TiltLevel.MODERATE
        permitted = False
        rec = "⚠ TILT MODERATE: 30-minute pause. Review setup plan before resuming."
        cool_down = 30
    elif score >= 0.20:
        level = TiltLevel.MILD
        permitted = True
        rec = "🟡 TILT MILD: Continue with extra caution. No revenge trades."
        cool_down = 0
    else:
        level = TiltLevel.CLEAR
        permitted = True
        rec = "✅ Clear. No tilt indicators."
        cool_down = 0

    return TiltAssessment(
        level=level, trading_permitted=permitted,
        score=round(score, 4), triggers=triggers,
        recommendation=rec, cool_down_minutes=cool_down,
    )


# ── FORGE-38: Hot Hand Protocol ───────────────────────────────────────────────
# C-14: FTMO permanently disabled (returns 1.0 — no boost)
# C-15: Evaluation phase → hot hand only. Funded phase → win streak only.

def get_hot_hand_multiplier(
    consecutive_wins:   int,
    recent_win_rate:    float,   # Last 10 trades win rate
    firm_id:            str,
    is_evaluation:      bool,
    account_win_rate:   float,   # Lifetime win rate
) -> tuple[float, str]:
    """
    FORGE-38 + C-14/C-15: Hot Hand Protocol.

    Returns (size_multiplier, reason).

    C-14: FTMO permanently disabled → always returns 1.0
    C-15: Evaluation = hot hand only; Funded = win streak only (see FORGE-39)
    """
    # C-14: FTMO — hot hand permanently disabled
    if firm_id == FirmID.FTMO:
        return 1.0, "C-14: FTMO hot hand permanently disabled."

    # C-15: Must be in evaluation phase to use hot hand
    if not is_evaluation:
        return 1.0, "C-15: Hot hand only in evaluation phase. Use win streak in funded."

    # Check for hot hand conditions: recent win rate significantly above baseline
    if recent_win_rate < account_win_rate + 0.10:
        return 1.0, f"No hot hand: recent WR {recent_win_rate:.0%} not 10%+ above baseline."

    if consecutive_wins >= 5:
        return 1.15, f"Hot hand active: {consecutive_wins} wins + recent WR {recent_win_rate:.0%}. +15% size."
    elif consecutive_wins >= 3:
        return 1.10, f"Hot hand mild: {consecutive_wins} wins. +10% size."
    return 1.0, "Hot hand: insufficient consecutive wins (need 3+)."


# ── FORGE-39: Win Streak Management ──────────────────────────────────────────
# C-15: Funded phase uses win streak (not hot hand) for sizing decisions.

def get_win_streak_multiplier(
    consecutive_wins:   int,
    is_funded:          bool,
    firm_id:            str,
) -> tuple[float, str]:
    """
    FORGE-39 + C-15: Win streak management.

    Returns (size_multiplier, reason).
    C-15: Funded mode only — win streak drives sizing in funded accounts.
    """
    if not is_funded:
        return 1.0, "C-15: Win streak sizing applies in funded mode only."

    if consecutive_wins >= 7:
        return 1.20, f"Win streak: {consecutive_wins} wins in funded mode. +20% size."
    elif consecutive_wins >= 5:
        return 1.15, f"Win streak: {consecutive_wins} wins. +15% size."
    elif consecutive_wins >= 3:
        return 1.10, f"Win streak: {consecutive_wins} wins. +10% size."
    return 1.0, f"No win streak multiplier (< 3 consecutive wins)."


# ── FORGE-37: Multi-Stage Consistent Entry ────────────────────────────────────
# Stage-based entry: confirmation → wait for retrace → enter clean setup.

class EntryStage(Enum):
    WAITING_SETUP     = auto()   # No setup identified yet
    SETUP_IDENTIFIED  = auto()   # Setup confirmed — waiting for clean entry
    ENTRY_ZONE        = auto()   # Price in entry zone — take trade
    MISSED            = auto()   # Price moved too far — wait for next

@dataclass
class EntryStageResult:
    stage:              EntryStage
    can_enter:          bool
    entry_price:        Optional[float]
    wait_for_price:     Optional[float]  # Price to wait for (pullback target)
    reason:             str

def evaluate_entry_stage(
    setup_confirmed:    bool,
    current_price:      float,
    optimal_entry:      float,     # Ideal entry (e.g. VWAP, OB level)
    atr:                float,
    direction:          str,
    time_since_setup:   float,     # Minutes since setup identified
    max_wait_minutes:   float = 30.0,
) -> EntryStageResult:
    """FORGE-37: Multi-stage consistent entry evaluation."""
    if not setup_confirmed:
        return EntryStageResult(
            EntryStage.WAITING_SETUP, False, None, None,
            "No setup confirmed — waiting."
        )

    # Check if still within time window
    if time_since_setup > max_wait_minutes:
        return EntryStageResult(
            EntryStage.MISSED, False, None, None,
            f"Setup aged out ({time_since_setup:.0f} min > {max_wait_minutes:.0f} min limit). Wait for next."
        )

    # Check if price is in entry zone (within 0.5 ATR of optimal)
    distance = abs(current_price - optimal_entry) / atr
    in_zone   = distance <= 0.5

    if in_zone:
        return EntryStageResult(
            EntryStage.ENTRY_ZONE, True, optimal_entry, None,
            f"In entry zone (distance: {distance:.2f} ATR). Execute."
        )

    # Price not yet at optimal entry — wait for pullback
    return EntryStageResult(
        EntryStage.SETUP_IDENTIFIED, False, None, optimal_entry,
        f"Setup valid but price {distance:.2f} ATR from optimal. Wait for {optimal_entry:.2f}."
    )


# ── FORGE-56: Behavioral Consistency Monitor ─────────────────────────────────
# FTMO AI monitors behavioral patterns. Self-police before being flagged.

@dataclass
class BehavioralConsistencyCheck:
    """Checks if the account's behavioral profile has drifted."""
    is_consistent:          bool
    sizing_variance:        float       # CV of position sizes
    timing_variance:        float       # Spread of entry hours
    win_rate_drift:         float       # Recent vs baseline drift
    flags:                  list[str]
    severity:               str         # "CLEAN" / "CAUTION" / "FLAGGED"
    action_required:        str

def check_behavioral_consistency(
    position_sizes:         list[float],
    entry_hours:            list[int],       # Hour of day (0–23)
    baseline_win_rate:      float,
    recent_win_rate:        float,
) -> BehavioralConsistencyCheck:
    """FORGE-56: Self-monitor behavioral consistency before FTMO AI flags it."""
    import math
    flags = []

    # Sizing variance
    mean_size = sum(position_sizes) / len(position_sizes) if position_sizes else 1.0
    if mean_size > 0:
        var = sum((s - mean_size)**2 for s in position_sizes) / len(position_sizes)
        size_cv = math.sqrt(var) / mean_size
    else:
        size_cv = 0.0

    if size_cv > 0.30:
        flags.append(f"Sizing CV {size_cv:.2f} > 0.30 — FTMO may flag inconsistent sizing")

    # Timing variance
    if entry_hours:
        mean_h = sum(entry_hours) / len(entry_hours)
        timing_var = math.sqrt(sum((h - mean_h)**2 for h in entry_hours) / len(entry_hours)) / 12.0
    else:
        timing_var = 0.0

    if timing_var > 0.20:
        flags.append(f"Timing variance {timing_var:.2f} > 0.20 — session consistency concern")

    # Win rate drift
    drift = abs(recent_win_rate - baseline_win_rate)
    if drift > 0.15:
        flags.append(f"Win rate drift {drift:.0%} > 15% from baseline")
    elif drift > 0.10:
        flags.append(f"Win rate drift {drift:.0%} approaching limit")

    is_consistent = len(flags) == 0

    if len(flags) >= 2:
        severity = "FLAGGED"
        action = "⚠ Multiple behavioral flags — review and correct patterns immediately."
    elif flags:
        severity = "CAUTION"
        action = "🟡 Minor behavioral drift — monitor closely."
    else:
        severity = "CLEAN"
        action = "✅ Behavioral profile consistent."

    return BehavioralConsistencyCheck(
        is_consistent=is_consistent,
        sizing_variance=round(size_cv, 4),
        timing_variance=round(timing_var, 4),
        win_rate_drift=round(drift, 4),
        flags=flags,
        severity=severity,
        action_required=action,
    )
