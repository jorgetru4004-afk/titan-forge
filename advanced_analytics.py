"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   advanced_analytics.py — Layer 3                           ║
║  FORGE-54: Performance Attribution                                          ║
║  FORGE-57: Real-Time Risk Assessment                                        ║
║  FORGE-74: Information Ratio Optimization                                   ║
║  FORGE-75: Sortino Ratio Targeting (≥ 2.0)                                 ║
║  FORGE-79: Statistical Edge Verification                                    ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("titan_forge.advanced_analytics")


# ── FORGE-74: Information Ratio Optimization ─────────────────────────────────
# IR = (Strategy Return - Benchmark) / Tracking Error
# Target IR ≥ 1.0 for each strategy type.

@dataclass
class InformationRatio:
    setup_id:       str
    ir_value:       float
    meets_target:   bool    # IR ≥ 1.0
    active_return:  float   # Return above benchmark
    tracking_error: float
    recommendation: str

def calculate_information_ratio(
    setup_id:           str,
    returns:            list[float],   # Per-trade returns
    benchmark_return:   float = 0.007, # 0.7% benchmark per trade (typical 2:1 at 65%)
) -> InformationRatio:
    """FORGE-74: Information Ratio per setup."""
    if len(returns) < 10:
        return InformationRatio(setup_id, 0.0, False, 0.0, 0.0,
                                f"Need 10+ trades for IR calculation ({len(returns)} so far).")

    avg_return = sum(returns) / len(returns)
    active     = avg_return - benchmark_return

    variance   = sum((r - avg_return) ** 2 for r in returns) / len(returns)
    track_err  = math.sqrt(variance)

    ir = active / track_err if track_err > 0 else 0.0
    meets = ir >= 1.0

    rec = (f"IR {ir:.2f} {'✓' if meets else '✗'} (target ≥ 1.0). "
           f"Active return: {active:.3f}. Tracking error: {track_err:.3f}.")

    return InformationRatio(
        setup_id=setup_id, ir_value=round(ir, 4),
        meets_target=meets, active_return=round(active, 6),
        tracking_error=round(track_err, 6), recommendation=rec,
    )


# ── FORGE-75: Sortino Ratio Targeting ────────────────────────────────────────
# Sortino = (Return - Risk-Free) / Downside Deviation
# Target ≥ 2.0 for each active setup.

@dataclass
class SortinoResult:
    setup_id:           str
    sortino_ratio:      float
    meets_target:       bool   # ≥ 2.0
    avg_return:         float
    downside_deviation: float
    max_loss:           float
    recommendation:     str

TARGET_SORTINO: float = 2.0
RISK_FREE_RATE: float = 0.0   # Risk-free per trade ≈ 0 for short-duration

def calculate_sortino(
    setup_id:       str,
    returns:        list[float],
    risk_free:      float = RISK_FREE_RATE,
) -> SortinoResult:
    """FORGE-75: Sortino ratio targeting ≥ 2.0."""
    if len(returns) < 10:
        return SortinoResult(setup_id, 0.0, False, 0.0, 0.0, 0.0,
                             f"Need 10+ trades ({len(returns)} so far).")

    avg = sum(returns) / len(returns)
    losses = [r for r in returns if r < risk_free]
    max_loss = min(returns) if returns else 0.0

    if not losses:
        return SortinoResult(setup_id, float("inf"), True, avg, 0.0, 0.0,
                             "Infinite Sortino — no losing trades!")

    downside_var = sum((r - risk_free) ** 2 for r in losses) / len(returns)
    downside_dev = math.sqrt(downside_var)

    sortino = (avg - risk_free) / downside_dev if downside_dev > 0 else 0.0
    meets   = sortino >= TARGET_SORTINO

    rec = (f"Sortino {sortino:.2f} {'✓' if meets else '✗'} (target ≥ {TARGET_SORTINO:.1f}). "
           f"Avg return: {avg:.4f}. Downside dev: {downside_dev:.4f}.")

    return SortinoResult(
        setup_id=setup_id, sortino_ratio=round(sortino, 4),
        meets_target=meets, avg_return=round(avg, 6),
        downside_deviation=round(downside_dev, 6),
        max_loss=round(max_loss, 4), recommendation=rec,
    )


# ── FORGE-79: Statistical Edge Verification ──────────────────────────────────
# Verify edge is real: Z-test, p-value, required sample size.

@dataclass
class EdgeVerification:
    setup_id:           str
    is_statistically_significant: bool
    z_score:            float
    p_value:            float      # One-tailed
    sample_size:        int
    required_sample:    int
    win_rate:           float
    edge_strength:      str        # "STRONG" / "MODERATE" / "WEAK" / "NONE"
    recommendation:     str

def verify_statistical_edge(
    setup_id:   str,
    wins:       int,
    total:      int,
    null_win_rate: float = 0.50,   # Null hypothesis: 50% (coin flip)
    alpha:      float = 0.05,      # Significance level
) -> EdgeVerification:
    """FORGE-79: Statistical edge verification via Z-test."""
    if total < 20:
        return EdgeVerification(
            setup_id, False, 0.0, 1.0, total, 30,
            wins/total if total > 0 else 0.0,
            "NONE", f"Need 30+ trades for significance test ({total} so far)."
        )

    win_rate = wins / total
    # Z-score for proportion test
    p0       = null_win_rate
    std_err  = math.sqrt(p0 * (1 - p0) / total)
    z        = (win_rate - p0) / std_err if std_err > 0 else 0.0

    # Approximate p-value (one-tailed)
    def norm_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    p_value = 1.0 - norm_cdf(z)
    significant = p_value < alpha

    # Required sample for 80% power
    req_n = max(30, int((1.96 + 0.84) ** 2 * p0 * (1 - p0) / (win_rate - p0) ** 2)
                if win_rate != p0 else 100)

    if z >= 3.0:
        strength = "STRONG"
    elif z >= 2.0:
        strength = "MODERATE"
    elif z >= 1.0:
        strength = "WEAK"
    else:
        strength = "NONE"

    rec = (f"Z={z:.2f}, p={p_value:.4f}, WR={win_rate:.1%}. "
           f"Edge: {strength}. {'✓ Significant' if significant else '✗ Not yet significant'}.")

    return EdgeVerification(
        setup_id=setup_id,
        is_statistically_significant=significant,
        z_score=round(z, 4), p_value=round(p_value, 6),
        sample_size=total, required_sample=req_n,
        win_rate=round(win_rate, 4), edge_strength=strength,
        recommendation=rec,
    )


# ── FORGE-54: Performance Attribution ────────────────────────────────────────

@dataclass
class PerformanceAttribution:
    """FORGE-54: Attribution of P&L to setup types, sessions, regimes."""
    total_pnl:              float
    by_setup:               dict[str, float]   # setup_id → total P&L
    by_regime:              dict[str, float]
    by_session_hour:        dict[int, float]
    best_setup:             str
    worst_setup:            str
    best_regime:            str
    best_hour:              int
    recommendation:         str

def attribute_performance(
    trades: list[dict],   # Each: {setup_id, regime, hour, pnl}
) -> PerformanceAttribution:
    """FORGE-54: Attribute P&L across dimensions."""
    by_setup:   dict[str, float] = {}
    by_regime:  dict[str, float] = {}
    by_hour:    dict[int, float] = {}
    total = 0.0

    for t in trades:
        sid  = t.get("setup_id", "UNKNOWN")
        reg  = t.get("regime",   "UNKNOWN")
        hour = t.get("hour",     0)
        pnl  = t.get("pnl",      0.0)
        by_setup[sid]  = by_setup.get(sid,  0.0) + pnl
        by_regime[reg] = by_regime.get(reg, 0.0) + pnl
        by_hour[hour]  = by_hour.get(hour,  0.0) + pnl
        total += pnl

    best_s  = max(by_setup,  key=by_setup.get)  if by_setup  else "N/A"
    worst_s = min(by_setup,  key=by_setup.get)  if by_setup  else "N/A"
    best_r  = max(by_regime, key=by_regime.get) if by_regime else "N/A"
    best_h  = max(by_hour,   key=by_hour.get)   if by_hour   else 9

    rec = (f"Best setup: {best_s} (${by_setup.get(best_s, 0):+,.0f}). "
           f"Best regime: {best_r}. Best hour: {best_h}:00 ET.")

    return PerformanceAttribution(
        total_pnl=total, by_setup=by_setup, by_regime=by_regime,
        by_session_hour=by_hour, best_setup=best_s, worst_setup=worst_s,
        best_regime=best_r, best_hour=best_h, recommendation=rec,
    )


# ── FORGE-57: Real-Time Risk Assessment ──────────────────────────────────────

@dataclass
class RiskAssessment:
    """FORGE-57: Live risk assessment before any new entry."""
    total_risk_pct:         float   # Current open risk as % of account
    max_allowed_pct:        float   # Maximum allowed concurrent risk
    can_open_new:           bool
    open_positions_count:   int
    risk_utilization:       float   # 0–1
    recommendation:         str

def assess_current_risk(
    account_equity:         float,
    open_stop_distances:    list[float],   # Dollar risk per open position
    max_risk_pct:           float = 0.06,  # Max 6% of account in open risk
) -> RiskAssessment:
    """FORGE-57: Real-time risk assessment."""
    total_dollar_risk = sum(open_stop_distances)
    total_risk_pct    = total_dollar_risk / account_equity if account_equity > 0 else 0.0
    utilization       = total_risk_pct / max_risk_pct if max_risk_pct > 0 else 0.0
    can_open          = total_risk_pct < max_risk_pct

    rec = (f"Risk: {total_risk_pct:.1%} of account ({utilization:.0%} utilized). "
           + ("✅ Can open new position." if can_open
              else f"❌ At capacity. Close a position first ({total_risk_pct:.1%} ≥ {max_risk_pct:.0%})."))

    return RiskAssessment(
        total_risk_pct=round(total_risk_pct, 4),
        max_allowed_pct=max_risk_pct,
        can_open_new=can_open,
        open_positions_count=len(open_stop_distances),
        risk_utilization=round(utilization, 4),
        recommendation=rec,
    )
