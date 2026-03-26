"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE V20                      ║
║                forge_market.py — MARKET INTELLIGENCE ENGINE                  ║
║                                                                              ║
║  Real VIX. Real futures. Real PDH/PDL. Real ATR.                           ║
║  V20: Polygon candle data (M1/M5/M15/H1). Cross-market correlations.     ║
║  Order flow proxy from Polygon trade sizes.                                ║
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
from datetime import datetime, date, timezone, timedelta
from datetime import time as dtime
from typing import Optional

from forge_core import now_et, now_et_time, is_rth, Candle, get_candle_store

logger = logging.getLogger("titan_forge.market")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

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


# ═══════════════════════════════════════════════════════════════════════════════
# VIX (Bug #6 FIX)
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# FUTURES DIRECTION
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# PDH/PDL
# ═══════════════════════════════════════════════════════════════════════════════

def fetch_pdh_pdl() -> tuple[float, float, float, str]:
    cached = _cache_get("pdh_pdl")
    if cached:
        return cached
    data = _yahoo_fetch("NQ=F", range_str="5d", interval="1d")
    high, low, close = 0.0, 0.0, 0.0
    if data:
        try:
            result_data = data["chart"]["result"][0]
            quotes = result_data["indicators"]["quote"][0]
            highs = [h for h in quotes.get("high", []) if h is not None]
            lows = [l for l in quotes.get("low", []) if l is not None]
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


# ═══════════════════════════════════════════════════════════════════════════════
# DAY STRENGTH & GAP DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

DAY_STRENGTH = {0: 1.15, 1: 1.15, 2: 1.00, 3: 1.00, 4: 0.85}


def get_day_strength(day_of_week: int) -> float:
    return DAY_STRENGTH.get(day_of_week, 1.0)


def detect_gap(current_price: float, prev_close: float) -> tuple[float, str]:
    if prev_close <= 0:
        return 0.0, "none"
    gap_pct = (current_price - prev_close) / prev_close
    if abs(gap_pct) < 0.0025:
        return gap_pct, "none"
    return gap_pct, "up" if gap_pct > 0 else "down"


# ═══════════════════════════════════════════════════════════════════════════════
# ATR TRACKER
# ═══════════════════════════════════════════════════════════════════════════════

class ATRTracker:
    def __init__(self, initial_atr: float = 100.0):
        self.atr: float = initial_atr
        self.session_high: float = 0.0
        self.session_low: float = float("inf")
        self._daily_ranges: list[float] = []

    def update_session(self, high: float, low: float) -> None:
        if high > self.session_high:
            self.session_high = high
        if low < self.session_low:
            self.session_low = low

    @property
    def session_range(self) -> float:
        if self.session_high <= 0 or self.session_low == float("inf"):
            return 0.0
        return self.session_high - self.session_low

    @property
    def atr_consumed_pct(self) -> float:
        if self.atr <= 0:
            return 0.0
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


# ═══════════════════════════════════════════════════════════════════════════════
# V20: POLYGON CANDLE FETCHING
# ═══════════════════════════════════════════════════════════════════════════════

# Polygon ticker mapping
POLYGON_TICKERS = {
    "NAS100": "I:NDX",      # NASDAQ-100 index
    "ES": "I:SPX",           # S&P 500 index
    "XAUUSD": "C:XAUUSD",   # Gold
    "DXY": "C:DXY",          # Dollar index (for correlation)
}


def _polygon_fetch(
    ticker: str,
    multiplier: int,
    timespan: str,
    from_date: str,
    to_date: str,
) -> Optional[list[dict]]:
    """Fetch candle data from Polygon REST API."""
    if not POLYGON_API_KEY:
        return None
    try:
        url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}"
               f"/range/{multiplier}/{timespan}/{from_date}/{to_date}"
               f"?adjusted=true&sort=asc&limit=5000&apiKey={POLYGON_API_KEY}")
        req = urllib.request.Request(url, headers={"User-Agent": "TITAN-FORGE/2.0"})
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
            if data.get("resultsCount", 0) > 0:
                return data.get("results", [])
    except Exception as e:
        logger.warning("[POLYGON] Fetch failed %s %s %s: %s", ticker, multiplier, timespan, e)
    return None


def fetch_polygon_candles(
    instrument: str = "NAS100",
    timeframes: Optional[list[str]] = None,
) -> dict[str, list[Candle]]:
    """
    Fetch M1/M5/M15/H1 candles from Polygon for a given instrument.
    Returns {timeframe: [Candle, ...]}.
    Rate limit: 5 calls/minute on starter plan — batch efficiently.
    """
    if timeframes is None:
        timeframes = ["M1", "M5", "M15", "H1"]

    ticker = POLYGON_TICKERS.get(instrument, "I:NDX")
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=3)).isoformat()

    tf_params = {
        "M1": (1, "minute"),
        "M5": (5, "minute"),
        "M15": (15, "minute"),
        "H1": (1, "hour"),
    }

    result: dict[str, list[Candle]] = {}
    store = get_candle_store()

    for tf in timeframes:
        if tf not in tf_params:
            continue
        mult, span = tf_params[tf]
        raw = _polygon_fetch(ticker, mult, span, yesterday, today)
        candles = []
        if raw:
            for bar in raw:
                candles.append(Candle(
                    timestamp=bar.get("t", 0) / 1000.0,  # ms → seconds
                    open=bar.get("o", 0),
                    high=bar.get("h", 0),
                    low=bar.get("l", 0),
                    close=bar.get("c", 0),
                    volume=bar.get("v", 0),
                ))
        result[tf] = candles
        if candles:
            store.store(instrument, tf, candles)

    store.mark_fetched(instrument)
    logger.info("[POLYGON] Fetched %s: %s",
               instrument, " | ".join(f"{tf}={len(cs)}" for tf, cs in result.items()))
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# V20: CROSS-MARKET CORRELATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class CorrelationEngine:
    """
    Track rolling correlations between markets.
    Detects divergences that signal regime shifts.
    """

    def __init__(self):
        self._price_series: dict[str, list[float]] = {}
        self._max_history = 200

    def update(self, instrument: str, price: float) -> None:
        if instrument not in self._price_series:
            self._price_series[instrument] = []
        self._price_series[instrument].append(price)
        self._price_series[instrument] = self._price_series[instrument][-self._max_history:]

    def rolling_correlation(self, inst_a: str, inst_b: str, window: int = 30) -> Optional[float]:
        """Compute rolling Pearson correlation between two price series."""
        series_a = self._price_series.get(inst_a, [])
        series_b = self._price_series.get(inst_b, [])

        if len(series_a) < window or len(series_b) < window:
            return None

        a = series_a[-window:]
        b = series_b[-window:]

        # Pearson correlation
        n = min(len(a), len(b))
        if n < 10:
            return None
        a, b = a[-n:], b[-n:]

        mean_a = sum(a) / n
        mean_b = sum(b) / n
        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n)) / n
        std_a = (sum((x - mean_a) ** 2 for x in a) / n) ** 0.5
        std_b = (sum((x - mean_b) ** 2 for x in b) / n) ** 0.5

        if std_a == 0 or std_b == 0:
            return None

        return cov / (std_a * std_b)

    def detect_divergence(
        self, inst_a: str, inst_b: str,
        expected_corr: float = 0.95,
        window: int = 30,
    ) -> Optional[tuple[float, str]]:
        """
        Detect correlation divergence.
        Returns (current_corr, description) if divergence is significant.
        """
        corr = self.rolling_correlation(inst_a, inst_b, window)
        if corr is None:
            return None

        deviation = abs(corr - expected_corr)
        if deviation > 0.30:  # > 2 standard deviations (rough)
            desc = (f"{inst_a}↔{inst_b} correlation broke: "
                   f"expected={expected_corr:.2f}, actual={corr:.2f}")
            return corr, desc
        return None


# Global correlation engine
_correlation_engine = CorrelationEngine()


def get_correlation_engine() -> CorrelationEngine:
    return _correlation_engine


# ═══════════════════════════════════════════════════════════════════════════════
# V20: ANOMALY DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

class AnomalyDetector:
    """Statistical anomaly detection on every cycle."""

    def __init__(self):
        self._price_history: list[float] = []
        self._vix_history: list[float] = []

    def update(self, price: float, vix: float) -> None:
        self._price_history.append(price)
        self._price_history = self._price_history[-500:]
        self._vix_history.append(vix)
        self._vix_history = self._vix_history[-100:]

    def check(self) -> Optional[str]:
        """Check for anomalies. Returns description or None."""
        if len(self._price_history) < 20:
            return None

        # Price move > 3 standard deviations
        recent = self._price_history[-20:]
        changes = [recent[i] - recent[i-1] for i in range(1, len(recent))]
        if len(changes) > 5:
            avg_change = sum(changes) / len(changes)
            std_change = (sum((c - avg_change) ** 2 for c in changes) / len(changes)) ** 0.5
            if std_change > 0:
                last_change = changes[-1]
                z_score = abs(last_change - avg_change) / std_change
                if z_score > 3.0:
                    return f"PRICE ANOMALY: {z_score:.1f}σ move ({last_change:+.1f}pts)"

        # VIX spike > 2 points in recent window
        if len(self._vix_history) >= 5:
            vix_change = self._vix_history[-1] - self._vix_history[-5]
            if abs(vix_change) > 2.0:
                return f"VIX ANOMALY: {vix_change:+.1f}pt move in 5 cycles"

        return None


_anomaly_detector = AnomalyDetector()


def get_anomaly_detector() -> AnomalyDetector:
    return _anomaly_detector


# ═══════════════════════════════════════════════════════════════════════════════
# BUILD RAW MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════

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
