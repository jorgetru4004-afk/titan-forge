"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              forge_core.py — THE FOUNDATION                                 ║
║                                                                              ║
║  Everything else depends on this module. Zero external dependencies.       ║
║                                                                              ║
║  CONTAINS:                                                                   ║
║    1. DST-aware UTC→ET conversion (Bug #1 fix — NEVER hardcode offset)    ║
║    2. 8-state session machine with transition detection                    ║
║    3. Telegram alerts (Bug #15 fix — always wrapped in try/except)        ║
║    4. Price cache with 2-min TTL (Bug #3 fix — API resilience)            ║
║    5. Instrument tracker (ORB, IB, session H/L, close prices)             ║
║    6. Signal + SignalVerdict structures                                     ║
║    7. News blackout intelligence (Bug #17 fix — DST-aware)                ║
║    8. MarketContext dataclass                                               ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from enum import Enum
from typing import Optional

logger = logging.getLogger("titan_forge.core")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DST-AWARE TIMEZONE — Bug #1 FIX
#
# Every time comparison in FORGE goes through here.
# No hardcoded timedelta(hours=5) ANYWHERE. EVER.
#
# US DST: Second Sunday of March → clocks spring forward (UTC-4)
#         First Sunday of November → clocks fall back (UTC-5)
# ═══════════════════════════════════════════════════════════════════════════════

def is_dst(utc_dt: Optional[datetime] = None) -> bool:
    """Check if a UTC datetime falls within US Eastern Daylight Time."""
    if utc_dt is None:
        utc_dt = datetime.now(timezone.utc)
    year = utc_dt.year

    # Second Sunday of March at 7 AM UTC (= 2 AM EST)
    march_1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    days_to_sun = (6 - march_1.weekday()) % 7
    dst_start = march_1 + timedelta(days=days_to_sun + 7)
    dst_start = dst_start.replace(hour=7, minute=0, second=0)

    # First Sunday of November at 6 AM UTC (= 2 AM EDT)
    nov_1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    days_to_sun_nov = (6 - nov_1.weekday()) % 7
    dst_end = nov_1 + timedelta(days=days_to_sun_nov)
    dst_end = dst_end.replace(hour=6, minute=0, second=0)

    return dst_start <= utc_dt < dst_end


def utc_to_et(utc_dt: datetime) -> datetime:
    """
    Convert UTC datetime to US Eastern Time.
    EDT (summer): UTC - 4h | EST (winter): UTC - 5h
    """
    offset = 4 if is_dst(utc_dt) else 5
    return utc_dt - timedelta(hours=offset)


def now_et() -> datetime:
    """Current time in Eastern Time."""
    return utc_to_et(datetime.now(timezone.utc))


def now_et_time() -> dtime:
    """Current time-of-day in Eastern Time."""
    return now_et().time()


def is_rth(t: Optional[dtime] = None) -> bool:
    """Is it Regular Trading Hours? (9:30 - 16:00 ET)"""
    if t is None:
        t = now_et_time()
    return dtime(9, 30) <= t < dtime(16, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SESSION STATE MACHINE — 8 states, regime-aware
# ═══════════════════════════════════════════════════════════════════════════════

class SessionState(Enum):
    PRE_MARKET      = "PRE_MARKET"        # 4:00 - 9:30
    OPENING_DRIVE   = "OPENING_DRIVE"     # 9:30 - 9:50
    IB_FORMATION    = "IB_FORMATION"      # 9:30 - 10:30 (overlaps OD)
    MID_MORNING     = "MID_MORNING"       # 10:30 - 11:30
    LUNCH_CHOP      = "LUNCH_CHOP"        # 11:30 - 13:00
    AFTERNOON       = "AFTERNOON"         # 13:00 - 15:00
    POWER_HOUR      = "POWER_HOUR"        # 15:00 - 15:50
    CLOSE_POSITION  = "CLOSE_POSITION"    # 15:50 - 16:00
    CLOSED          = "CLOSED"            # After hours


def get_session_state(t: Optional[dtime] = None) -> SessionState:
    """Determine current session state from time."""
    if t is None:
        t = now_et_time()

    if t < dtime(9, 30):
        return SessionState.PRE_MARKET
    elif t < dtime(9, 50):
        return SessionState.OPENING_DRIVE
    elif t < dtime(10, 30):
        return SessionState.IB_FORMATION
    elif t < dtime(11, 30):
        return SessionState.MID_MORNING
    elif t < dtime(13, 0):
        return SessionState.LUNCH_CHOP
    elif t < dtime(15, 0):
        return SessionState.AFTERNOON
    elif t < dtime(15, 50):
        return SessionState.POWER_HOUR
    elif t < dtime(16, 0):
        return SessionState.CLOSE_POSITION
    else:
        return SessionState.CLOSED


# Setup suitability per session state (0 = unsuitable, 1 = optimal)
_STATE_WEIGHTS: dict[str, dict[SessionState, float]] = {
    "ORD-02": {
        SessionState.OPENING_DRIVE: 0.5, SessionState.IB_FORMATION: 1.0,
        SessionState.MID_MORNING: 0.8, SessionState.LUNCH_CHOP: 0.0,
    },
    "ICT-01": {
        SessionState.IB_FORMATION: 0.7, SessionState.MID_MORNING: 1.0,
        SessionState.LUNCH_CHOP: 0.3, SessionState.AFTERNOON: 0.8,
    },
    "ICT-02": {
        SessionState.OPENING_DRIVE: 0.3, SessionState.IB_FORMATION: 0.9,
        SessionState.MID_MORNING: 1.0, SessionState.LUNCH_CHOP: 0.5,
    },
    "ICT-03": {
        SessionState.OPENING_DRIVE: 0.5, SessionState.IB_FORMATION: 1.0,
        SessionState.MID_MORNING: 0.9, SessionState.LUNCH_CHOP: 0.3,
    },
    "VOL-03": {
        SessionState.MID_MORNING: 1.0, SessionState.LUNCH_CHOP: 0.2,
        SessionState.AFTERNOON: 0.8, SessionState.POWER_HOUR: 0.6,
    },
    "VOL-05": {
        SessionState.MID_MORNING: 0.5, SessionState.LUNCH_CHOP: 0.8,
        SessionState.AFTERNOON: 1.0, SessionState.POWER_HOUR: 0.7,
    },
    "VOL-06": {
        SessionState.LUNCH_CHOP: 1.0, SessionState.AFTERNOON: 0.3,
    },
    "SES-01": {
        SessionState.PRE_MARKET: 1.0,
    },
}


def get_state_weight(setup_id: str, state: SessionState) -> float:
    """Get suitability weight of a setup for a given session state."""
    weights = _STATE_WEIGHTS.get(setup_id, {})
    return weights.get(state, 0.0)


def session_minutes_remaining(t: Optional[dtime] = None) -> float:
    """Minutes remaining until market close (16:00 ET)."""
    if t is None:
        t = now_et_time()
    close = dtime(16, 0)
    if t >= close:
        return 0.0
    now_mins = t.hour * 60 + t.minute
    close_mins = 16 * 60
    return max(0.0, close_mins - now_mins)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TELEGRAM ALERTS — Bug #15 FIX (NEVER crash for notification failure)
# ═══════════════════════════════════════════════════════════════════════════════

_TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5264397522")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def send_telegram(text: str) -> None:
    """Send a Telegram message. NEVER raises — all errors logged and swallowed."""
    if not _TELEGRAM_BOT_TOKEN:
        logger.debug("[TELEGRAM] No bot token — skipping.")
        return
    try:
        import urllib.parse
        url = (f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage?"
               f"chat_id={_TELEGRAM_CHAT_ID}&parse_mode=HTML&"
               f"text={urllib.parse.quote(text[:4000])}")
        req = urllib.request.Request(url, headers={"User-Agent": "TITAN-FORGE"})
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            resp.read()
    except Exception as e:
        logger.warning("[TELEGRAM] Send failed (non-fatal): %s", e)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. PRICE CACHE — Bug #3 FIX (API resilience, 2-min TTL)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CachedPrice:
    """A cached price with age tracking."""
    bid:        float
    ask:        float
    timestamp:  datetime
    max_age:    float = 120.0   # 2 minutes

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.timestamp).total_seconds()

    @property
    def stale(self) -> bool:
        return self.age_seconds > self.max_age


class PriceCache:
    """
    Cache last known good price per instrument.
    When MetaAPI returns 504/500, use cached price if < 2 min old.
    """
    def __init__(self, max_age_seconds: int = 120):
        self._cache: dict[str, CachedPrice] = {}
        self._max_age = max_age_seconds

    def update(self, instrument: str, bid: float, ask: float) -> None:
        """Store a fresh price."""
        self._cache[instrument] = CachedPrice(
            bid=bid, ask=ask,
            timestamp=datetime.now(timezone.utc),
            max_age=self._max_age,
        )

    def get(self, instrument: str) -> Optional[CachedPrice]:
        """Get cached price if not stale."""
        cp = self._cache.get(instrument)
        if cp is None or cp.stale:
            return None
        return cp

    def get_mid(self, instrument: str) -> Optional[float]:
        """Get mid price or None if stale/missing."""
        cp = self.get(instrument)
        return cp.mid if cp else None

    def age(self, instrument: str) -> float:
        """Age in seconds. Returns inf if not cached."""
        cp = self._cache.get(instrument)
        return cp.age_seconds if cp else float("inf")


# Global price cache instance
_price_cache = PriceCache(max_age_seconds=120)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. INSTRUMENT TRACKER — ORB, IB, session H/L, close prices
#
# Bug #4: ATR consumed only after 9:30 ET
# Bug #5: IB minimum range of 5pts before direction applies
# Bug #8: ORB requires minimum 5 ticks + 5pt range + wait until 9:45
# ═══════════════════════════════════════════════════════════════════════════════

class InstrumentTracker:
    """
    Per-instrument session tracker. Tracks ORB, IB, session high/low,
    5-min close prices, and VWAP proxy (open price).
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        """Reset for new session."""
        # ORB tracking — Bug #8 fixed
        self.orb_high: Optional[float] = None
        self.orb_low: Optional[float] = None
        self.orb_locked: bool = False
        self.orb_valid: bool = False
        self._orb_tick_count: int = 0
        self._orb_tracking: bool = False

        # IB tracking — Bug #5 fixed
        self.ib_high: float = 0.0
        self.ib_low: float = float("inf")
        self.ib_locked: bool = False
        self.ib_direction: Optional[str] = None
        self._ib_tick_count: int = 0

        # Session tracking
        self.session_high: Optional[float] = None
        self.session_low: Optional[float] = None
        self.open_price: Optional[float] = None

        # Price history (for entropy, energy, pattern detection)
        self.price_history: list[float] = []
        self.close_prices: list[float] = []      # 5-min close prices
        self.volume_history: list[float] = []     # spread as volume proxy
        self.last_close: Optional[float] = None

        # Timing
        self._last_close_time: Optional[datetime] = None
        self._close_interval_sec: float = 300     # 5 minutes

    def update(self, bid: float, ask: float, ctx: "MarketContext") -> None:
        """
        Feed new price data. Call every cycle (~60s).
        Handles ORB, IB, session tracking, and candle simulation.
        """
        mid = (bid + ask) / 2.0
        spread = ask - bid
        t = now_et_time()

        if mid <= 0:
            return

        # Record open price once
        if self.open_price is None and is_rth(t):
            self.open_price = mid

        # Session high/low — Bug #4: only track during RTH
        if is_rth(t):
            if self.session_high is None or mid > self.session_high:
                self.session_high = mid
            if self.session_low is None or mid < self.session_low:
                self.session_low = mid

        # Price and spread history
        self.price_history.append(mid)
        self.price_history = self.price_history[-200:]  # keep last 200 ticks
        self.volume_history.append(spread)
        self.volume_history = self.volume_history[-200:]

        # Simulate 5-min candle closes
        now = datetime.now(timezone.utc)
        if self._last_close_time is None:
            self._last_close_time = now
        elif (now - self._last_close_time).total_seconds() >= self._close_interval_sec:
            self.close_prices.append(mid)
            self.close_prices = self.close_prices[-50:]  # keep 50 candles
            self.last_close = mid
            self._last_close_time = now

        # ── ORB Tracking (Bug #8 fix) ────────────────────────────────────────
        if dtime(9, 30) <= t < dtime(9, 45) and not self.orb_locked:
            self._orb_tracking = True
            self._orb_tick_count += 1
            if self.orb_high is None or mid > self.orb_high:
                self.orb_high = mid
            if self.orb_low is None or mid < self.orb_low:
                self.orb_low = mid

        if t >= dtime(9, 45) and self._orb_tracking and not self.orb_locked:
            self.orb_locked = True
            orb_range = (self.orb_high or 0) - (self.orb_low or 0)
            # Bug #8: require 5+ ticks AND 5+ point range
            self.orb_valid = (self._orb_tick_count >= 5 and orb_range >= 5.0)
            if self.orb_valid:
                logger.info("[ORB] Locked: H=%.2f L=%.2f Range=%.1fpts (%d ticks)",
                           self.orb_high, self.orb_low, orb_range, self._orb_tick_count)
            else:
                logger.info("[ORB] INVALID: Range=%.1fpts ticks=%d (min 5pts, 5 ticks)",
                           orb_range, self._orb_tick_count)

        # ── IB Tracking (Bug #5 fix) ─────────────────────────────────────────
        if dtime(9, 30) <= t < dtime(10, 30) and not self.ib_locked:
            self._ib_tick_count += 1
            if mid > self.ib_high:
                self.ib_high = mid
            if mid < self.ib_low:
                self.ib_low = mid

        if t >= dtime(10, 30) and not self.ib_locked and self._ib_tick_count >= 5:
            self.ib_locked = True
            ib_range = self.ib_high - self.ib_low if self.ib_low != float("inf") else 0
            # Bug #5: minimum 5pt range before IB direction applies
            if ib_range < 5.0:
                self.ib_direction = "none"
                logger.info("[IB] Locked but DEGENERATE range (%.1fpts < 5pt min)", ib_range)
            else:
                logger.info("[IB] Locked: H=%.2f L=%.2f Range=%.1fpts", self.ib_high, self.ib_low, ib_range)

        # IB direction detection (after lock, first break)
        if self.ib_locked and self.ib_direction is None:
            ib_range = self.ib_high - self.ib_low if self.ib_low != float("inf") else 0
            if ib_range >= 5.0:  # Bug #5: only detect if range is valid
                if mid > self.ib_high:
                    self.ib_direction = "long"
                    logger.info("[IB] High broke → LONG bias (82%% single break)")
                elif mid < self.ib_low:
                    self.ib_direction = "short"
                    logger.info("[IB] Low broke → SHORT bias (82%% single break)")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SIGNAL + VERDICT STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

class SignalVerdict(Enum):
    CONFIRMED = "CONFIRMED"
    PENDING   = "PENDING"
    REJECTED  = "REJECTED"


@dataclass
class Signal:
    """A trading signal from a setup generator."""
    setup_id:     str
    verdict:      SignalVerdict
    direction:    Optional[str]      # "long" / "short"
    entry_price:  Optional[float]
    stop_loss:    Optional[float]
    take_profit:  Optional[float]
    conviction:   float              # raw conviction 0-1
    reason:       str


# ═══════════════════════════════════════════════════════════════════════════════
# 7. NEWS BLACKOUT INTELLIGENCE — Bug #17 FIX (DST-aware)
#
# Known high-impact event times (ET):
# 8:30 — CPI, PPI, Retail Sales, GDP, Jobless Claims, NFP
# 10:00 — Consumer Confidence, ISM, JOLTS
# 14:00 — FOMC announcements
# 14:30 — FOMC press conference
# ═══════════════════════════════════════════════════════════════════════════════

HIGH_IMPACT_TIMES_ET = [
    dtime(8, 30),
    dtime(10, 0),
    dtime(14, 0),
    dtime(14, 30),
]


def is_news_blackout(
    blackout_before: int = 5,
    blackout_after: int = 5,
) -> bool:
    """
    Check if current ET time is within a news blackout window.
    Bug #17 fix: uses DST-aware now_et_time(), not hardcoded offset.
    """
    t = now_et_time()
    now_mins = t.hour * 60 + t.minute

    for event_time in HIGH_IMPACT_TIMES_ET:
        event_mins = event_time.hour * 60 + event_time.minute
        if (event_mins - blackout_before) <= now_mins <= (event_mins + blackout_after):
            return True
    return False


def minutes_to_next_news() -> Optional[float]:
    """Minutes until the next high-impact event. None if none today."""
    t = now_et_time()
    now_mins = t.hour * 60 + t.minute

    for event_time in HIGH_IMPACT_TIMES_ET:
        event_mins = event_time.hour * 60 + event_time.minute
        if event_mins > now_mins:
            return event_mins - now_mins
    return None


def should_close_for_news(close_minutes_before: int = 3) -> bool:
    """Should existing positions be closed for upcoming news?"""
    mins = minutes_to_next_news()
    if mins is not None and 0 < mins <= close_minutes_before:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 8. MARKET CONTEXT — The unified state object
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MarketContext:
    """Complete market context — all real data, nothing hardcoded."""
    # VIX
    vix:                float = 20.0
    vix_regime:         str   = "NORMAL"
    vix_size_mult:      float = 1.0
    # Futures
    futures_pct:        float = 0.0
    futures_bias:       str   = "neutral"
    # Previous day levels
    pdh:                float = 0.0
    pdl:                float = 0.0
    prev_close:         float = 0.0
    # IB (populated from InstrumentTracker)
    ib_high:            float = 0.0
    ib_low:             float = 0.0
    ib_locked:          bool  = False
    ib_direction:       Optional[str] = None
    # ATR
    atr:                float = 100.0
    atr_consumed_pct:   float = 0.0
    # Session
    session_state:      SessionState = SessionState.CLOSED
    minutes_remaining:  float = 0.0
    # Day
    day_of_week:        int   = 0
    day_name:           str   = "Monday"
    day_strength:       float = 1.0
    # Timestamp
    fetched_at:         Optional[datetime] = None

    def sync_from_tracker(self, tracker: InstrumentTracker) -> None:
        """Sync IB data from the instrument tracker."""
        self.ib_high = tracker.ib_high
        self.ib_low = tracker.ib_low if tracker.ib_low != float("inf") else 0.0
        self.ib_locked = tracker.ib_locked
        self.ib_direction = tracker.ib_direction


# ═══════════════════════════════════════════════════════════════════════════════
# 9. EVIDENCE RE-EXPORT (for import convenience)
# ═══════════════════════════════════════════════════════════════════════════════

from forge_evidence import TradeFingerprint, EvidenceLogger  # noqa: E402

_evidence = EvidenceLogger()


# ═══════════════════════════════════════════════════════════════════════════════
# 10. MARKET DATA FETCH (delegates to forge_market)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_market_context() -> MarketContext:
    """Build a complete MarketContext from live data sources."""
    # Import here to avoid circular dependency
    from forge_market import build_market_context as _build
    raw = _build()

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    dow = date.today().weekday()

    ctx = MarketContext(
        vix=raw.vix,
        vix_regime=raw.vix_regime,
        vix_size_mult=raw.vix_size_mult,
        futures_pct=raw.futures_pct,
        futures_bias=raw.futures_bias,
        pdh=raw.prev_day_high,
        pdl=raw.prev_day_low,
        prev_close=raw.prev_day_close,
        atr=raw.atr if hasattr(raw, "atr") else 100.0,
        session_state=get_session_state(),
        minutes_remaining=session_minutes_remaining(),
        day_of_week=dow,
        day_name=day_names[dow],
        day_strength=raw.day_strength,
        fetched_at=datetime.now(timezone.utc),
    )
    return ctx
