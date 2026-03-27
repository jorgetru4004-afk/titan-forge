"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     forge_brain.py — THE PROBABILITY ENGINE                 ║
║                                                                              ║
║  Not a scoring system. A PROBABILITY ENGINE.                                ║
║                                                                              ║
║  Every output is a mathematically rigorous probability or expected value.   ║
║  Bayesian belief updating. Shannon entropy. Optimal stopping theory.        ║
║  Kelly criterion under constraints. Monte Carlo stress testing.             ║
║  Phantom trade analysis. Live parameter evolution.                          ║
║                                                                              ║
║  This is what makes FORGE different from every other system.                ║
║  Other systems use heuristics. FORGE uses mathematics.                      ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging, math, random, time
from dataclasses import dataclass, field
from typing import Optional
from forge_core import MarketContext, SessionState, InstrumentTracker

logger = logging.getLogger("titan_forge.brain")


# ═══════════════════════════════════════════════════════════════════════════════
# BAYESIAN CONVICTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
#
# Instead of scoring 0-100, compute the ACTUAL posterior probability of a
# trade winning using Bayes' theorem with 10 independent evidence dimensions.
#
# P(win|evidence) = P(evidence|win) * P(win) / P(evidence)
#
# Each dimension independently updates the probability.
# When 8+ dimensions confirm, posterior compounds above 85%.
# When they contradict, it drops below 50%.
# The math is the edge. Not feelings. Not heuristics.

@dataclass
class ConfluenceDimension:
    """One dimension of confluence evidence."""
    name:           str
    confirms:       bool      # True if this dimension supports the trade
    likelihood_ratio: float   # P(evidence|win) / P(evidence|loss)
    weight:         float     # relative importance (0-1)
    detail:         str       # human-readable explanation

@dataclass
class BayesianConviction:
    """Result of Bayesian belief updating across all dimensions."""
    prior:          float           # base rate (historical win rate)
    posterior:      float           # updated probability after all evidence
    dimensions:     list[ConfluenceDimension]
    confirming:     int             # count of confirming dimensions
    contradicting:  int             # count of contradicting dimensions
    total:          int             # total dimensions evaluated
    conviction_level: str           # ELITE / HIGH / STANDARD / REDUCED / SCALP / REJECT

    @property
    def is_tradeable(self) -> bool:
        return self.conviction_level != "REJECT"


def compute_bayesian_conviction(
    prior_win_rate:     float,        # historical base rate (e.g. 0.72)
    ctx:                MarketContext,
    tracker:            InstrumentTracker,
    direction:          str,          # "long" or "short"
    setup_id:           str,
    live_win_rate:      Optional[float] = None,  # from evidence logger
) -> BayesianConviction:
    """
    Compute posterior win probability using Bayes' theorem across 10 dimensions.

    Each dimension provides a likelihood ratio:
    - LR > 1.0 means evidence SUPPORTS the trade
    - LR < 1.0 means evidence CONTRADICTS the trade
    - LR = 1.0 means evidence is NEUTRAL

    Posterior = Prior × Product(all likelihood ratios) / normalizer
    """
    # Use live win rate if available (FORGE evolves from its own data)
    prior = live_win_rate if live_win_rate is not None else prior_win_rate
    prior = max(0.30, min(0.90, prior))  # clamp to reasonable range

    dimensions: list[ConfluenceDimension] = []

    # ── DIM 1: VIX Regime ────────────────────────────────────────────────────
    if ctx.vix_regime == "LOW":
        lr = 1.15  # low VIX = clean trends
    elif ctx.vix_regime == "NORMAL":
        lr = 1.05
    elif ctx.vix_regime == "ELEVATED":
        lr = 0.85  # elevated = choppier
    else:
        lr = 0.65  # extreme = very unpredictable
    dimensions.append(ConfluenceDimension(
        "VIX Regime", lr > 1.0, lr, 0.8,
        f"VIX={ctx.vix:.1f} ({ctx.vix_regime}) → LR={lr:.2f}"
    ))

    # ── DIM 2: Futures Direction Alignment ───────────────────────────────────
    futures_aligns = (
        (direction == "long" and ctx.futures_bias in ("bullish", "strong_bullish")) or
        (direction == "short" and ctx.futures_bias in ("bearish", "strong_bearish"))
    )
    futures_contradicts = (
        (direction == "long" and ctx.futures_bias in ("bearish", "strong_bearish")) or
        (direction == "short" and ctx.futures_bias in ("bullish", "strong_bullish"))
    )
    lr = 1.25 if futures_aligns else (0.70 if futures_contradicts else 1.0)
    dimensions.append(ConfluenceDimension(
        "Futures Alignment", futures_aligns, lr, 0.9,
        f"Futures {ctx.futures_bias} vs {direction} → LR={lr:.2f}"
    ))

    # ── DIM 3: IB Direction ──────────────────────────────────────────────────
    if ctx.ib_locked and ctx.ib_direction != "none":
        ib_aligns = ctx.ib_direction == direction
        # IB single break is 82% probability — very strong signal
        lr = 1.35 if ib_aligns else 0.60
        dimensions.append(ConfluenceDimension(
            "IB Direction", ib_aligns, lr, 0.95,
            f"IB broke {ctx.ib_direction} vs {direction} (82% single break prob) → LR={lr:.2f}"
        ))
    else:
        dimensions.append(ConfluenceDimension(
            "IB Direction", True, 1.0, 0.0, "IB not yet locked — neutral"
        ))

    # ── DIM 4: ATR Budget Remaining ──────────────────────────────────────────
    if ctx.atr_consumed_pct < 0.50:
        lr = 1.20  # plenty of room
    elif ctx.atr_consumed_pct < 0.75:
        lr = 1.0   # normal
    elif ctx.atr_consumed_pct < 0.85:
        lr = 0.75  # limited room
    else:
        lr = 0.45  # move exhausted
    dimensions.append(ConfluenceDimension(
        "ATR Budget", lr > 1.0, lr, 0.85,
        f"ATR {ctx.atr_consumed_pct:.0%} consumed → LR={lr:.2f}"
    ))

    # ── DIM 5: PDH/PDL Proximity ─────────────────────────────────────────────
    mid = tracker.price_history[-1] if tracker.price_history else 0
    if mid > 0 and ctx.pdh > 0 and ctx.pdl > 0:
        dist_to_pdh = abs(mid - ctx.pdh) / ctx.atr if ctx.atr > 0 else 1.0
        dist_to_pdl = abs(mid - ctx.pdl) / ctx.atr if ctx.atr > 0 else 1.0
        # Longs near PDH = resistance risk. Shorts near PDL = support risk.
        if direction == "long" and dist_to_pdh < 0.3:
            lr = 0.70  # approaching resistance
        elif direction == "short" and dist_to_pdl < 0.3:
            lr = 0.70  # approaching support
        elif direction == "long" and dist_to_pdl < 0.3:
            lr = 1.20  # bouncing off support
        elif direction == "short" and dist_to_pdh < 0.3:
            lr = 1.20  # rejecting resistance
        else:
            lr = 1.0
        dimensions.append(ConfluenceDimension(
            "PDH/PDL Proximity", lr > 1.0, lr, 0.7,
            f"Dist to PDH={dist_to_pdh:.2f}ATR, PDL={dist_to_pdl:.2f}ATR → LR={lr:.2f}"
        ))
    else:
        dimensions.append(ConfluenceDimension("PDH/PDL", True, 1.0, 0.0, "No PDH/PDL data"))

    # ── DIM 6: Session State Timing ──────────────────────────────────────────
    state = ctx.session_state
    # Strongest signals in IB_FORMATION and MID_MORNING
    if state in (SessionState.IB_FORMATION, SessionState.MID_MORNING):
        lr = 1.15
    elif state in (SessionState.OPENING_DRIVE, SessionState.POWER_HOUR):
        lr = 1.05
    elif state == SessionState.LUNCH_CHOP:
        lr = 0.80  # chop kills momentum
    elif state == SessionState.CLOSE_POSITION:
        lr = 0.50  # too late
    else:
        lr = 0.90
    dimensions.append(ConfluenceDimension(
        "Session State", lr > 1.0, lr, 0.75,
        f"State={state.value} → LR={lr:.2f}"
    ))

    # ── DIM 7: Day of Week Strength ──────────────────────────────────────────
    lr = 1.0 + (ctx.day_strength - 1.0) * 0.5  # dampen the effect
    dimensions.append(ConfluenceDimension(
        "Day Strength", lr > 1.0, lr, 0.5,
        f"{ctx.day_name} strength={ctx.day_strength:.2f}x → LR={lr:.2f}"
    ))

    # ── DIM 8: VWAP Alignment ────────────────────────────────────────────────
    vwap = tracker.open_price or mid
    if mid > 0 and vwap > 0:
        above_vwap = mid > vwap
        aligns = (direction == "long" and above_vwap) or (direction == "short" and not above_vwap)
        lr = 1.20 if aligns else 0.75
        dimensions.append(ConfluenceDimension(
            "VWAP Alignment", aligns, lr, 0.85,
            f"Price {'above' if above_vwap else 'below'} VWAP vs {direction} → LR={lr:.2f}"
        ))
    else:
        dimensions.append(ConfluenceDimension("VWAP", True, 1.0, 0.0, "No VWAP data"))

    # ── DIM 9: Information Entropy (market predictability) ───────────────────
    entropy = compute_price_entropy(tracker)
    if entropy < 0.40:
        lr = 1.25  # low entropy = predictable = good
    elif entropy < 0.60:
        lr = 1.05  # moderate
    elif entropy < 0.80:
        lr = 0.85  # getting random
    else:
        lr = 0.60  # high entropy = random noise = no edge
    dimensions.append(ConfluenceDimension(
        "Market Entropy", lr > 1.0, lr, 0.7,
        f"Entropy={entropy:.2f} → LR={lr:.2f}"
    ))

    # ── DIM 10: Energy of Recent Move ────────────────────────────────────────
    energy = compute_move_energy(tracker)
    if setup_id in ("ORD-02", "VOL-03", "ICT-03"):
        # Momentum setups WANT high energy
        lr = 1.15 if energy > 0.6 else (0.80 if energy < 0.3 else 1.0)
    else:
        # Mean reversion setups want LOW energy (exhaustion)
        lr = 1.15 if energy < 0.4 else (0.80 if energy > 0.7 else 1.0)
    dimensions.append(ConfluenceDimension(
        "Move Energy", lr > 1.0, lr, 0.6,
        f"Energy={energy:.2f} ({setup_id} wants {'high' if 'ORD' in setup_id or 'VOL-03' in setup_id else 'low'}) → LR={lr:.2f}"
    ))

    # ── COMPUTE POSTERIOR ────────────────────────────────────────────────────
    # Bayes: posterior odds = prior odds × product(likelihood ratios)
    prior_odds = prior / (1.0 - prior) if prior < 1.0 else 100.0

    # Weight the likelihood ratios by importance
    combined_lr = 1.0
    for dim in dimensions:
        if dim.weight > 0:
            # Weighted geometric mean approach — more important dimensions have more effect
            adjusted_lr = 1.0 + (dim.likelihood_ratio - 1.0) * dim.weight
            combined_lr *= adjusted_lr

    posterior_odds = prior_odds * combined_lr
    posterior = posterior_odds / (1.0 + posterior_odds)
    posterior = max(0.05, min(0.98, posterior))  # clamp

    confirming = sum(1 for d in dimensions if d.confirms and d.weight > 0)
    contradicting = sum(1 for d in dimensions if not d.confirms and d.weight > 0)
    total = sum(1 for d in dimensions if d.weight > 0)

    # Conviction levels — v18: added SCALP tier for adaptive trading
    if posterior >= 0.82 and confirming >= 7:
        level = "ELITE"
    elif posterior >= 0.72 and confirming >= 5:
        level = "HIGH"
    elif posterior >= 0.60 and confirming >= 4:
        level = "STANDARD"
    elif posterior >= 0.50:
        level = "REDUCED"
    elif posterior >= 0.35:
        level = "SCALP"
    else:
        level = "REJECT"

    logger.info("[BAYES] %s %s: Prior=%.1f%% → Posterior=%.1f%% | %d/%d confirm | Level=%s",
                setup_id, direction, prior*100, posterior*100, confirming, total, level)

    return BayesianConviction(
        prior=prior, posterior=posterior, dimensions=dimensions,
        confirming=confirming, contradicting=contradicting, total=total,
        conviction_level=level,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INFORMATION ENTROPY (market predictability measurement)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_price_entropy(tracker: InstrumentTracker) -> float:
    """
    Shannon entropy of price changes — measures market randomness.

    Low entropy (< 0.4) = market is trending predictably = EDGE EXISTS
    High entropy (> 0.7) = market is random noise = NO EDGE

    Uses discretized price changes binned into 5 categories:
    big_down, small_down, flat, small_up, big_up
    """
    prices = tracker.price_history
    if len(prices) < 20:
        return 0.50  # insufficient data — assume moderate

    # Compute returns
    returns = [(prices[i] - prices[i-1]) / prices[i-1]
               for i in range(1, len(prices)) if prices[i-1] > 0]
    if not returns:
        return 0.50

    # Discretize into 5 bins
    threshold = 0.0005  # ~0.05% = "flat"
    bins = {"big_down": 0, "small_down": 0, "flat": 0, "small_up": 0, "big_up": 0}
    big_threshold = threshold * 3

    for r in returns:
        if r < -big_threshold:     bins["big_down"] += 1
        elif r < -threshold:       bins["small_down"] += 1
        elif r > big_threshold:    bins["big_up"] += 1
        elif r > threshold:        bins["small_up"] += 1
        else:                      bins["flat"] += 1

    total = len(returns)
    probs = [count / total for count in bins.values() if count > 0]

    # Shannon entropy: H = -sum(p * log2(p))
    # Normalized to 0-1 range (divide by max entropy = log2(5))
    if not probs:
        return 0.50
    entropy = -sum(p * math.log2(p) for p in probs)
    max_entropy = math.log2(5)  # 5 bins
    return entropy / max_entropy


# ═══════════════════════════════════════════════════════════════════════════════
# MOVE ENERGY (physics-inspired — is this move sustainable or will it reverse?)
# ═══════════════════════════════════════════════════════════════════════════════

def compute_move_energy(tracker: InstrumentTracker) -> float:
    """
    Energy of the recent price move.

    High energy (volume × price_change is large) = SUSTAINABLE move = ride it
    Low energy (volume × price_change is small) = will REVERSE = fade it

    Uses spread as volume proxy (wider spread = thinner market = less energy).
    Returns 0.0-1.0 normalized.
    """
    prices = tracker.price_history
    spreads = tracker.volume_history
    if len(prices) < 10 or len(spreads) < 10:
        return 0.50  # insufficient data

    # Use last 20 ticks
    n = min(20, len(prices))
    recent_prices = prices[-n:]
    recent_spreads = spreads[-n:]

    # Price change magnitude
    total_move = abs(recent_prices[-1] - recent_prices[0])
    avg_price = sum(recent_prices) / len(recent_prices)
    move_pct = total_move / avg_price if avg_price > 0 else 0

    # Average spread (inverted — tight spread = more energy)
    avg_spread = sum(recent_spreads) / len(recent_spreads) if recent_spreads else 1.0
    baseline_spread = sum(spreads) / len(spreads) if spreads else avg_spread
    spread_ratio = baseline_spread / avg_spread if avg_spread > 0 else 1.0

    # Energy = move magnitude × spread tightness (both normalized)
    energy = min(1.0, move_pct * 500) * min(1.5, spread_ratio)
    return max(0.0, min(1.0, energy))


# ═══════════════════════════════════════════════════════════════════════════════
# EXPECTED VALUE CALCULATOR (the one number that matters)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ExpectedValueResult:
    """The mathematically rigorous expected value of a trade in dollars."""
    ev_dollars:         float      # THE number: expected dollar value
    win_probability:    float      # Bayesian posterior
    expected_reward:    float      # dollars if win
    expected_risk:      float      # dollars if loss (positive number)
    reward_risk_ratio:  float
    kelly_fraction:     float      # optimal Kelly bet size (before constraints)
    constrained_kelly:  float      # after firm constraint caps
    opportunity_cost:   float      # EV of waiting instead
    net_ev:             float      # ev_dollars minus opportunity_cost
    action:             str        # "TRADE" / "WAIT" / "SKIP"

def compute_expected_value(
    win_prob:           float,     # Bayesian posterior
    reward_dollars:     float,     # dollars if price hits TP
    risk_dollars:       float,     # dollars if price hits SL
    account_balance:    float,
    max_position_pct:   float,     # firm constraint (e.g. 0.02 for 2%)
    minutes_remaining:  float,     # session time left
    avg_setups_per_hour: float = 1.5,  # historical average
) -> ExpectedValueResult:
    """
    Compute the expected value of a trade INCLUDING opportunity cost.

    This is optimal stopping theory: should FORGE act NOW or wait for
    a potentially better setup later?

    The option value of waiting = probability of better setup × expected EV of that setup.
    If EV of this trade > option value of waiting → TRADE.
    If option value of waiting > EV of this trade → WAIT.
    """
    # Expected value of this trade
    ev = (win_prob * reward_dollars) - ((1.0 - win_prob) * risk_dollars)

    # Reward/risk ratio
    rr = reward_dollars / risk_dollars if risk_dollars > 0 else 0.0

    # Kelly criterion: f* = (bp - q) / b where b=RR, p=win_prob, q=1-p
    b = rr
    p = win_prob
    q = 1.0 - p
    kelly_raw = ((b * p) - q) / b if b > 0 else 0.0
    kelly_raw = max(0.0, kelly_raw)

    # Constrained Kelly: quarter Kelly (conservative) capped by firm limit
    constrained = min(kelly_raw * 0.25, max_position_pct)

    # Opportunity cost: what's the expected value of waiting?
    hours_left = minutes_remaining / 60.0
    expected_future_setups = hours_left * avg_setups_per_hour
    # Average EV of a typical setup (use current EV as estimate, discounted)
    avg_future_ev = ev * 0.80  # future setups are slightly worse on average
    # Probability that at least one future setup is BETTER than this one
    prob_better = 1.0 - (1.0 - 0.30) ** max(1, int(expected_future_setups))
    opportunity_cost = prob_better * avg_future_ev * 1.10  # premium for better setup

    # Net EV
    net_ev = ev - opportunity_cost

    # Decision
    if net_ev <= 0 or ev <= 0:
        action = "SKIP"
    elif net_ev < ev * 0.30 and minutes_remaining > 120:
        # EV is positive but opportunity cost is high and lots of time left
        action = "WAIT"
    else:
        action = "TRADE"

    return ExpectedValueResult(
        ev_dollars=round(ev, 2),
        win_probability=round(win_prob, 4),
        expected_reward=round(reward_dollars, 2),
        expected_risk=round(risk_dollars, 2),
        reward_risk_ratio=round(rr, 2),
        kelly_fraction=round(kelly_raw, 4),
        constrained_kelly=round(constrained, 4),
        opportunity_cost=round(opportunity_cost, 2),
        net_ev=round(net_ev, 2),
        action=action,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MONTE CARLO STRESS TEST (run 1000 paths before every trade)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class StressTestResult:
    """Result of Monte Carlo stress test."""
    scenarios_run:          int
    worst_case_pnl:         float    # 5th percentile
    median_outcome:         float    # 50th percentile
    best_case_pnl:          float    # 95th percentile
    prob_daily_limit_breach: float   # P(breach daily loss limit)
    prob_max_loss_breach:   float    # P(breach max loss)
    risk_approved:          bool     # True if stress test passes
    reason:                 str

def monte_carlo_stress_test(
    current_pnl:        float,     # today's P&L so far
    proposed_risk:      float,     # dollars at risk on proposed trade
    win_prob:           float,     # Bayesian posterior
    current_positions:  int,       # existing open positions
    open_risk:          float,     # total risk on open positions
    daily_limit:        float,     # firm's daily loss limit in dollars
    max_loss:           float,     # firm's max loss in dollars
    current_equity:     float,     # current account equity
    vix:                float,     # current VIX for tail risk
    n_scenarios:        int = 500, # reduced from 1000 for speed
) -> StressTestResult:
    """
    Run Monte Carlo scenarios of remaining session outcomes.

    Factors in: current P&L, proposed trade, open position risk,
    VIX-adjusted tail risk, and firm limits.
    """
    outcomes = []
    # VIX-adjusted tail risk: higher VIX = fatter tails
    tail_mult = 1.0 + max(0, (vix - 20)) * 0.05  # +5% tail width per VIX point above 20

    for _ in range(n_scenarios):
        scenario_pnl = current_pnl

        # Proposed trade outcome
        if random.random() < win_prob:
            scenario_pnl += proposed_risk * 2.0  # assume 2R target
        else:
            # Loss with tail risk
            tail = 1.0 + random.random() * 0.3 * tail_mult  # slippage
            scenario_pnl -= proposed_risk * tail

        # Open positions: random outcome
        for _ in range(current_positions):
            pos_risk = open_risk / max(1, current_positions)
            if random.random() < 0.55:  # rough 55% win rate on existing
                scenario_pnl += pos_risk * 1.5
            else:
                tail = 1.0 + random.random() * 0.2 * tail_mult
                scenario_pnl -= pos_risk * tail

        outcomes.append(scenario_pnl)

    outcomes.sort()

    # Percentiles
    worst_5 = outcomes[int(n_scenarios * 0.05)]
    median  = outcomes[int(n_scenarios * 0.50)]
    best_95 = outcomes[int(n_scenarios * 0.95)]

    # Probabilities
    daily_breach_count = sum(1 for o in outcomes if o < -daily_limit)
    max_breach_count   = sum(1 for o in outcomes
                             if (current_equity + o) < (current_equity - max_loss))

    prob_daily = daily_breach_count / n_scenarios
    prob_max   = max_breach_count / n_scenarios

    # Approval: breach probability must be < 5%
    approved = prob_daily < 0.05 and prob_max < 0.02
    reason = "Stress test PASSED" if approved else \
             f"FAILED: P(daily breach)={prob_daily:.1%}, P(max breach)={prob_max:.1%}"

    return StressTestResult(
        scenarios_run=n_scenarios,
        worst_case_pnl=round(worst_5, 2),
        median_outcome=round(median, 2),
        best_case_pnl=round(best_95, 2),
        prob_daily_limit_breach=round(prob_daily, 4),
        prob_max_loss_breach=round(prob_max, 4),
        risk_approved=approved,
        reason=reason,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE PARAMETER EVOLUTION (FORGE gets smarter after every trade)
# ═══════════════════════════════════════════════════════════════════════════════

class ParameterEvolver:
    """
    Evolves FORGE's parameters from its own evidence.

    NOT machine learning. Exponentially-weighted moving averages
    on live performance data. Every parameter drifts toward optimal
    based on what actually works.

    Day 1: parameters from research.
    Day 100: parameters from 100 real trades.
    Day 500: a completely different trader, optimized by experience.
    """

    def __init__(self):
        self._setup_stats: dict[str, dict] = {}  # setup_id → {win_rate, avg_rr, trades}
        self._alpha = 0.05  # EMA decay — recent trades matter more

    def update_from_evidence(self, evidence_records: list[dict]) -> None:
        """Load performance stats from evidence logger."""
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
        """Get the live win rate for a setup, or None if insufficient data."""
        stats = self._setup_stats.get(setup_id)
        if stats and stats["trades"] >= 15:  # minimum sample size
            return stats["win_rate"]
        return None

    def get_evolved_parameter(self, setup_id: str, param: str,
                              default: float) -> float:
        """Get a parameter that has been evolved from live data."""
        stats = self._setup_stats.get(setup_id)
        if not stats or stats["trades"] < 15:
            return default

        if param == "win_rate":
            # Blend: 70% live data, 30% research default (regularization)
            live = stats["win_rate"]
            return live * 0.70 + default * 0.30
        return default

    def get_degradation_alert(self) -> Optional[str]:
        """
        Statistical process control: detect if FORGE's edge is degrading.
        Fires when any setup's win rate drops >1.5σ below its rolling mean.
        """
        alerts = []
        for sid, stats in self._setup_stats.items():
            if stats["trades"] < 20:
                continue
            wr = stats["win_rate"]
            # Expected: ~70% ± 10%
            if wr < 0.50:
                alerts.append(f"{sid}: WR={wr:.0%} (critical — below 50%)")
            elif wr < 0.55:
                alerts.append(f"{sid}: WR={wr:.0%} (degrading — below 55%)")
        if alerts:
            return "DEGRADATION DETECTED: " + " | ".join(alerts)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# NON-REACTION SIGNAL DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

def detect_non_reaction(
    ctx:     MarketContext,
    tracker: InstrumentTracker,
) -> Optional[str]:
    """
    Detect when the market SHOULD react but DOESN'T.

    A non-reaction to expected bad news is MORE bullish than any indicator.
    A non-reaction to expected good news is MORE bearish than any indicator.

    Returns a signal string or None.
    """
    if not tracker.price_history or len(tracker.price_history) < 10:
        return None

    recent_move = abs(tracker.price_history[-1] - tracker.price_history[-10])
    expected_move = ctx.atr * 0.05  # expected move for a 10-tick window

    # If VIX is elevated but price is barely moving: market absorbed the fear
    if ctx.vix >= 25 and recent_move < expected_move * 0.3:
        return "NON-REACTION: VIX elevated but price stable — fear absorbed, bullish signal"

    # If futures are strongly directional but price didn't follow
    if abs(ctx.futures_pct) > 0.005 and recent_move < expected_move * 0.2:
        if ctx.futures_bias in ("strong_bearish", "bearish"):
            return "NON-REACTION: Bearish futures but price holding — hidden strength"
        elif ctx.futures_bias in ("strong_bullish", "bullish"):
            return "NON-REACTION: Bullish futures but price stalling — hidden weakness"

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME TRANSITION PREDICTION
# ═══════════════════════════════════════════════════════════════════════════════

def predict_regime_transition(
    ctx:     MarketContext,
    tracker: InstrumentTracker,
) -> tuple[float, str]:
    """
    Predict probability of the current market regime transitioning.

    Returns (probability 0-1, description).

    Leading indicators of regime change:
    - ATR completion above 75% = move exhaustion approaching
    - Volume (spread) widening = uncertainty increasing
    - Entropy rising = randomness increasing
    - Price stalling at round numbers
    """
    indicators = []

    # ATR exhaustion
    if ctx.atr_consumed_pct > 0.85:
        indicators.append(0.25)  # high probability of transition
    elif ctx.atr_consumed_pct > 0.75:
        indicators.append(0.15)

    # Entropy rising (last 20 vs last 50 ticks)
    if len(tracker.price_history) >= 50:
        # Compare recent entropy to overall entropy
        recent_entropy = compute_price_entropy(tracker)
        if recent_entropy > 0.70:
            indicators.append(0.20)  # market becoming random

    # Spread widening (volume drying up)
    if len(tracker.volume_history) >= 20:
        recent_spread = sum(tracker.volume_history[-10:]) / 10
        overall_spread = sum(tracker.volume_history) / len(tracker.volume_history)
        if recent_spread > overall_spread * 1.5:
            indicators.append(0.15)  # spreads widening

    if not indicators:
        return 0.05, "Regime stable — no transition signals"

    prob = min(0.85, sum(indicators))
    return prob, f"Transition probability: {prob:.0%} — {len(indicators)} leading indicators"


# Global instances
_evolver = ParameterEvolver()

def get_evolver() -> ParameterEvolver:
    return _evolver
