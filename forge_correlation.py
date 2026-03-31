"""
FORGE v22 — Correlation Guard
===============================
Prevents opening correlated positions simultaneously.
Based on research correlation data across 20 instruments.

Rules:
- REDUNDANT (>0.80): NEVER trade simultaneously
- HIGH (0.50-0.80): Max 1 position in the group
- DIVERSIFIED (near 0): Trade freely
"""

import logging
from typing import Dict, List, Optional, Set, Tuple

from forge_instruments_v22 import CORRELATION_GROUPS

logger = logging.getLogger("FORGE.correlation")


class CorrelationGuard:
    """
    Checks whether a new trade would violate correlation constraints.
    
    Called before every trade entry to ensure portfolio diversification.
    """

    # Thresholds
    REDUNDANT_THRESHOLD = 0.80
    HIGH_THRESHOLD = 0.50

    def __init__(self):
        # Build lookup tables for fast checking
        self._redundant_pairs: List[Tuple[str, str]] = []
        self._high_groups: List[Tuple[str, str]] = []
        
        for group in CORRELATION_GROUPS.get("redundant", []):
            self._redundant_pairs.append(group["pair"])
        
        for group in CORRELATION_GROUPS.get("high", []):
            self._high_groups.append(group["pair"])

    def can_trade(self, symbol: str, active_symbols: Set[str]) -> Tuple[bool, Optional[str]]:
        """
        Check if opening a position on `symbol` would violate correlation rules.
        
        Args:
            symbol: The instrument to check
            active_symbols: Set of symbols currently having open positions
            
        Returns:
            (allowed, reason)
            - (True, None) = OK to trade
            - (False, reason) = Blocked
        """
        if not active_symbols:
            return True, None

        # Check redundant pairs (>0.80 — NEVER trade simultaneously)
        for sym_a, sym_b in self._redundant_pairs:
            if symbol == sym_a and sym_b in active_symbols:
                reason = f"CORR BLOCK: {symbol} redundant with active {sym_b} (>0.80)"
                logger.info(reason)
                return False, reason
            if symbol == sym_b and sym_a in active_symbols:
                reason = f"CORR BLOCK: {symbol} redundant with active {sym_a} (>0.80)"
                logger.info(reason)
                return False, reason

        # Check high correlation groups (0.50-0.80 — max 1 in group)
        for sym_a, sym_b in self._high_groups:
            if symbol == sym_a and sym_b in active_symbols:
                reason = f"CORR BLOCK: {symbol} highly correlated with active {sym_b} (0.50-0.80)"
                logger.info(reason)
                return False, reason
            if symbol == sym_b and sym_a in active_symbols:
                reason = f"CORR BLOCK: {symbol} highly correlated with active {sym_a} (0.50-0.80)"
                logger.info(reason)
                return False, reason

        return True, None

    def get_blocked_symbols(self, active_symbols: Set[str]) -> Set[str]:
        """Get all symbols that are currently blocked due to correlation."""
        blocked = set()
        for sym in active_symbols:
            for sym_a, sym_b in self._redundant_pairs:
                if sym == sym_a:
                    blocked.add(sym_b)
                elif sym == sym_b:
                    blocked.add(sym_a)
            for sym_a, sym_b in self._high_groups:
                if sym == sym_a:
                    blocked.add(sym_b)
                elif sym == sym_b:
                    blocked.add(sym_a)
        return blocked
