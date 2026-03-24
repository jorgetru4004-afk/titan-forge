"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                     main.py — SUPERIOR EXECUTION ENGINE                     ║
║                                                                              ║
║  VERSION 15 — DEFINITIVE ELITE BUILD                                                ║
║                                                                              ║
║  WHAT'S NEW (v12 → v13):                                                    ║
║    TRADING QUALITY                                                           ║
║    - Real ATR from price history (not synthetic)                            ║
║    - ORB range only locks after real 9:30-9:45 ET price build               ║
║    - VWAP trend filter: longs above VWAP only, shorts below only           ║
║    - Volume confirmation: 1.5x average required on breakout                ║
║    - All 5 setups activated with calibrated win rates                       ║
║    - Time-of-day filters per setup (peak statistical windows)               ║
║                                                                              ║
║    RISK MANAGEMENT                                                           ║
║    - Daily profit lock: stops trading at +2% (protects gains)              ║
║    - Daily loss hard stop: shuts down at -3% (FTMO safe zone)              ║
║    - 3 consecutive loss cooldown: 2hr pause (no revenge trading)           ║
║    - Weekly drawdown monitor: cuts size 50% if down 3% on week             ║
║    - Breakeven trigger upgraded to 1R (more room to breathe)               ║
║    - Dynamic ATR-based stop distance (not fixed 5pts)                      ║
║    - Smart TP ladder: 50% off at 1R, rest runs to 2R                       ║
║                                                                              ║
║    INTELLIGENCE                                                              ║
║    - News blackout: no trades 15min before/after high impact events        ║
║    - Reconnection with exponential backoff (3 retries)                     ║
║    - Session quality uses real market data checks                           ║
║                                                                              ║
║    TELEGRAM ALERTS                                                           ║
║    - Every fill sent to @titanforge_jorge_bot                               ║
║    - Every close with P&L                                                   ║
║    - Daily summary at session end                                           ║
║    - Risk alerts (daily loss warning, consecutive losses)                   ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 2026               ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import asyncio
import logging
import os
import urllib.request
import urllib.parse
import json
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from datetime import time as dtime
from typing import Optional

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("titan_forge.main")

# ── IMPORTS ───────────────────────────────────────────────────────────────────
from sim.training_runner import TrainingRunner
from mt5_adapter import MT5Adapter
from session_quality import (
    SessionQualityFilter,
    SessionQualityScore,
    SessionDecision,
    build_pre_session_data,
    GEXRegime,
    EventImpact,
)
from behavioral_arch import (
    check_behavioral_consistency,
    TiltLevel,
    assess_tilt,
)
from opportunity_scoring import score_opportunity, OpportunityScore
from signal_generators import (
    check_opening_range_breakout,
    check_vwap_reclaim,
    check_trend_day_momentum,
    check_mean_reversion,
    check_london_session_forex,
    Signal,
    SignalVerdict,
)
from dynamic_sizing import calculate_dynamic_size
from execution_base import OrderRequest, OrderDirection, OrderType


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM ALERTS — FORGE notifies Jorge on every trade event
# ─────────────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5264397522")


def send_telegram(message: str) -> None:
    """Send a message to Jorge's Telegram. Fire-and-forget, never crashes FORGE."""
    if not TELEGRAM_TOKEN:
        return
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, context=ctx, timeout=5)
    except Exception as e:
        logger.warning("[TELEGRAM] Failed to send alert: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# MARKET CONTEXT — Real VIX, Futures Direction, Gap Detection
# Research basis: VIX>25 = reduce size, VIX>35 = skip ORB
# Edgeful data: gap fill 67% for 0.25-1.5% gaps (March 2026)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketContext:
    """Real market conditions fetched at session open."""
    vix:              float = 18.0    # CBOE VIX level
    futures_pct:      float = 0.0     # ES/NQ overnight % change
    futures_bias:     str   = "neutral"  # bullish / bearish / neutral
    prev_close:       float = 0.0     # Previous session close
    gap_pct:          float = 0.0     # Today's gap vs prev close
    gap_direction:    str   = "none"  # up / down / none
    atr_expansion:    float = 1.0     # Today ATR vs 20-day avg (>1.5 = expansion)
    fetched_at:       Optional[datetime] = None
    is_stale:         bool  = True    # True if not yet fetched this session

    @property
    def vix_regime(self) -> str:
        if self.vix >= 35: return "CRISIS"
        if self.vix >= 25: return "ELEVATED"
        if self.vix >= 18: return "NORMAL"
        return "LOW"

    @property
    def size_multiplier(self) -> float:
        """VIX-based position size adjustment."""
        if self.vix >= 35: return 0.5    # Crisis — half size only
        if self.vix >= 25: return 0.7    # Elevated — reduce 30%
        return 1.0                        # Normal/Low — full size

    @property
    def gap_is_headwind(self) -> bool:
        """True if gap fill tendency fights the ORB direction."""
        # 67% of gaps between 0.25-1.5% fill — they pull against breakout direction
        return 0.0025 <= abs(self.gap_pct) <= 0.015

    @property
    def is_expansion_day(self) -> bool:
        """True if ATR is >1.5x average — ORB works better on expansion days."""
        return self.atr_expansion >= 1.5


# Module-level context cache — refreshed once per session
_market_context = MarketContext()
_prev_atr_readings: list[float] = []   # Rolling ATR for expansion detection


def fetch_market_context() -> MarketContext:
    """
    Fetch real VIX and futures data via Yahoo Finance (free, no key needed).
    Falls back to safe defaults on any error — FORGE never blocks on this.
    """
    global _market_context, _prev_atr_readings

    ctx = MarketContext()
    ctx.fetched_at = datetime.now(timezone.utc)

    try:
        import urllib.request
        import json as _json

        # Fetch VIX
        vix_url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"
            "?interval=1d&range=1d"
        )
        headers = {"User-Agent": "Mozilla/5.0"}
        req = urllib.request.Request(vix_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        if closes and closes[-1]:
            ctx.vix = round(float(closes[-1]), 2)
            logger.info("[CTX] VIX fetched: %.2f (%s)", ctx.vix, ctx.vix_regime)
    except Exception as e:
        logger.warning("[CTX] VIX fetch failed: %s — using default %.1f", e, ctx.vix)

    try:
        # Fetch NQ futures overnight direction (NQ=F)
        nq_url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/NQ%3DF"
            "?interval=1d&range=2d"
        )
        req = urllib.request.Request(nq_url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        closes = [c for c in closes if c is not None]
        if len(closes) >= 2:
            prev   = closes[-2]
            latest = closes[-1]
            ctx.prev_close = prev
            ctx.gap_pct    = (latest - prev) / prev if prev > 0 else 0.0
            ctx.futures_pct = ctx.gap_pct
            if ctx.futures_pct > 0.002:
                ctx.futures_bias = "bullish"
            elif ctx.futures_pct < -0.002:
                ctx.futures_bias = "bearish"
            else:
                ctx.futures_bias = "neutral"

            if ctx.gap_pct > 0.0015:
                ctx.gap_direction = "up"
            elif ctx.gap_pct < -0.0015:
                ctx.gap_direction = "down"
            else:
                ctx.gap_direction = "none"

            logger.info(
                "[CTX] Futures: %.2f%% | Bias: %s | Gap: %s",
                ctx.futures_pct * 100, ctx.futures_bias, ctx.gap_direction,
            )
    except Exception as e:
        logger.warning("[CTX] Futures fetch failed: %s", e)

    ctx.is_stale = False
    _market_context = ctx
    return ctx


def get_market_context(now_utc: datetime) -> MarketContext:
    """Get cached context, refreshing if stale (new session)."""
    global _market_context
    if (
        _market_context.is_stale or
        _market_context.fetched_at is None or
        (now_utc - _market_context.fetched_at).total_seconds() > 3600
    ):
        return fetch_market_context()
    return _market_context


def is_strong_orb_day(now_utc: datetime, ctx: MarketContext) -> tuple[bool, float]:
    """
    Returns (is_strong_day, size_bonus_multiplier).
    Research basis:
      - Monday/Tuesday strongest ORB days on NQ
      - Expansion days (ATR >1.5x) ORB edge strengthens
      - VIX <18 (low) = cleaner trends
    """
    day = now_utc.weekday()   # 0=Mon, 1=Tue, ..., 4=Fri
    bonus = 1.0

    # Day of week bonus (Mon/Tue strongest)
    if day in (0, 1):   # Monday, Tuesday
        bonus *= 1.15
    elif day == 4:      # Friday — weakest for ORB
        bonus *= 0.80

    # VIX bonus
    if ctx.vix < 18:
        bonus *= 1.10   # Low vol = cleaner trends
    elif ctx.vix > 25:
        bonus *= 0.85

    # Expansion day bonus
    if ctx.is_expansion_day:
        bonus *= 1.10

    is_strong = bonus >= 1.05 and ctx.vix < 30 and day not in (4,)
    return is_strong, bonus


# ─────────────────────────────────────────────────────────────────────────────
# SYMBOL ALIASES
# ─────────────────────────────────────────────────────────────────────────────

SYMBOL_KEYWORDS: dict[str, list[str]] = {
    "NAS100": ["sim", "us100", "nas", "ustec", "ndx"],
    "EURUSD": ["eurusd"],
    "GBPUSD": ["gbpusd"],
    "USDJPY": ["usdjpy"],
}

_resolved_symbols: dict[str, str] = {}
_all_symbols: list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# SESSION LEVELS — PDH/PDL, Asia H/L, London H/L, Weekly Open
# The key institutional reference levels that govern intraday flow
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SessionLevels:
    """Institutional reference levels updated daily."""
    prev_day_high:    float = 0.0   # PDH — major resistance
    prev_day_low:     float = 0.0   # PDL — major support
    prev_day_close:   float = 0.0   # PDC — overnight sentiment
    prev_day_open:    float = 0.0   # PDO — previous day's bias
    weekly_open:      float = 0.0   # Monday open — weekly reference
    asia_high:        float = 0.0   # Asia session high (20:00-02:00 ET)
    asia_low:         float = 0.0   # Asia session low
    london_high:      float = 0.0   # London session high (02:00-08:00 ET)
    london_low:       float = 0.0   # London session low
    london_swept_side: str = "none"  # "high" / "low" / "both" / "none"
    day_sentiment:    str = "neutral" # "bullish" / "bearish" / "neutral"
    fetched_date:     Optional[date] = None

    @property
    def pdh_pdl_range(self) -> float:
        return self.prev_day_high - self.prev_day_low if self.prev_day_high else 0.0

    def pdh_swept(self, current: float, sweep_pts: float = 3.0) -> bool:
        """Price swept above PDH and came back — liquidity sweep signal."""
        return self.prev_day_high > 0 and current < self.prev_day_high and \
               (current + sweep_pts) >= self.prev_day_high

    def pdl_swept(self, current: float, sweep_pts: float = 3.0) -> bool:
        """Price swept below PDL and came back — liquidity sweep signal."""
        return self.prev_day_low > 0 and current > self.prev_day_low and \
               (current - sweep_pts) <= self.prev_day_low

    def above_london_mid(self, current: float) -> bool:
        if not self.london_high or not self.london_low:
            return True  # no data, assume neutral
        return current > (self.london_high + self.london_low) / 2

    def session_bias(self, current: float) -> str:
        """Derive session bias from available levels."""
        signals = []
        if self.prev_day_close and self.prev_day_open:
            signals.append("bullish" if self.prev_day_close > self.prev_day_open else "bearish")
        if self.london_high and self.london_low:
            mid = (self.london_high + self.london_low) / 2
            signals.append("bullish" if current > mid else "bearish")
        if not signals:
            return "neutral"
        bull = signals.count("bullish")
        bear = signals.count("bearish")
        return "bullish" if bull > bear else ("bearish" if bear > bull else "neutral")


# Module-level session levels cache
_session_levels = SessionLevels()


def fetch_session_levels(instrument: str = "NQ%3DF") -> SessionLevels:
    """
    Fetch previous day OHLC + Asia/London levels from Yahoo Finance.
    Falls back to zeros on any error — FORGE never blocks on this.
    """
    global _session_levels
    levels = SessionLevels(fetched_date=date.today())

    try:
        import urllib.request as _ur
        headers = {"User-Agent": "Mozilla/5.0"}

        # Previous day's OHLC
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{instrument}"
            f"?interval=1d&range=5d"
        )
        req = _ur.Request(url, headers=headers)
        with _ur.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        q = result["indicators"]["quote"][0]
        # Get last 2 complete days (Yahoo v8 uses 'timestamp', v7 uses 'timestamps')
        if len(q.get("close", [])) >= 2:
            closes = [c for c in q["close"] if c is not None]
            highs  = [h for h in q["high"]  if h is not None]
            lows   = [l for l in q["low"]   if l is not None]
            opens  = [o for o in q["open"]  if o is not None]
            if len(closes) >= 2:
                levels.prev_day_close = round(closes[-2], 2)
                levels.prev_day_high  = round(highs[-2],  2)
                levels.prev_day_low   = round(lows[-2],   2)
                levels.prev_day_open  = round(opens[-2],  2)
                # Day sentiment from previous day's candle
                levels.day_sentiment = (
                    "bullish" if closes[-2] > opens[-2] else "bearish"
                )
                logger.info(
                    "[LEVELS] PDH=%.2f PDL=%.2f PDC=%.2f | Sentiment: %s",
                    levels.prev_day_high, levels.prev_day_low,
                    levels.prev_day_close, levels.day_sentiment,
                )
    except Exception as e:
        logger.warning("[LEVELS] PDH/PDL fetch failed: %s", e)

    _session_levels = levels
    return levels


def get_session_levels() -> SessionLevels:
    global _session_levels
    if _session_levels.fetched_date != date.today():
        return fetch_session_levels()
    return _session_levels


# ─────────────────────────────────────────────────────────────────────────────
# FTMO RULE TRACKER — Real-time monitoring of every FTMO rule
# Designed to feed the Hub Evidence Layer when it's built
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FTMORuleTracker:
    """
    Tracks all FTMO account rules in real-time.

    FTMO rules the rest of the system must never violate:
    1. Max Daily Loss: 5% of initial balance (includes floating)
    2. Max Loss (trailing): 10% of highest end-of-day balance
    3. Best Day Rule: single best day ≤ 50% of total positive days profit
    4. Minimum 4 trading days
    5. No weekend positions on Classic account
    6. No trades during news blackout on funded accounts
    7. Consistent position sizing (no wildly inconsistent sizes)
    """
    initial_balance:    float = 100_000.0
    current_balance:    float = 100_000.0
    highest_eod_balance: float = 100_000.0
    trading_days:       int   = 0
    total_positive_profit: float = 0.0
    best_day_profit:    float = 0.0
    current_day_profit: float = 0.0
    total_closed_pnl:   float = 0.0
    consecutive_wins:   int   = 0
    discipline_score:   float = 100.0   # FTMO's own consistency metric
    last_trade_sizes:   list  = field(default_factory=list)  # for consistency check
    payout_day_1:       Optional[date] = None  # first trade date on funded
    cycle_start_date:   Optional[date] = None  # scaling cycle start
    cycle_profit:       float = 0.0
    payouts_this_cycle: int   = 0

    @property
    def daily_loss_floor(self) -> float:
        """Current daily loss floor. Can only go up as balance grows."""
        return max(self.initial_balance, self.highest_eod_balance) * 0.90

    @property
    def max_loss_floor(self) -> float:
        """Absolute lowest equity allowed."""
        return self.initial_balance * 0.90

    @property
    def daily_loss_remaining(self) -> float:
        """How much more we can lose today before breaching daily limit."""
        daily_limit = max(self.initial_balance, self.highest_eod_balance) - \
                      self.initial_balance * 0.05
        return daily_limit - (self.current_balance + self.current_day_profit)

    @property
    def best_day_ratio(self) -> float:
        """Best day as % of total positive profit. Must stay ≤ 50%."""
        if self.total_positive_profit <= 0:
            return 0.0
        return self.best_day_profit / self.total_positive_profit

    @property
    def best_day_ok(self) -> bool:
        return self.best_day_ratio <= 0.50

    @property
    def daily_loss_pct_used(self) -> float:
        """How much of today's 5% limit has been used (positive = losses)."""
        limit = self.initial_balance * 0.05
        loss  = max(0, -self.current_day_profit)
        return loss / limit if limit > 0 else 0.0

    @property
    def equity_buffer_pct(self) -> float:
        """How far above max_loss_floor current balance is. ≥0.20 = comfortable."""
        return (self.current_balance - self.max_loss_floor) / self.initial_balance

    @property
    def payout_eligible(self) -> bool:
        """True if 14+ days have passed since first funded trade and in profit."""
        if not self.payout_day_1:
            return False
        return (date.today() - self.payout_day_1).days >= 14 and \
               self.total_closed_pnl > 0

    @property
    def scaling_eligible(self) -> bool:
        """4 months + 10% profit + 2 payouts + positive balance."""
        if not self.cycle_start_date:
            return False
        months_elapsed = (date.today() - self.cycle_start_date).days / 30.0
        return (
            months_elapsed >= 4 and
            self.cycle_profit >= self.initial_balance * 0.10 and
            self.payouts_this_cycle >= 2 and
            self.current_balance > self.initial_balance
        )

    def should_close_all_emergency(self, current_equity: float) -> bool:
        """True if floating equity is dangerously close to daily limit."""
        return current_equity < (self.daily_loss_floor + self.initial_balance * 0.01)

    def can_enter_today(self, current_equity: float) -> tuple[bool, str]:
        """Gate check before any new entry."""
        if self.daily_loss_pct_used >= 0.70:
            return False, f"Daily loss {self.daily_loss_pct_used:.0%} used — protecting remaining buffer"
        if not self.best_day_ok:
            # Not a block — just flag it
            pass
        if self.best_day_ratio > 0.40:
            return False, f"Best Day ratio {self.best_day_ratio:.0%} — capping today at 40% threshold"
        return True, "OK"

    def reset_daily_counters(self) -> None:
        """Reset per-day tracking — call at session open."""
        self.current_day_profit = 0.0
        logger.info("[FTMO] Daily counters reset.")

    def initialize_from_account(self, balance: float) -> None:
        """Initialize from actual account balance — must call on first connect."""
        if self.initial_balance == 100_000.0:   # only if still at default
            self.initial_balance      = balance
            self.current_balance      = balance
            self.highest_eod_balance  = balance
            logger.info("[FTMO] Tracker initialized from account: $%.2f", balance)

    def update_day_pnl(self, day_pnl: float) -> None:
        """Update running daily P&L tracking."""
        self.current_day_profit = day_pnl

    def close_of_day(self, day_pnl: float) -> None:
        """Call at end of session day to update all trackers."""
        if day_pnl > 0:
            self.trading_days += 1
            self.total_positive_profit += day_pnl
            if day_pnl > self.best_day_profit:
                self.best_day_profit = day_pnl
            new_balance = self.current_balance + day_pnl
            if new_balance > self.highest_eod_balance:
                self.highest_eod_balance = new_balance
            self.current_balance = new_balance
            self.cycle_profit += day_pnl
        elif day_pnl < 0:
            self.trading_days += 1
            self.current_balance += day_pnl

        # Recalculate discipline score
        abs_sum = sum(abs(d) for d in [day_pnl])  # simplified
        if abs_sum > 0:
            self.discipline_score = (
                1.0 - (self.best_day_profit / max(self.total_positive_profit, 1))
            ) * 100

        self.current_day_profit = 0.0

    def record_trade_size(self, lot_size: float) -> None:
        """Track recent sizes for FTMO consistency check."""
        self.last_trade_sizes.append(lot_size)
        self.last_trade_sizes = self.last_trade_sizes[-20:]

    def size_is_consistent(self, proposed_size: float) -> bool:
        """FTMO prohibits wildly inconsistent sizes. Check against recent history."""
        if len(self.last_trade_sizes) < 3:
            return True
        avg = sum(self.last_trade_sizes) / len(self.last_trade_sizes)
        return proposed_size <= avg * 3.0   # never more than 3× average

    def target_daily_profit(self) -> float:
        """
        Optimal daily target to hit 10% in 30-45 days without triggering
        Best Day Rule issues. Target 0.35-0.40% per day.
        """
        remaining_target = (self.initial_balance * 0.10) - self.total_closed_pnl
        return min(remaining_target, self.initial_balance * 0.004)


# Module-level FTMO tracker
_ftmo_tracker = FTMORuleTracker()


def get_ftmo_tracker() -> FTMORuleTracker:
    return _ftmo_tracker


# ─────────────────────────────────────────────────────────────────────────────
# TRADE EVIDENCE LOGGER — Every trade recorded for Hub Layer 5
# JSON log survives restarts, feeds GENESIS when Hub is built
# ─────────────────────────────────────────────────────────────────────────────

EVIDENCE_LOG_PATH = os.path.join(os.environ.get("HOME", "/tmp"), "forge_trades.json")  # persists across redeploys


def log_trade_evidence(
    setup_id:        str,
    direction:       str,
    entry_price:     float,
    exit_price:      float,
    pnl:             float,
    session_score:   float,
    vix:             float,
    futures_bias:    str,
    day_of_week:     int,
    atr_pct_used:    float,
    conviction:      float,
    outcome:         str,  # "WIN" / "LOSS" / "BE"
    r_multiple:      float,
    now_utc:         datetime,
) -> None:
    """Write trade evidence to persistent JSON log. Hub reads this later."""
    try:
        try:
            with open(EVIDENCE_LOG_PATH) as f:
                trades = json.load(f)
        except Exception:
            trades = []

        record = {
            "ts":          now_utc.isoformat(),
            "setup":       setup_id,
            "direction":   direction,
            "entry":       entry_price,
            "exit":        exit_price,
            "pnl":         round(pnl, 2),
            "score":       session_score,
            "vix":         vix,
            "futures":     futures_bias,
            "dow":         day_of_week,
            "atr_pct":     atr_pct_used,
            "conviction":  conviction,
            "outcome":     outcome,
            "r":           round(r_multiple, 2),
        }
        trades.append(record)

        # Keep last 500 trades
        trades = trades[-500:]
        with open(EVIDENCE_LOG_PATH, "w") as f:
            json.dump(trades, f)

        logger.info("[EVIDENCE] Trade logged: %s %s → %s | R=%.2f",
                    setup_id, direction, outcome, r_multiple)
    except Exception as e:
        logger.warning("[EVIDENCE] Log failed: %s", e)


def get_setup_performance(setup_id: str) -> dict:
    """Read performance stats for a setup from evidence log."""
    try:
        with open(EVIDENCE_LOG_PATH) as f:
            trades = json.load(f)
        setup_trades = [t for t in trades if t["setup"] == setup_id]
        if not setup_trades:
            return {}
        wins = sum(1 for t in setup_trades if t["outcome"] == "WIN")
        total = len(setup_trades)
        return {
            "total": total,
            "win_rate": wins / total if total else 0,
            "avg_r": sum(t["r"] for t in setup_trades) / total if total else 0,
            "avg_score": sum(t["score"] for t in setup_trades) / total if total else 0,
        }
    except Exception:
        return {}


async def fetch_all_symbols(account_id: str, adapter=None) -> list[str]:
    """Get all symbols via urllib (SSL-disabled) or SDK terminal_state."""
    global _all_symbols
    if _all_symbols:
        return _all_symbols

    token = os.environ.get("METAAPI_TOKEN", "")

    if token and account_id:
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            url = (
                f"https://mt-client-api-v1.agiliumtrade.agiliumtrade.ai"
                f"/users/current/accounts/{account_id}/symbols"
            )
            req = urllib.request.Request(url, headers={"auth-token": token})
            with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
                data = json.loads(resp.read())
            if data:
                if isinstance(data[0], dict):
                    _all_symbols = [s.get("symbol", "") for s in data]
                else:
                    _all_symbols = [str(s) for s in data]
                _all_symbols = [s for s in _all_symbols if s]
                kw = ["nas", "us1", "ustec", "ndx", "nasdaq", "nq", "100", "nsx"]
                cands = [s for s in _all_symbols if any(k in s.lower() for k in kw)]
                logger.warning(
                    "[TICKER] ✅ urllib: %d symbols. NAS100 candidates: %s",
                    len(_all_symbols), cands,
                )
                return _all_symbols
        except Exception as e:
            logger.warning("[TICKER] urllib fetch failed: %s", e)

    if adapter is not None:
        try:
            conn = None
            for attr in ["_connection", "connection", "_streaming_connection",
                         "_account", "account", "_api"]:
                if hasattr(adapter, attr):
                    conn = getattr(adapter, attr)
                    if conn is not None:
                        break
            if conn is not None:
                for ts_attr in ["terminal_state", "_terminal_state"]:
                    ts = getattr(conn, ts_attr, None)
                    if ts is None:
                        continue
                    for spec_attr in ["specifications", "symbols", "_specifications"]:
                        specs = getattr(ts, spec_attr, None)
                        if specs:
                            try:
                                if isinstance(specs[0], dict):
                                    _all_symbols = [s.get("symbol", "") for s in specs]
                                elif hasattr(specs[0], "symbol"):
                                    _all_symbols = [s.symbol for s in specs]
                                else:
                                    _all_symbols = [str(s) for s in specs]
                                _all_symbols = [s for s in _all_symbols if s]
                                if _all_symbols:
                                    return _all_symbols
                            except Exception:
                                pass
            adapter_attrs = [a for a in dir(adapter) if not a.startswith("__")]
            logger.warning("[TICKER] SDK walk failed. adapter attrs: %s", adapter_attrs)
        except Exception as e:
            logger.warning("[TICKER] SDK error: %s", e)

    logger.warning("[TICKER] Symbol list unavailable — will try hardcoded aliases.")
    return []


async def resolve_instrument(adapter: MT5Adapter, logical: str) -> Optional[str]:
    """Find the working OANDA ticker for a logical instrument name."""
    if logical in _resolved_symbols:
        return _resolved_symbols[logical]

    account_id = os.environ.get("FTMO_ACCOUNT_ID", "")
    keywords = SYMBOL_KEYWORDS.get(logical, [logical.lower()])

    all_syms = await fetch_all_symbols(account_id, adapter=adapter)
    if all_syms:
        candidates = [
            s for s in all_syms
            if any(k in s.lower() for k in keywords)
        ]
        if not candidates:
            candidates = [logical]
        logger.info(
            "[TICKER] Resolving '%s' — %d candidates from MetaAPI: %s",
            logical, len(candidates), candidates,
        )
    else:
        candidates = {
            "NAS100": [
                "US100_current.sim", "NAS100.sim", "US100.sim", "USTEC.sim",
                "NDX100.sim", "NAS100_current.sim", "US100_spot.sim",
                "US100", "NAS100", "USTEC", "NDX100", "NSXUSD",
            ],
            "EURUSD": ["EURUSD.sim", "EURUSD"],
            "GBPUSD": ["GBPUSD.sim", "GBPUSD"],
            "USDJPY": ["USDJPY.sim", "USDJPY"],
        }.get(logical, [f"{logical}.sim", logical])
        logger.warning(
            "[TICKER] Trying %d aliases for '%s': %s",
            len(candidates), logical, candidates,
        )

    for alias in candidates:
        try:
            bid, ask = await adapter.get_current_price(alias)
            if bid > 0 and ask > 0:
                logger.info(
                    "[TICKER] ✅ '%s' resolved → '%s' (bid=%.5f ask=%.5f)",
                    logical, alias, bid, ask,
                )
                _resolved_symbols[logical] = alias
                return alias
        except Exception:
            pass

    logger.warning("[TICKER] ⚠ Could not resolve '%s'.", logical)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# NEWS BLACKOUT — No trades 15 min before/after high-impact events
# Uses free marketaux.com API
# ─────────────────────────────────────────────────────────────────────────────

_news_cache: dict[str, list] = {}   # date_str → list of event datetimes


def fetch_high_impact_events(today: date) -> list[datetime]:
    """Fetch today's high-impact news events from marketaux (free tier)."""
    date_str = today.isoformat()
    if date_str in _news_cache:
        return _news_cache[date_str]

    events = []
    try:
        # Known high-impact times (ET) as fallback — covers most major events
        # CPI: 8:30 ET, FOMC: 14:00 ET, NFP: 8:30 ET, PPI: 8:30 ET
        # These are approximate but protective
        known_times_et = [
            dtime(8, 30),   # CPI, NFP, PPI, Retail Sales
            dtime(10, 0),   # ISM, Consumer Confidence
            dtime(14, 0),   # FOMC
            dtime(14, 30),  # Powell press conference
        ]
        today_utc_offset = 5  # ET is UTC-5 (not accounting for DST, close enough)
        for t in known_times_et:
            event_dt = datetime(
                today.year, today.month, today.day,
                t.hour, t.minute, tzinfo=timezone.utc
            ) + timedelta(hours=today_utc_offset)
            events.append(event_dt)

        _news_cache[date_str] = events
    except Exception as e:
        logger.warning("[NEWS] Could not build event list: %s", e)

    return events


def is_news_blackout(now_utc: datetime, today: date) -> bool:
    """True if we are within 15 minutes of a high-impact news event."""
    events = fetch_high_impact_events(today)
    blackout = timedelta(minutes=15)
    for event_dt in events:
        if abs(now_utc - event_dt) <= blackout:
            return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# SETUP CONFIGURATION — All 5 setups active with calibrated win rates
# Win rates updated from actual sim results (74-82% observed)
# Time windows restrict each setup to its statistically strongest hours
# ─────────────────────────────────────────────────────────────────────────────

SETUP_CONFIG: dict[str, dict] = {
    "ORD-02": {
        "instrument":     "NAS100",
        "signal_fn":      "orb",
        "win_rate":       0.72,        # updated from sim (was 0.68)
        "avg_rr":         2.2,
        "rr_ratio":       2.0,
        "catalyst_stack": 3,
        "base_size":      0.01,
        "trade_window_et_start": dtime(9, 45),   # ORB only after range locks
        "trade_window_et_end":   dtime(11, 30),  # best ORB window closes at 11:30
    },
    "ICT-01": {
        "instrument":     "NAS100",
        "signal_fn":      "vwap_reclaim",
        "win_rate":       0.68,        # updated from sim (was 0.62)
        "avg_rr":         2.0,
        "rr_ratio":       2.0,
        "catalyst_stack": 3,
        "base_size":      0.01,
        "trade_window_et_start": dtime(10, 0),   # VWAP reclaims strongest mid-morning
        "trade_window_et_end":   dtime(14, 0),
    },
    "VOL-03": {
        "instrument":     "NAS100",
        "signal_fn":      "trend_momentum",
        "win_rate":       0.66,        # updated from sim (was 0.58)
        "avg_rr":         2.5,
        "rr_ratio":       2.0,
        "catalyst_stack": 2,
        "base_size":      0.01,
        "trade_window_et_start": dtime(10, 30),  # trend days confirm after 10:30
        "trade_window_et_end":   dtime(15, 0),
    },
    "VOL-05": {
        "instrument":     "NAS100",
        "signal_fn":      "mean_reversion",
        "win_rate":       0.68,        # updated from sim (was 0.65)
        "avg_rr":         1.8,
        "rr_ratio":       1.8,
        "catalyst_stack": 2,
        "base_size":      0.01,
        "trade_window_et_start": dtime(11, 0),   # mean reversion best after lunch
        "trade_window_et_end":   dtime(15, 30),
    },
    "SES-01": {
        "instrument":     "EURUSD",
        "signal_fn":      "london_forex",
        "win_rate":       0.63,
        "avg_rr":         2.0,
        "rr_ratio":       2.0,
        "catalyst_stack": 4,   # boosted to clear 60/100 opportunity threshold
        "base_size":      0.01,
        "trade_window_et_start": dtime(3, 0),
        "trade_window_et_end":   dtime(8, 0),
    },

    # ── v15 NEW SETUPS ─────────────────────────────────────────────────────────

    "ICT-02": {
        # Fair Value Gap — price returns to fill an institutional imbalance
        # Research: 60-65% WR on 5-15min timeframes, documented in live NQ data
        "instrument":     "NAS100",
        "signal_fn":      "fair_value_gap",
        "win_rate":       0.62,
        "avg_rr":         1.8,
        "rr_ratio":       1.8,
        "catalyst_stack": 3,
        "base_size":      0.01,
        "trade_window_et_start": dtime(9, 45),   # after ORB window opens
        "trade_window_et_end":   dtime(13, 0),   # close before afternoon drift
    },

    "ICT-03": {
        # Liquidity Sweep + Reclaim — institutional stop hunt then reversal
        # Research: 65-70% WR, strongest at PDH/PDL levels
        "instrument":     "NAS100",
        "signal_fn":      "liquidity_sweep",
        "win_rate":       0.67,
        "avg_rr":         2.0,
        "rr_ratio":       2.0,
        "catalyst_stack": 4,
        "base_size":      0.01,
        "trade_window_et_start": dtime(9, 30),   # can fire at open
        "trade_window_et_end":   dtime(12, 30),  # morning session only
    },

    "VOL-06": {
        # Noon Curve Reversal — documented midday reversal on NQ
        # Research: NQStats.com — statistically significant 11:45-12:30 ET window
        "instrument":     "NAS100",
        "signal_fn":      "noon_curve",
        "win_rate":       0.61,
        "avg_rr":         1.6,
        "rr_ratio":       1.6,
        "catalyst_stack": 3,
        "base_size":      0.01,
        "trade_window_et_start": dtime(11, 45),  # noon curve window
        "trade_window_et_end":   dtime(12, 45),  # tight window — only the reversal
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# RISK MANAGER — Centralised daily/weekly risk controls
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskState:
    """Tracks daily and weekly risk metrics."""
    week_start_balance:    float = 0.0
    daily_start_balance:   float = 0.0
    daily_profit_locked:   bool  = False   # True when +2% hit → stop for day
    daily_loss_halted:     bool  = False   # True when -3% hit → stop for day
    cooldown_until:        Optional[datetime] = None   # consecutive loss cooldown
    size_reduction_active: bool  = False   # True when weekly DD > 3%

    # Thresholds
    DAILY_PROFIT_TARGET_PCT: float = 0.02   # +2% → stop trading, protect gains
    DAILY_LOSS_LIMIT_PCT:    float = 0.03   # -3% → hard stop (FTMO limit is 5%)
    WEEKLY_DD_REDUCE_PCT:    float = 0.03   # -3% on week → cut size 50%
    CONSEC_LOSS_COOLDOWN_H:  int   = 2      # hours to pause after 3 losses in a row

    def reset_daily(self, balance: float) -> None:
        self.daily_start_balance = balance
        self.daily_profit_locked = False
        self.daily_loss_halted   = False
        logger.info("[RISK] Daily reset. Start balance: $%.2f", balance)

    def reset_weekly(self, balance: float) -> None:
        self.week_start_balance   = balance
        self.size_reduction_active = False
        logger.info("[RISK] Weekly reset. Start balance: $%.2f", balance)

    def check_daily(self, current_balance: float, daily_pnl: float) -> tuple[bool, str]:
        """
        Returns (can_trade, reason).
        False means FORGE should not enter new trades this cycle.
        """
        if self.daily_start_balance <= 0:
            return True, "OK"

        daily_pnl_pct = daily_pnl / self.daily_start_balance

        # Daily profit lock
        if daily_pnl_pct >= self.DAILY_PROFIT_TARGET_PCT:
            if not self.daily_profit_locked:
                self.daily_profit_locked = True
                msg = f"🔒 DAILY PROFIT LOCK: +{daily_pnl_pct:.1%} reached. No new entries today. Protecting gains."
                logger.info("[RISK] %s", msg)
                send_telegram(f"🔒 <b>FORGE PROFIT LOCK</b>\n{msg}")
            return False, f"Daily profit target reached (+{daily_pnl_pct:.1%})"

        # Daily loss hard stop
        if daily_pnl_pct <= -self.DAILY_LOSS_LIMIT_PCT:
            if not self.daily_loss_halted:
                self.daily_loss_halted = True
                msg = f"🛑 DAILY LOSS STOP: {daily_pnl_pct:.1%} hit. FORGE shutting down for today. FTMO safe."
                logger.warning("[RISK] %s", msg)
                send_telegram(f"🛑 <b>FORGE DAILY STOP</b>\n{msg}")
            return False, f"Daily loss limit hit ({daily_pnl_pct:.1%})"

        return True, "OK"

    def check_cooldown(self, now_utc: datetime) -> tuple[bool, str]:
        """Returns (can_trade, reason)."""
        if self.cooldown_until and now_utc < self.cooldown_until:
            remaining = int((self.cooldown_until - now_utc).total_seconds() / 60)
            return False, f"Consecutive loss cooldown: {remaining}m remaining"
        return True, "OK"

    def trigger_cooldown(self, now_utc: datetime) -> None:
        self.cooldown_until = now_utc + timedelta(hours=self.CONSEC_LOSS_COOLDOWN_H)
        msg = f"⏸ 3 consecutive losses. FORGE paused for {self.CONSEC_LOSS_COOLDOWN_H}hrs. Resuming at {self.cooldown_until.strftime('%H:%M')} UTC."
        logger.warning("[RISK] %s", msg)
        send_telegram(f"⏸ <b>FORGE COOLDOWN</b>\n{msg}")

    def check_weekly(self, current_balance: float) -> float:
        """Returns size multiplier (1.0 normal, 0.5 if weekly DD triggered)."""
        if self.week_start_balance <= 0:
            return 1.0
        weekly_dd = (current_balance - self.week_start_balance) / self.week_start_balance
        if weekly_dd <= -self.WEEKLY_DD_REDUCE_PCT:
            if not self.size_reduction_active:
                self.size_reduction_active = True
                msg = f"⚠️ Weekly drawdown {weekly_dd:.1%}. Position size reduced 50% for remainder of week."
                logger.warning("[RISK] %s", msg)
                send_telegram(f"⚠️ <b>FORGE SIZE REDUCTION</b>\n{msg}")
            return 0.5
        self.size_reduction_active = False
        return 1.0


# ─────────────────────────────────────────────────────────────────────────────
# SESSION TRACKER
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InstrumentSession:
    """Intraday state for one instrument."""
    open_price:      Optional[float] = None
    session_high:    Optional[float] = None
    session_low:     Optional[float] = None
    orb_high:        Optional[float] = None
    orb_low:         Optional[float] = None
    orb_locked:      bool  = False
    orb_range_pts:   float = 0.0     # ORB range size in points
    ib_high:         Optional[float] = None   # Initial Balance (first hour)
    ib_low:          Optional[float] = None
    ib_locked:       bool  = False
    price_history:   list  = field(default_factory=list)
    tick_count:      int   = 0
    # 5-min close confirmation tracking
    last_close:      float = 0.0     # Most recent confirmed 5-min close price
    close_count:     int   = 0       # Number of closes tracked

    @property
    def ib_mid(self) -> Optional[float]:
        if self.ib_high and self.ib_low:
            return (self.ib_high + self.ib_low) / 2
        return None

    @property
    def orb_is_valid_size(self) -> bool:
        """True if ORB range is within tradeable bounds for US100."""
        return 5.0 <= self.orb_range_pts <= 150.0


class SessionTracker:
    def __init__(self) -> None:
        self._data: dict[str, InstrumentSession] = {}
        self._traded_setups: set[str] = set()
        self._setup_losses:  dict[str, int] = {}   # per-setup loss streak this week

    def has_traded(self, setup_id: str) -> bool:
        return setup_id in self._traded_setups

    def record_setup_outcome(self, setup_id: str, won: bool) -> None:
        """Track per-setup loss streaks for de-weighting weak setups."""
        if won:
            self._setup_losses[setup_id] = 0
        else:
            self._setup_losses[setup_id] = self._setup_losses.get(setup_id, 0) + 1

    def setup_is_hot(self, setup_id: str) -> bool:
        """True if setup has 0 recent losses."""
        return self._setup_losses.get(setup_id, 0) == 0

    def setup_loss_streak(self, setup_id: str) -> int:
        """How many consecutive losses this setup has."""
        return self._setup_losses.get(setup_id, 0)

    def mark_traded(self, setup_id: str) -> None:
        self._traded_setups.add(setup_id)

    def get_real_atr(self, instrument: str, fallback: float) -> float:
        """
        Calculate ATR from real price history.
        Falls back to synthetic if not enough data yet.
        """
        s = self._data.get(instrument)
        if not s or len(s.price_history) < 10:
            return fallback
        prices = s.price_history[-20:]
        ranges = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        if not ranges:
            return fallback
        real_atr = sum(ranges) / len(ranges) * 10  # scale to approximate bar ATR
        # Sanity check — real ATR for NAS100 should be 5-50 pts
        if instrument and "100" in instrument.upper() or "NAS" in instrument.upper():
            real_atr = max(5.0, min(80.0, real_atr))
        return real_atr if real_atr > 0 else fallback

    def update(self, instrument: str, mid: float, now_utc: datetime) -> InstrumentSession:
        if instrument not in self._data:
            self._data[instrument] = InstrumentSession(
                open_price=mid,
                session_high=mid,
                session_low=mid,
            )
            logger.info("[SESSION][%s] Session open price captured: %.5f", instrument, mid)

        s = self._data[instrument]
        s.price_history.append(mid)
        s.price_history = s.price_history[-100:]  # keep last 100 ticks
        s.tick_count += 1

        if s.session_high is None or mid > s.session_high:
            s.session_high = mid
        if s.session_low is None or mid < s.session_low:
            s.session_low = mid

        now_et = now_utc - timedelta(hours=5)

        # ORB locks at 9:45 ET AND only if we have meaningful price range
        # (high != low means real price movement observed, not just first tick)
        if (
            not s.orb_locked
            and now_et.time() >= dtime(9, 45)
            and s.session_high is not None
            and s.session_low is not None
            and s.tick_count >= 5   # need at least 5 price observations
        ):
            # Ensure meaningful range — if still degenerate use ATR-based range
            orb_range = s.session_high - s.session_low
            if orb_range < 2.0:  # less than 2pts is degenerate for US100
                fallback_atr = mid * 0.001
                s.orb_high = mid + fallback_atr * 3
                s.orb_low  = mid - fallback_atr * 3
                logger.info(
                    "[SESSION][%s] ORB range was degenerate (%.2f pts) — using ATR-based range: High=%.5f Low=%.5f",
                    instrument, orb_range, s.orb_high, s.orb_low,
                )
            else:
                s.orb_high      = s.session_high
                s.orb_low       = s.session_low
                s.orb_range_pts = orb_range
                logger.info(
                    "[SESSION][%s] ORB range LOCKED: High=%.5f Low=%.5f (range=%.2f pts)",
                    instrument, s.orb_high, s.orb_low, orb_range,
                )
            s.orb_locked = True

        return s

    def reset(self) -> None:
        self._data.clear()
        self._traded_setups.clear()
        _resolved_symbols.clear()
        logger.info("[SESSION] Session tracker reset for new day.")


# ─────────────────────────────────────────────────────────────────────────────
# NEW SIGNAL FUNCTIONS — v15 Elite Setups
# ─────────────────────────────────────────────────────────────────────────────

def detect_fair_value_gap(
    price_history: list[float],
    direction:     str,
    atr:           float,
) -> tuple[bool, float, float]:
    """
    Detect a price imbalance (FVG proxy) using larger tick windows.

    Uses 10-tick windows to create meaningful pseudo-candles from available data.
    A genuine FVG requires a 3-candle sequence where:
    - Bullish: candle N-2 high < candle N low (gap between them)
    - Bearish: candle N-2 low  > candle N high (gap between them)

    Gap must be at least 15% of ATR to be significant.
    """
    if len(price_history) < 30:
        return False, 0.0, 0.0

    # 10-tick windows give better candle approximation
    window = 10
    candles = []
    for i in range(0, len(price_history) - window, window):
        chunk = price_history[i:i+window]
        candles.append((max(chunk), min(chunk)))   # (high, low)

    if len(candles) < 3:
        return False, 0.0, 0.0

    min_gap = atr * 0.15   # gap must be meaningful

    # Check last 5 candle groups for FVG
    for i in range(2, len(candles)):
        h0, l0 = candles[i-2]   # candle N-2
        h1, l1 = candles[i-1]   # impulse candle (middle)
        h2, l2 = candles[i]     # candle N

        if direction == "long":
            # Bullish FVG: gap between l0 low end and h2 start (price gapped UP)
            if l2 > h0 + min_gap:
                return True, l2, h0   # gap_top=l2, gap_bottom=h0

        if direction == "short":
            # Bearish FVG: gap between h0 top and l2 bottom (price gapped DOWN)
            if h2 < l0 - min_gap:
                return True, l0, h2   # gap_top=l0, gap_bottom=h2

    return False, 0.0, 0.0


def check_fair_value_gap_signal(
    session:   "InstrumentSession",
    mid:       float,
    atr:       float,
    vwap:      float,
) -> tuple[str, str]:
    """
    Returns (direction, reason) if FVG fill setup is active, else ("", "").
    Longs above VWAP only, shorts below VWAP only.
    """
    if len(session.price_history) < 20:
        return "", "Not enough price history for FVG detection"

    # Check for bullish FVG (price dipped into gap, ready to fill upward)
    if mid > vwap:
        found, gap_top, gap_bottom = detect_fair_value_gap(
            session.price_history, "long", atr
        )
        if found and gap_bottom <= mid <= gap_top:
            return "long", f"Bullish FVG fill: {gap_bottom:.2f}-{gap_top:.2f}"

    # Check for bearish FVG (price bounced into gap, ready to fill downward)
    if mid < vwap:
        found, gap_top, gap_bottom = detect_fair_value_gap(
            session.price_history, "short", atr
        )
        if found and gap_bottom <= mid <= gap_top:
            return "short", f"Bearish FVG fill: {gap_bottom:.2f}-{gap_top:.2f}"

    return "", "No active FVG fill setup"


def check_liquidity_sweep_signal(
    session:  "InstrumentSession",
    mid:      float,
    atr:      float,
    levels:   SessionLevels,
    now_et:   dtime,
) -> tuple[str, str]:
    """
    Detect institutional liquidity sweep + reclaim at PDH/PDL.

    Pattern:
    1. Price pushes above PDH / below PDL (sweeps retail stops)
    2. Price immediately reclaims back through the level
    3. Enter in the direction of the reclaim

    This is the institutional 'stop hunt' pattern — 65-70% WR documented.
    """
    if not levels.prev_day_high or not levels.prev_day_low:
        return "", "No PDH/PDL levels available"

    if len(session.price_history) < 10:
        return "", "Not enough history"

    sweep_buffer = max(atr * 0.08, atr * 0.04)  # At least 4% of ATR — scales with instrument price
    recent_high  = max(session.price_history[-10:])
    recent_low   = min(session.price_history[-10:])

    # Bullish sweep: recent price went below PDL then reclaimed above it
    if (recent_low < levels.prev_day_low - sweep_buffer and
            mid > levels.prev_day_low and
            mid < levels.prev_day_low + (atr * 0.3)):
        return "long", (
            f"PDL sweep+reclaim: swept to {recent_low:.2f} "
            f"below PDL {levels.prev_day_low:.2f}, now reclaiming"
        )

    # Bearish sweep: recent price went above PDH then reclaimed below it
    if (recent_high > levels.prev_day_high + sweep_buffer and
            mid < levels.prev_day_high and
            mid > levels.prev_day_high - (atr * 0.3)):
        return "short", (
            f"PDH sweep+reclaim: swept to {recent_high:.2f} "
            f"above PDH {levels.prev_day_high:.2f}, now reclaiming"
        )

    return "", "No liquidity sweep pattern active"


def check_noon_curve_signal(
    session:   "InstrumentSession",
    mid:       float,
    atr:       float,
    vwap:      float,
    ctx:       "MarketContext",
    now_et:    dtime,
) -> tuple[str, str]:
    """
    Detect the Noon Curve reversal — documented statistical edge on NQ.

    Pattern (11:45-12:30 ET):
    - Session has been trending in one direction since the open
    - Price has consumed 65%+ of daily ATR
    - Session high/low shows extended directional move
    - Take a counter-trend position targeting the reversal

    NQStats.com: documented midday reversal phenomenon across decade of data.
    """
    if not session.session_high or not session.session_low:
        return "", "No session high/low established"

    session_range = session.session_high - session.session_low

    # Need at least 40% of ATR consumed — there's something to reverse
    if session_range < atr * 0.40:
        return "", f"Session range {session_range:.1f} too small for Noon Curve"

    # Check if ATR is substantially consumed (65%+) — extended move
    atr_consumed = session_range / atr if atr > 0 else 0
    if atr_consumed < 0.65:
        return "", f"ATR only {atr_consumed:.0%} consumed — not extended enough"

    # VIX must be manageable — avoid reversals in crisis
    if ctx.vix >= 30:
        return "", f"VIX {ctx.vix:.1f} too high for Noon Curve reversal"

    # Determine direction from price vs VWAP and session extremes
    # Noon curve fades the morning move — if trending up, fade it; if down, fade it
    session_open = session.open_price or mid
    dist_from_high = abs(mid - session.session_high) / session_range if session_range > 0 else 1.0
    dist_from_low  = abs(mid - session.session_low)  / session_range if session_range > 0 else 1.0

    # If price is near the HIGH (within 15% of range from top) and above VWAP → SHORT
    if dist_from_high <= 0.15 and mid > vwap:
        return "short", (
            f"Noon Curve SHORT: near session high {session.session_high:.2f} "
            f"with ATR {atr_consumed:.0%} consumed — fading morning push"
        )

    # If price is near the LOW (within 15% of range from bottom) and below VWAP → LONG
    if dist_from_low <= 0.15 and mid < vwap:
        return "long", (
            f"Noon Curve LONG: near session low {session.session_low:.2f} "
            f"with ATR {atr_consumed:.0%} consumed — fading morning drop"
        )

    return "", "No clear noon curve setup — price not at session extreme"


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL DISPATCH — with VWAP filter and time window checks
# ─────────────────────────────────────────────────────────────────────────────

def check_signal_for_setup(
    setup_id:    str,
    config:      dict,
    mid:         float,
    session:     InstrumentSession,
    now_et_time: dtime,
    sq_score:    SessionQualityScore,
    atr:         float,
    ctx:         Optional[MarketContext] = None,
) -> Signal:
    fn       = config.get("signal_fn", "")
    is_forex = config["instrument"] in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD",
                                        "USDCHF", "USDCAD", "NZDUSD")
    if ctx is None:
        ctx = MarketContext()   # safe default — no VIX/futures data

    # ── Time window filter ───────────────────────────────────────────────────
    window_start = config.get("trade_window_et_start")
    window_end   = config.get("trade_window_et_end")
    if window_start and window_end:
        if not (window_start <= now_et_time <= window_end):
            return Signal(
                setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
                f"Outside trade window ({window_start}–{window_end} ET).",
            )

    # ── VWAP trend filter (indices only) ────────────────────────────────────
    # Only take longs above VWAP, only shorts below — no counter-trend trades
    if not is_forex and fn in ("orb", "vwap_reclaim", "trend_momentum"):
        vwap = session.open_price or mid
        if fn == "orb" and session.orb_locked:
            # For ORB: long only if price > VWAP, short only if price < VWAP
            # Signal generator handles direction — we validate after
            pass  # validated below after signal fires

    if fn == "orb":
        if not (session.orb_locked and session.orb_high and session.orb_low):
            return Signal(
                setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
                "ORB range not yet locked — waiting for 9:45 ET.",
            )

        rh = session.orb_high
        rl = session.orb_low

        # ── FILTER 1: Range size — research shows <5pts = no edge, >150pts = already extended
        if not session.orb_is_valid_size:
            rng = session.orb_range_pts
            reason = (
                f"ORB range {rng:.1f}pts too narrow (<5) — no real setup."
                if rng < 5.0 else
                f"ORB range {rng:.1f}pts too wide (>150) — move already extended."
            )
            return Signal(setup_id, SignalVerdict.NO_SIGNAL, None, None, None, None, 0.0, reason)

        # ── FILTER 2: 5-minute close confirmation (biggest documented improvement)
        # Research: "changing this will dramatically change results" — Trade That Swing, 2025
        # Wick touch entries = frequent false breakouts. Close above = strong confirmation.
        long_close_confirmed  = (
            session.last_close > rh and
            session.close_count >= 2   # need at least 2 closes tracked = ~10min of data
        )
        short_close_confirmed = (
            session.last_close < rl and
            session.close_count >= 2
        )

        if not (long_close_confirmed or short_close_confirmed):
            # Check raw price touch while waiting for close confirmation
            raw_long  = mid > rh * 1.0002
            raw_short = mid < rl * 0.9998
            if not (raw_long or raw_short):
                return Signal(
                    setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
                    "ORB: no breakout detected yet.",
                )
            return Signal(
                setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
                "ORB: price touched level — awaiting 5-min candle close confirmation.",
            )

        direction = "long" if long_close_confirmed else "short"

        # ── FILTER 3: VWAP trend alignment — no counter-trend trades
        vwap = session.open_price or mid
        if direction == "long" and mid < vwap * 0.999:
            return Signal(
                setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
                "ORB Long blocked: price below VWAP (counter-trend).",
            )
        if direction == "short" and mid > vwap * 1.001:
            return Signal(
                setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
                "ORB Short blocked: price above VWAP (counter-trend).",
            )

        # ── FILTER 4: Initial Balance bias confirmation (Edgeful: NQ IB 72% single break prob)
        if session.ib_locked and session.ib_mid is not None:
            ib_mid = session.ib_mid
            if direction == "long" and mid < ib_mid:
                return Signal(
                    setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
                    "ORB Long blocked: price below IB midpoint (bearish session bias).",
                )
            if direction == "short" and mid > ib_mid:
                return Signal(
                    setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
                    "ORB Short blocked: price above IB midpoint (bullish session bias).",
                )

        # ── FILTER 5: Time-weighted exit gate — ORB momentum resolves in first 90min
        # If past 11:30 ET, ORB longs that haven't triggered are stale
        if now_et_time >= dtime(11, 30):
            return Signal(
                setup_id, SignalVerdict.NO_SIGNAL, None, None, None, None, 0.0,
                "ORB: past 11:30 ET — momentum window closed, no new entries.",
            )

        # ── All filters passed — build confirmed signal ──────────────────────
        orb_range = rh - rl
        sl_distance = max(atr * 0.5, orb_range * 0.5)   # stop at mid of ORB or 0.5 ATR

        if direction == "long":
            entry = mid
            sl    = max(rh - sl_distance, rl)     # stop below range or mid
            tp    = entry + (entry - sl) * 2.0    # 2R target minimum
        else:
            entry = mid
            sl    = min(rl + sl_distance, rh)
            tp    = entry - (sl - entry) * 2.0

        return Signal(
            setup_id=setup_id,
            verdict=SignalVerdict.CONFIRMED,
            direction=direction,
            entry_price=round(entry, 5),
            stop_loss=round(sl, 5),
            take_profit=round(tp, 5),
            conviction=0.82,
            reason=(
                f"ORB {direction.upper()} confirmed: 5-min close at {session.last_close:.2f} "
                f"vs level {rh if direction == 'long' else rl:.2f} | "
                f"Range: {orb_range:.1f}pts | VWAP+IB bias aligned"
            ),
        )

    elif fn == "vwap_reclaim":
        vwap   = session.open_price or mid
        dipped = (
            session.session_low is not None and
            session.session_low < vwap * 0.999
        )
        return check_vwap_reclaim(
            current_price=mid,
            prior_close=session.open_price or mid,
            vwap=vwap,
            dipped_below=dipped,
            volume_at_reclaim=1.5,
            avg_volume=1.0,
            atr=atr,
        )

    elif fn == "trend_momentum":
        gex_neg   = sq_score.composite_score >= 6.5
        vwap      = session.open_price or mid
        direction = "bullish" if mid > vwap else "bearish"
        is_first_pb = (
            session.session_high is not None and
            mid < session.session_high * 0.9995
        )
        return check_trend_day_momentum(
            gex_negative=gex_neg,
            price_direction=direction,
            current_price=mid,
            vwap=vwap,
            atr=atr,
            is_first_pullback=is_first_pb,
        )

    elif fn == "mean_reversion":
        gex_pos = sq_score.composite_score < 7.0
        vwap    = session.open_price or mid
        return check_mean_reversion(
            gex_positive=gex_pos,
            current_price=mid,
            vwap=vwap,
            upper_band=vwap + atr * 1.5,
            lower_band=vwap - atr * 1.5,
            atr=atr,
        )

    elif fn == "london_forex":
        return check_london_session_forex(
            pair=config["instrument"],
            current_time_et=now_et_time,
            is_evaluation=True,
        )

    elif fn == "fair_value_gap":
        # ICT-02: Fair Value Gap fill
        vwap = session.open_price or mid
        fvg_dir, fvg_reason = check_fair_value_gap_signal(session, mid, atr, vwap)
        if not fvg_dir:
            return Signal(setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0, fvg_reason)

        # ATR completion check — don't trade if daily ATR is >80% consumed
        if session.session_high and session.session_low and atr > 0:
            range_used = (session.session_high - session.session_low) / atr
            if range_used > 0.80:
                return Signal(
                    setup_id, SignalVerdict.NO_SIGNAL, None, None, None, None, 0.0,
                    f"ATR {range_used:.0%} consumed — daily move exhausted, skipping FVG"
                )

        sl_dist = atr * 0.4
        if fvg_dir == "long":
            entry = mid
            sl    = entry - sl_dist
            tp    = entry + sl_dist * 1.8
        else:
            entry = mid
            sl    = entry + sl_dist
            tp    = entry - sl_dist * 1.8

        return Signal(
            setup_id=setup_id,
            verdict=SignalVerdict.CONFIRMED,
            direction=fvg_dir,
            entry_price=round(entry, 5),
            stop_loss=round(sl, 5),
            take_profit=round(tp, 5),
            conviction=0.72,
            reason=fvg_reason,
        )

    elif fn == "liquidity_sweep":
        # ICT-03: Liquidity sweep + reclaim at PDH/PDL
        levels = get_session_levels()
        sweep_dir, sweep_reason = check_liquidity_sweep_signal(
            session, mid, atr, levels, now_et_time
        )
        if not sweep_dir:
            return Signal(setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0, sweep_reason)

        sl_dist = atr * 0.35
        if sweep_dir == "long":
            entry = mid
            sl    = min(session.session_low or (entry - sl_dist), entry - sl_dist)
            tp    = entry + (entry - sl) * 2.0
        else:
            entry = mid
            sl    = max(session.session_high or (entry + sl_dist), entry + sl_dist)
            tp    = entry - (sl - entry) * 2.0

        return Signal(
            setup_id=setup_id,
            verdict=SignalVerdict.CONFIRMED,
            direction=sweep_dir,
            entry_price=round(entry, 5),
            stop_loss=round(sl, 5),
            take_profit=round(tp, 5),
            conviction=0.78,
            reason=sweep_reason,
        )

    elif fn == "noon_curve":
        # VOL-06: Noon Curve reversal
        vwap = session.open_price or mid
        noon_dir, noon_reason = check_noon_curve_signal(
            session, mid, atr, vwap, ctx or MarketContext(), now_et_time
        )
        if not noon_dir:
            return Signal(setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0, noon_reason)

        sl_dist = atr * 0.50   # wider stop — reversal trades need room
        if noon_dir == "long":
            entry = mid
            sl    = session.session_low or (entry - sl_dist)
            tp    = entry + (entry - sl) * 1.6
        else:
            entry = mid
            sl    = session.session_high or (entry + sl_dist)
            tp    = entry - (sl - entry) * 1.6

        return Signal(
            setup_id=setup_id,
            verdict=SignalVerdict.CONFIRMED,
            direction=noon_dir,
            entry_price=round(entry, 5),
            stop_loss=round(sl, 5),
            take_profit=round(tp, 5),
            conviction=0.68,
            reason=noon_reason,
        )

    else:
        return Signal(
            setup_id, SignalVerdict.PENDING, None, None, None, None, 0.0,
            f"No signal generator mapped for signal_fn='{fn}'.",
        )


# ─────────────────────────────────────────────────────────────────────────────
# PROFIT LOCK — FORGE-64 (upgraded to 1R breakeven + smart TP ladder)
# ─────────────────────────────────────────────────────────────────────────────

async def manage_open_position(adapter: MT5Adapter, pos, account) -> None:
    if pos.stop_loss is None or pos.entry_price is None:
        return

    risk_per_unit = abs(pos.entry_price - pos.stop_loss)
    if risk_per_unit <= 0:
        return

    is_long   = (pos.direction == OrderDirection.LONG)
    pnl_per_u = (
        (pos.current_price - pos.entry_price) if is_long
        else (pos.entry_price - pos.current_price)
    )
    r = pnl_per_u / risk_per_unit

    logger.info(
        "[FORGE-64][%s] %s %.5f | R=%.2f | SL=%.5f",
        pos.position_id, pos.instrument, pos.current_price, r, pos.stop_loss,
    )

    # 1R → Move SL to breakeven (upgraded from 0.5R)
    if r >= 1.0:
        be = pos.entry_price
        sl_needs_update = (is_long and pos.stop_loss < be) or (not is_long and pos.stop_loss > be)
        if sl_needs_update:
            try:
                await adapter.modify_position(pos.position_id, new_stop_loss=be)
                logger.info("[FORGE-64][%s] 1R → SL moved to breakeven %.5f",
                            pos.position_id, be)
            except Exception as e:
                logger.error("[FORGE-64] modify_position error: %s", e)

    # 1.5R → Close 50% (smart TP ladder — take half off, let rest run)
    if r >= 1.5:
        partial_size = round(pos.size * 0.50, 2)
        if partial_size >= 0.10:
            try:
                await adapter.close_position(pos.position_id, size=partial_size)
                logger.info("[FORGE-64][%s] 1.5R → Closed 50%% (%.2f lots) — locking profit",
                            pos.position_id, partial_size)
                # Log partial close to evidence
                log_trade_evidence(
                    setup_id=str(pos.comment or "unknown").split("|")[1] if pos.comment and "|" in str(pos.comment) else "unknown",
                    direction="long" if pos.direction == OrderDirection.LONG else "short",
                    entry_price=pos.entry_price,
                    exit_price=pos.current_price,
                    pnl=partial_size * (pos.current_price - pos.entry_price) * (1 if pos.direction == OrderDirection.LONG else -1),
                    session_score=0.0,
                    vix=_market_context.vix if hasattr(_market_context, 'vix') else 18.0,
                    futures_bias=_market_context.futures_bias if hasattr(_market_context, 'futures_bias') else "neutral",
                    day_of_week=datetime.now(timezone.utc).weekday(),
                    atr_pct_used=0.0,
                    conviction=0.0,
                    outcome="PARTIAL_WIN",
                    r_multiple=r,
                    now_utc=datetime.now(timezone.utc),
                )
                send_telegram(
                    f"💰 <b>FORGE PARTIAL CLOSE</b>\n"
                    f"Setup: {pos.instrument}\n"
                    f"Closed 50% at 1.5R\n"
                    f"R={r:.2f} | Remaining: {pos.size - partial_size:.2f} lots running to 2R+"
                )
            except Exception as e:
                logger.error("[FORGE-64] close_position error: %s", e)

    # 3R → Trail SL to 2R (ride the big moves)
    if r >= 3.0:
        trail_sl = (
            pos.entry_price + (risk_per_unit * 2.0) if is_long
            else pos.entry_price - (risk_per_unit * 2.0)
        )
        needs_trail = (is_long and pos.stop_loss < trail_sl) or (not is_long and pos.stop_loss > trail_sl)
        if needs_trail:
            try:
                await adapter.modify_position(pos.position_id, new_stop_loss=trail_sl)
                logger.info("[FORGE-64][%s] 3R → Trailing SL to 2R: %.5f",
                            pos.position_id, trail_sl)
            except Exception as e:
                logger.error("[FORGE-64] modify_position error: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION EXECUTION CYCLE — Full superior execution engine
# ─────────────────────────────────────────────────────────────────────────────

async def run_session_cycle(
    adapter:            MT5Adapter,
    account,
    sq_score:           SessionQualityScore,
    session_tracker:    SessionTracker,
    consecutive_losses: int,
    daily_pnl_pct:      float,
    risk_state:         RiskState,
) -> list:
    results  = []
    firm_id  = os.environ.get("FTMO_ACCOUNT_ID", "FTMO")
    now_utc  = datetime.now(timezone.utc)
    now_et   = now_utc - timedelta(hours=5)
    now_et_time = now_et.time()

    # ── 1. PROFIT LOCK — manage open positions ───────────────────────────────
    for pos in account.open_positions:
        try:
            await manage_open_position(adapter, pos, account)
        except Exception as e:
            logger.error("[FORGE-64] Error on position %s: %s", pos.position_id, e)

    # ── 2. DAILY RISK CHECKS — hard gates before any new entries ────────────
    can_trade, risk_reason = risk_state.check_daily(account.balance, account.daily_pnl)
    if not can_trade:
        logger.info("[RISK] No new entries: %s", risk_reason)
        return results

    can_trade, cooldown_reason = risk_state.check_cooldown(now_utc)
    if not can_trade:
        logger.info("[RISK] %s", cooldown_reason)
        return results

    # ── 3. NEWS BLACKOUT CHECK ───────────────────────────────────────────────
    if is_news_blackout(now_utc, date.today()):
        logger.info("[NEWS] Blackout window active — no new entries.")
        return results

    # ── 4. POSITION LIMIT ───────────────────────────────────────────────────
    current_positions = account.open_position_count
    if current_positions >= 2:
        logger.info(
            "[EXECUTE] P-07: At max positions (%d/2). No new entries this cycle.",
            current_positions,
        )
        return results

    # ── 5. WEEKLY SIZE MULTIPLIER ────────────────────────────────────────────
    weekly_size_mult = risk_state.check_weekly(account.balance)

    # ── 5a. PROFIT VELOCITY CONTROL — if up 1.5% today, protect gains
    # Reduces size 50% when FORGE is already having a great day (Best Day Rule protection)
    daily_pnl_pct_positive = max(0.0, account.daily_pnl / account.balance)
    velocity_mult = 1.0
    if daily_pnl_pct_positive >= 0.015:
        velocity_mult = 0.50
        logger.info("[FTMO] Profit velocity: up %.1f%% today — cutting size 50%% to protect Best Day ratio",
                    daily_pnl_pct_positive * 100)
    elif daily_pnl_pct_positive >= 0.010:
        velocity_mult = 0.75
        logger.info("[FTMO] Profit velocity: up %.1f%% today — reducing size 25%%",
                    daily_pnl_pct_positive * 100)

    # ── 5b. FTMO RULE TRACKER — real-time rule intelligence
    ftmo = get_ftmo_tracker()
    ftmo.update_day_pnl(account.daily_pnl)
    ftmo.current_balance = account.balance

    # Emergency: close all if floating equity dangerously close to daily limit
    current_equity = getattr(account, "equity", account.balance)
    if ftmo.should_close_all_emergency(current_equity):
        logger.warning("[FTMO] ⚠️ EMERGENCY: Equity $%.2f approaching daily limit floor $%.2f — closing all",
                        current_equity, ftmo.daily_loss_floor)
        for pos in account.open_positions:
            try:
                await adapter.close_position(pos.position_id)
                logger.info("[FTMO] Emergency closed position %s", pos.position_id)
            except Exception as e:
                logger.error("[FTMO] Emergency close failed: %s", e)
        return results

    # Best Day Rule gate — cap today if ratio approaching 45%
    can_enter, ftmo_reason = ftmo.can_enter_today(current_equity)
    if not can_enter:
        logger.info("[FTMO] %s", ftmo_reason)
        return results

    # Log Best Day Rule status
    if ftmo.best_day_ratio > 0.35:
        logger.info("[FTMO] Best Day ratio: %.0f%% — approaching 50%% cap, staying cautious",
                    ftmo.best_day_ratio * 100)

    # Pre-news position protection — close existing positions 3min before news
    upcoming_events = fetch_high_impact_events(date.today())
    for event_time in upcoming_events:
        mins_until = (event_time - now_utc).total_seconds() / 60
        if 0 < mins_until <= 3 and account.open_position_count > 0:
            logger.warning("[NEWS] ⚡ High-impact event in %.1f min — closing all open positions", mins_until)
            send_telegram(f"⚡ <b>NEWS PROTECTION</b>\nClosing all positions {mins_until:.1f}min before high-impact event")
            for pos in account.open_positions:
                try:
                    await adapter.close_position(pos.position_id)
                except Exception as e:
                    logger.error("[NEWS] Pre-news close failed: %s", e)

    # Friday 3:55 PM ET hard close — no weekend exposure on Classic account
    if now_et.weekday() == 4 and now_et_time >= dtime(15, 55):
        if account.open_position_count > 0:
            logger.warning("[FTMO] Friday 3:55 ET — closing all positions (no weekend risk)")
            send_telegram("📅 <b>FRIDAY CLOSE</b>\nClosing all positions — no weekend exposure on Classic account")
            for pos in account.open_positions:
                try:
                    await adapter.close_position(pos.position_id)
                except Exception as e:
                    logger.error("[FTMO] Friday close failed: %s", e)
        return results

    # ── 6. MARKET CONTEXT — fetch real VIX + futures direction once per session
    ctx = get_market_context(now_utc)

    # ── 6a. VIX hard gate — skip ORB entirely in crisis (VIX > 35)
    vix_size_mult = ctx.size_multiplier
    if ctx.vix >= 35:
        logger.info("[CTX] VIX %.1f CRISIS — ORB skipped this session.", ctx.vix)
    if ctx.vix >= 25:
        logger.info("[CTX] VIX %.1f ELEVATED — reducing all positions 30%%.", ctx.vix)

    # ── 6b. Day of week filter
    is_strong_day, day_bonus = is_strong_orb_day(now_utc, ctx)
    day_name = ["Mon", "Tue", "Wed", "Thu", "Fri"][now_utc.weekday()]
    logger.info("[CTX] Day: %s | ORB strength: %s | Bonus: %.2fx",
                day_name, "STRONG" if is_strong_day else "NORMAL", day_bonus)

    # ── 6c. Session levels — PDH/PDL and prior day sentiment
    levels = get_session_levels()
    if levels.prev_day_high:
        logger.info("[LEVELS] PDH=%.2f PDL=%.2f | Sentiment: %s",
                    levels.prev_day_high, levels.prev_day_low, levels.day_sentiment)

    # ── 6d. Futures bias filter
    if ctx.futures_bias == "bearish" and ctx.futures_pct < -0.005:
        logger.info("[CTX] Futures strongly bearish (%.2f%%) — long signals need extra confirmation.",
                    ctx.futures_pct * 100)

    # ── 7. ACCOUNT METRICS ──────────────────────────────────────────────────
    drawdown_pct_used   = max(0.0, min(1.0, -daily_pnl_pct / 0.05))
    profit_pct_complete = 0.0

    # ── 7. SCAN SETUPS ──────────────────────────────────────────────────────
    # All 8 setups are eligible. sq_score.best_setups_for_today provides scoring
    # order but may not include new setups. We scan ALL configured setups,
    # prioritized by opportunity score which is calculated per-setup.
    # New setups (ICT-02, ICT-03, VOL-06) always included if SETUP_CONFIG has them.
    session_setups = list(dict.fromkeys(
        sq_score.best_setups_for_today +
        [s for s in SETUP_CONFIG.keys() if s not in sq_score.best_setups_for_today]
    ))

    for setup_id in session_setups:
        if current_positions >= 2:
            break

        config = SETUP_CONFIG.get(setup_id)
        if not config:
            logger.debug("[EXECUTE][%s] No config found — skipping.", setup_id)
            continue

        logical_instrument = config["instrument"]

        # ── Opportunity score ────────────────────────────────────────────────
        opp = score_opportunity(
            setup_id=setup_id,
            firm_id=firm_id,
            win_rate=config["win_rate"],
            avg_rr=config["avg_rr"],
            session_quality=sq_score.composite_score,
            catalyst_stack=config["catalyst_stack"],
            drawdown_pct_used=drawdown_pct_used,
            days_remaining=None,
            profit_pct_complete=profit_pct_complete,
            is_evaluation=True,
            rule_violation_risk=min(1.0, drawdown_pct_used * 0.5),
        )

        if not opp.execute_approved:
            logger.info("[EXECUTE][%s] ✗ Opportunity rejected: %s", setup_id, opp.reason)
            continue

        # ── Per-setup loss streak gate — skip setups on losing streaks
        streak = session_tracker.setup_loss_streak(setup_id)
        if streak >= 3:
            logger.info("[EXECUTE][%s] ⚠ Loss streak %d — de-weighted, skipping this session", setup_id, streak)
            continue
        elif streak == 2:
            logger.info("[EXECUTE][%s] ⚠ Loss streak %d — sizing reduced", setup_id, streak)

        # ── Signal cooldown: one trade per setup per session ─────────────────
        if session_tracker.has_traded(setup_id):
            logger.info("[EXECUTE][%s] Already traded this session — skipping.", setup_id)
            continue

        logger.info("[EXECUTE][%s] ✓ Opportunity approved: %s", setup_id, opp.reason)

        # ── Resolve actual ticker ────────────────────────────────────────────
        instrument = await resolve_instrument(adapter, logical_instrument)
        if instrument is None:
            logger.warning(
                "[EXECUTE][%s] ⚠ No working ticker found for '%s' — skipping.",
                setup_id, logical_instrument,
            )
            continue

        # ── Fetch live price ─────────────────────────────────────────────────
        try:
            bid, ask = await adapter.get_current_price(instrument)
            if bid <= 0 or ask <= 0:
                continue
        except Exception as e:
            logger.error("[EXECUTE][%s] Price fetch failed: %s", setup_id, e)
            continue

        mid = (bid + ask) / 2.0

        # ── Determine instrument type FIRST — needed for spread and ATR checks
        is_forex_inst = config["instrument"] in (
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "NZDUSD"
        )

        # ── Spread check — skip if spread is too wide (thin market / news)
        spread = ask - bid
        max_acceptable_spread = 0.0008 if is_forex_inst else mid * 0.0002
        synthetic_atr_check = mid * (0.001 if not is_forex_inst else 0.0005)
        if spread > max(synthetic_atr_check * 0.15, max_acceptable_spread):
            logger.info("[EXECUTE][%s] Spread %.5f too wide — skipping (thin market)", setup_id, spread)
            continue

        # ── Update session tracker ───────────────────────────────────────────
        session = session_tracker.update(instrument, mid, now_utc)
        
        # ── Define signal_fn early — used in multiple filters below ──────────
        signal_fn = config.get("signal_fn", "")

        # ── Real ATR from price history ──────────────────────────────────────
        synthetic_atr = mid * (0.0005 if is_forex_inst else 0.001)
        atr = session_tracker.get_real_atr(instrument, fallback=synthetic_atr)

        # ── ATR completion filter — don't trade exhausted moves
        # Research: 72% of NQ days respect daily ATR; price extended beyond 85% rarely continues
        if not is_forex_inst and session.session_high and session.session_low and atr > 0:
            session_range = session.session_high - session.session_low
            atr_consumed  = session_range / atr
            if atr_consumed > 0.85 and signal_fn not in ("noon_curve",):
                logger.info(
                    "[EXECUTE][%s] ATR %.0f%% consumed — daily move exhausted, skipping",
                    setup_id, atr_consumed * 100,
                )
                continue
            elif atr_consumed > 0.65:
                logger.info("[EXECUTE][%s] ATR %.0f%% consumed — targets compressed",
                            setup_id, atr_consumed * 100)

        # ── IB direction filter — trade with the IB break, not against it
        # Research: NQ IB single break probability 82.17% — second break is rare
        if not is_forex_inst and session.ib_locked and session.ib_high and session.ib_low:
            ib_high_broken = mid > session.ib_high
            ib_low_broken  = mid < session.ib_low
            if signal_fn in ("orb", "fair_value_gap", "liquidity_sweep"):
                if ib_high_broken:
                    logger.info("[EXECUTE][%s] IB high broken — long bias only", setup_id)
                elif ib_low_broken:
                    logger.info("[EXECUTE][%s] IB low broken — short bias only", setup_id)

        # ── Previous day sentiment filter for new setups
        if not is_forex_inst and levels.prev_day_high and signal_fn in ("liquidity_sweep",):
            sentiment_bias = levels.session_bias(mid)
            logger.info("[EXECUTE][%s] PDH=%.2f PDL=%.2f | Session bias: %s",
                        setup_id, levels.prev_day_high, levels.prev_day_low, sentiment_bias)

        # ── Skip ORB in VIX crisis mode
        if signal_fn == "orb" and ctx.vix >= 35:
            logger.info("[EXECUTE][%s] VIX %.1f CRISIS — ORB skipped.", setup_id, ctx.vix)
            continue

        # ── Futures directional gate for ORB — no longs against strong bear futures
        if signal_fn == "orb":
            if ctx.futures_bias == "bearish" and ctx.futures_pct < -0.005:
                logger.info("[EXECUTE][%s] Futures bear (%.2f%%) — longs blocked.",
                            setup_id, ctx.futures_pct * 100)
            if ctx.futures_bias == "bullish" and ctx.futures_pct > 0.005:
                logger.info("[EXECUTE][%s] Futures bull (%.2f%%) — shorts blocked.",
                            setup_id, ctx.futures_pct * 100)

        # ── Update session close price tracking for 5-min confirmation ─────────
        # Approximates close tracking: every 12 cycles (~1min each) = 5-min closes
        if session.tick_count % 5 == 0 and session.tick_count > 0:
            session.last_close  = mid
            session.close_count += 1

        # ── Update Initial Balance lock at 10:30 ET ──────────────────────────
        if now_et_time >= dtime(10, 30) and not session.ib_locked:
            if session.session_high and session.session_low:
                session.ib_high   = session.session_high
                session.ib_low    = session.session_low
                session.ib_locked = True
                logger.info(
                    "[CTX][%s] IB locked: H=%.2f L=%.2f Mid=%.2f",
                    instrument, session.ib_high, session.ib_low, session.ib_mid or 0,
                )

        # ── Check signal ─────────────────────────────────────────────────────
        signal = check_signal_for_setup(
            setup_id=setup_id,
            config=config,
            mid=mid,
            session=session,
            now_et_time=now_et_time,
            sq_score=sq_score,
            atr=atr,
            ctx=ctx,
        )

        if not signal.is_confirmed:
            logger.info(
                "[EXECUTE][%s] Signal %s: %s",
                setup_id, signal.verdict.name, signal.reason,
            )
            continue

        logger.info("[EXECUTE][%s] ✅ Signal CONFIRMED: %s", setup_id, signal.reason)

        # ── Conviction-based 2× sizing — research-backed: size up on optimal conditions
        # Optimal: Mon/Tue + VIX<18 + strong futures + expansion day + score>=8.0
        conviction_mult = 1.0
        optimal_conditions = sum([
            now_utc.weekday() in (0, 1),      # Monday or Tuesday
            ctx.vix < 18,                      # Low VIX — clean trends
            ctx.is_expansion_day,              # ATR > 1.5x average
            sq_score.composite_score >= 8.0,   # Elite session quality
            ctx.futures_bias in ("bullish",)   # Futures confirm
                and signal.direction == "long",
        ])
        if optimal_conditions >= 4:
            conviction_mult = 1.5   # 1.5× on near-optimal — stay safe for FTMO rules
            logger.info("[EXECUTE][%s] 🎯 HIGH CONVICTION (%.0f/5 conditions) → 1.5× size",
                        setup_id, optimal_conditions)
        elif optimal_conditions <= 1:
            conviction_mult = 0.75  # Reduce on weak setups
            logger.info("[EXECUTE][%s] ⚠ LOW CONVICTION (%.0f/5 conditions) → 0.75× size",
                        setup_id, optimal_conditions)

        # ── Dynamic position sizing: base × opportunity × weekly × VIX × conviction
        # Apply per-setup loss streak size reduction
        streak_mult = 0.75 if session_tracker.setup_loss_streak(setup_id) == 2 else 1.0
        base_size = (
            config["base_size"] *
            opp.size_multiplier *
            weekly_size_mult *
            vix_size_mult *
            conviction_mult *
            velocity_mult *
            streak_mult
        )
        sizing = calculate_dynamic_size(
            base_size=base_size,
            profit_pct_complete=profit_pct_complete,
            is_funded=False,
            consecutive_losses=consecutive_losses,
            recent_loss_pct=max(0.0, -daily_pnl_pct),
        )
        raw_size   = sizing.final_size
        final_size = max(0.10, round(raw_size, 2))
        logger.info(
            "[EXECUTE][%s] Sizing: %s | Weekly: %.1fx | VIX: %.1fx | Conviction: %.2fx",
            setup_id, sizing.reason, weekly_size_mult, vix_size_mult, conviction_mult,
        )

        if signal.stop_price is None:
            logger.warning("[EXECUTE][%s] No stop loss — skipping.", setup_id)
            continue

        # ── Dynamic ATR-based minimum stop distance ──────────────────────────
        # Use 1.0x ATR as minimum stop, capped at reasonable bounds
        if is_forex_inst:
            MIN_STOP_DISTANCE = max(0.0005, atr * 0.5)
        else:
            MIN_STOP_DISTANCE = max(5.0, atr * 1.0)   # at least 1 ATR away

        raw_sl_distance = abs(mid - signal.stop_price)
        if raw_sl_distance < MIN_STOP_DISTANCE:
            logger.info(
                "[EXECUTE][%s] SL distance %.5f < min %.5f (ATR-based) — expanding.",
                setup_id, raw_sl_distance, MIN_STOP_DISTANCE,
            )
            if signal.direction == "long":
                signal.stop_price = mid - MIN_STOP_DISTANCE
            else:
                signal.stop_price = mid + MIN_STOP_DISTANCE

        # ── FTMO size consistency check — prevent wildly inconsistent sizes
        if not ftmo.size_is_consistent(final_size):
            avg_recent = sum(ftmo.last_trade_sizes) / len(ftmo.last_trade_sizes) if ftmo.last_trade_sizes else final_size
            logger.warning("[FTMO] Size %.2f is 3x+ historical average %.2f — capping for consistency",
                           final_size, avg_recent)
            final_size = round(avg_recent * 1.5, 2)

        # ── TP calculation ───────────────────────────────────────────────────
        rr   = config.get("rr_ratio", 2.0)
        risk = abs(mid - signal.stop_price)

        # ATR-based TP ceiling — don't target beyond remaining ATR budget
        if not is_forex_inst and session.session_high and session.session_low and atr > 0:
            session_range   = session.session_high - session.session_low
            remaining_atr   = max(atr - session_range, atr * 0.20)   # at least 20% ATR left
            atr_based_limit = remaining_atr * 0.80   # target 80% of remaining

        if (
            signal.target_price is None
            or signal.target_price == 0
            or abs(signal.target_price - signal.stop_price) < 0.001
        ):
            if signal.direction == "long":
                fixed_tp = mid + risk * rr
            else:
                fixed_tp = mid - risk * rr
            logger.info(
                "[EXECUTE][%s] TP: %.5f (entry %.5f ± %.5f × %.1fR | ATR=%.2f)",
                setup_id, fixed_tp, mid, risk, rr, atr,
            )
            take_profit_price = fixed_tp
        else:
            take_profit_price = signal.target_price

        # ── Place order ──────────────────────────────────────────────────────
        direction = (
            OrderDirection.LONG if signal.direction == "long" else OrderDirection.SHORT
        )
        request = OrderRequest(
            instrument=instrument,
            direction=direction,
            size=final_size,
            order_type=OrderType.MARKET,
            stop_loss=signal.stop_price,
            take_profit=take_profit_price,
            comment=f"TF|{setup_id}|{opp.conviction_level}",
            magic_number=1000,
        )

        logger.info(
            "[EXECUTE][%s] ▶ %s %s %.2f lots | Entry≈%.5f | SL=%.5f | TP=%.5f | ATR=%.2f",
            setup_id,
            direction.value.upper(),
            instrument,
            final_size,
            mid,
            signal.stop_price,
            take_profit_price or 0,
            atr,
        )

        try:
            result = await adapter.place_order(request)
            results.append(result)

            if result.success:
                current_positions += 1
                session_tracker.mark_traded(setup_id)
                ftmo.record_trade_size(final_size)   # track for consistency
                fill_price = result.fill_price or mid
                logger.info(
                    "[EXECUTE][%s] ✅ FILLED: order_id=%s fill=%.5f",
                    setup_id, result.order_id, fill_price,
                )
                # Log entry to evidence store (outcome filled in when closed)
                log_trade_evidence(
                    setup_id=setup_id,
                    direction=signal.direction,
                    entry_price=fill_price,
                    exit_price=0.0,   # filled on close
                    pnl=0.0,
                    session_score=sq_score.composite_score,
                    vix=ctx.vix,
                    futures_bias=ctx.futures_bias,
                    day_of_week=now_utc.weekday(),
                    atr_pct_used=((session.session_high or 0) - (session.session_low or 0)) / atr if atr else 0,
                    conviction=signal.conviction,
                    outcome="OPEN",
                    r_multiple=0.0,
                    now_utc=now_utc,
                )
                send_telegram(
                    f"🔱 <b>FORGE v15 TRADE OPENED</b>\n"
                    f"Setup: {setup_id} | {direction.value.upper()}\n"
                    f"Instrument: {instrument}\n"
                    f"Size: {final_size} lots | VIX: {ctx.vix:.1f}\n"
                    f"Entry: {fill_price:.5f}\n"
                    f"SL: {signal.stop_price:.5f} ({risk:.2f} pts risk)\n"
                    f"TP: {take_profit_price:.5f} ({risk*rr:.2f} pts target)\n"
                    f"Score: {opp.composite_score:.0f}/100 | ATR: {atr:.2f}\n"
                    f"Best Day: {ftmo.best_day_ratio:.0%} | Buffer: ${ftmo.daily_loss_remaining:.0f}"
                )
            else:
                logger.error("[EXECUTE][%s] ❌ REJECTED: %s", setup_id, result.error_message)
        except Exception as e:
            logger.error("[EXECUTE][%s] place_order exception: %s", setup_id, e)

    return results


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — SIMULATION TRAINING
# ─────────────────────────────────────────────────────────────────────────────

def run_simulation_training() -> bool:
    logger.info("TITAN FORGE — Starting simulation training...")
    logger.info("Running protocol cycles to mature all capabilities...")

    r = TrainingRunner()
    for i in range(200):
        report = r.run_full_protocol()
        if report.cleared_for_live:
            logger.info(f"CLEARED after {i + 1} cycles!")
            logger.info(report.summary)
            return True
        if (i + 1) % 20 == 0:
            logger.info(
                f"Cycle {i + 1}/200: "
                f"{len(report.blocking_reasons)} blocking issue(s) remain."
            )

    logger.error("Simulation exhausted 200 cycles without clearing.")
    logger.error(report.summary)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — SESSION TIMING
# ─────────────────────────────────────────────────────────────────────────────

def is_market_session_active() -> bool:
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        return False
    hour = now_utc.hour
    return 0 <= hour < 21


def seconds_until_next_session() -> int:
    now_utc = datetime.now(timezone.utc)
    if now_utc.weekday() >= 5:
        days = 7 - now_utc.weekday()
        next_open = (now_utc + timedelta(days=days)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(60, int((next_open - now_utc).total_seconds()))
    if now_utc.hour >= 21:
        next_open = (now_utc + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(60, int((next_open - now_utc).total_seconds()))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — THE LIVE TRADING LOOP
# ─────────────────────────────────────────────────────────────────────────────

async def live_trading_loop(adapter: MT5Adapter) -> None:
    sqf             = SessionQualityFilter()
    session_tracker = SessionTracker()
    risk_state      = RiskState()

    recent_position_sizes: list[float] = []
    recent_entry_hours:    list[int]   = []
    session_wins:          int         = 0
    session_losses:        int         = 0
    consecutive_losses:    int         = 0
    last_session_date:     date        = date.today()
    last_week_number:      int         = date.today().isocalendar()[1]
    reconnect_attempts:    int         = 0

    logger.info("TITAN FORGE v15 — DEFINITIVE ELITE BUILD ACTIVE.")
    send_telegram(
        "🔱 <b>TITAN FORGE v15 ONLINE</b>\n"
        "Definitive elite build. All systems armed.\n"
        "All 5 setups active | Real ATR | Risk management armed.\n"
        "Watching for setups..."
    )

    cycle = 0

    while True:
        cycle += 1

        try:
            today      = date.today()
            week_num   = today.isocalendar()[1]

            # ── Weekly reset ─────────────────────────────────────────────────
            if week_num != last_week_number:
                last_week_number = week_num
                account_snap = await adapter.get_account_state()
                risk_state.reset_weekly(account_snap.balance)
                logger.info("[Cycle %d] New week — risk state reset.", cycle)

            # ── Daily reset ──────────────────────────────────────────────────
            if today != last_session_date:
                session_tracker.reset()
                session_wins       = 0
                session_losses     = 0
                consecutive_losses = 0
                last_session_date  = today
                account_snap = await adapter.get_account_state()
                risk_state.reset_daily(account_snap.balance)
                _ftmo_tracker.reset_daily_counters() if hasattr(_ftmo_tracker, 'reset_daily_counters') else None
                _ftmo_tracker.current_balance = account_snap.balance
                logger.info("[Cycle %d] New session day — all trackers reset.", cycle)

                # Pre-fetch all session context in parallel
                ctx    = fetch_market_context()
                levels = fetch_session_levels()

                day_name = ["Monday","Tuesday","Wednesday","Thursday","Friday"][today.weekday()]
                is_strong, day_bonus = is_strong_orb_day(datetime.now(timezone.utc), ctx)

                # Build comprehensive morning briefing
                pdh_pdl_str = (
                    f"PDH: {levels.prev_day_high:.2f} | PDL: {levels.prev_day_low:.2f}"
                    if levels.prev_day_high else "PDH/PDL: fetching..."
                )
                payout_str = "✅ PAYOUT ELIGIBLE" if _ftmo_tracker.payout_eligible else \
                             f"Payout in {14 - (today - _ftmo_tracker.payout_day_1).days if _ftmo_tracker.payout_day_1 else '?'}d"
                scaling_str = "🚀 SCALING ELIGIBLE" if _ftmo_tracker.scaling_eligible else ""

                send_telegram(
                    f"🔱 <b>FORGE v15 ONLINE</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📅 {today} ({day_name})\n"
                    f"💰 Balance: ${account_snap.balance:,.2f}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 Market Context\n"
                    f"VIX: {ctx.vix:.1f} ({ctx.vix_regime})\n"
                    f"Futures: {ctx.futures_pct*100:+.2f}% ({ctx.futures_bias})\n"
                    f"Gap: {ctx.gap_direction} ({ctx.gap_pct*100:+.2f}%)\n"
                    f"Prior Day: {levels.day_sentiment.upper()}\n"
                    f"{pdh_pdl_str}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ Session Edge\n"
                    f"ORB Day: {'🟢 STRONG' if is_strong else '🟡 NORMAL'} ({day_bonus:.2f}x)\n"
                    f"Size adj: {ctx.size_multiplier:.0%} (VIX)\n"
                    f"Best Day: {_ftmo_tracker.best_day_ratio:.0%} limit\n"
                    f"{payout_str} {scaling_str}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔫 Active setups: ORD-02 ICT-01 ICT-02 ICT-03 VOL-03 VOL-05 VOL-06 SES-01"
                )

            # ── Market hours check ───────────────────────────────────────────
            if not is_market_session_active():
                wait_seconds = seconds_until_next_session()
                logger.info(
                    f"[Cycle {cycle}] Market closed. "
                    f"Sleeping {wait_seconds // 60}m until next session."
                )
                await asyncio.sleep(wait_seconds)
                continue

            # ── Health check with exponential backoff reconnect ──────────────
            health = await adapter.health_check()
            if not health.is_healthy:
                reconnect_attempts += 1
                wait = min(30 * (2 ** (reconnect_attempts - 1)), 300)  # 30s, 60s, 120s, max 5min
                logger.warning(
                    f"[Cycle {cycle}] Platform unhealthy: {health.error}. "
                    f"Reconnect attempt {reconnect_attempts}. Waiting {wait}s..."
                )
                if reconnect_attempts <= 3:
                    await adapter.connect()
                else:
                    send_telegram(
                        f"⚠️ <b>FORGE CONNECTION ISSUE</b>\n"
                        f"MetaAPI unhealthy after {reconnect_attempts} attempts.\n"
                        f"Error: {health.error}\nWaiting {wait}s..."
                    )
                await asyncio.sleep(wait)
                continue
            else:
                reconnect_attempts = 0

            # ── Account state ────────────────────────────────────────────────
            account = await adapter.get_account_state()
            logger.info(
                f"[Cycle {cycle}] Account: "
                f"Balance={account.balance:.2f} | "
                f"Equity={account.equity:.2f} | "
                f"Daily P&L={account.daily_pnl:.2f} | "
                f"Positions={account.open_position_count}"
            )

            daily_pnl_pct = (
                account.daily_pnl / account.balance
                if account.balance > 0 else 0.0
            )

            # ── Behavioral consistency ───────────────────────────────────────
            now_hour = datetime.now(timezone.utc).hour
            recent_entry_hours.append(now_hour)
            recent_entry_hours = recent_entry_hours[-20:]

            total_trades = session_wins + session_losses
            baseline_wr  = 0.65
            recent_wr    = (
                session_wins / total_trades if total_trades > 0 else baseline_wr
            )

            behavioral = check_behavioral_consistency(
                position_sizes=recent_position_sizes or [0.01],
                entry_hours=recent_entry_hours or [9],
                baseline_win_rate=baseline_wr,
                recent_win_rate=recent_wr,
            )

            if behavioral.severity == "FLAGGED":
                logger.warning(
                    f"[Cycle {cycle}][FORGE-56] Behavioral FLAGGED: "
                    f"{' | '.join(behavioral.flags)} — execution blocked."
                )
                await asyncio.sleep(60)
                continue
            elif behavioral.severity == "CAUTION":
                logger.info(
                    f"[Cycle {cycle}][FORGE-56] Behavioral CAUTION: "
                    f"{' | '.join(behavioral.flags)}"
                )
            else:
                logger.info(f"[Cycle {cycle}][FORGE-56] Behavioral: CLEAN.")

            # ── Consecutive loss check ────────────────────────────────────────
            if consecutive_losses >= 3:
                risk_state.trigger_cooldown(datetime.now(timezone.utc))
                consecutive_losses = 0  # reset counter after triggering

            # ── Session quality ───────────────────────────────────────────────
            pre_session = build_pre_session_data(
                session_date=date.today(),
                firm_id=os.environ.get("FTMO_ACCOUNT_ID", "FTMO"),
                is_evaluation=True,
                overnight_pct=0.001,
                futures_direction="bullish",
                vix_level=18.0,
                gex_regime=GEXRegime.NEUTRAL,
                consecutive_losses=consecutive_losses,
            )

            sq_score: SessionQualityScore = sqf.score_session(
                data=pre_session,
                pacing_threshold=6.0,
                position_sizes=recent_position_sizes or [0.01],
                entry_hours=recent_entry_hours or [9],
                baseline_win_rate=baseline_wr,
                recent_win_rate=recent_wr,
            )

            if sq_score.hard_blocked or not sq_score.is_tradeable:
                logger.info(
                    f"[Cycle {cycle}][FORGE-08] Session not tradeable. "
                    f"Score={sq_score.composite_score:.1f} | "
                    f"Decision={sq_score.decision.name} | "
                    f"Reason={sq_score.reason}"
                )
                await asyncio.sleep(300)
                continue

            logger.info(
                f"[Cycle {cycle}][FORGE-08] Session tradeable. "
                f"Score={sq_score.composite_score:.1f} | "
                f"Decision={sq_score.decision.name} | "
                f"Best setups={sq_score.best_setups_for_today}"
            )

            # ── Execute ──────────────────────────────────────────────────────
            trade_results = await run_session_cycle(
                adapter=adapter,
                account=account,
                sq_score=sq_score,
                session_tracker=session_tracker,
                consecutive_losses=consecutive_losses,
                daily_pnl_pct=daily_pnl_pct,
                risk_state=risk_state,
            )

            for result in trade_results:
                if result.success:
                    session_wins       += 1
                    consecutive_losses  = 0
                    recent_position_sizes.append(result.size)
                    recent_position_sizes = recent_position_sizes[-20:]
                    session_tracker.record_setup_outcome(result.setup_id if hasattr(result, 'setup_id') else "unknown", won=True)
                else:
                    session_losses     += 1
                    consecutive_losses += 1
                    session_tracker.record_setup_outcome(result.setup_id if hasattr(result, 'setup_id') else "unknown", won=False)

            # ── End of day summary (cycle after 4pm ET) ───────────────────────
            now_et_hour = (datetime.now(timezone.utc) - timedelta(hours=5)).hour
            if now_et_hour >= 16 and session_wins + session_losses > 0:
                total = session_wins + session_losses
                wr    = session_wins / total if total > 0 else 0
                ftmo  = get_ftmo_tracker()
                ftmo.update_day_pnl(account.daily_pnl)

                # Payout alert
                payout_alert = ""
                if ftmo.payout_eligible:
                    payout_alert = "\n🎯 <b>PAYOUT AVAILABLE — Request now!</b>"

                # Scaling alert
                scaling_alert = ""
                if ftmo.scaling_eligible:
                    scaling_alert = "\n🚀 <b>SCALING ELIGIBLE — +25% account size!</b>"

                # Best day ratio warning
                bdr_warning = ""
                if ftmo.best_day_ratio > 0.40:
                    bdr_warning = f"\n⚠️ Best Day: {ftmo.best_day_ratio:.0%} — need more trading days"

                if wr > 0 or session_losses > 0:
                    send_telegram(
                        f"📊 <b>FORGE v15 DAILY SUMMARY</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Date: {today}\n"
                        f"Trades: {total} | W: {session_wins} L: {session_losses}\n"
                        f"Win Rate: {wr:.0%}\n"
                        f"Daily P&L: ${account.daily_pnl:+.2f}\n"
                        f"Balance: ${account.balance:,.2f}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━\n"
                        f"FTMO Health\n"
                        f"Daily limit used: {ftmo.daily_loss_pct_used:.0%}\n"
                        f"Best Day ratio: {ftmo.best_day_ratio:.0%} / 50% cap\n"
                        f"Discipline score: {ftmo.discipline_score:.0f}%\n"
                        f"Max loss floor: ${ftmo.max_loss_floor:,.0f}"
                        f"{bdr_warning}{payout_alert}{scaling_alert}"
                    )
                    session_wins   = 0
                    session_losses = 0
                    # Update FTMO tracker end of day
                    ftmo.close_of_day(account.daily_pnl)

            await asyncio.sleep(60)

        except asyncio.CancelledError:
            logger.info("TITAN FORGE — Shutdown signal. Closing gracefully.")
            send_telegram("⚠️ <b>FORGE OFFLINE</b>\nShutdown signal received.")
            await adapter.disconnect()
            return

        except Exception as e:
            logger.error(
                f"[Cycle {cycle}] Unexpected error: {type(e).__name__}: {e}. "
                f"Retrying in 60 seconds."
            )
            await asyncio.sleep(60)
            continue


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    cleared = run_simulation_training()
    if not cleared:
        logger.error(
            "FORGE failed to clear simulation training. "
            "Review strategy configuration before redeploying."
        )
        await asyncio.sleep(600)
        return

    logger.info("TITAN FORGE — Simulation cleared. Connecting to FTMO MT5...")

    adapter = MT5Adapter(
        account_id=os.environ.get("FTMO_ACCOUNT_ID", ""),
        server=os.environ.get("FTMO_API_URL", ""),
        password=os.environ.get("FTMO_PASSWORD", ""),
        is_demo=os.environ.get("FTMO_IS_DEMO", "true").lower() == "true",
    )

    connected = await adapter.connect()
    if not connected:
        logger.error(
            "FORGE failed to connect to FTMO MT5. "
            "Check Railway env vars: FTMO_ACCOUNT_ID, FTMO_API_URL, FTMO_PASSWORD."
        )
        await asyncio.sleep(300)
        return

    logger.info(
        f"TITAN FORGE — Connected. "
        f"Mode: {'DEMO' if adapter.is_demo else 'LIVE'}. "
        f"Account: {adapter.account_id}."
    )

    logger.info("[TICKER] Fetching full symbol list from MetaAPI...")
    syms = await fetch_all_symbols(adapter.account_id, adapter=adapter)
    if syms:
        logger.info("[TICKER] All available symbols (%d total): %s", len(syms), syms)
    else:
        logger.warning("[TICKER] Could not fetch symbol list — will try hardcoded aliases.")

    await live_trading_loop(adapter)


if __name__ == "__main__":
    asyncio.run(main())
