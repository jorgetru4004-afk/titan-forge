"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE V20                      ║
║                     forge_brain.py — THE PROBABILITY ENGINE                 ║
║                                                                              ║
║  V20: 11 Bayesian dimensions (added multi-timeframe alignment).            ║
║  Regime-driven conviction multipliers. Candlestick pattern awareness.     ║
║  Session memory (intraday learning). Parameter evolution.                  ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging, math, random, time
from dataclasses import dataclass, field
from typing import Optional
from forge_core import MarketContext, SessionState, InstrumentTracker, now_et_time
from datetime import time as dtime

logger = logging.getLogger("titan_forge.brain")


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME MULTIPLIERS — V20: Setup activation per regime
# ═══════════════════════════════════════════════════════════════════════════════

REGIME_MULT: dict[str, dict[str, float]] = {
    # GENESIS CALIBRATED — from 22d × 100 MC backtest (March 2026)
    # TREND: 51.8% WR, +$460 avg | CHOP: 47.9% WR, +$321 avg | NORMAL: 48.3% WR, +$251 avg
    # Trend-following setups: boosted on TREND, suppressed on CHOP
    "ORD-02":     {"TREND": 1.15, "CHOP": 0.85, "NORMAL": 1.0, "REVERSAL": 0.90},
    "OD-01":      {"TREND": 1.15, "CHOP": 0.70, "NORMAL": 1.0, "REVERSAL": 0.80},
    "GAP-02":     {"TREND": 1.10, "CHOP": 0.75, "NORMAL": 1.0, "REVERSAL": 0.80},
    "IB-01":      {"TREND": 1.15, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.90},
    "VOL-03":     {"TREND": 1.15, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.85},
    "MID-02":     {"TREND": 1.10, "CHOP": 0.75, "NORMAL": 0.95, "REVERSAL": 0.85},
    "VWAP-03":    {"TREND": 1.10, "CHOP": 0.85, "NORMAL": 1.0, "REVERSAL": 0.95},
    "ES-ORD-02":  {"TREND": 1.10, "CHOP": 0.85, "NORMAL": 1.0, "REVERSAL": 0.90},
    # Mean reversion / range: boosted on CHOP, suppressed on TREND
    "VOL-05":     {"TREND": 0.85, "CHOP": 1.15, "NORMAL": 1.0, "REVERSAL": 1.10},
    "VOL-06":     {"TREND": 0.85, "CHOP": 1.10, "NORMAL": 1.0, "REVERSAL": 1.15},
    "IB-02":      {"TREND": 0.00, "CHOP": 1.15, "NORMAL": 0.90, "REVERSAL": 0.95},
    "LVL-02":     {"TREND": 0.85, "CHOP": 1.10, "NORMAL": 1.0, "REVERSAL": 1.0},
    # Neutral setups — work in all regimes
    "ICT-01":     {"TREND": 1.05, "CHOP": 0.90, "NORMAL": 1.0, "REVERSAL": 0.95},
    "ICT-02":     {"TREND": 1.00, "CHOP": 0.90, "NORMAL": 1.0, "REVERSAL": 0.95},
    "ICT-03":     {"TREND": 0.90, "CHOP": 1.00, "NORMAL": 1.0, "REVERSAL": 1.15},
    "VWAP-01":    {"TREND": 1.10, "CHOP": 0.95, "NORMAL": 1.0, "REVERSAL": 0.90},
    "VWAP-02":    {"TREND": 1.10, "CHOP": 0.95, "NORMAL": 1.0, "REVERSAL": 0.90},
    "LVL-01":     {"TREND": 1.00, "CHOP": 1.00, "NORMAL": 1.0, "REVERSAL": 1.00},
    "GAP-01":     {"TREND": 0.85, "CHOP": 0.90, "NORMAL": 1.0, "REVERSAL": 1.10},
    "SES-01":     {"TREND": 1.00, "CHOP": 1.00, "NORMAL": 1.0, "REVERSAL": 1.00},
    "GOLD-CORR-01": {"TREND": 1.0, "CHOP": 1.0, "NORMAL": 1.0, "REVERSAL": 1.0},
    # DISABLED setups — regime mults don't matter but keep for reference
    "MID-01":     {"TREND": 0.80, "CHOP": 1.15, "NORMAL": 1.0, "REVERSAL": 1.10},
    "PWR-01":     {"TREND": 1.10, "CHOP": 0.80, "NORMAL": 1.0, "REVERSAL": 0.85},
    "PWR-02":     {"TREND": 1.10, "CHOP": 0.75, "NORMAL": 1.0, "REVERSAL": 0.85},
    "PWR-03":     {"TREND": 0.80, "CHOP": 0.90, "NORMAL": 0.90, "REVERSAL": 1.10},
}


def get_regime_mult(setup_id: str, regime: str) -> float:
    """Get regime multiplier for a setup. 0.0 = SUPPRESSED (don't trade)."""
    mults = REGIME_MULT.get(setup_id, {})
    return mults.get(regime, 1.0)


# ═══════════════════════════════════════════════════════════════════════════════
# BAYESIAN CONVICTION ENGINE — V20: 11 DIMENSIONS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ConfluenceDimension:
    name:           str
    confirms:       bool
    likelihood_ratio: float
    weight:         float
    detail:         str

@dataclass
class BayesianConviction:
    prior:          float
    posterior:      float
    dimensions:     list[ConfluenceDimension]
    confirming:     int
    contradicting:  int
    total:          int
    conviction_level: str

    @property
    def is_tradeable(self) -> bool:
        return self.conviction_level != "REJECT"


def compute_bayesian_conviction(
    prior_win_rate:     float,
    ctx:                MarketContext,
    tracker:            InstrumentTracker,
    direction:          str,
    setup_id:           str,
    live_win_rate:      Optional[float] = None,
) -> BayesianConviction:
    prior = live_win_rate if live_win_rate is not None else prior_win_rate
    prior = max(0.30, min(0.90, prior))

    dimensions: list[ConfluenceDimension] = []

    # ── DIM 1: VIX Regime ────────────────────────────────────────────────────
    if ctx.vix_regime == "LOW":     lr = 1.15
    elif ctx.vix_regime == "NORMAL": lr = 1.05
    elif ctx.vix_regime == "ELEVATED": lr = 0.85
    else: lr = 0.65
    dimensions.append(ConfluenceDimension(
        "VIX Regime", lr > 1.0, lr, 0.8,
        f"VIX={ctx.vix:.1f} ({ctx.vix_regime}) → LR={lr:.2f}"))

    # ── DIM 2: Futures Direction ─────────────────────────────────────────────
    futures_aligns = (
        (direction == "long" and ctx.futures_bias in ("bullish", "strong_bullish")) or
        (direction == "short" and ctx.futures_bias in ("bearish", "strong_bearish")))
    futures_contradicts = (
        (direction == "long" and ctx.futures_bias in ("bearish", "strong_bearish")) or
        (direction == "short" and ctx.futures_bias in ("bullish", "strong_bullish")))
    lr = 1.25 if futures_aligns else (0.70 if futures_contradicts else 1.0)
    dimensions.append(ConfluenceDimension(
        "Futures Alignment", futures_aligns, lr, 0.9,
        f"Futures {ctx.futures_bias} vs {direction} → LR={lr:.2f}"))

    # ── DIM 3: IB Direction ──────────────────────────────────────────────────
    if ctx.ib_locked and ctx.ib_direction not in ("none", None):
        ib_aligns = ctx.ib_direction == direction
        lr = 1.35 if ib_aligns else 0.60
        dimensions.append(ConfluenceDimension(
            "IB Direction", ib_aligns, lr, 0.95,
            f"IB broke {ctx.ib_direction} vs {direction} → LR={lr:.2f}"))
    else:
        dimensions.append(ConfluenceDimension("IB Direction", True, 1.0, 0.0, "IB not locked"))

    # ── DIM 4: ATR Budget ────────────────────────────────────────────────────
    if ctx.atr_consumed_pct < 0.50:   lr = 1.20
    elif ctx.atr_consumed_pct < 0.75: lr = 1.0
    elif ctx.atr_consumed_pct < 0.85: lr = 0.75
    else: lr = 0.45
    dimensions.append(ConfluenceDimension(
        "ATR Budget", lr > 1.0, lr, 0.85,
        f"ATR {ctx.atr_consumed_pct:.0%} consumed → LR={lr:.2f}"))

    # ── DIM 5: PDH/PDL Proximity ─────────────────────────────────────────────
    mid = tracker.price_history[-1] if tracker.price_history else 0
    if mid > 0 and ctx.pdh > 0 and ctx.pdl > 0:
        dist_to_pdh = abs(mid - ctx.pdh) / ctx.atr if ctx.atr > 0 else 1.0
        dist_to_pdl = abs(mid - ctx.pdl) / ctx.atr if ctx.atr > 0 else 1.0
        if direction == "long" and dist_to_pdh < 0.3:     lr = 0.70
        elif direction == "short" and dist_to_pdl < 0.3:   lr = 0.70
        elif direction == "long" and dist_to_pdl < 0.3:    lr = 1.20
        elif direction == "short" and dist_to_pdh < 0.3:   lr = 1.20
        else: lr = 1.0
        dimensions.append(ConfluenceDimension(
            "PDH/PDL Proximity", lr > 1.0, lr, 0.7,
            f"Dist PDH={dist_to_pdh:.2f}ATR, PDL={dist_to_pdl:.2f}ATR → LR={lr:.2f}"))
    else:
        dimensions.append(ConfluenceDimension("PDH/PDL", True, 1.0, 0.0, "No PDH/PDL data"))

    # ── DIM 6: Session State ─────────────────────────────────────────────────
    state = ctx.session_state
    if state in (SessionState.IB_FORMATION, SessionState.MID_MORNING): lr = 1.15
    elif state in (SessionState.OPENING_DRIVE, SessionState.POWER_HOUR): lr = 1.05
    elif state == SessionState.LUNCH_CHOP: lr = 0.80
    elif state == SessionState.CLOSE_POSITION: lr = 0.50
    else: lr = 0.90
    dimensions.append(ConfluenceDimension(
        "Session State", lr > 1.0, lr, 0.75,
        f"State={state.value} → LR={lr:.2f}"))

    # ── DIM 7: Day of Week ───────────────────────────────────────────────────
    lr = 1.0 + (ctx.day_strength - 1.0) * 0.5
    dimensions.append(ConfluenceDimension(
        "Day Strength", lr > 1.0, lr, 0.5,
        f"{ctx.day_name} strength={ctx.day_strength:.2f}x → LR={lr:.2f}"))

    # ── DIM 8: VWAP Alignment ────────────────────────────────────────────────
    vwap = tracker.vwap or tracker.open_price or mid
    if mid > 0 and vwap > 0:
        above_vwap = mid > vwap
        aligns = (direction == "long" and above_vwap) or (direction == "short" and not above_vwap)
        lr = 1.20 if aligns else 0.75
        dimensions.append(ConfluenceDimension(
            "VWAP Alignment", aligns, lr, 0.85,
            f"Price {'above' if above_vwap else 'below'} VWAP vs {direction} → LR={lr:.2f}"))
    else:
        dimensions.append(ConfluenceDimension("VWAP", True, 1.0, 0.0, "No VWAP data"))

    # ── DIM 9: Information Entropy ───────────────────────────────────────────
    entropy = compute_price_entropy(tracker)
    if entropy < 0.40:   lr = 1.25
    elif entropy < 0.60: lr = 1.05
    elif entropy < 0.80: lr = 0.85
    else: lr = 0.60
    dimensions.append(ConfluenceDimension(
        "Market Entropy", lr > 1.0, lr, 0.7,
        f"Entropy={entropy:.2f} → LR={lr:.2f}"))

    # ── DIM 10: Move Energy ──────────────────────────────────────────────────
    energy = compute_move_energy(tracker)
    momentum_setups = ("ORD-02", "VOL-03", "ICT-03", "OD-01", "GAP-02",
                       "IB-01", "MID-02", "PWR-01", "PWR-02", "VWAP-03", "ES-ORD-02")
    if setup_id in momentum_setups:
        lr = 1.15 if energy > 0.6 else (0.80 if energy < 0.3 else 1.0)
    else:
        lr = 1.15 if energy < 0.4 else (0.80 if energy > 0.7 else 1.0)
    dimensions.append(ConfluenceDimension(
        "Move Energy", lr > 1.0, lr, 0.6,
        f"Energy={energy:.2f} → LR={lr:.2f}"))

    # ── DIM 11: Multi-Timeframe Alignment (V20 NEW) ─────────────────────────
    mtf_aligned = False
    mtf_contradicts = False
    if ctx.mtf_trend_m15 != "neutral":
        if ctx.mtf_trend_m15 == direction and ctx.mtf_m5_confirms:
            mtf_aligned = True
            lr = 1.30  # strong MTF agreement = very bullish
        elif ctx.mtf_trend_m15 != direction and ctx.mtf_trend_m15 != "neutral":
            mtf_contradicts = True
            lr = 0.55  # fighting M15 trend = dangerous
        else:
            lr = 1.0
    else:
        lr = 1.0
    # H1 overlay: if H1 also agrees, extra boost
    if ctx.mtf_trend_h1 == direction and mtf_aligned:
        lr = min(lr * 1.10, 1.50)
    elif ctx.mtf_trend_h1 != direction and ctx.mtf_trend_h1 != "neutral":
        lr = max(lr * 0.85, 0.40)
    dimensions.append(ConfluenceDimension(
        "MTF Alignment", mtf_aligned, lr, 0.90,
        f"M15={ctx.mtf_trend_m15} H1={ctx.mtf_trend_h1} M5conf={ctx.mtf_m5_confirms} → LR={lr:.2f}"))

    # ── COMPUTE POSTERIOR ────────────────────────────────────────────────────
    prior_odds = prior / (1.0 - prior) if prior < 1.0 else 100.0
    combined_lr = 1.0
    for dim in dimensions:
        if dim.weight > 0:
            adjusted_lr = 1.0 + (dim.likelihood_ratio - 1.0) * dim.weight
            combined_lr *= adjusted_lr

    posterior_odds = prior_odds * combined_lr
    posterior = posterior_odds / (1.0 + posterior_odds)
    posterior = max(0.05, min(0.98, posterior))

    # V20: Apply regime multiplier
    regime_m = get_regime_mult(setup_id, ctx.regime)
    if regime_m <= 0.0:
        posterior = 0.0  # SUPPRESSED
    else:
        if regime_m != 1.0:
            adjusted_odds = posterior_odds * regime_m
            posterior = adjusted_odds / (1.0 + adjusted_odds)
            posterior = max(0.05, min(0.98, posterior))

    # 3B: Time-of-day multiplier (from ghost data analysis)
    _tod = now_et_time()
    if dtime(9, 30) <= _tod < dtime(10, 0):      tod_mult = 0.95  # opening noise
    elif dtime(10, 0) <= _tod < dtime(11, 30):    tod_mult = 1.05  # institutional setup
    elif dtime(11, 30) <= _tod < dtime(13, 0):    tod_mult = 0.90  # lunch chop
    elif dtime(13, 0) <= _tod < dtime(14, 0):     tod_mult = 0.95  # early afternoon
    elif dtime(14, 0) <= _tod < dtime(15, 30):    tod_mult = 1.05  # afternoon trend
    else:                                          tod_mult = 1.00  # closing
    posterior = max(0.05, min(0.98, posterior * tod_mult))

    confirming = sum(1 for d in dimensions if d.confirms and d.weight > 0)
    contradicting = sum(1 for d in dimensions if not d.confirms and d.weight > 0)
    total = sum(1 for d in dimensions if d.weight > 0)

    # Conviction levels
    if posterior >= 0.82 and confirming >= 7:     level = "ELITE"
    elif posterior >= 0.72 and confirming >= 5:   level = "HIGH"
    elif posterior >= 0.60 and confirming >= 4:   level = "STANDARD"
    elif posterior >= 0.50:                       level = "REDUCED"
    elif posterior >= 0.40 and confirming >= 3:   level = "SCALP"
    else:                                         level = "REJECT"

    logger.info("[BAYES] %s %s: Prior=%.1f%% → Post=%.1f%% | %d/%d | R=%s×%.2f | %s",
                setup_id, direction, prior*100, posterior*100,
                confirming, total, ctx.regime, regime_m, level)

    return BayesianConviction(
        prior=prior, posterior=posterior, dimensions=dimensions,
        confirming=confirming, contradicting=contradicting, total=total,
        conviction_level=level,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ENTROPY & ENERGY
# ═══════════════════════════════════════════════════════════════════════════════

def compute_price_entropy(tracker: InstrumentTracker) -> float:
    prices = tracker.price_history
    if len(prices) < 20:
        return 0.50
    returns = [(prices[i] - prices[i-1]) / prices[i-1]
               for i in range(1, len(prices)) if prices[i-1] > 0]
    if not returns:
        return 0.50
    threshold = 0.0005
    big_threshold = threshold * 3
    bins = {"big_down": 0, "small_down": 0, "flat": 0, "small_up": 0, "big_up": 0}
    for r in returns:
        if r < -big_threshold:     bins["big_down"] += 1
        elif r < -threshold:       bins["small_down"] += 1
        elif r > big_threshold:    bins["big_up"] += 1
        elif r > threshold:        bins["small_up"] += 1
        else:                      bins["flat"] += 1
    total = len(returns)
    probs = [count / total for count in bins.values() if count > 0]
    if not probs:
        return 0.50
    entropy = -sum(p * math.log2(p) for p in probs)
    max_entropy = math.log2(5)
    return entropy / max_entropy


def compute_move_energy(tracker: InstrumentTracker) -> float:
    prices = tracker.price_history
    spreads = tracker.volume_history
    if len(prices) < 10 or len(spreads) < 10:
        return 0.50
    n = min(20, len(prices))
    recent_prices = prices[-n:]
    recent_spreads = spreads[-n:]
    total_move = abs(recent_prices[-1] - recent_prices[0])
    avg_price = sum(recent_prices) / len(recent_prices)
    move_pct = total_move / avg_price if avg_price > 0 else 0
    avg_spread = sum(recent_spreads) / len(recent_spreads) if recent_spreads else 1.0
    baseline_spread = sum(spreads) / len(spreads) if spreads else avg_spread
    spread_ratio = baseline_spread / avg_spread if avg_spread > 0 else 1.0
    energy = min(1.0, move_pct * 500) * min(1.5, spread_ratio)
    return max(0.0, min(1.0, energy))


# ═══════════════════════════════════════════════════════════════════════════════
# EXPECTED VALUE CALCULATOR
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExpectedValueResult:
    ev_dollars:         float
    win_probability:    float
    expected_reward:    float
    expected_risk:      float
    reward_risk_ratio:  float
    kelly_fraction:     float
    constrained_kelly:  float
    opportunity_cost:   float
    net_ev:             float
    action:             str


def compute_expected_value(
    win_prob: float, reward_dollars: float, risk_dollars: float,
    account_balance: float, max_position_pct: float,
    minutes_remaining: float, avg_setups_per_hour: float = 1.5,
) -> ExpectedValueResult:
    ev = (win_prob * reward_dollars) - ((1.0 - win_prob) * risk_dollars)
    rr = reward_dollars / risk_dollars if risk_dollars > 0 else 0.0
    b, p, q = rr, win_prob, 1.0 - win_prob
    kelly_raw = max(0.0, ((b * p) - q) / b) if b > 0 else 0.0
    constrained = min(kelly_raw * 0.25, max_position_pct)

    hours_left = minutes_remaining / 60.0
    expected_future_setups = hours_left * avg_setups_per_hour
    avg_future_ev = ev * 0.80
    prob_better = 1.0 - (1.0 - 0.30) ** max(1, int(expected_future_setups))
    opportunity_cost = prob_better * avg_future_ev * 1.10
    net_ev = ev - opportunity_cost

    if net_ev <= 0 or ev <= 0:
        action = "SKIP"
    elif net_ev < ev * 0.30 and minutes_remaining > 120:
        action = "WAIT"
    else:
        action = "TRADE"

    return ExpectedValueResult(
        ev_dollars=round(ev, 2), win_probability=round(win_prob, 4),
        expected_reward=round(reward_dollars, 2), expected_risk=round(risk_dollars, 2),
        reward_risk_ratio=round(rr, 2), kelly_fraction=round(kelly_raw, 4),
        constrained_kelly=round(constrained, 4), opportunity_cost=round(opportunity_cost, 2),
        net_ev=round(net_ev, 2), action=action,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MONTE CARLO STRESS TEST
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class StressTestResult:
    scenarios_run:          int
    worst_case_pnl:         float
    median_outcome:         float
    best_case_pnl:          float
    prob_daily_limit_breach: float
    prob_max_loss_breach:   float
    risk_approved:          bool
    reason:                 str


def monte_carlo_stress_test(
    current_pnl: float, proposed_risk: float, win_prob: float,
    current_positions: int, open_risk: float, daily_limit: float,
    max_loss: float, current_equity: float, vix: float,
    n_scenarios: int = 500,
) -> StressTestResult:
    outcomes = []
    tail_mult = 1.0 + max(0, (vix - 20)) * 0.05
    for _ in range(n_scenarios):
        scenario_pnl = current_pnl
        if random.random() < win_prob:
            scenario_pnl += proposed_risk * 2.0
        else:
            tail = 1.0 + random.random() * 0.3 * tail_mult
            scenario_pnl -= proposed_risk * tail
        for _ in range(current_positions):
            pos_risk = open_risk / max(1, current_positions)
            if random.random() < 0.55:
                scenario_pnl += pos_risk * 1.5
            else:
                tail = 1.0 + random.random() * 0.2 * tail_mult
                scenario_pnl -= pos_risk * tail
        outcomes.append(scenario_pnl)
    outcomes.sort()
    worst_5 = outcomes[int(n_scenarios * 0.05)]
    median  = outcomes[int(n_scenarios * 0.50)]
    best_95 = outcomes[int(n_scenarios * 0.95)]
    daily_breach_count = sum(1 for o in outcomes if o < -daily_limit)
    max_breach_count = sum(1 for o in outcomes
                           if (current_equity + o) < (current_equity - max_loss))
    prob_daily = daily_breach_count / n_scenarios
    prob_max = max_breach_count / n_scenarios
    approved = prob_daily < 0.05 and prob_max < 0.02
    reason = "Stress test PASSED" if approved else \
             f"FAILED: P(daily)={prob_daily:.1%}, P(max)={prob_max:.1%}"
    return StressTestResult(
        scenarios_run=n_scenarios, worst_case_pnl=round(worst_5, 2),
        median_outcome=round(median, 2), best_case_pnl=round(best_95, 2),
        prob_daily_limit_breach=round(prob_daily, 4),
        prob_max_loss_breach=round(prob_max, 4),
        risk_approved=approved, reason=reason,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PARAMETER EVOLUTION
# ═══════════════════════════════════════════════════════════════════════════════

class ParameterEvolver:
    def __init__(self):
        self._setup_stats: dict[str, dict] = {}
        self._alpha = 0.05

    def update_from_evidence(self, evidence_records: list[dict]) -> None:
        stats: dict[str, dict] = {}
        for r in evidence_records:
            sid = r.get("setup_id", "")
            outcome = r.get("outcome", "")
            if outcome not in ("WIN", "LOSS") or not sid:
                continue
            if sid not in stats:
                stats[sid] = {"wins": 0, "losses": 0, "total_pnl": 0.0, "trades": 0}
            stats[sid]["trades"] += 1
            if outcome == "WIN":
                stats[sid]["wins"] += 1
            else:
                stats[sid]["losses"] += 1
            stats[sid]["total_pnl"] += r.get("pnl", 0)
        for sid, s in stats.items():
            if s["trades"] > 0:
                self._setup_stats[sid] = {
                    "win_rate": s["wins"] / s["trades"],
                    "avg_pnl": s["total_pnl"] / s["trades"],
                    "trades": s["trades"],
                }

    def get_live_win_rate(self, setup_id: str) -> Optional[float]:
        stats = self._setup_stats.get(setup_id)
        if stats and stats["trades"] >= 15:
            return stats["win_rate"]
        return None

    def get_evolved_parameter(self, setup_id: str, param: str, default: float) -> float:
        stats = self._setup_stats.get(setup_id)
        if not stats or stats["trades"] < 15:
            return default
        if param == "win_rate":
            return stats["win_rate"] * 0.70 + default * 0.30
        return default

    def get_degradation_alert(self) -> Optional[str]:
        alerts = []
        for sid, stats in self._setup_stats.items():
            if stats["trades"] < 20:
                continue
            wr = stats["win_rate"]
            if wr < 0.50:
                alerts.append(f"{sid}: WR={wr:.0%} (critical)")
            elif wr < 0.55:
                alerts.append(f"{sid}: WR={wr:.0%} (degrading)")
        if alerts:
            return "DEGRADATION: " + " | ".join(alerts)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# NON-REACTION + REGIME TRANSITION PREDICTION
# ═══════════════════════════════════════════════════════════════════════════════

def detect_non_reaction(ctx: MarketContext, tracker: InstrumentTracker) -> Optional[str]:
    if not tracker.price_history or len(tracker.price_history) < 10:
        return None
    recent_move = abs(tracker.price_history[-1] - tracker.price_history[-10])
    expected_move = ctx.atr * 0.05
    if ctx.vix >= 25 and recent_move < expected_move * 0.3:
        return "NON-REACTION: VIX elevated but price stable — fear absorbed"
    if abs(ctx.futures_pct) > 0.005 and recent_move < expected_move * 0.2:
        if ctx.futures_bias in ("strong_bearish", "bearish"):
            return "NON-REACTION: Bearish futures but price holding — hidden strength"
        elif ctx.futures_bias in ("strong_bullish", "bullish"):
            return "NON-REACTION: Bullish futures but price stalling — hidden weakness"
    return None


def predict_regime_transition(ctx: MarketContext, tracker: InstrumentTracker) -> tuple[float, str]:
    indicators = []
    if ctx.atr_consumed_pct > 0.85:
        indicators.append(0.25)
    elif ctx.atr_consumed_pct > 0.75:
        indicators.append(0.15)
    if len(tracker.price_history) >= 50:
        recent_entropy = compute_price_entropy(tracker)
        if recent_entropy > 0.70:
            indicators.append(0.20)
    if len(tracker.volume_history) >= 20:
        recent_spread = sum(tracker.volume_history[-10:]) / 10
        overall_spread = sum(tracker.volume_history) / len(tracker.volume_history)
        if recent_spread > overall_spread * 1.5:
            indicators.append(0.15)
    if not indicators:
        return 0.05, "Regime stable"
    prob = min(0.85, sum(indicators))
    return prob, f"Transition prob: {prob:.0%} — {len(indicators)} indicators"


# Global
_evolver = ParameterEvolver()

def get_evolver() -> ParameterEvolver:
    return _evolver
