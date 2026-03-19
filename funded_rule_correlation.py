"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                  funded_rule_correlation.py — FORGE-16 — Layer 1            ║
║  FUNDED ACCOUNT RULE CORRELATION                                             ║
║  Tracks high water mark continuously.                                        ║
║  All risk calculations update to funded account rules immediately on funding.║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from firm_rules import FirmID, MultiFirmRuleEngine

logger = logging.getLogger("titan_forge.funded_rule_correlation")

@dataclass
class HighWaterMark:
    account_id:     str
    hwm_balance:    float       # Highest realized balance ever reached
    hwm_equity:     float       # Highest equity (balance + unrealized) ever reached
    hwm_achieved_at: datetime
    current_balance: float
    current_equity:  float
    drawdown_from_hwm: float    # Current drawdown from HWM
    drawdown_pct:      float    # As fraction of HWM
    is_at_hwm:         bool

@dataclass
class FundedRuleSet:
    """The complete funded rule set, separate from evaluation rules."""
    account_id:             str
    firm_id:                str
    is_funded:              bool
    # Funded-specific drawdown
    total_drawdown_pct:     float
    daily_drawdown_pct:     Optional[float]
    firm_floor:             float
    # Position limits
    minimum_position_size:  float
    maximum_position_size:  float
    # Funded-specific restrictions
    no_scalping:            bool
    no_grid:                bool
    no_martingale:          bool
    min_hold_seconds:       Optional[int]
    # Payout
    payout_optimization_active: bool
    consistency_rule_pct:   Optional[float]
    # Scaling
    scaling_eligible:       bool
    current_account_size:   float
    # Updated at
    activated_at:           datetime

class FundedRuleCorrelation:
    """
    FORGE-16: Funded Account Rule Correlation.
    Tracks high water mark and ensures risk rules update immediately on funding.
    """

    def __init__(self, rule_engine: MultiFirmRuleEngine):
        self._rule_engine = rule_engine
        self._hwm_registry: dict[str, HighWaterMark] = {}
        self._funded_rules: dict[str, FundedRuleSet] = {}

    def initialize_account(
        self,
        account_id:      str,
        firm_id:         str,
        starting_balance: float,
        is_funded:       bool = False,
        funded_at:       Optional[datetime] = None,
    ) -> FundedRuleSet:
        """
        Initialize tracking for a new account.
        Builds the funded rule set immediately if is_funded=True.
        """
        now = funded_at or datetime.now(timezone.utc)
        rules = self._rule_engine.get_firm_rules(firm_id)

        hwm = HighWaterMark(
            account_id=account_id,
            hwm_balance=starting_balance,
            hwm_equity=starting_balance,
            hwm_achieved_at=now,
            current_balance=starting_balance,
            current_equity=starting_balance,
            drawdown_from_hwm=0.0,
            drawdown_pct=0.0,
            is_at_hwm=True,
        )
        self._hwm_registry[account_id] = hwm

        rule_set = FundedRuleSet(
            account_id=account_id,
            firm_id=firm_id,
            is_funded=is_funded,
            total_drawdown_pct=rules.total_drawdown_pct,
            daily_drawdown_pct=rules.daily_drawdown_pct,
            firm_floor=starting_balance * (1.0 - rules.total_drawdown_pct),
            minimum_position_size=rules.minimum_position_size,
            maximum_position_size=rules.maximum_position_size,
            no_scalping=rules.funded_no_scalping if is_funded else False,
            no_grid=rules.funded_no_grid if is_funded else False,
            no_martingale=rules.funded_no_martingale if is_funded else False,
            min_hold_seconds=rules.funded_min_hold_seconds if is_funded else None,
            payout_optimization_active=is_funded,
            consistency_rule_pct=rules.consistency_rule_pct,
            scaling_eligible=rules.has_scaling_plan and is_funded,
            current_account_size=starting_balance,
            activated_at=now,
        )
        self._funded_rules[account_id] = rule_set

        if is_funded:
            logger.warning(
                "[FORGE-16][%s] FUNDED rule set activated IMMEDIATELY. "
                "No-scalping: %s. Payout optimization: ON.",
                account_id, rules.funded_no_scalping,
            )
        return rule_set

    def confirm_funded(
        self,
        account_id: str,
        confirmed_at: Optional[datetime] = None,
    ) -> FundedRuleSet:
        """
        Switch to funded rules IMMEDIATELY upon confirmation.
        FORGE-16: No delay — funded rules activate the instant firm confirms.
        """
        if account_id not in self._funded_rules:
            raise KeyError(f"Account {account_id} not initialized.")

        rs = self._funded_rules[account_id]
        rules = self._rule_engine.get_firm_rules(rs.firm_id)
        now = confirmed_at or datetime.now(timezone.utc)

        # Rebuild with funded=True — IMMEDIATE switch
        updated = FundedRuleSet(
            account_id=account_id,
            firm_id=rs.firm_id,
            is_funded=True,
            total_drawdown_pct=rules.total_drawdown_pct,
            daily_drawdown_pct=rules.daily_drawdown_pct,
            firm_floor=rs.current_account_size * (1.0 - rules.total_drawdown_pct),
            minimum_position_size=rules.minimum_position_size,
            maximum_position_size=rules.maximum_position_size,
            no_scalping=rules.funded_no_scalping,
            no_grid=rules.funded_no_grid,
            no_martingale=rules.funded_no_martingale,
            min_hold_seconds=rules.funded_min_hold_seconds,
            payout_optimization_active=True,
            consistency_rule_pct=rules.consistency_rule_pct,
            scaling_eligible=rules.has_scaling_plan,
            current_account_size=rs.current_account_size,
            activated_at=now,
        )
        self._funded_rules[account_id] = updated
        logger.warning(
            "[FORGE-16][%s] ⚡ FUNDED RULES ACTIVATED IMMEDIATELY at %s.",
            account_id, now.isoformat(),
        )
        return updated

    def update_equity(
        self,
        account_id:      str,
        current_balance: float,
        current_equity:  float,
        as_of:           Optional[datetime] = None,
    ) -> HighWaterMark:
        """Update equity and recalculate high water mark."""
        if account_id not in self._hwm_registry:
            raise KeyError(f"Account {account_id} not initialized.")

        now = as_of or datetime.now(timezone.utc)
        hwm = self._hwm_registry[account_id]

        new_hwm_balance = hwm.hwm_balance
        new_hwm_equity  = hwm.hwm_equity
        new_hwm_time    = hwm.hwm_achieved_at

        if current_balance > hwm.hwm_balance:
            new_hwm_balance = current_balance
            new_hwm_time    = now
            logger.info(
                "[FORGE-16][%s] New balance HWM: $%.2f", account_id, new_hwm_balance
            )
        if current_equity > hwm.hwm_equity:
            new_hwm_equity = current_equity

        dd_from_hwm  = max(0.0, new_hwm_balance - current_balance)
        dd_pct       = dd_from_hwm / new_hwm_balance if new_hwm_balance > 0 else 0.0

        updated_hwm = HighWaterMark(
            account_id=account_id,
            hwm_balance=new_hwm_balance,
            hwm_equity=new_hwm_equity,
            hwm_achieved_at=new_hwm_time,
            current_balance=current_balance,
            current_equity=current_equity,
            drawdown_from_hwm=dd_from_hwm,
            drawdown_pct=round(dd_pct, 6),
            is_at_hwm=(current_balance >= new_hwm_balance),
        )
        self._hwm_registry[account_id] = updated_hwm

        # Update funded rule set floor if account size has grown (scaling)
        if account_id in self._funded_rules:
            rs = self._funded_rules[account_id]
            if current_balance > rs.current_account_size and rs.is_funded:
                rules = self._rule_engine.get_firm_rules(rs.firm_id)
                self._funded_rules[account_id] = FundedRuleSet(
                    **{**rs.__dict__,
                       "current_account_size": current_balance,
                       "firm_floor": current_balance * (1.0 - rules.total_drawdown_pct),
                    }
                )

        return updated_hwm

    def get_hwm(self, account_id: str) -> Optional[HighWaterMark]:
        return self._hwm_registry.get(account_id)

    def get_rules(self, account_id: str) -> Optional[FundedRuleSet]:
        return self._funded_rules.get(account_id)

    def get_risk_pct_on_hwm(self, account_id: str, position_risk_dollars: float) -> float:
        """Return position risk as % of the HIGH WATER MARK (not current balance)."""
        hwm = self._hwm_registry.get(account_id)
        if not hwm or hwm.hwm_balance <= 0:
            return 0.0
        return position_risk_dollars / hwm.hwm_balance
