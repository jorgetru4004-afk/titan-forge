"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║              correlation_entry.py — FORGE-09 + FORGE-70 — Layer 1           ║
║                                                                              ║
║  CORRELATION AWARE ENTRY + MULTI-FIRM CORRELATION GUARD                     ║
║                                                                              ║
║  FORGE-09: Correlation Aware Entry                                           ║
║    Never takes two highly correlated positions simultaneously during         ║
║    evaluation. Correlation ≥ 0.70 = blocked. Period.                         ║
║                                                                              ║
║  FORGE-70: Correlation Guard Multi-Firm                                      ║
║    Prevents all simultaneous evaluations from holding correlated             ║
║    positions at the same time. Cross-account correlation check.             ║
║                                                                              ║
║  Why this matters:                                                           ║
║    Two correlated positions (e.g., ES long + NQ long) = effectively          ║
║    one large position with doubled drawdown risk. During evaluation          ║
║    where drawdown limits are sacred, this is unacceptable. TITAN FORGE       ║
║    self-polices — firms don't know we track this, but we do.                 ║
║                                                                              ║
║  Correlation thresholds:                                                     ║
║    ≥ 0.85 = HIGHLY_CORRELATED — hard block                                  ║
║    ≥ 0.70 = CORRELATED — blocked during evaluation                           ║
║    ≥ 0.50 = MODERATE — warning, reduced size allowed                         ║
║    < 0.50 = LOW — proceed normally                                           ║
║                                                                              ║
║  Pre-loaded correlation matrix: ES, NQ, YM, RTY, Gold, Oil, Bonds,          ║
║  EURUSD, GBPUSD, USDJPY, AUDUSD, SPY, QQQ, IWM, GLD, TLT, VXX             ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Partner | March 19, 2026            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger("titan_forge.correlation_entry")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# Correlation thresholds (absolute value of Pearson correlation coefficient)
CORR_HIGHLY_CORRELATED: float = 0.85   # Hard block
CORR_CORRELATED:        float = 0.70   # Blocked during evaluation
CORR_MODERATE:          float = 0.50   # Warning — size reduced
CORR_LOW:               float = 0.50   # Below this = free to trade

# During funded mode: slightly relaxed (0.80 instead of 0.70)
CORR_FUNDED_THRESHOLD:  float = 0.80


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — CORRELATION MATRIX
# Pre-loaded correlations from historical data.
# These are approximate 1-year rolling correlations under normal conditions.
# ─────────────────────────────────────────────────────────────────────────────

# Correlation lookup: abs(correlation) between two instruments
# Format: {frozenset({inst_a, inst_b}): correlation}
# Note: correlation with self = 1.0 (handled separately)

_CORRELATION_DATA: dict[tuple[str, str], float] = {
    # ── Index Futures ────────────────────────────────────────────────────────
    ("ES",  "NQ"):   0.94,   # S&P 500 vs Nasdaq 100 — very high
    ("ES",  "YM"):   0.95,   # S&P 500 vs Dow Jones
    ("ES",  "RTY"):  0.82,   # S&P 500 vs Russell 2000
    ("NQ",  "YM"):   0.92,   # Nasdaq vs Dow
    ("NQ",  "RTY"):  0.80,
    ("YM",  "RTY"):  0.83,
    # ── Index ETFs ───────────────────────────────────────────────────────────
    ("SPY", "QQQ"):  0.93,
    ("SPY", "IWM"):  0.84,
    ("SPY", "ES"):   0.99,   # ETF tracks futures closely
    ("QQQ", "NQ"):   0.99,
    ("IWM", "RTY"):  0.99,
    ("SPY", "DIA"):  0.96,
    ("QQQ", "IWM"):  0.82,
    # ── Commodities ─────────────────────────────────────────────────────────
    ("GC",  "GLD"):  0.99,   # Gold futures vs GLD ETF
    ("CL",  "USO"):  0.98,   # Oil futures vs USO
    ("GC",  "CL"):   0.35,   # Gold vs Oil — moderate
    ("GC",  "ES"):  -0.15,   # Gold vs S&P — low/negative (risk-off)
    ("CL",  "ES"):   0.45,   # Oil vs S&P — moderate
    # ── Bonds ────────────────────────────────────────────────────────────────
    ("ZN",  "TLT"):  0.97,   # 10-yr futures vs TLT ETF
    ("ZB",  "TLT"):  0.92,   # 30-yr futures vs TLT
    ("ZN",  "ZB"):   0.91,   # 10-yr vs 30-yr
    ("ZN",  "ES"):  -0.40,   # Bonds vs stocks — negative
    ("TLT", "SPY"): -0.40,
    ("TLT", "QQQ"): -0.42,
    # ── Volatility ───────────────────────────────────────────────────────────
    ("VXX", "ES"):  -0.75,   # VXX inverse to ES — treat as correlated but opposite
    ("VXX", "SPY"): -0.74,
    # ── Forex ────────────────────────────────────────────────────────────────
    ("EURUSD", "GBPUSD"):  0.82,   # EUR and GBP move together
    ("EURUSD", "AUDUSD"):  0.68,
    ("EURUSD", "NZDUSD"):  0.72,
    ("EURUSD", "USDJPY"): -0.65,   # EUR/USD vs USD/JPY (inverse USD)
    ("EURUSD", "USDCHF"): -0.90,   # Nearly inverse
    ("GBPUSD", "AUDUSD"):  0.65,
    ("GBPUSD", "USDJPY"): -0.60,
    ("AUDUSD", "NZDUSD"):  0.88,   # Commodity currencies
    ("USDJPY", "USDCHF"):  0.72,
    ("USDJPY", "USDCAD"):  0.60,
    # ── Equity Sectors ───────────────────────────────────────────────────────
    ("XLK",  "QQQ"):  0.92,   # Tech vs Nasdaq
    ("XLK",  "SPY"):  0.88,
    ("XLF",  "SPY"):  0.85,
    ("XLE",  "CL"):   0.78,
    ("XLU",  "TLT"):  0.65,
    # ── Cross-asset ──────────────────────────────────────────────────────────
    ("BTC",  "ETH"):  0.88,   # Crypto
    ("ES",   "BTC"):  0.50,   # Risk-on correlation
}


def _build_correlation_matrix() -> dict[frozenset, float]:
    """Build a bidirectional correlation lookup from the raw data."""
    matrix: dict[frozenset, float] = {}
    for (a, b), corr in _CORRELATION_DATA.items():
        key = frozenset({a.upper(), b.upper()})
        matrix[key] = abs(corr)  # Always use absolute value
    return matrix


_CORRELATION_MATRIX: dict[frozenset, float] = _build_correlation_matrix()


def get_correlation(inst_a: str, inst_b: str) -> float:
    """
    Get the absolute correlation between two instruments.
    Returns 1.0 if same instrument, 0.0 if unknown pair.
    """
    a, b = inst_a.upper(), inst_b.upper()
    if a == b:
        return 1.0
    key = frozenset({a, b})
    return _CORRELATION_MATRIX.get(key, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — OPEN POSITION REGISTRY
# Tracks all currently open positions across all accounts.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OpenPosition:
    """An open position currently held in an account."""
    position_id:    str
    account_id:     str
    eval_id:        str         # Evaluation or funded account ID
    firm_id:        str
    instrument:     str         # e.g. "ES", "EURUSD", "NQ"
    direction:      str         # "long" or "short"
    size:           float       # Position size
    entry_price:    float
    unrealized_pnl: float = 0.0
    is_evaluation:  bool  = True


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — CORRELATION CHECK RESULT
# ─────────────────────────────────────────────────────────────────────────────

class CorrelationLevel(Enum):
    HIGHLY_CORRELATED = auto()   # ≥ 0.85 — hard block always
    CORRELATED        = auto()   # ≥ 0.70 — blocked in evaluation
    MODERATE          = auto()   # ≥ 0.50 — warning, size reduced
    LOW               = auto()   # < 0.50 — proceed freely


@dataclass
class CorrelationCheckResult:
    """
    Result of a correlation check before a new position is opened.
    Always check .entry_permitted before executing.
    """
    proposed_instrument:    str
    proposed_account_id:    str
    entry_permitted:        bool
    is_evaluation:          bool
    # Conflicting positions
    blocking_positions:     list[OpenPosition]
    highest_correlation:    float         # Highest correlation found
    highest_corr_instrument: Optional[str]
    correlation_level:      CorrelationLevel
    # Whether FORGE-70 cross-account check found conflicts
    cross_account_conflict: bool
    cross_account_positions: list[OpenPosition]
    # Explanation
    reason:                 str
    size_reduction_pct:     float    # 0.0 = no reduction, 0.50 = cut in half


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — CORRELATION ENTRY GUARD
# FORGE-09 + FORGE-70
# ─────────────────────────────────────────────────────────────────────────────

class CorrelationEntryGuard:
    """
    FORGE-09: Correlation Aware Entry
    FORGE-70: Multi-Firm Correlation Guard

    Checks correlation before every new entry.
    Maintains a live registry of all open positions.

    Usage:
        guard = CorrelationEntryGuard()

        # When a position opens:
        guard.register_open("pos_001", "FTMO-001", "EVAL-001",
                            FirmID.FTMO, "ES", "long", 1.0, 4800.0)

        # Before opening a new position — single account:
        result = guard.check_entry(
            proposed_instrument="NQ",
            account_id="FTMO-001",
            is_evaluation=True,
        )

        if not result.entry_permitted:
            # Cannot open NQ while ES is open (0.94 correlation)
            return

        # Cross-firm check (FORGE-70):
        result = guard.check_entry_cross_firm(
            proposed_instrument="NQ",
            is_evaluation=True,
        )
    """

    def __init__(self, correlation_threshold_evaluation: float = CORR_CORRELATED,
                 correlation_threshold_funded: float = CORR_FUNDED_THRESHOLD):
        self._positions: dict[str, OpenPosition] = {}   # position_id → OpenPosition
        self._eval_threshold  = correlation_threshold_evaluation
        self._funded_threshold = correlation_threshold_funded

    # ── POSITION REGISTRY ─────────────────────────────────────────────────────

    def register_open(
        self,
        position_id: str,
        account_id:  str,
        eval_id:     str,
        firm_id:     str,
        instrument:  str,
        direction:   str,
        size:        float,
        entry_price: float,
        is_evaluation: bool = True,
    ) -> None:
        """Register a new open position in the registry."""
        pos = OpenPosition(
            position_id=position_id,
            account_id=account_id,
            eval_id=eval_id,
            firm_id=firm_id,
            instrument=instrument.upper(),
            direction=direction.lower(),
            size=size,
            entry_price=entry_price,
            is_evaluation=is_evaluation,
        )
        self._positions[position_id] = pos
        logger.debug(
            "[FORGE-09] Position registered: %s | %s %s %s | Account: %s",
            position_id, direction, size, instrument, account_id,
        )

    def close_position(self, position_id: str) -> None:
        """Remove a position from the registry when it closes."""
        if position_id in self._positions:
            pos = self._positions.pop(position_id)
            logger.debug(
                "[FORGE-09] Position closed: %s | %s %s",
                position_id, pos.instrument, pos.account_id,
            )

    def update_unrealized(self, position_id: str, unrealized_pnl: float) -> None:
        if position_id in self._positions:
            self._positions[position_id].unrealized_pnl = unrealized_pnl

    def get_open_positions(
        self,
        account_id: Optional[str] = None,
        firm_id:    Optional[str] = None,
    ) -> list[OpenPosition]:
        """Return open positions, optionally filtered by account or firm."""
        positions = list(self._positions.values())
        if account_id:
            positions = [p for p in positions if p.account_id == account_id]
        if firm_id:
            positions = [p for p in positions if p.firm_id == firm_id]
        return positions

    # ── CORRELATION CHECK — SINGLE ACCOUNT (FORGE-09) ─────────────────────────

    def check_entry(
        self,
        proposed_instrument: str,
        account_id:          str,
        is_evaluation:       bool = True,
    ) -> CorrelationCheckResult:
        """
        FORGE-09: Check if a new entry on this account would create
        a highly correlated position.

        Args:
            proposed_instrument:  The instrument being considered (e.g., "NQ").
            account_id:           Account that would hold the new position.
            is_evaluation:        True = stricter 0.70 threshold.

        Returns:
            CorrelationCheckResult with entry_permitted flag.
        """
        threshold = self._eval_threshold if is_evaluation else self._funded_threshold
        account_positions = self.get_open_positions(account_id=account_id)

        if not account_positions:
            return CorrelationCheckResult(
                proposed_instrument=proposed_instrument.upper(),
                proposed_account_id=account_id,
                entry_permitted=True,
                is_evaluation=is_evaluation,
                blocking_positions=[],
                highest_correlation=0.0,
                highest_corr_instrument=None,
                correlation_level=CorrelationLevel.LOW,
                cross_account_conflict=False,
                cross_account_positions=[],
                reason="No open positions — no correlation conflict.",
                size_reduction_pct=0.0,
            )

        # Find the highest correlation with any open position
        blocking: list[OpenPosition] = []
        highest_corr    = 0.0
        highest_inst    = None

        for pos in account_positions:
            corr = get_correlation(proposed_instrument, pos.instrument)
            if corr > highest_corr:
                highest_corr = corr
                highest_inst = pos.instrument

            if corr >= threshold:
                blocking.append(pos)

        # Determine level
        level = self._correlation_level(highest_corr)

        # Decision
        entry_permitted = len(blocking) == 0

        if not entry_permitted:
            blocking_desc = ", ".join(f"{p.instrument}({p.direction})" for p in blocking)
            reason = (
                f"CORRELATION BLOCKED: {proposed_instrument.upper()} correlates "
                f"{highest_corr:.2f} with open position(s): [{blocking_desc}]. "
                f"Threshold: {threshold:.2f}. "
                f"Cannot hold two correlated positions during evaluation."
            )
            logger.warning(
                "[FORGE-09][%s] BLOCKED: %s vs %s (corr=%.2f ≥ %.2f)",
                account_id, proposed_instrument, blocking_desc,
                highest_corr, threshold,
            )
        elif level == CorrelationLevel.MODERATE:
            reason = (
                f"MODERATE correlation ({highest_corr:.2f}) with {highest_inst}. "
                f"Entry permitted with 50% size reduction."
            )
        else:
            reason = (
                f"Correlation clear. Highest: {highest_corr:.2f} with "
                f"{highest_inst or 'none'}. Entry permitted."
            )

        size_reduction = 0.50 if level == CorrelationLevel.MODERATE and entry_permitted else 0.0

        return CorrelationCheckResult(
            proposed_instrument=proposed_instrument.upper(),
            proposed_account_id=account_id,
            entry_permitted=entry_permitted,
            is_evaluation=is_evaluation,
            blocking_positions=blocking,
            highest_correlation=highest_corr,
            highest_corr_instrument=highest_inst,
            correlation_level=level,
            cross_account_conflict=False,
            cross_account_positions=[],
            reason=reason,
            size_reduction_pct=size_reduction,
        )

    # ── CORRELATION GUARD — CROSS-FIRM (FORGE-70) ─────────────────────────────

    def check_entry_cross_firm(
        self,
        proposed_instrument: str,
        proposed_account_id: str,
        is_evaluation:       bool = True,
    ) -> CorrelationCheckResult:
        """
        FORGE-70: Multi-Firm Correlation Guard.

        Prevents ALL simultaneous evaluations from holding correlated
        positions at the same time — even across different firms.

        Running ES on FTMO and NQ on Apex simultaneously = 0.94 correlation
        across both accounts = effectively one double-sized position.

        Args:
            proposed_instrument:   The new instrument.
            proposed_account_id:   Account wanting to open the position.
            is_evaluation:         True = strict mode.

        Returns:
            CorrelationCheckResult including cross-account conflicts.
        """
        threshold = self._eval_threshold if is_evaluation else self._funded_threshold

        # First check the account's own positions
        single_account_check = self.check_entry(
            proposed_instrument, proposed_account_id, is_evaluation
        )

        # Now check ALL other accounts
        all_other_positions = [
            p for p in self._positions.values()
            if p.account_id != proposed_account_id
        ]

        cross_conflicts: list[OpenPosition] = []
        cross_highest_corr = 0.0
        cross_highest_inst = None

        for pos in all_other_positions:
            corr = get_correlation(proposed_instrument, pos.instrument)
            if corr > cross_highest_corr:
                cross_highest_corr = corr
                cross_highest_inst = pos.instrument
            if corr >= threshold:
                cross_conflicts.append(pos)

        cross_conflict_detected = len(cross_conflicts) > 0

        # Overall decision: blocked if either own account OR cross-account blocks
        overall_permitted = (
            single_account_check.entry_permitted and
            not cross_conflict_detected
        )

        if cross_conflict_detected and single_account_check.entry_permitted:
            # Cross-firm conflict is the new blocker
            cross_desc = ", ".join(
                f"{p.instrument}@{p.firm_id}({p.account_id})"
                for p in cross_conflicts[:3]
            )
            reason = (
                f"FORGE-70 CROSS-FIRM BLOCKED: {proposed_instrument.upper()} "
                f"correlates {cross_highest_corr:.2f} with positions at other "
                f"firm(s): [{cross_desc}]. "
                f"Cannot hold correlated positions across simultaneous evaluations."
            )
            logger.error(
                "[FORGE-70] Cross-firm correlation BLOCKED: %s vs [%s] (%.2f)",
                proposed_instrument, cross_desc, cross_highest_corr,
            )

            # Choose the higher of the two correlation levels
            level = self._correlation_level(
                max(single_account_check.highest_correlation, cross_highest_corr)
            )

            return CorrelationCheckResult(
                proposed_instrument=proposed_instrument.upper(),
                proposed_account_id=proposed_account_id,
                entry_permitted=False,
                is_evaluation=is_evaluation,
                blocking_positions=single_account_check.blocking_positions,
                highest_correlation=cross_highest_corr,
                highest_corr_instrument=cross_highest_inst,
                correlation_level=level,
                cross_account_conflict=True,
                cross_account_positions=cross_conflicts,
                reason=reason,
                size_reduction_pct=0.0,
            )

        # Combine into final result
        combined_highest = max(
            single_account_check.highest_correlation, cross_highest_corr
        )
        return CorrelationCheckResult(
            proposed_instrument=proposed_instrument.upper(),
            proposed_account_id=proposed_account_id,
            entry_permitted=overall_permitted,
            is_evaluation=is_evaluation,
            blocking_positions=single_account_check.blocking_positions,
            highest_correlation=combined_highest,
            highest_corr_instrument=single_account_check.highest_corr_instrument,
            correlation_level=self._correlation_level(combined_highest),
            cross_account_conflict=cross_conflict_detected,
            cross_account_positions=cross_conflicts,
            reason=single_account_check.reason,
            size_reduction_pct=single_account_check.size_reduction_pct,
        )

    # ── UTILITIES ────────────────────────────────────────────────────────────

    def _correlation_level(self, corr: float) -> CorrelationLevel:
        if corr >= CORR_HIGHLY_CORRELATED:
            return CorrelationLevel.HIGHLY_CORRELATED
        elif corr >= CORR_CORRELATED:
            return CorrelationLevel.CORRELATED
        elif corr >= CORR_MODERATE:
            return CorrelationLevel.MODERATE
        return CorrelationLevel.LOW

    def get_correlation_exposure(self, account_id: str) -> dict:
        """
        Return a summary of correlation exposure for an account.
        Useful for FORGE-31 Dashboard.
        """
        positions = self.get_open_positions(account_id=account_id)
        if len(positions) <= 1:
            return {
                "account_id":    account_id,
                "open_positions": len(positions),
                "max_correlation": 0.0,
                "correlated_pairs": [],
            }

        pairs = []
        max_corr = 0.0
        for i, p1 in enumerate(positions):
            for p2 in positions[i + 1:]:
                corr = get_correlation(p1.instrument, p2.instrument)
                pairs.append({
                    "pair": f"{p1.instrument}/{p2.instrument}",
                    "correlation": corr,
                    "level": self._correlation_level(corr).name,
                })
                max_corr = max(max_corr, corr)

        return {
            "account_id":     account_id,
            "open_positions": len(positions),
            "max_correlation": max_corr,
            "correlated_pairs": sorted(pairs, key=lambda x: -x["correlation"]),
        }

    @property
    def total_open_positions(self) -> int:
        return len(self._positions)

    def clear_account(self, account_id: str) -> int:
        """Remove all positions for an account (e.g., EOD forced close). Returns count."""
        to_remove = [pid for pid, p in self._positions.items()
                     if p.account_id == account_id]
        for pid in to_remove:
            del self._positions[pid]
        return len(to_remove)
