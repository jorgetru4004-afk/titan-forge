# FORGE v22 — INTEGRATION GUIDE
## "ALL GAS FIRST THEN BRAKES"

---

## FILES DELIVERED

| File | Purpose | Status |
|------|---------|--------|
| `forge_instruments_v22.py` | 14 instruments, strategy mappings, time-of-day edges, correlations, seasonality | NEW — replaces instrument config |
| `forge_signals_v22.py` | 10 strategy implementations + signal engine | NEW — replaces `forge_signals_v21.py` |
| `forge_runner.py` | Runner detection + trailing stop + trade lifecycle management | NEW |
| `forge_limit.py` | Limit order tracking with 5-bar timeout + market fallback | NEW |
| `forge_correlation.py` | Correlation guard preventing redundant simultaneous positions | NEW |
| `forge_main_v22.py` | Main loop integration showing how everything wires together | NEW — integration template |

---

## WHAT CHANGED FROM v21

### KILLED (trend-following doesn't work on mean-reverting markets)
- All trend-following setups (ORD-02, OD-01, etc.)
- 40+ overlapping/conflicting setup definitions
- 0.35 conviction threshold (killed trade flow)
- 180s cooldown in EVAL mode
- Confluence requirement (needed 6+ of 11 dimensions)

### ADDED
- 10 proven mean-reversion strategies from real Polygon data
- SCALP vs RUNNER trade type classification at entry
- Runner detection (ADX + VWAP + ATR budget + reversal candle checks)
- Limit orders with 5-bar timeout + intelligent market fallback
- Time-of-day edge boosting (+15% confidence at proven hours)
- Monthly seasonality sizing adjustments
- Correlation guard (blocks redundant simultaneous positions)
- Breakeven at +0.5R on ALL trades (protect capital fast)

### TUNED
- Conviction threshold: **0.20** (was 0.35)
- Cooldown: **120s** (was 180s in EVAL)
- Instruments: **14** (was 33 setups on 13 instruments)
- Each instrument gets exactly **1 strategy** (was multiple overlapping)
- Min R:R relaxed to **1.2 for scalps** (was 1.5 universal)

---

## EXISTING FILES — WHAT TO DO

### KEEP UNCHANGED (do not touch)
```
forge_risk.py          — All 13 risk gates (they're good, let them work)
forge_evidence.py      — Evidence logging
forge_heartbeat.py     — Dead man switch
forge_readiness.py     — Pre-flight checks
forge_commands.py      — Telegram commands
forge_mode.py          — EVAL/FUNDED switching
mt5_adapter.py         — MetaAPI connection
execution_base.py      — Order execution
```

### KEEP BUT MODIFY

#### `forge_brain.py`
Add time-of-day boosting to the Bayesian conviction calculation:

```python
# In the conviction scoring method, add after computing base conviction:

from forge_instruments_v22 import TIME_OF_DAY_EDGES, TOD_EDGE_BOOST, TOD_SUPPRESS

def apply_tod_adjustment(self, symbol, direction, hour_utc, base_conviction):
    """Boost/suppress conviction based on proven time-of-day edges."""
    edges = TIME_OF_DAY_EDGES.get(hour_utc, [])
    
    for sym, edge_dir, p_value in edges:
        if sym == symbol and edge_dir == direction:
            return base_conviction + TOD_EDGE_BOOST  # +0.15
    
    # If symbol has edge at different hour, suppress
    has_any_edge = any(
        sym == symbol
        for hour_edges in TIME_OF_DAY_EDGES.values()
        for sym, _, _ in hour_edges
    )
    if has_any_edge:
        return base_conviction + TOD_SUPPRESS  # -0.15
    
    return base_conviction
```

#### `forge_market.py` (indicator computation)
Add these indicators if not already computed:
- **Keltner Channels** (20-period EMA ± 1.5 * ATR) — needed for VOL_COMPRESS
- **VWAP standard deviation** — needed for VWAP_REVERT
- **ADX 5 bars ago** — needed for runner detection
- **Opening Range (ORB)** high/low — first 30 minutes of session
- **Asian Range** high/low — 00:00-07:00 UTC

```python
# Keltner channels
keltner_mid = ema(closes, 20)
keltner_upper = keltner_mid + 1.5 * atr
keltner_lower = keltner_mid - 1.5 * atr

# VWAP std
vwap_std = np.std(typical_prices - vwap)  # or rolling std

# ADX lookback
adx_prev = adx_series[-6]  # 5 bars ago
```

#### `main.py`
Replace the v21 signal scanning loop with `ForgeV22Engine`:

```python
from forge_main_v22 import ForgeV22Engine

# In your async main():
engine = ForgeV22Engine(
    risk_manager=risk_mgr,
    evidence_logger=evidence,
    brain=brain,
    mode_manager=mode,
    market_engine=market,
    mt5=adapter,
    telegram=tg_bot,
)

# Replace old signal loop with:
await engine.scan_loop()
```

### DELETE
```
forge_signals_v21.py   — Completely replaced by forge_signals_v22.py
```

---

## ARCHITECTURE FLOW

```
Every 30 seconds:
  │
  ├─ Build MarketSnapshots for 14 instruments
  │   (uses existing forge_market.py + mt5_adapter.py)
  │
  ├─ Manage Active Trades
  │   ├─ SCALP: check breakeven (+0.5R), TP, SL
  │   └─ RUNNER: breakeven → partial (50% at 1R) → trail → runner detection
  │       ├─ KEEP if: ADX>25 rising, price on VWAP side, ATR<85%, no reversal
  │       └─ CUT if: ADX<20, VWAP cross, reversal+volume, max 50 bars
  │
  ├─ Update Pending Limits
  │   ├─ Check if filled (MetaAPI callback)
  │   ├─ Check bars elapsed (max 5)
  │   ├─ If expired + setup valid → MARKET FALLBACK
  │   └─ If expired + setup invalid → CANCEL
  │
  ├─ Risk Gates (existing forge_risk.py — all 13 gates)
  │
  ├─ Generate New Signals
  │   ├─ Each instrument → its ONE assigned strategy
  │   ├─ Apply time-of-day boost/suppress
  │   ├─ Apply seasonality sizing
  │   └─ Filter by conviction threshold (0.20)
  │
  └─ Execute Signals
      ├─ Check: max positions, cooldown, correlation, duplicates
      ├─ LIMIT strategies → place limit, register for tracking
      └─ MARKET strategies → execute immediately
```

---

## KEY NUMBERS

| Parameter | v21 | v22 | Why |
|-----------|-----|-----|-----|
| Conviction threshold | 0.35 | **0.20** | v21 barely traded |
| Cooldown (EVAL) | 180s | **120s** | More opportunities |
| Setups | 33 | **14** | One per instrument, all proven |
| Strategies | Many overlapping | **10 distinct** | Each backed by data |
| Confluence required | Yes (6+/11) | **No** | Single signal enough |
| Trade types | One size fits all | **SCALP + RUNNER** | Right management per setup |
| Breakeven | None | **+0.5R** | Protect capital fast |
| Runner detection | None | **ADX+VWAP+ATR+reversal** | Smart exits |
| Limit fallback | None | **5 bars → market** | Don't miss entries |

---

## TRADE FLOW PHILOSOPHY

```
Signal fires (confidence ≥ 0.20)
  │
  ├─ Is context even remotely reasonable? → TAKE THE TRADE
  │
  ├─ Move SL to breakeven at +0.5R → CAPITAL PROTECTED
  │   └─ If it hits BE → scratch at zero, no harm done
  │
  ├─ SCALP: wait for TP or SL, no discretion
  │
  └─ RUNNER: partial 50% at +1R → trail rest
      ├─ Trend alive? → HOLD (let it run to +3R, +5R)
      └─ Trend dying? → CUT (take what you have)

The math:
  65% of trades scratch at breakeven or small loss (0R to -0.3R)
  35% run to +1R to +5R
  Net result: strongly positive (winners 3-10x size of losers)
```

---

## DEPLOYMENT CHECKLIST

1. [ ] Copy all 6 new files to the titan-forge repo
2. [ ] Update `forge_market.py` to compute new indicators (Keltner, VWAP std, ORB, Asian range)
3. [ ] Add `apply_tod_adjustment()` to `forge_brain.py`
4. [ ] Update `main.py` to use `ForgeV22Engine` instead of v21 signal loop
5. [ ] Delete `forge_signals_v21.py`
6. [ ] Update `requirements.txt` if any new dependencies (none expected — all numpy)
7. [ ] Update `forge_readiness.py` to check v22 instruments exist on FTMO
8. [ ] Test locally with paper data before Railway push
9. [ ] Push to Railway (project: intelligent-enchantment)
10. [ ] Verify Telegram alerts flowing
11. [ ] Monitor first 24h — expect 2-4 trades minimum
