"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   emergency_overrides.py — Layer 3                          ║
║                                                                              ║
║  LEVEL 2 EMERGENCY OVERRIDES                                                 ║
║  Section 3: "Flash Crash (FORGE-129), Correlation Spike (FORGE-131),        ║
║  Liquidity Vacuum (FORGE-88) override everything except Level 1."            ║
║                                                                              ║
║  FORGE-129: Flash Crash Detection                                           ║
║    Detects abnormal price moves. Immediately closes all positions.          ║
║    Halts new entries until market stabilizes.                               ║
║                                                                              ║
║  FORGE-131: Correlation Spike Detection                                     ║
║    Detects when previously uncorrelated assets suddenly move together.      ║
║    Signals systemic risk event. All positions paused.                       ║
║                                                                              ║
║  These are LEVEL 2 in the 5-level priority hierarchy.                       ║
║  They override: Risk Management (L3), Behavioral (L4), Strategy (L5).      ║
║  They yield to: ABSOLUTE firm rules (L1) only.                             ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.emergency_overrides")


class EmergencyLevel(Enum):
    NONE     = auto()   # Normal
    CAUTION  = auto()   # Elevated risk — reduce exposure
    ACTIVE   = auto()   # Emergency override — close/pause
    CRITICAL = auto()   # System-wide shutdown


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-129: FLASH CRASH DETECTION
# ─────────────────────────────────────────────────────────────────────────────

# Flash crash thresholds
FLASH_CRASH_MOVE_PCT_1MIN:  float = 0.005   # 0.5% in 1 minute = flash crash
FLASH_CRASH_MOVE_PCT_5MIN:  float = 0.015   # 1.5% in 5 minutes = severe
FLASH_CRASH_VOLUME_SPIKE:   float = 5.0     # 5× normal volume = panic


@dataclass
class FlashCrashStatus:
    """FORGE-129: Flash crash detection result."""
    is_crash:             bool
    severity:             EmergencyLevel
    price_move_pct:       float       # % move in detection window
    volume_spike:         float       # Volume / average volume
    detection_window:     str         # "1min" or "5min"
    close_all_positions:  bool        # True = close everything NOW
    halt_new_entries:     bool        # True = no new trades
    resume_after_minutes: int         # How long to wait before resuming
    reason:               str


def detect_flash_crash(
    price_now:      float,
    price_1min_ago: float,
    price_5min_ago: float,
    volume_now:     float,
    avg_volume:     float,
) -> FlashCrashStatus:
    """
    FORGE-129: Flash crash detection.
    Abnormal move in short time = close all, halt trading.
    Level 2 Emergency — overrides Risk Management, Behavioral, Strategy.
    """
    move_1min = abs(price_now - price_1min_ago) / price_1min_ago if price_1min_ago > 0 else 0.0
    move_5min = abs(price_now - price_5min_ago) / price_5min_ago if price_5min_ago > 0 else 0.0
    vol_spike = volume_now / avg_volume if avg_volume > 0 else 1.0

    # Determine severity
    if move_1min >= FLASH_CRASH_MOVE_PCT_1MIN and vol_spike >= FLASH_CRASH_VOLUME_SPIKE:
        level  = EmergencyLevel.CRITICAL
        close  = True
        halt   = True
        resume = 30
        window = "1min"
        move   = move_1min
        reason = (
            f"🚨 FLASH CRASH DETECTED: {move_1min:.2%} in 1 minute + "
            f"{vol_spike:.1f}× volume spike. "
            f"LEVEL 2 EMERGENCY: All positions closed. "
            f"No new entries for {resume} minutes."
        )
        logger.critical("[FORGE-129] %s", reason)

    elif move_5min >= FLASH_CRASH_MOVE_PCT_5MIN:
        level  = EmergencyLevel.ACTIVE
        close  = True
        halt   = True
        resume = 15
        window = "5min"
        move   = move_5min
        reason = (
            f"⚠ SEVERE MOVE: {move_5min:.2%} in 5 minutes. "
            f"FORGE-129: Closing all positions. "
            f"Halting entries for {resume} minutes."
        )
        logger.error("[FORGE-129] %s", reason)

    elif move_1min >= FLASH_CRASH_MOVE_PCT_1MIN * 0.6:
        level  = EmergencyLevel.CAUTION
        close  = False
        halt   = True   # No NEW entries — existing positions may stay
        resume = 5
        window = "1min"
        move   = move_1min
        reason = (
            f"🟡 Elevated volatility: {move_1min:.2%} in 1 minute. "
            f"No new entries for {resume} minutes."
        )
        logger.warning("[FORGE-129] %s", reason)

    else:
        return FlashCrashStatus(
            is_crash=False, severity=EmergencyLevel.NONE,
            price_move_pct=move_1min, volume_spike=vol_spike,
            detection_window="1min",
            close_all_positions=False, halt_new_entries=False,
            resume_after_minutes=0,
            reason=f"Normal. 1min move: {move_1min:.3%}. Volume: {vol_spike:.1f}×.",
        )

    return FlashCrashStatus(
        is_crash=True, severity=level,
        price_move_pct=round(move, 4), volume_spike=round(vol_spike, 2),
        detection_window=window,
        close_all_positions=close, halt_new_entries=halt,
        resume_after_minutes=resume, reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORGE-131: CORRELATION SPIKE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

# Correlation spike thresholds
CORRELATION_SPIKE_THRESHOLD: float = 0.85   # Normally uncorrelated assets at 0.85+
CORRELATION_NORMAL_MAX:      float = 0.50   # Pairs that normally don't correlate much

@dataclass
class CorrelationSpikeStatus:
    """FORGE-131: Correlation spike detection result."""
    is_spike:             bool
    severity:             EmergencyLevel
    spike_pairs:          list[tuple[str, str, float]]   # (asset1, asset2, correlation)
    systemic_risk:        bool    # True = all markets moving together = systemic event
    close_all_positions:  bool
    halt_new_entries:     bool
    reason:               str


def detect_correlation_spike(
    asset_returns: dict[str, float],           # asset_id → return in last 5min
    normal_correlations: dict[tuple[str, str], float],   # Expected correlations
) -> CorrelationSpikeStatus:
    """
    FORGE-131: Correlation spike detection.
    When previously uncorrelated assets suddenly move together = systemic risk.
    Level 2 Emergency.
    """
    if len(asset_returns) < 2:
        return CorrelationSpikeStatus(
            is_spike=False, severity=EmergencyLevel.NONE,
            spike_pairs=[], systemic_risk=False,
            close_all_positions=False, halt_new_entries=False,
            reason="Insufficient assets for correlation check.",
        )

    assets  = list(asset_returns.keys())
    returns = list(asset_returns.values())
    spike_pairs: list[tuple[str, str, float]] = []

    # Check all pairs
    for i in range(len(assets)):
        for j in range(i+1, len(assets)):
            a1, a2 = assets[i], assets[j]
            r1, r2 = returns[i], returns[j]

            # Simple correlation proxy: same-direction large moves
            same_dir = (r1 > 0 and r2 > 0) or (r1 < 0 and r2 < 0)
            magnitude = abs(r1) + abs(r2)
            expected  = normal_correlations.get((a1, a2),
                        normal_correlations.get((a2, a1), 0.30))

            # If both moving significantly in same direction
            if same_dir and magnitude > 0.01:  # Both moved > 0.5% each
                implied_corr = min(1.0, magnitude * 20)  # Rough proxy
                if implied_corr > CORRELATION_SPIKE_THRESHOLD and expected < CORRELATION_NORMAL_MAX:
                    spike_pairs.append((a1, a2, round(implied_corr, 3)))

    # Are MOST assets moving together?
    all_same_dir = all(r > 0 for r in returns) or all(r < 0 for r in returns)
    systemic     = all_same_dir and len(assets) >= 3

    if systemic or len(spike_pairs) >= 2:
        level  = EmergencyLevel.ACTIVE
        close  = True
        halt   = True
        reason = (
            f"🚨 CORRELATION SPIKE FORGE-131: {len(spike_pairs)} abnormal pair(s). "
            f"Systemic: {systemic}. "
            f"All previously uncorrelated assets moving together = crisis event. "
            f"Close all, halt entries."
        )
        logger.critical("[FORGE-131] %s", reason)

    elif len(spike_pairs) == 1:
        level  = EmergencyLevel.CAUTION
        close  = False
        halt   = True
        reason = (
            f"⚠ Correlation spike: {spike_pairs[0]}. "
            f"No new entries until resolves."
        )
        logger.warning("[FORGE-131] %s", reason)

    else:
        return CorrelationSpikeStatus(
            is_spike=False, severity=EmergencyLevel.NONE,
            spike_pairs=[], systemic_risk=False,
            close_all_positions=False, halt_new_entries=False,
            reason="Normal correlation. No systemic risk detected.",
        )

    return CorrelationSpikeStatus(
        is_spike=True, severity=level, spike_pairs=spike_pairs,
        systemic_risk=systemic, close_all_positions=close,
        halt_new_entries=halt, reason=reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# EMERGENCY GATE — called before ANY trade entry
# Checks all Level 2 triggers simultaneously.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Level2EmergencyCheck:
    """Combined Level 2 emergency check result."""
    is_emergency:         bool
    severity:             EmergencyLevel
    flash_crash:          FlashCrashStatus
    correlation_spike:    CorrelationSpikeStatus
    all_clear:            bool
    blocking_reason:      Optional[str]


def run_level2_emergency_check(
    # Flash crash inputs
    price_now:      float,
    price_1min_ago: float,
    price_5min_ago: float,
    volume_now:     float,
    avg_volume:     float,
    # Correlation inputs
    asset_returns:  Optional[dict[str, float]] = None,
    normal_corrs:   Optional[dict] = None,
) -> Level2EmergencyCheck:
    """
    Run all Level 2 emergency checks.
    Called before every entry — BEFORE L3 risk, L4 behavioral, L5 strategy.
    Any L2 trigger = immediate close/halt.
    """
    flash = detect_flash_crash(price_now, price_1min_ago, price_5min_ago,
                               volume_now, avg_volume)

    corr = detect_correlation_spike(
        asset_returns or {},
        normal_corrs or {},
    )

    is_emergency = flash.is_crash or corr.is_spike
    severity     = (EmergencyLevel.CRITICAL
                    if any(x == EmergencyLevel.CRITICAL for x in [flash.severity, corr.severity])
                    else EmergencyLevel.ACTIVE
                    if any(x == EmergencyLevel.ACTIVE   for x in [flash.severity, corr.severity])
                    else EmergencyLevel.CAUTION
                    if is_emergency else EmergencyLevel.NONE)

    blocking = None
    if flash.is_crash:
        blocking = f"[L2-FLASH-129] {flash.reason}"
    elif corr.is_spike:
        blocking = f"[L2-CORR-131] {corr.reason}"

    return Level2EmergencyCheck(
        is_emergency=is_emergency,
        severity=severity,
        flash_crash=flash,
        correlation_spike=corr,
        all_clear=not is_emergency,
        blocking_reason=blocking,
    )
