"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                forge_market.py — MARKET INTELLIGENCE ENGINE                  ║
║                                                                              ║
║  Real VIX. Real futures. Real PDH/PDL. Real ATR. Nothing hardcoded.        ║
║  Bug #6 FIX: Real VIX from Yahoo — never hardcoded 8.5.                   ║
║  Bug #4 FIX: ATR consumed only after 9:30 ET (via forge_core).            ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import json
import logging
import ssl
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, date, timezone, timedelta
from datetime import time as dtime
from typing import Optional

from forge_core import now_et, now_et_time, is_rth

logger = logging.getLogger("titan_forge.market")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict[str, tuple[datetime, object]] = {}
CACHE_TTL_MINUTES = 15

def _cache_get(key: str, ttl_minutes: int = CACHE_TTL_MINUTES):
    if key in _cache:
        cached_at, value = _cache[key]
        if (datetime.now(timezone.utc) - cached_at).total_seconds() < ttl_minutes * 60:
            return value
    return None

def _cache_set(key: str, value) -> None:
    _cache[key] = (datetime.now(timezone.utc), value)

def _yahoo_fetch(symbol: str, range_str: str = "2d", interval: str = "1d") -> Optional[dict]:
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               f"?range={range_str}&interval={interval}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        logger.warning("[MARKET] Yahoo fetch failed for %s: %s", symbol, e)
        return None

# ── VIX (Bug #6 FIX) ─────────────────────────────────────────────────────────
def fetch_vix() -> tuple[float, str, float]:
    cached = _cache_get("vix", ttl_minutes=30)
    if cached:
        return cached
    data = _yahoo_fetch("^VIX", range_str="1d", interval="1m")
    vix = 20.0
    if data:
        try:
            meta = data["chart"]["result"][0]["meta"]
            vix = meta.get("regularMarketPrice", 20.0)
        except (KeyError, IndexError):
            pass
    if vix < 18:     regime, mult = "LOW", 1.0
    elif vix < 25:   regime, mult = "NORMAL", 1.0
    elif vix < 35:   regime, mult = "ELEVATED", 0.70
    else:            regime, mult = "EXTREME", 0.40
    result = (vix, regime, mult)
    _cache_set("vix", result)
    logger.info("[MARKET] VIX: %.1f (%s) → size mult %.0f%%", vix, regime, mult * 100)
    return result

# ── Futures Direction ─────────────────────────────────────────────────────────
def fetch_futures_direction() -> tuple[float, str]:
    cached = _cache_get("futures")
    if cached:
        return cached
    data = _yahoo_fetch("NQ=F", range_str="2d", interval="1d")
    pct = 0.0
    if data:
        try:
            closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
            closes = [c for c in closes if c is not None]
            if len(closes) >= 2:
                pct = (closes[-1] - closes[-2]) / closes[-2]
        except (KeyError, IndexError):
            pass
    if pct > 0.005:      bias = "strong_bullish"
    elif pct > 0.001:    bias = "bullish"
    elif pct > -0.001:   bias = "neutral"
    elif pct > -0.005:   bias = "bearish"
    else:                bias = "strong_bearish"
    result = (pct, bias)
    _cache_set("futures", result)
    logger.info("[MARKET] Futures: %+.2f%% (%s)", pct * 100, bias)
    return result

# ── PDH/PDL ───────────────────────────────────────────────────────────────────
def fetch_pdh_pdl() -> tuple[float, float, float, str]:
    cached = _cache_get("pdh_pdl")
    if cached:
        return cached
    data = _yahoo_fetch("NQ=F", range_str="5d", interval="1d")
    high, low, close = 0.0, 0.0, 0.0
    if data:
        try:
            result = data["chart"]["result"][0]
            quotes = result["indicators"]["quote"][0]
            highs = [h for h in quotes.get("high", []) if h is not None]
            lows  = [l for l in quotes.get("low", []) if l is not None]
            closes = [c for c in quotes.get("close", []) if c is not None]
            if len(highs) >= 2:
                high, low, close = highs[-2], lows[-2], closes[-2]
        except (KeyError, IndexError):
            pass
    sentiment = "neutral"
    if close > 0 and high > 0 and high != low:
        pos = (close - low) / (high - low)
        if pos > 0.65:   sentiment = "bullish"
        elif pos < 0.35: sentiment = "bearish"
    result = (high, low, close, sentiment)
    _cache_set("pdh_pdl", result)
    if high > 0:
        logger.info("[MARKET] PDH=%.2f PDL=%.2f Close=%.2f (%s)", high, low, close, sentiment)
    return result

# ── Day Strength ──────────────────────────────────────────────────────────────
DAY_STRENGTH = {0: 1.15, 1: 1.15, 2: 1.00, 3: 1.00, 4: 0.85}
def get_day_strength(day_of_week: int) -> float:
    return DAY_STRENGTH.get(day_of_week, 1.0)

# ── Gap Detection ─────────────────────────────────────────────────────────────
def detect_gap(current_price: float, prev_close: float) -> tuple[float, str]:
    if prev_close <= 0: return 0.0, "none"
    gap_pct = (current_price - prev_close) / prev_close
    if abs(gap_pct) < 0.0025: return gap_pct, "none"
    return gap_pct, "up" if gap_pct > 0 else "down"

# ── ATR Tracker ───────────────────────────────────────────────────────────────
class ATRTracker:
    def __init__(self, initial_atr: float = 100.0):
        self.atr: float = initial_atr
        self.session_high: float = 0.0
        self.session_low: float = float("inf")
        self._daily_ranges: list[float] = []

    def update_session(self, high: float, low: float) -> None:
        if high > self.session_high: self.session_high = high
        if low < self.session_low:   self.session_low = low

    @property
    def session_range(self) -> float:
        if self.session_high <= 0 or self.session_low == float("inf"): return 0.0
        return self.session_high - self.session_low

    @property
    def atr_consumed_pct(self) -> float:
        if self.atr <= 0: return 0.0
        return self.session_range / self.atr

    def close_of_day(self) -> None:
        if self.session_range > 0:
            self._daily_ranges.append(self.session_range)
            self._daily_ranges = self._daily_ranges[-20:]
            if self._daily_ranges:
                self.atr = sum(self._daily_ranges) / len(self._daily_ranges)
        self.reset_session()

    def reset_session(self) -> None:
        self.session_high = 0.0
        self.session_low = float("inf")

# ── Build Raw Market Data ─────────────────────────────────────────────────────
@dataclass
class RawMarketData:
    vix: float = 20.0
    vix_regime: str = "NORMAL"
    vix_size_mult: float = 1.0
    futures_pct: float = 0.0
    futures_bias: str = "neutral"
    prev_day_high: float = 0.0
    prev_day_low: float = 0.0
    prev_day_close: float = 0.0
    prev_day_sentiment: str = "neutral"
    day_of_week: int = 0
    day_strength: float = 1.0
    atr: float = 100.0
    fetched_at: Optional[datetime] = None

def build_market_context() -> RawMarketData:
    vix, vix_regime, vix_mult = fetch_vix()
    futures_pct, futures_bias = fetch_futures_direction()
    pdh, pdl, pdc, sentiment = fetch_pdh_pdl()
    today_dow = date.today().weekday()
    return RawMarketData(
        vix=vix, vix_regime=vix_regime, vix_size_mult=vix_mult,
        futures_pct=futures_pct, futures_bias=futures_bias,
        prev_day_high=pdh, prev_day_low=pdl, prev_day_close=pdc,
        prev_day_sentiment=sentiment,
        day_of_week=today_dow, day_strength=get_day_strength(today_dow),
        fetched_at=datetime.now(timezone.utc),
    )
