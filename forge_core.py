"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE V20                      ║
║              forge_core.py — THE FOUNDATION                                 ║
║                                                                              ║
║  CONTAINS:                                                                   ║
║    1. DST-aware UTC→ET conversion (Bug #1 fix)                            ║
║    2. 8-state session machine with transition detection                    ║
║    3. Telegram alerts (Bug #15 fix)                                        ║
║    4. Price cache with 2-min TTL (Bug #3 fix)                             ║
║    5. Instrument tracker (ORB, IB, session H/L, close prices)             ║
║    6. Signal + SignalVerdict structures                                     ║
║    7. News blackout intelligence (Bug #17 fix)                             ║
║    8. MarketContext dataclass                                               ║
║    9. CandleStore — rolling candle windows from Polygon (V20 NEW)          ║
║   10. Candlestick pattern detection (V20 NEW)                              ║
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
# ═══════════════════════════════════════════════════════════════════════════════

def is_dst(utc_dt: Optional[datetime] = None) -> bool:
    if utc_dt is None:
        utc_dt = datetime.now(timezone.utc)
    year = utc_dt.year
    march_1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    days_to_sun = (6 - march_1.weekday()) % 7
    dst_start = march_1 + timedelta(days=days_to_sun + 7)
    dst_start = dst_start.replace(hour=7, minute=0, second=0)
    nov_1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    days_to_sun_nov = (6 - nov_1.weekday()) % 7
    dst_end = nov_1 + timedelta(days=days_to_sun_nov)
    dst_end = dst_end.replace(hour=6, minute=0, second=0)
    return dst_start <= utc_dt < dst_end


def utc_to_et(utc_dt: datetime) -> datetime:
    offset = 4 if is_dst(utc_dt) else 5
    return utc_dt - timedelta(hours=offset)


def now_et() -> datetime:
    return utc_to_et(datetime.now(timezone.utc))


def now_et_time() -> dtime:
    return now_et().time()


def is_rth(t: Optional[dtime] = None) -> bool:
    if t is None:
        t = now_et_time()
    return dtime(9, 30) <= t < dtime(16, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. SESSION STATE MACHINE — 8 states
# ═══════════════════════════════════════════════════════════════════════════════

class SessionState(Enum):
    PRE_MARKET    = "PRE_MARKET"
    OPENING_DRIVE = "OPENING_DRIVE"
    IB_FORMATION  = "IB_FORMATION"
    MID_MORNING   = "MID_MORNING"
    LUNCH_CHOP    = "LUNCH_CHOP"
    AFTERNOON     = "AFTERNOON"
    POWER_HOUR    = "POWER_HOUR"
    CLOSE_POSITION = "CLOSE_POSITION"
    CLOSED        = "CLOSED"


def get_session_state(t: Optional[dtime] = None) -> SessionState:
    if t is None:
        t = now_et_time()
    if t < dtime(9, 30):   return SessionState.PRE_MARKET
    elif t < dtime(9, 50): return SessionState.OPENING_DRIVE
    elif t < dtime(10, 30): return SessionState.IB_FORMATION
    elif t < dtime(11, 30): return SessionState.MID_MORNING
    elif t < dtime(13, 0):  return SessionState.LUNCH_CHOP
    elif t < dtime(15, 0):  return SessionState.AFTERNOON
    elif t < dtime(15, 50): return SessionState.POWER_HOUR
    elif t < dtime(16, 0):  return SessionState.CLOSE_POSITION
    else:                   return SessionState.CLOSED


# ── Setup suitability weights for ALL 25+ setups ──────────────────────────────
_STATE_WEIGHTS: dict[str, dict[SessionState, float]] = {
    # === EXISTING 8 ===
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
    # === V20 NEW SETUPS ===
    "OD-01": {
        SessionState.OPENING_DRIVE: 1.0,
    },
    "GAP-01": {
        SessionState.OPENING_DRIVE: 1.0, SessionState.IB_FORMATION: 0.6,
    },
    "GAP-02": {
        SessionState.OPENING_DRIVE: 1.0,
    },
    "IB-01": {
        SessionState.MID_MORNING: 1.0, SessionState.LUNCH_CHOP: 0.7,
        SessionState.AFTERNOON: 0.8,
    },
    "IB-02": {
        SessionState.MID_MORNING: 0.9, SessionState.LUNCH_CHOP: 1.0,
        SessionState.AFTERNOON: 0.8,
    },
    "VWAP-01": {
        SessionState.IB_FORMATION: 0.8, SessionState.MID_MORNING: 1.0,
        SessionState.LUNCH_CHOP: 0.6, SessionState.AFTERNOON: 0.9,
        SessionState.POWER_HOUR: 0.7,
    },
    "VWAP-02": {
        SessionState.IB_FORMATION: 0.8, SessionState.MID_MORNING: 1.0,
        SessionState.LUNCH_CHOP: 0.6, SessionState.AFTERNOON: 0.9,
        SessionState.POWER_HOUR: 0.7,
    },
    "VWAP-03": {
        SessionState.IB_FORMATION: 0.7, SessionState.MID_MORNING: 1.0,
        SessionState.LUNCH_CHOP: 0.5, SessionState.AFTERNOON: 0.8,
    },
    "LVL-01": {
        SessionState.OPENING_DRIVE: 0.6, SessionState.IB_FORMATION: 0.9,
        SessionState.MID_MORNING: 1.0, SessionState.LUNCH_CHOP: 0.5,
        SessionState.AFTERNOON: 0.8, SessionState.POWER_HOUR: 0.6,
    },
    "LVL-02": {
        SessionState.OPENING_DRIVE: 0.5, SessionState.IB_FORMATION: 0.7,
        SessionState.MID_MORNING: 0.9, SessionState.LUNCH_CHOP: 1.0,
        SessionState.AFTERNOON: 0.8, SessionState.POWER_HOUR: 0.6,
    },
    "MID-01": {
        SessionState.LUNCH_CHOP: 1.0, SessionState.AFTERNOON: 0.3,
    },
    "MID-02": {
        SessionState.AFTERNOON: 1.0,
    },
    "PWR-01": {
        SessionState.POWER_HOUR: 1.0,
    },
    "PWR-02": {
        SessionState.POWER_HOUR: 1.0,
    },
    "PWR-03": {
        SessionState.POWER_HOUR: 1.0,
    },
    # Multi-instrument
    "ES-ORD-02": {
        SessionState.OPENING_DRIVE: 0.5, SessionState.IB_FORMATION: 1.0,
        SessionState.MID_MORNING: 0.8,
    },
    "GOLD-CORR-01": {
        SessionState.IB_FORMATION: 0.7, SessionState.MID_MORNING: 1.0,
        SessionState.LUNCH_CHOP: 0.5, SessionState.AFTERNOON: 0.9,
        SessionState.POWER_HOUR: 0.6,
    },
}


def get_state_weight(setup_id: str, state: SessionState) -> float:
    weights = _STATE_WEIGHTS.get(setup_id, {})
    return weights.get(state, 0.0)


def session_minutes_remaining(t: Optional[dtime] = None) -> float:
    if t is None:
        t = now_et_time()
    close = dtime(16, 0)
    if t >= close:
        return 0.0
    now_mins = t.hour * 60 + t.minute
    close_mins = 16 * 60
    return max(0.0, close_mins - now_mins)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. TELEGRAM ALERTS — Bug #15 FIX
# ═══════════════════════════════════════════════════════════════════════════════

_TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5264397522")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def send_telegram(text: str) -> None:
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
# 4. PRICE CACHE — Bug #3 FIX
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class CachedPrice:
    bid: float
    ask: float
    timestamp: datetime
    max_age: float = 120.0

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
    def __init__(self, max_age_seconds: int = 120):
        self._cache: dict[str, CachedPrice] = {}
        self._max_age = max_age_seconds

    def update(self, instrument: str, bid: float, ask: float) -> None:
        self._cache[instrument] = CachedPrice(
            bid=bid, ask=ask, timestamp=datetime.now(timezone.utc), max_age=self._max_age,
        )

    def get(self, instrument: str) -> Optional[CachedPrice]:
        cp = self._cache.get(instrument)
        if cp is None or cp.stale:
            return None
        return cp

    def get_mid(self, instrument: str) -> Optional[float]:
        cp = self.get(instrument)
        return cp.mid if cp else None

    def age(self, instrument: str) -> float:
        cp = self._cache.get(instrument)
        return cp.age_seconds if cp else float("inf")


_price_cache = PriceCache(max_age_seconds=120)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. INSTRUMENT TRACKER — ORB, IB, session H/L, close prices, VWAP
# ═══════════════════════════════════════════════════════════════════════════════

class InstrumentTracker:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.orb_high: Optional[float] = None
        self.orb_low: Optional[float] = None
        self.orb_locked: bool = False
        self.orb_valid: bool = False
        self._orb_tick_count: int = 0
        self._orb_tracking: bool = False

        self.ib_high: float = 0.0
        self.ib_low: float = float("inf")
        self.ib_locked: bool = False
        self.ib_direction: Optional[str] = None
        self._ib_tick_count: int = 0

        self.session_high: Optional[float] = None
        self.session_low: Optional[float] = None
        self.open_price: Optional[float] = None

        self.price_history: list[float] = []
        self.close_prices: list[float] = []
        self.volume_history: list[float] = []
        self.last_close: Optional[float] = None
        self._last_close_time: Optional[datetime] = None
        self._close_interval_sec: float = 300

        # VWAP — calculated from FORGE's own NQ price data (Bug 1 fix)
        # Uses spread as volume proxy. NEVER fed from QQQ/Polygon candle prices.
        self.vwap: Optional[float] = None
        self._cumulative_tpv: float = 0.0   # sum(price * spread_volume)
        self._cumulative_vol: float = 0.0   # sum(spread_volume)

        # V20: Lunch range tracking for MID-02
        self.lunch_high: Optional[float] = None
        self.lunch_low: Optional[float] = None

        # V20: 3-min candle tracking for GAP-02
        self._first_3m_candles: list[dict] = []  # {open, close, high, low}
        self._3m_start: Optional[datetime] = None

    def update(self, bid: float, ask: float, ctx: "MarketContext") -> None:
        mid = (bid + ask) / 2.0
        spread = ask - bid
        t = now_et_time()

        if mid <= 0:
            return

        if self.open_price is None and is_rth(t):
            self.open_price = mid

        if is_rth(t):
            if self.session_high is None or mid > self.session_high:
                self.session_high = mid
            if self.session_low is None or mid < self.session_low:
                self.session_low = mid

            # Bug 1 FIX: Calculate VWAP from FORGE's own NQ prices
            # spread as volume proxy — wider spread = less liquidity = less weight
            # invert spread so tight spreads get MORE weight
            vol_proxy = max(0.01, 1.0 / max(spread, 0.01))
            self._cumulative_tpv += mid * vol_proxy
            self._cumulative_vol += vol_proxy
            if self._cumulative_vol > 0:
                self.vwap = self._cumulative_tpv / self._cumulative_vol

        # V20: Track lunch range for MID-02
        if dtime(12, 0) <= t < dtime(13, 0):
            if self.lunch_high is None or mid > self.lunch_high:
                self.lunch_high = mid
            if self.lunch_low is None or mid < self.lunch_low:
                self.lunch_low = mid

        self.price_history.append(mid)
        self.price_history = self.price_history[-200:]
        self.volume_history.append(spread)
        self.volume_history = self.volume_history[-200:]

        now = datetime.now(timezone.utc)
        if self._last_close_time is None:
            self._last_close_time = now
        elif (now - self._last_close_time).total_seconds() >= self._close_interval_sec:
            self.close_prices.append(mid)
            self.close_prices = self.close_prices[-50:]
            self.last_close = mid
            self._last_close_time = now

        # ── ORB Tracking ─────────────────────────────────────────────────────
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
            self.orb_valid = (self._orb_tick_count >= 5 and orb_range >= 5.0)
            if self.orb_valid:
                logger.info("[ORB] Locked: H=%.2f L=%.2f Range=%.1fpts (%d ticks)",
                           self.orb_high, self.orb_low, orb_range, self._orb_tick_count)
            else:
                logger.info("[ORB] INVALID: Range=%.1fpts ticks=%d", orb_range, self._orb_tick_count)

        # ── IB Tracking ──────────────────────────────────────────────────────
        if dtime(9, 30) <= t < dtime(10, 30) and not self.ib_locked:
            self._ib_tick_count += 1
            if mid > self.ib_high:
                self.ib_high = mid
            if mid < self.ib_low:
                self.ib_low = mid

        if t >= dtime(10, 30) and not self.ib_locked and self._ib_tick_count >= 5:
            self.ib_locked = True
            ib_range = self.ib_high - self.ib_low if self.ib_low != float("inf") else 0
            if ib_range < 5.0:
                self.ib_direction = "none"
                logger.info("[IB] Locked but DEGENERATE range (%.1fpts)", ib_range)
            else:
                logger.info("[IB] Locked: H=%.2f L=%.2f Range=%.1fpts",
                           self.ib_high, self.ib_low, ib_range)

        if self.ib_locked and self.ib_direction is None:
            ib_range = self.ib_high - self.ib_low if self.ib_low != float("inf") else 0
            if ib_range >= 5.0:
                if mid > self.ib_high:
                    self.ib_direction = "long"
                    logger.info("[IB] High broke → LONG bias")
                elif mid < self.ib_low:
                    self.ib_direction = "short"
                    logger.info("[IB] Low broke → SHORT bias")


# ═══════════════════════════════════════════════════════════════════════════════
# 6. SIGNAL + VERDICT STRUCTURES
# ═══════════════════════════════════════════════════════════════════════════════

class SignalVerdict(Enum):
    CONFIRMED = "CONFIRMED"
    PENDING   = "PENDING"
    REJECTED  = "REJECTED"


@dataclass
class Signal:
    setup_id:     str
    verdict:      SignalVerdict
    direction:    Optional[str]
    entry_price:  Optional[float]
    stop_loss:    Optional[float]
    take_profit:  Optional[float]
    conviction:   float
    reason:       str


# ═══════════════════════════════════════════════════════════════════════════════
# 7. NEWS BLACKOUT INTELLIGENCE — Bug #17 FIX
# ═══════════════════════════════════════════════════════════════════════════════

HIGH_IMPACT_TIMES_ET = [dtime(8, 30), dtime(10, 0), dtime(14, 0), dtime(14, 30)]


def is_news_blackout(blackout_before: int = 5, blackout_after: int = 5) -> bool:
    t = now_et_time()
    now_mins = t.hour * 60 + t.minute
    for event_time in HIGH_IMPACT_TIMES_ET:
        event_mins = event_time.hour * 60 + event_time.minute
        if (event_mins - blackout_before) <= now_mins <= (event_mins + blackout_after):
            return True
    return False


def minutes_to_next_news() -> Optional[float]:
    t = now_et_time()
    now_mins = t.hour * 60 + t.minute
    for event_time in HIGH_IMPACT_TIMES_ET:
        event_mins = event_time.hour * 60 + event_time.minute
        if event_mins > now_mins:
            return event_mins - now_mins
    return None


def should_close_for_news(close_minutes_before: int = 3) -> bool:
    mins = minutes_to_next_news()
    return mins is not None and 0 < mins <= close_minutes_before


# ═══════════════════════════════════════════════════════════════════════════════
# 8. MARKET CONTEXT — The unified state object
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MarketContext:
    vix: float = 20.0
    vix_regime: str = "NORMAL"
    vix_size_mult: float = 1.0
    futures_pct: float = 0.0
    futures_bias: str = "neutral"
    pdh: float = 0.0
    pdl: float = 0.0
    prev_close: float = 0.0
    ib_high: float = 0.0
    ib_low: float = 0.0
    ib_locked: bool = False
    ib_direction: Optional[str] = None
    atr: float = 100.0
    atr_consumed_pct: float = 0.0
    session_state: SessionState = SessionState.CLOSED
    minutes_remaining: float = 0.0
    day_of_week: int = 0
    day_name: str = "Monday"
    day_strength: float = 1.0
    fetched_at: Optional[datetime] = None
    # V20: Regime classification
    regime: str = "NORMAL"          # TREND / CHOP / NORMAL / REVERSAL
    regime_bias: str = "neutral"    # long / short / neutral
    # V20: Multi-timeframe
    mtf_trend_m15: str = "neutral"  # long / short / neutral
    mtf_trend_h1: str = "neutral"
    mtf_m5_confirms: bool = False

    def sync_from_tracker(self, tracker: InstrumentTracker) -> None:
        self.ib_high = tracker.ib_high
        self.ib_low = tracker.ib_low if tracker.ib_low != float("inf") else 0.0
        self.ib_locked = tracker.ib_locked
        self.ib_direction = tracker.ib_direction


# ═══════════════════════════════════════════════════════════════════════════════
# 9. CANDLE STORE — V20 Multi-Timeframe from Polygon
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Candle:
    """A single OHLCV candle."""
    timestamp: float   # epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


class CandleStore:
    """
    Maintains rolling windows of candle data per instrument per timeframe.
    Fed from Polygon API responses.
    """

    def __init__(self):
        # {instrument: {timeframe: [Candle, ...]}}
        self._candles: dict[str, dict[str, list[Candle]]] = {}
        self._last_fetch: dict[str, datetime] = {}  # instrument → last fetch time
        self._max_candles = {"M1": 200, "M5": 100, "M15": 60, "H1": 30}

    def store(self, instrument: str, timeframe: str, candles: list[Candle]) -> None:
        if instrument not in self._candles:
            self._candles[instrument] = {}
        if timeframe not in self._candles[instrument]:
            self._candles[instrument][timeframe] = []

        existing = self._candles[instrument][timeframe]
        existing_ts = {c.timestamp for c in existing}
        for c in candles:
            if c.timestamp not in existing_ts:
                existing.append(c)

        existing.sort(key=lambda c: c.timestamp)
        max_keep = self._max_candles.get(timeframe, 100)
        self._candles[instrument][timeframe] = existing[-max_keep:]

    def get(self, instrument: str, timeframe: str, n: int = 50) -> list[Candle]:
        try:
            return self._candles[instrument][timeframe][-n:]
        except KeyError:
            return []

    def latest(self, instrument: str, timeframe: str) -> Optional[Candle]:
        candles = self.get(instrument, timeframe, 1)
        return candles[0] if candles else None

    def should_fetch(self, instrument: str, interval_sec: int = 300) -> bool:
        last = self._last_fetch.get(instrument)
        if last is None:
            return True
        return (datetime.now(timezone.utc) - last).total_seconds() >= interval_sec

    def mark_fetched(self, instrument: str) -> None:
        self._last_fetch[instrument] = datetime.now(timezone.utc)


# Global candle store
_candle_store = CandleStore()


def get_candle_store() -> CandleStore:
    return _candle_store


# ═══════════════════════════════════════════════════════════════════════════════
# 10. CANDLESTICK PATTERN DETECTION — V20 NEW
# ═══════════════════════════════════════════════════════════════════════════════

def detect_candlestick_pattern(candles: list[Candle]) -> Optional[str]:
    """
    Detect candlestick patterns from M1 candle data.
    Returns pattern name or None.
    """
    if len(candles) < 3:
        return None

    c_prev = candles[-2]
    c_curr = candles[-1]

    body_curr = c_curr.body
    range_curr = c_curr.range
    body_prev = c_prev.body

    if range_curr <= 0 or body_prev <= 0:
        return None

    # Bullish engulfing
    if (c_curr.is_bullish and c_prev.is_bearish and
        c_curr.close > c_prev.open and c_curr.open < c_prev.close and
        body_curr > body_prev):
        return "BULLISH_ENGULFING"

    # Bearish engulfing
    if (c_curr.is_bearish and c_prev.is_bullish and
        c_curr.close < c_prev.open and c_curr.open > c_prev.close and
        body_curr > body_prev):
        return "BEARISH_ENGULFING"

    # Hammer (bullish)
    if (c_curr.lower_wick > body_curr * 2 and
        c_curr.upper_wick < body_curr * 0.5 and
        body_curr > 0):
        return "HAMMER"

    # Shooting star (bearish)
    if (c_curr.upper_wick > body_curr * 2 and
        c_curr.lower_wick < body_curr * 0.5 and
        body_curr > 0):
        return "SHOOTING_STAR"

    # Doji
    if range_curr > 0 and body_curr < range_curr * 0.1:
        return "DOJI"

    # Three white soldiers
    if len(candles) >= 3:
        c3 = candles[-3]
        if (c3.is_bullish and c_prev.is_bullish and c_curr.is_bullish and
            c_prev.close > c3.close and c_curr.close > c_prev.close):
            return "THREE_WHITE_SOLDIERS"

    # Three black crows
    if len(candles) >= 3:
        c3 = candles[-3]
        if (c3.is_bearish and c_prev.is_bearish and c_curr.is_bearish and
            c_prev.close < c3.close and c_curr.close < c_prev.close):
            return "THREE_BLACK_CROWS"

    return None


def get_m15_trend(candles: list[Candle]) -> str:
    """Determine trend from M15 candles: higher highs/lows = uptrend."""
    if len(candles) < 4:
        return "neutral"
    recent = candles[-4:]
    higher_highs = all(recent[i].high > recent[i-1].high for i in range(1, len(recent)))
    higher_lows = all(recent[i].low > recent[i-1].low for i in range(1, len(recent)))
    lower_highs = all(recent[i].high < recent[i-1].high for i in range(1, len(recent)))
    lower_lows = all(recent[i].low < recent[i-1].low for i in range(1, len(recent)))

    if higher_highs and higher_lows:
        return "long"
    elif lower_highs and lower_lows:
        return "short"
    elif higher_lows:
        return "long"
    elif lower_highs:
        return "short"
    return "neutral"


def get_h1_trend(candles: list[Candle]) -> str:
    """Determine big-picture trend from H1 candles."""
    if len(candles) < 3:
        return "neutral"
    recent = candles[-3:]
    if all(c.is_bullish for c in recent):
        return "long"
    elif all(c.is_bearish for c in recent):
        return "short"
    avg_close = sum(c.close for c in recent) / len(recent)
    if recent[-1].close > avg_close * 1.001:
        return "long"
    elif recent[-1].close < avg_close * 0.999:
        return "short"
    return "neutral"


def m5_confirms_m1(m1_direction: str, m5_candles: list[Candle]) -> bool:
    """Check if M5 candle direction confirms M1 signal direction."""
    if not m5_candles:
        return True  # no data = don't block
    last_m5 = m5_candles[-1]
    if m1_direction == "long" and last_m5.is_bullish:
        return True
    if m1_direction == "short" and last_m5.is_bearish:
        return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# 11. EVIDENCE RE-EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

from forge_evidence import TradeFingerprint, EvidenceLogger  # noqa: E402

_evidence = EvidenceLogger()


# ═══════════════════════════════════════════════════════════════════════════════
# 12. MARKET DATA FETCH (delegates to forge_market)
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_market_context() -> MarketContext:
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
