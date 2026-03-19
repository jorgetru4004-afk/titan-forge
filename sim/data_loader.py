"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                sim/data_loader.py — Section 12 Simulation Engine            ║
║                                                                              ║
║  DATA LOADER — Polygon.io Historical Data                                   ║
║  Loads 2021–2024 training data + 2024–2025 out-of-sample validation.        ║
║  Supports all instruments traded across all 5 firms.                        ║
║  Overfitting protection: train/validate split enforced here.                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import csv
import json
import logging
import math
import os
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("titan_forge.sim.data_loader")

# ─────────────────────────────────────────────────────────────────────────────
# TRAINING / VALIDATION SPLIT  (Section 12: overfitting protection)
# ─────────────────────────────────────────────────────────────────────────────
TRAIN_START = date(2021, 1, 1)
TRAIN_END   = date(2023, 12, 31)   # 3 years training
VAL_START   = date(2024, 1, 1)
VAL_END     = date(2025, 8, 31)    # Out-of-sample validation

# P-12: Four required regime date ranges
REGIME_WINDOWS: dict[str, tuple[date, date]] = {
    "trending_bull":    (date(2023, 1,  1),  date(2023, 3, 31)),   # Q1 2023
    "trending_bear":    (date(2022, 6,  1),  date(2022, 9, 30)),   # Mid-2022
    "choppy_ranging":   (date(2023, 8,  1),  date(2023, 10, 31)),  # Aug-Oct 2023
    "high_vol_crisis":  (date(2020, 2, 20),  date(2020, 4, 30)),   # COVID crash
}


@dataclass
class OHLCV:
    """One price bar: Open, High, Low, Close, Volume + derived fields."""
    timestamp:  datetime
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     float
    vwap:       float = 0.0    # Volume-weighted average price
    atr:        float = 0.0    # ATR at this bar (computed rolling)
    regime:     str   = ""     # Set by training_runner after loading

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def bar_date(self) -> date:
        return self.timestamp.date()


@dataclass
class DailySession:
    """All bars for one trading day, with session-level metrics."""
    session_date:   date
    instrument:     str
    bars:           list[OHLCV]
    session_open:   float
    session_high:   float
    session_low:    float
    session_close:  float
    total_volume:   float
    vwap:           float
    atr_daily:      float
    regime:         str = ""

    @property
    def is_valid(self) -> bool:
        """True if this session has enough data to trade."""
        return len(self.bars) >= 10 and self.total_volume > 0


class DataLoader:
    """
    Loads and caches historical OHLCV data for simulation training.

    Two modes:
        1. LIVE:    Fetches from Polygon.io API (requires POLYGON_API_KEY env var)
        2. SYNTHETIC: Generates statistically realistic price data for testing
                      (used when no API key is present — development/CI mode)

    Section 12: Train 2021–2024. Validate 2024–2025 out-of-sample.
    """

    # Instruments per firm for simulation coverage
    INSTRUMENTS: dict[str, list[str]] = {
        "equities":  ["SPY", "QQQ", "IWM"],
        "futures":   ["ES", "NQ", "RTY"],
        "forex":     ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"],
        "indices":   ["US30", "US500", "US100"],
    }

    def __init__(self, polygon_api_key: Optional[str] = None):
        self._api_key   = polygon_api_key or os.environ.get("POLYGON_API_KEY")
        self._cache:    dict[str, list[OHLCV]] = {}
        self._mode      = "LIVE" if self._api_key else "SYNTHETIC"
        logger.info("[SIM][DataLoader] Mode: %s", self._mode)

    # ── PUBLIC API ────────────────────────────────────────────────────────────

    def load_training_data(
        self,
        instrument:  str,
        start:       Optional[date] = None,
        end:         Optional[date] = None,
        timeframe:   str = "5min",
    ) -> list[OHLCV]:
        """
        Load training data. Default: 2021-01-01 to 2023-12-31.
        Section 12: train on this range, never validate on it.
        """
        start = start or TRAIN_START
        end   = end   or TRAIN_END
        return self._load(instrument, start, end, timeframe, split="train")

    def load_validation_data(
        self,
        instrument:  str,
        start:       Optional[date] = None,
        end:         Optional[date] = None,
        timeframe:   str = "5min",
    ) -> list[OHLCV]:
        """
        Load out-of-sample validation data. Default: 2024-01-01 to 2025-08-31.
        Section 12 overfitting protection: never train on this range.
        """
        start = start or VAL_START
        end   = end   or VAL_END
        return self._load(instrument, start, end, timeframe, split="validate")

    def load_regime_window(self, regime_name: str, instrument: str) -> list[OHLCV]:
        """
        P-12: Load specific historical regime window.
        One of: trending_bull, trending_bear, choppy_ranging, high_vol_crisis
        """
        if regime_name not in REGIME_WINDOWS:
            raise ValueError(
                f"Unknown regime: {regime_name}. "
                f"Valid: {list(REGIME_WINDOWS.keys())}"
            )
        start, end = REGIME_WINDOWS[regime_name]
        bars = self._load(instrument, start, end, "5min", split="regime")
        for bar in bars:
            bar.regime = regime_name
        logger.info(
            "[SIM][DataLoader] Regime '%s': %s bars for %s (%s → %s)",
            regime_name, len(bars), instrument, start, end,
        )
        return bars

    def to_daily_sessions(self, bars: list[OHLCV], instrument: str) -> list[DailySession]:
        """Group intraday bars into trading sessions for the simulation."""
        by_date: dict[date, list[OHLCV]] = {}
        for bar in bars:
            d = bar.bar_date
            by_date.setdefault(d, []).append(bar)

        sessions = []
        for d in sorted(by_date.keys()):
            day_bars = sorted(by_date[d], key=lambda b: b.timestamp)
            if not day_bars:
                continue

            opens   = [b.open  for b in day_bars]
            highs   = [b.high  for b in day_bars]
            lows    = [b.low   for b in day_bars]
            closes  = [b.close for b in day_bars]
            vols    = [b.volume for b in day_bars]
            total_v = sum(vols)

            # VWAP = sum(price × volume) / sum(volume)
            vwap = (
                sum(b.typical_price * b.volume for b in day_bars) / total_v
                if total_v > 0 else (opens[0] + closes[-1]) / 2
            )

            # ATR (daily): average of last 14 ranges
            atr = sum(b.range for b in day_bars[-14:]) / min(14, len(day_bars))

            session = DailySession(
                session_date=d, instrument=instrument,
                bars=day_bars,
                session_open=opens[0], session_high=max(highs),
                session_low=min(lows), session_close=closes[-1],
                total_volume=total_v, vwap=round(vwap, 5),
                atr_daily=round(atr, 5),
                regime=day_bars[0].regime if day_bars else "",
            )
            sessions.append(session)

        return sessions

    # ── INTERNAL ─────────────────────────────────────────────────────────────

    def _load(
        self, instrument: str, start: date, end: date,
        timeframe: str, split: str,
    ) -> list[OHLCV]:
        cache_key = f"{instrument}-{start}-{end}-{timeframe}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if self._mode == "LIVE":
            bars = self._fetch_polygon(instrument, start, end, timeframe)
        else:
            bars = self._generate_synthetic(instrument, start, end, timeframe)

        # Compute rolling ATR on loaded data
        bars = self._compute_atr(bars)
        self._cache[cache_key] = bars
        logger.info(
            "[SIM][DataLoader] Loaded %d bars for %s [%s → %s] (%s)",
            len(bars), instrument, start, end, split,
        )
        return bars

    def _fetch_polygon(
        self, instrument: str, start: date, end: date, timeframe: str
    ) -> list[OHLCV]:
        """
        Fetch from Polygon.io REST API.
        Requires POLYGON_API_KEY env var.
        """
        try:
            import urllib.request
            multiplier, span = self._parse_timeframe(timeframe)
            url = (
                f"https://api.polygon.io/v2/aggs/ticker/{instrument.upper()}/range"
                f"/{multiplier}/{span}/{start.isoformat()}/{end.isoformat()}"
                f"?adjusted=true&sort=asc&limit=50000&apiKey={self._api_key}"
            )
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read())

            results = data.get("results", [])
            bars = []
            for r in results:
                ts = datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc)
                bars.append(OHLCV(
                    timestamp=ts,
                    open=r["o"], high=r["h"], low=r["l"], close=r["c"],
                    volume=r.get("v", 0), vwap=r.get("vw", 0),
                ))
            return bars

        except Exception as e:
            logger.warning(
                "[SIM][DataLoader] Polygon fetch failed (%s). "
                "Falling back to synthetic data.", e,
            )
            return self._generate_synthetic(instrument, start, end, timeframe)

    def _generate_synthetic(
        self,
        instrument: str,
        start:      date,
        end:        date,
        timeframe:  str,
    ) -> list[OHLCV]:
        """
        Generate statistically realistic OHLCV bars for simulation testing.
        Used in development mode (no API key) and CI testing.

        Produces realistic price series with:
            - Geometric Brownian Motion (GBM) for price evolution
            - Regime-aware volatility (higher vol during crisis periods)
            - Session volume patterns (higher at open/close)
            - ATR comparable to real instruments
        """
        rng   = random.Random(hash(f"{instrument}{start}{end}"))
        bars  = []
        # Instrument starting prices
        base_prices = {
            "ES": 4500.0, "NQ": 15000.0, "RTY": 1800.0, "SPY": 450.0,
            "QQQ": 370.0, "IWM": 180.0, "EURUSD": 1.0900,
            "GBPUSD": 1.2500, "USDJPY": 130.0, "AUDUSD": 0.6800,
            "US30": 34000.0, "US500": 4500.0, "US100": 15000.0,
        }
        price = base_prices.get(instrument.upper(), 1000.0)

        # Volatility parameters
        is_forex = instrument.upper() in ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD")
        daily_vol = 0.0005 if is_forex else 0.008   # Daily return std dev

        current = start
        session_minutes = [5, 15, 30, 60]  # 5-minute bars → 78 per day
        bars_per_day = 78 if timeframe == "5min" else 26

        while current <= end:
            if current.weekday() >= 5:  # Skip weekends
                current += timedelta(days=1)
                continue

            # Check if crisis regime — higher volatility
            in_crisis = (
                date(2020, 2, 20) <= current <= date(2020, 4, 30) or
                date(2022, 6,  1) <= current <= date(2022, 9, 30)
            )
            regime_vol = daily_vol * (2.5 if in_crisis else 1.0)
            bar_vol    = regime_vol / math.sqrt(bars_per_day)

            day_open = price
            for i in range(bars_per_day):
                # GBM price step
                dt     = 1.0 / bars_per_day
                drift  = 0.0001 * dt  # Slight positive drift
                shock  = rng.gauss(0, 1) * bar_vol
                ret    = drift + shock
                close  = price * (1 + ret)

                # Generate OHLC from close
                spread = price * bar_vol * 0.5
                high   = max(price, close) + abs(rng.gauss(0, spread * 0.3))
                low    = min(price, close) - abs(rng.gauss(0, spread * 0.3))

                # Volume: higher at session open and close
                base_vol = 1_000_000 if not is_forex else 100_000
                vol_mult = 2.0 if i < 6 or i > 70 else 1.0
                volume   = base_vol * vol_mult * abs(rng.gauss(1.0, 0.3))

                bar_time = datetime.combine(
                    current,
                    datetime.min.time(),
                    tzinfo=timezone.utc
                ) + timedelta(minutes=i * 5 + 570)  # Start 9:30am ET ≈ 14:30 UTC

                bars.append(OHLCV(
                    timestamp=bar_time,
                    open=round(price, 5), high=round(high, 5),
                    low=round(low, 5), close=round(close, 5),
                    volume=round(volume, 2),
                ))
                price = close

            current += timedelta(days=1)

        return bars

    @staticmethod
    def _compute_atr(bars: list[OHLCV], period: int = 14) -> list[OHLCV]:
        """Compute rolling ATR and attach to each bar."""
        for i, bar in enumerate(bars):
            if i < period:
                bar.atr = bar.range
            else:
                recent   = [b.range for b in bars[i-period:i]]
                bar.atr  = sum(recent) / period
        return bars

    @staticmethod
    def _parse_timeframe(tf: str) -> tuple[int, str]:
        """Parse '5min' → (5, 'minute'), '1h' → (1, 'hour')."""
        if "min" in tf:
            return int(tf.replace("min", "")), "minute"
        elif "h" in tf:
            return int(tf.replace("h", "")), "hour"
        elif "d" in tf:
            return int(tf.replace("d", "")), "day"
        return 5, "minute"

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def cache_size(self) -> int:
        return len(self._cache)
