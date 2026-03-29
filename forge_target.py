"""
FORGE v21 — DAILY TARGET ENGINE + TRADE MANAGEMENT
====================================================
DailyTargetEngine: calculates PATH to $2,000 every morning. Adapts hourly.
SessionAdapter: tracks what's working RIGHT NOW, boosts hot strategies.
PartialExitManager: 50% at +1R, trail rest.
DynamicTargetCalculator: R:R from ATR remaining.
FastModeController: 15s cycles at key levels.
CrossMarketExploit: ES → NQ lead detection.

NEXUS Capital — Jorge Trujillo | Claude — AI Architect | March 2026
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("FORGE.target")


# ─────────────────────────────────────────────────────────────────
# ADAPTIVE TRADE MIX — regime-driven daily plan
# ─────────────────────────────────────────────────────────────────

TRADE_MIX = {
    "TREND": {
        "description": "TREND day — ride runners, momentum scalps, speed exploits",
        "components": [
            {"type": "runner",   "count": 3, "lots": 0.80, "per_trade": 600.0},
            {"type": "momentum","count": 4, "lots": 0.30, "per_trade": 60.0},
            {"type": "exploit", "count": 2, "lots": 0.50, "per_trade": 180.0},
        ],
        "target": 2400.0,
        "size_mult": 1.20,
        "lot_target": 0.60,
    },
    "CHOP": {
        "description": "CHOP day — level scalps, mean reversion, extended session",
        "components": [
            {"type": "scalp",       "count": 20, "lots": 0.25, "per_trade": 40.0},
            {"type": "mean_revert", "count": 3,  "lots": 0.40, "per_trade": 160.0},
            {"type": "extended",    "count": 2,  "lots": 0.30, "per_trade": 90.0},
        ],
        "target": 1460.0,
        "size_mult": 0.80,
        "lot_target": 0.25,
    },
    "NORMAL": {
        "description": "NORMAL day — balanced swings, scalps, runners",
        "components": [
            {"type": "swing",    "count": 6, "lots": 0.50, "per_trade": 142.0},
            {"type": "scalp",    "count": 8, "lots": 0.25, "per_trade": 45.0},
            {"type": "runner",   "count": 2, "lots": 0.60, "per_trade": 240.0},
            {"type": "extended", "count": 3, "lots": 0.30, "per_trade": 67.0},
        ],
        "target": 1892.0,
        "size_mult": 1.00,
        "lot_target": 0.35,
    },
}

# Map setups to trade types for mix tracking
SETUP_TRADE_TYPE: Dict[str, str] = {
    # Runners (hold for 2R+)
    "ORD-02": "runner", "IB-01": "runner", "VOL-03": "runner",
    "LONDON-GOLD-01": "runner", "LONDON-NQ-01": "runner",
    "CL-TREND-01": "runner",
    # Momentum
    "OD-01": "momentum", "GAP-02": "momentum", "NEWS-MOM-01": "momentum",
    "CL-MOM-01": "momentum", "VWAP-03": "momentum", "ES-LEAD-01": "momentum",
    # Scalps
    "LVL-02": "scalp", "IB-02": "scalp", "VWAP-01": "scalp",
    "VWAP-02": "scalp", "LVL-01": "scalp",
    # Mean reversion
    "VOL-05": "mean_revert", "VOL-06": "mean_revert",
    "ASIA-REVERT-01": "mean_revert", "EXT-REVERT-01": "mean_revert",
    # Swing
    "ICT-01": "swing", "ICT-02": "swing", "ICT-03": "swing",
    "SES-01": "swing", "GOLD-CORR-01": "swing",
    # Extended
    "ASIA-GOLD-01": "extended", "LONDON-FX-01": "extended",
    "PRE-RANGE-01": "extended",
    # Exploit (speed)
    "CL-GAP-01": "exploit",
}


# ─────────────────────────────────────────────────────────────────
# DAILY TARGET ENGINE
# ─────────────────────────────────────────────────────────────────

@dataclass
class DailyPlan:
    mode: str
    regime: str
    target: float
    remaining: float
    size_mult: float
    lot_target: float
    trades_needed: int
    description: str
    msg: str


class DailyTargetEngine:
    """
    Calculates the PATH to $2,000/day every morning.
    Adapts hourly based on regime, P&L, and trade outcomes.

    Behind target = add more scalps.
    Runner catches a big move = protect and reduce new entries.
    Target hit = defensive mode.
    """

    def __init__(self, target: float = 2000.0):
        self.target = target
        self.daily_pnl = 0.0
        self.trades_taken = 0
        self.trades_won = 0
        self.trade_type_pnl: Dict[str, float] = {}
        self.trade_type_count: Dict[str, int] = {}
        self._active_regime = "NORMAL"
        self._last_plan_time = 0.0
        self._big_runner_active = False

    def get_plan(self, regime: str, hours_remaining: float = 6.5,
                 atr: float = 150, atr_consumed_pct: float = 0.0) -> DailyPlan:
        """Generate adaptive daily plan based on regime and progress."""
        self._active_regime = regime
        remaining = self.target - self.daily_pnl
        self._last_plan_time = time.time()

        # TARGET HIT — defensive mode
        if remaining is not None and remaining <= 0:
            return DailyPlan(
                mode="PROTECT", regime=regime, target=self.target,
                remaining=remaining, size_mult=0.50, lot_target=0.20,
                trades_needed=0,
                description="Target hit. Protect gains.",
                msg=f"TARGET HIT (${self.daily_pnl:.0f}). Defensive mode."
            )

        # BIG RUNNER ACTIVE — reduce new entries, protect the move
        if self._big_runner_active:
            return DailyPlan(
                mode="PROTECT_RUNNER", regime=regime, target=self.target,
                remaining=remaining, size_mult=0.60, lot_target=0.25,
                trades_needed=max(1, int(remaining / 200)),
                description="Runner riding. Protect and reduce.",
                msg=f"Runner active. Reduced new entries. Need ${remaining:.0f} more."
            )

        mix = TRADE_MIX.get(regime, TRADE_MIX["NORMAL"])

        if regime == "TREND":
            per_trade_avg = 400
            trades_needed = max(1, int(remaining / per_trade_avg))
            mode = "RIDE_TREND"
        elif regime == "CHOP":
            per_trade_avg = 80
            trades_needed = max(1, int(remaining / per_trade_avg))
            mode = "SCALP_HEAVY"
        else:
            per_trade_avg = 150
            trades_needed = max(1, int(remaining / per_trade_avg))
            mode = "BALANCED"

        # Adapt: if behind schedule, add scalps
        expected_progress = 1.0 - (hours_remaining / 6.5) if hours_remaining < 6.5 else 0
        actual_progress = self.daily_pnl / self.target if self.target > 0 else 0
        if actual_progress < expected_progress * 0.5 and hours_remaining < 4:
            mode = "CATCH_UP"
            size_mult = mix["size_mult"] * 1.15
            per_trade_avg = 60  # smaller, more frequent trades
            trades_needed = max(1, int(remaining / per_trade_avg))
        else:
            size_mult = mix["size_mult"]

        # Adapt: if ATR mostly consumed, shrink targets
        if atr_consumed_pct is not None and atr_consumed_pct > 0.80:
            size_mult *= 0.70
            trades_needed = max(trades_needed, int(remaining / 40))

        return DailyPlan(
            mode=mode, regime=regime, target=self.target,
            remaining=remaining, size_mult=size_mult,
            lot_target=mix["lot_target"],
            trades_needed=trades_needed,
            description=mix["description"],
            msg=f"{mode}: Need {trades_needed} trades. ${remaining:.0f} remaining."
        )

    def record_trade(self, pnl: float, setup_id: str = "", r_multiple: float = 0.0):
        """Record trade outcome and update progress."""
        self.daily_pnl += pnl
        self.trades_taken += 1
        if pnl is not None and pnl > 0:
            self.trades_won += 1

        trade_type = SETUP_TRADE_TYPE.get(setup_id, "swing")
        self.trade_type_pnl[trade_type] = self.trade_type_pnl.get(trade_type, 0) + pnl
        self.trade_type_count[trade_type] = self.trade_type_count.get(trade_type, 0) + 1

        remaining = self.target - self.daily_pnl
        logger.info("[TARGET] PnL: $%.0f | Remaining: $%.0f | Trades: %d (%.0f%% WR)",
                     self.daily_pnl, remaining, self.trades_taken,
                     (self.trades_won / self.trades_taken * 100) if self.trades_taken > 0 else 0)
        return remaining

    def flag_big_runner(self, active: bool = True):
        """Signal that a runner is riding a big move."""
        self._big_runner_active = active
        if active:
            logger.info("[TARGET] Runner active — protecting and reducing new entries")

    def get_size_adjustment(self, setup_id: str) -> float:
        """Get trade-type-aware size adjustment based on today's performance."""
        trade_type = SETUP_TRADE_TYPE.get(setup_id, "swing")
        type_pnl = self.trade_type_pnl.get(trade_type, 0)
        type_count = self.trade_type_count.get(trade_type, 0)

        if type_count >= 3 and type_pnl > 0:
            return 1.15  # this type is working today, boost
        elif type_count >= 3 and type_pnl < -100:
            return 0.70  # this type is losing today, reduce
        return 1.0

    def telegram_update(self) -> str:
        """Generate hourly Telegram update."""
        remaining = self.target - self.daily_pnl
        wr = (self.trades_won / self.trades_taken * 100) if self.trades_taken > 0 else 0
        plan = self.get_plan(self._active_regime)

        lines = [
            f"🎯 TARGET UPDATE",
            f"Daily PnL: ${self.daily_pnl:+.0f}",
            f"Target remaining: ${remaining:.0f}",
            f"Trades: {self.trades_taken} ({self.trades_won}W {self.trades_taken - self.trades_won}L — {wr:.0f}% WR)",
            f"Mode: {plan.mode} | Need {plan.trades_needed} more",
        ]

        # Show what's working
        for tt, pnl in sorted(self.trade_type_pnl.items(), key=lambda x: x[1], reverse=True):
            count = self.trade_type_count.get(tt, 0)
            lines.append(f"  {tt}: ${pnl:+.0f} ({count} trades)")

        return "\n".join(lines)

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.trades_taken = 0
        self.trades_won = 0
        self.trade_type_pnl.clear()
        self.trade_type_count.clear()
        self._big_runner_active = False
        self._active_regime = "NORMAL"


# ─────────────────────────────────────────────────────────────────
# SESSION ADAPTER — boost what's working RIGHT NOW
# ─────────────────────────────────────────────────────────────────

# Strategy type categorization
STRATEGY_TYPES: Dict[str, str] = {
    "ORD-02": "breakout", "IB-01": "breakout", "OD-01": "breakout",
    "GAP-02": "breakout", "PRE-RANGE-01": "breakout", "LONDON-NQ-01": "breakout",
    "LONDON-GOLD-01": "breakout",
    "VOL-05": "mean_reversion", "VOL-06": "mean_reversion",
    "ASIA-REVERT-01": "mean_reversion", "EXT-REVERT-01": "mean_reversion",
    "VOL-03": "momentum", "CL-MOM-01": "momentum", "CL-TREND-01": "momentum",
    "NEWS-MOM-01": "momentum", "VWAP-03": "momentum",
    "ICT-01": "vwap", "VWAP-01": "vwap", "VWAP-02": "vwap",
    "ICT-02": "structure", "ICT-03": "structure",
    "LVL-01": "level", "LVL-02": "level",
    "SES-01": "forex", "LONDON-FX-01": "forex",
    "GOLD-CORR-01": "correlation", "ASIA-GOLD-01": "correlation",
    "CL-GAP-01": "gap", "ES-LEAD-01": "exploit",
}


class SessionAdapter:
    """
    Track what's working RIGHT NOW within the session.
    If first 3 trades were momentum wins → boost momentum.
    If first 2 breakouts stopped out → reduce breakout sizing.

    Ghost showed: mean reversion -44R, momentum +18R on March 27.
    With this adapter, after 2 mean reversion losses FORGE would
    automatically shift to momentum setups with boosted sizing.
    """

    def __init__(self):
        self.strategy_results: Dict[str, List[float]] = {}

    def record(self, setup_id: str, pnl: float):
        strategy_type = STRATEGY_TYPES.get(setup_id, "other")
        if strategy_type not in self.strategy_results:
            self.strategy_results[strategy_type] = []
        self.strategy_results[strategy_type].append(pnl)

    def get_multiplier(self, setup_id: str) -> float:
        strategy_type = STRATEGY_TYPES.get(setup_id, "other")
        results = self.strategy_results.get(strategy_type, [])
        if len(results) < 2:
            return 1.0  # not enough data

        wins = sum(1 for r in results if r > 0)
        wr = wins / len(results)

        if wr >= 0.70:
            return 1.30  # hot streak
        elif wr >= 0.50:
            return 1.00  # normal
        elif wr >= 0.30:
            return 0.70  # cold
        else:
            return 0.40  # very cold

    def get_best_strategy_type(self) -> Optional[str]:
        """Return the strategy type with best results today."""
        if not self.strategy_results:
            return None
        best_type = None
        best_pnl = float('-inf')
        for stype, results in self.strategy_results.items():
            total = sum(results)
            if total > best_pnl and len(results) >= 2:
                best_pnl = total
                best_type = stype
        return best_type

    def reset(self):
        self.strategy_results.clear()


# ─────────────────────────────────────────────────────────────────
# DYNAMIC R:R FROM ATR REMAINING
# ─────────────────────────────────────────────────────────────────

def dynamic_target(direction: str, entry: float, atr: float,
                   atr_consumed_pct: float, regime: str) -> float:
    """
    Don't use fixed 30pt target on a day with 150pt ATR remaining.

    At 9:45 AM with 0% ATR consumed on a TREND day:
        target = 150 × 0.60 = 90pts
    At 2:00 PM with 70% consumed:
        target = 45 × 0.60 = 27pts
    """
    remaining_atr = atr * (1.0 - min(atr_consumed_pct, 0.95))

    if regime == "TREND":
        target_pts = remaining_atr * 0.60
    elif regime == "CHOP":
        target_pts = remaining_atr * 0.20
    else:
        target_pts = remaining_atr * 0.35

    target_pts = max(target_pts, 15)    # minimum 15pt
    target_pts = min(target_pts, atr)   # cap at full ATR

    if direction == "long":
        return round(entry + target_pts, 2)
    else:
        return round(entry - target_pts, 2)


def dynamic_stop(direction: str, entry: float, atr: float,
                 atr_consumed_pct: float, regime: str) -> float:
    """Dynamic stop loss based on regime and ATR."""
    if regime == "TREND":
        sl_pts = atr * 0.30  # tighter stops on trend days
    elif regime == "CHOP":
        sl_pts = atr * 0.15  # very tight for scalps
    else:
        sl_pts = atr * 0.25

    sl_pts = max(sl_pts, 10)   # minimum 10pt
    sl_pts = min(sl_pts, atr * 0.5)  # max half ATR

    if direction == "long":
        return round(entry - sl_pts, 2)
    else:
        return round(entry + sl_pts, 2)


# ─────────────────────────────────────────────────────────────────
# PARTIAL EXIT MANAGER
# ─────────────────────────────────────────────────────────────────

class PartialExitManager:
    """
    Stage 1: At +1R, close 50% and move stop to breakeven.
    Stage 2: Trail remaining 50% aggressively.

    A trade that reaches +2R:
    - Without partials: 2R on full size
    - With partials: 1R on half (LOCKED) + 2R on half = 1.5R total
      BUT the 1R half is guaranteed. And trailing half might ride to +5R.
    """

    def __init__(self):
        self._partial_taken: Dict[str, bool] = {}

    async def manage(self, adapter, position, point_value: float = 20.0):
        """Check and execute partial exits on a position."""
        pid = position.position_id
        entry = position.entry_price
        sl = position.stop_loss
        current = position.current_price or entry

        if not entry or not sl:
            return

        risk = abs(entry - sl)
        if risk is not None and risk <= 0:
            return

        is_long = position.direction.value == "long" if hasattr(position.direction, 'value') else position.direction == "long"

        if is_long:
            current_r = (current - entry) / risk
        else:
            current_r = (entry - current) / risk

        # Stage 1: At +1R, close 50% and move stop to breakeven
        if current_r >= 1.0 and not self._partial_taken.get(pid, False):
            partial_size = round(position.size * 0.5, 2)
            if partial_size >= 0.01:
                try:
                    await adapter.close_position(pid, partial_size)
                    await adapter.modify_position(pid, new_stop_loss=round(entry, 2))
                    self._partial_taken[pid] = True
                    locked = risk * partial_size * point_value
                    logger.info("[PARTIAL] %s +%.1fR: closed %.2f lots, locked $%.0f, trailing rest",
                                pid, current_r, partial_size, locked)
                    return locked
                except Exception as e:
                    logger.error("[PARTIAL] Failed: %s", e)

        # Stage 2: Trail remaining (handled by main trailing stop logic)
        return None

    def clear(self, position_id: str):
        self._partial_taken.pop(position_id, None)

    def reset(self):
        self._partial_taken.clear()


# ─────────────────────────────────────────────────────────────────
# FAST MODE CONTROLLER
# ─────────────────────────────────────────────────────────────────

def get_cycle_speed(price: float, key_levels: List[float], atr: float) -> int:
    """
    Normal cycle: 60 seconds.
    Near key level with confluence: 15 seconds for precision entry.
    """
    if atr is not None and atr <= 0:
        return 60

    for level in key_levels:
        dist_pct = abs(price - level) / atr
        if dist_pct is not None and dist_pct < 0.05:  # within 5% of ATR from key level
            return 15
        elif dist_pct is not None and dist_pct < 0.10:
            return 30

    return 60


def collect_key_levels(tracker, ctx) -> List[float]:
    """Gather all key levels for fast mode detection."""
    levels = []

    # ORB levels
    if getattr(tracker, 'orb_locked', False):
        orb_h = getattr(tracker, 'orb_high', 0)
        orb_l = getattr(tracker, 'orb_low', 0)
        if orb_h is not None and orb_h > 0:
            levels.append(orb_h)
        if orb_l is not None and orb_l > 0:
            levels.append(orb_l)

    # IB levels
    if getattr(tracker, 'ib_locked', False):
        ib_h = getattr(tracker, 'ib_high', 0)
        ib_l = getattr(tracker, 'ib_low', 0)
        if ib_h is not None and ib_h > 0:
            levels.append(ib_h)
        if ib_l > 0 and ib_l < 999999:
            levels.append(ib_l)

    # PDH/PDL
    pdh = getattr(ctx, 'pdh', 0)
    pdl = getattr(ctx, 'pdl', 0)
    if pdh is not None and pdh > 0:
        levels.append(pdh)
    if pdl is not None and pdl > 0:
        levels.append(pdl)

    # VWAP
    vwap = getattr(tracker, 'rth_vwap', 0) or getattr(tracker, 'vwap', 0)
    if vwap is not None and vwap > 0:
        levels.append(vwap)

    # Round numbers
    round_nums = getattr(tracker, 'round_numbers', [])
    levels.extend(round_nums)

    # Session H/L
    sh = getattr(tracker, 'session_high', 0)
    sl = getattr(tracker, 'session_low', 0)
    if sh is not None and sh > 0:
        levels.append(sh)
    if sl > 0 and sl < 999999:
        levels.append(sl)

    return levels


# ─────────────────────────────────────────────────────────────────
# CROSS-MARKET SPEED EXPLOIT — ES → NQ LEAD
# ─────────────────────────────────────────────────────────────────

class CrossMarketExploit:
    """
    ES and NQ move together but ES often leads by 15-30 seconds.
    When ES breaks a level, enter NQ before NQ breaks.
    """

    def __init__(self):
        self.es_prices: List[float] = []
        self.es_key_levels: List[float] = []
        self._last_signal_time = 0.0
        self._cooldown = 120  # 2 min between signals

    def update_es(self, price: float):
        self.es_prices.append(price)
        if len(self.es_prices) > 60:
            self.es_prices = self.es_prices[-60:]

    def set_es_levels(self, levels: List[float]):
        self.es_key_levels = levels

    def check(self, nq_tracker) -> Optional[dict]:
        """Check if ES just broke a level that NQ hasn't broken yet."""
        if len(self.es_prices) < 2:
            return None

        if time.time() - self._last_signal_time < self._cooldown:
            return None

        es_price = self.es_prices[-1]
        es_prev = self.es_prices[-2]
        nq_price = nq_tracker.price_history[-1] if nq_tracker.price_history else 0

        if nq_price is not None and nq_price <= 0:
            return None

        for level in self.es_key_levels:
            # ES broke above a level
            if es_prev < level and es_price > level:
                nq_orb_h = getattr(nq_tracker, 'orb_high', 0)
                if nq_orb_h > 0 and nq_price < nq_orb_h:
                    self._last_signal_time = time.time()
                    return {
                        "signal": True, "direction": "long",
                        "reason": f"ES broke {level:.0f}, NQ lagging — long before NQ breaks"
                    }

            # ES broke below
            if es_prev > level and es_price < level:
                nq_orb_l = getattr(nq_tracker, 'orb_low', 0)
                if nq_orb_l > 0 and nq_price > nq_orb_l:
                    self._last_signal_time = time.time()
                    return {
                        "signal": True, "direction": "short",
                        "reason": f"ES broke {level:.0f}, NQ lagging — short before NQ breaks"
                    }

        return None


# ─────────────────────────────────────────────────────────────────
# PERFORMANCE MONITOR — self-monitoring + auto-adjust
# ─────────────────────────────────────────────────────────────────

class PerformanceMonitor:
    """
    Track live performance and auto-adjust sizing.
    daily_pnl < -$200 → DEFENSIVE (0.50x)
    daily_pnl < -$100 → CAUTIOUS (0.70x)
    daily_pnl > +$500 → PROTECT (0.80x)
    3 consecutive losses → SCALP_ONLY for 30 min
    """

    def __init__(self):
        self.trade_history: List[dict] = []
        self._consecutive_losses = 0
        self._scalp_only_until = 0.0
        self.session_pnl: Dict[str, float] = {}
        self.session_pnl_history: Dict[str, List[float]] = {}

    def record_trade(self, setup_id: str, outcome: str, pnl: float,
                     conviction: str, session: str = "RTH"):
        self.trade_history.append({
            "setup_id": setup_id, "outcome": outcome,
            "pnl": pnl, "conviction": conviction,
            "session": session, "time": time.time(),
        })

        if outcome == "LOSS":
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # 3 consecutive losses → SCALP_ONLY for 30 minutes
        if self._consecutive_losses >= 3:
            self._scalp_only_until = time.time() + 1800
            logger.warning("[MONITOR] 3 consecutive losses → SCALP_ONLY for 30min")

        # Track session P&L
        self.session_pnl[session] = self.session_pnl.get(session, 0) + pnl

    def get_size_adjustment(self, daily_pnl: float) -> Tuple[float, str]:
        """Auto-adjust sizing based on P&L."""
        if daily_pnl < -200:
            return 0.50, "DEFENSIVE"
        elif daily_pnl < -100:
            return 0.70, "CAUTIOUS"
        elif daily_pnl is not None and daily_pnl > 500:
            return 0.80, "PROTECT_GAINS"
        return 1.00, "NORMAL"

    @property
    def is_scalp_only(self) -> bool:
        return time.time() < self._scalp_only_until

    def check_health(self, daily_pnl: float) -> List[str]:
        """Check for degradation alerts."""
        alerts = []

        # Win rate check on recent trades
        recent = self.trade_history[-10:]
        if len(recent) >= 5:
            wins = sum(1 for t in recent if t["outcome"] == "WIN")
            wr = wins / len(recent)
            if wr is not None and wr < 0.25:
                alerts.append(f"CRITICAL WR: {wr:.0%} on last {len(recent)} trades")

        # High-conviction trades losing
        high_conv = [t for t in self.trade_history if t["conviction"] in ("ELITE", "HIGH")]
        if len(high_conv) >= 3:
            hc_wins = sum(1 for t in high_conv if t["outcome"] == "WIN")
            hc_wr = hc_wins / len(high_conv)
            if hc_wr is not None and hc_wr < 0.30:
                alerts.append(f"High conviction losing: {hc_wr:.0%} WR")

        return alerts

    def get_session_adjustment(self, session: str) -> float:
        """Adjust sizing per session based on multi-day history."""
        history = self.session_pnl_history.get(session, [])
        if len(history) < 3:
            return 1.0
        # Last 3 days
        recent = history[-3:]
        all_negative = all(p < 0 for p in recent)
        all_positive = all(p > 0 for p in recent)
        if all_negative:
            return 0.70  # 3 consecutive losing sessions → reduce 30%
        elif all_positive:
            return 1.10  # 3 consecutive winning sessions → boost 10%
        return 1.0

    def end_of_day(self):
        """Archive session P&L for multi-day tracking."""
        for session, pnl in self.session_pnl.items():
            if session not in self.session_pnl_history:
                self.session_pnl_history[session] = []
            self.session_pnl_history[session].append(pnl)
        self.session_pnl.clear()
        self.trade_history.clear()
        self._consecutive_losses = 0
        self._scalp_only_until = 0.0
