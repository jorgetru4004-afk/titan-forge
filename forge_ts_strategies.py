"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   forge_ts_strategies.py — Layer 3                          ║
║                                                                              ║
║  ALL 30 FORGE-TS STRATEGY SIGNAL IMPLEMENTATIONS                            ║
║  Section 10: 30 strategies, avg win rate 71.8%                              ║
║                                                                              ║
║  FORGE-TS-01: GEX-01 Gamma Flip Breakout (75%, 2.5:1)                      ║
║  FORGE-TS-02: GEX-02 Dealer Hedging Cascade (74%, 3:1)                     ║
║  FORGE-TS-03: GEX-03 GEX Pin and Break (73%, 2:1)                          ║
║  FORGE-TS-04: GEX-04 Vanna Flow Drift (70%, 2:1)                           ║
║  FORGE-TS-05: GEX-05 Charm Decay Fade (68%, 1.8:1)                         ║
║  FORGE-TS-06: ICT-01 Order Block + FVG Confluence (76%, 2.5:1)             ║
║  FORGE-TS-07: ICT-02 Liquidity Sweep and Reverse (74%, 3:1)                ║
║  FORGE-TS-08: ICT-03 Kill Zone OTE Entry (73%, 2.5:1)                      ║
║  FORGE-TS-09: ICT-04 Breaker Block Retest (72%, 2:1)                       ║
║  FORGE-TS-10: ICT-05 Asian Range Raid and Reverse (71%, 2.5:1)             ║
║  FORGE-TS-11: ICT-06 Premium/Discount Zone Filter (70%, 2.5:1)             ║
║  FORGE-TS-12: ICT-07 FVG Inversion Play (69%, 2:1)                         ║
║  FORGE-TS-13: ICT-08 Market Structure Break + OTE (73%, 3:1)               ║
║  FORGE-TS-14: VOL-01 POC Magnetic Revert (74%, 1.8:1)                      ║
║  FORGE-TS-15: VOL-02 Value Area Edge Fade (72%, 2:1)                       ║
║  FORGE-TS-16: VOL-03 Low Volume Node Express (73%, 2.5:1)                  ║
║  FORGE-TS-17: VOL-04 High Volume Node Cluster Trade (70%, 2:1)             ║
║  FORGE-TS-18: VOL-05 Anchored VWAP Confluence (71%, 2:1)                   ║
║  FORGE-TS-19: ORD-01 Delta Divergence Reversal (75%, 2.5:1)                ║
║  FORGE-TS-20: ORD-02 Footprint Absorption Entry (73%, 2.5:1)               ║
║  FORGE-TS-21: ORD-03 Order Block Stacking Breakout (71%, 2:1)              ║
║  FORGE-TS-22: ORD-04 Bid/Ask Imbalance Cascade (70%, 2:1)                  ║
║  FORGE-TS-23: SES-01 NY Kill Zone Power Hour (74%, 2.5:1)                  ║
║  FORGE-TS-24: SES-02 London-NY Overlap Momentum (73%, 2:1)                 ║
║  FORGE-TS-25: SES-03 First Hour Reversal Pattern (70%, 2:1)                ║
║  FORGE-TS-26: SES-04 Pre-Close Institutional Positioning (69%, 1.8:1)      ║
║  FORGE-TS-27: SES-05 Monday Gap Fill Strategy (72%, 2:1)                   ║
║  FORGE-TS-28: INS-01 Unusual Options Flow Follow (75%, 3:1)                ║
║  FORGE-TS-29: INS-02 Dark Pool Print Entry (73%, 2.5:1)                    ║
║  FORGE-TS-30: INS-03 COT Extreme Reversal (71%, 3:1)                       ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from typing import Optional

logger = logging.getLogger("titan_forge.forge_ts_strategies")


@dataclass
class TSSignal:
    """Unified output for all 30 FORGE-TS strategy signals."""
    forge_ts_id:   str    # e.g. "FORGE-TS-01"
    strategy_id:   str    # e.g. "GEX-01"
    name:          str
    valid:         bool
    direction:     Optional[str]   # "long" / "short" / None
    entry:         Optional[float]
    stop:          Optional[float]
    target:        Optional[float]
    win_rate:      float
    rr:            float
    confidence:    float   # 0–1
    reason:        str

    @property
    def ev(self) -> float:
        return (self.win_rate * self.rr) - (1.0 - self.win_rate)


def _signal(forge_ts_id, strategy_id, name, valid, direction, entry, stop, target,
            win_rate, rr, confidence, reason) -> TSSignal:
    return TSSignal(forge_ts_id, strategy_id, name, valid, direction,
                    entry, stop, target, win_rate, rr, confidence, reason)

def _no_signal(forge_ts_id, strategy_id, name, win_rate, rr, reason) -> TSSignal:
    return TSSignal(forge_ts_id, strategy_id, name, False, None, None, None, None,
                    win_rate, rr, 0.0, reason)


# ── FORGE-TS-01: GEX-01 Gamma Flip Breakout ─────────────────────────────────
def ts01_gamma_flip(prior_gex, current_gex, flip_price, current_price, atr, direction) -> TSSignal:
    flipped = prior_gex > 0 and current_gex < 0
    if not flipped:
        return _no_signal("FORGE-TS-01","GEX-01","Gamma Flip Breakout",0.75,2.5,"GEX not flipped.")
    dist = abs(current_price - flip_price) / atr if atr > 0 else 0
    if dist > 1.5:
        return _no_signal("FORGE-TS-01","GEX-01","Gamma Flip Breakout",0.75,2.5,f"Too far from flip: {dist:.1f} ATR.")
    e = flip_price + (atr*0.1 if direction=="long" else -atr*0.1)
    s = e - atr if direction=="long" else e + atr
    t = e + atr*2.5 if direction=="long" else e - atr*2.5
    return _signal("FORGE-TS-01","GEX-01","Gamma Flip Breakout",True,direction,e,s,t,0.75,2.5,
                   max(0.6,0.9-dist*0.15),f"GEX flipped {prior_gex:.0f}→{current_gex:.0f}. Entry {e:.2f}.")


# ── FORGE-TS-02: GEX-02 Dealer Hedging Cascade ──────────────────────────────
def ts02_dealer_cascade(gex_negative, momentum_confirmed, current_price, vwap, atr, direction) -> TSSignal:
    if not gex_negative:
        return _no_signal("FORGE-TS-02","GEX-02","Dealer Hedging Cascade",0.74,3.0,"GEX not negative.")
    if not momentum_confirmed:
        return _no_signal("FORGE-TS-02","GEX-02","Dealer Hedging Cascade",0.74,3.0,"Momentum not confirmed.")
    e = current_price
    s = vwap - atr*0.5 if direction=="long" else vwap + atr*0.5
    t = e + atr*3.0 if direction=="long" else e - atr*3.0
    return _signal("FORGE-TS-02","GEX-02","Dealer Hedging Cascade",True,direction,e,s,t,0.74,3.0,0.78,
                   f"GEX negative + momentum. Dealers forced to hedge cascade. Target {t:.2f}.")


# ── FORGE-TS-03: GEX-03 GEX Pin and Break ───────────────────────────────────
def ts03_gex_pin_break(gex_positive, pinning_near_strike, current_price, strike_price, atr, direction) -> TSSignal:
    if not gex_positive:
        return _no_signal("FORGE-TS-03","GEX-03","GEX Pin and Break",0.73,2.0,"GEX not positive.")
    if not pinning_near_strike:
        return _no_signal("FORGE-TS-03","GEX-03","GEX Pin and Break",0.73,2.0,"Not pinning near strike.")
    dist = abs(current_price - strike_price) / atr if atr > 0 else 1
    e = current_price
    s = e - atr if direction=="long" else e + atr
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    conf = max(0.55, 0.80 - dist*0.10)
    return _signal("FORGE-TS-03","GEX-03","GEX Pin and Break",True,direction,e,s,t,0.73,2.0,conf,
                   f"GEX pin at {strike_price:.2f}. Break imminent. Entry {e:.2f}.")


# ── FORGE-TS-04: GEX-04 Vanna Flow Drift ────────────────────────────────────
def ts04_vanna_drift(iv_falling, gex_positive, time_of_day_et: time, current_price, atr, direction) -> TSSignal:
    in_window = time(14,0) <= time_of_day_et <= time(16,0)  # Afternoon: vanna peaks
    if not iv_falling or not gex_positive:
        return _no_signal("FORGE-TS-04","GEX-04","Vanna Flow Drift",0.70,2.0,"IV not falling or GEX not positive.")
    if not in_window:
        return _no_signal("FORGE-TS-04","GEX-04","Vanna Flow Drift",0.70,2.0,"Outside vanna window (2-4pm ET).")
    e = current_price
    s = e - atr*0.8 if direction=="long" else e + atr*0.8
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    return _signal("FORGE-TS-04","GEX-04","Vanna Flow Drift",True,direction,e,s,t,0.70,2.0,0.68,
                   f"Vanna drift: IV falling + afternoon window. Mechanical dealer buying.")


# ── FORGE-TS-05: GEX-05 Charm Decay Fade ────────────────────────────────────
def ts05_charm_decay(time_of_day_et: time, near_expiry, current_price, vwap, atr, direction) -> TSSignal:
    in_window = time(14,30) <= time_of_day_et <= time(15,45)  # Late session charm decay
    if not in_window:
        return _no_signal("FORGE-TS-05","GEX-05","Charm Decay Fade",0.68,1.8,"Outside charm window (2:30-3:45pm ET).")
    if not near_expiry:
        return _no_signal("FORGE-TS-05","GEX-05","Charm Decay Fade",0.68,1.8,"Not near expiry.")
    e = current_price
    s = e - atr*0.6 if direction=="long" else e + atr*0.6
    t = vwap  # Mean reversion target
    return _signal("FORGE-TS-05","GEX-05","Charm Decay Fade",True,direction,e,s,t,0.68,1.8,0.65,
                   f"Charm decay late session. Delta unwind forces mean reversion to {vwap:.2f}.")


# ── FORGE-TS-06: ICT-01 Order Block + FVG Confluence ────────────────────────
def ts06_ob_fvg(current_price, ob_high, ob_low, fvg_high, fvg_low, atr, direction) -> TSSignal:
    overlap = not (ob_high < fvg_low or ob_low > fvg_high)
    if not overlap:
        return _no_signal("FORGE-TS-06","ICT-01","OB+FVG Confluence",0.76,2.5,"No OB+FVG confluence zone.")
    zone_h = max(ob_high, fvg_high)
    zone_l = min(ob_low,  fvg_low)
    in_zone = zone_l <= current_price <= zone_h
    e = current_price if in_zone else (zone_h if direction=="long" else zone_l)
    s = zone_l - atr*0.3 if direction=="long" else zone_h + atr*0.3
    t = e + (e-s)*2.5 if direction=="long" else e - (s-e)*2.5
    conf = 0.85 if in_zone else 0.70
    return _signal("FORGE-TS-06","ICT-01","OB+FVG Confluence",True,direction,e,s,t,0.76,2.5,conf,
                   f"OB+FVG zone [{zone_l:.2f}–{zone_h:.2f}]. Institutional zone. Entry {e:.2f}.")


# ── FORGE-TS-07: ICT-02 Liquidity Sweep and Reverse ─────────────────────────
def ts07_liquidity_sweep(swept_high_or_low, current_price, sweep_level, atr, direction) -> TSSignal:
    if not swept_high_or_low:
        return _no_signal("FORGE-TS-07","ICT-02","Liquidity Sweep & Reverse",0.74,3.0,"No sweep detected.")
    dist = abs(current_price - sweep_level) / atr if atr > 0 else 0
    if dist > 1.5:
        return _no_signal("FORGE-TS-07","ICT-02","Liquidity Sweep & Reverse",0.74,3.0,f"Too far post-sweep: {dist:.1f} ATR.")
    e = current_price
    s = sweep_level + atr*0.3 if direction=="long" else sweep_level - atr*0.3
    t = e + atr*3.0 if direction=="long" else e - atr*3.0
    return _signal("FORGE-TS-07","ICT-02","Liquidity Sweep & Reverse",True,direction,e,s,t,0.74,3.0,0.80,
                   f"Sweep of {sweep_level:.2f} complete. Reversal entry at {e:.2f}.")


# ── FORGE-TS-08: ICT-03 Kill Zone OTE Entry ──────────────────────────────────
def ts08_killzone_ote(time_et: time, in_kill_zone, ote_level, current_price, atr, direction) -> TSSignal:
    KILL_ZONES = [(time(2,0),time(5,0)),(time(8,0),time(11,0)),(time(13,0),time(16,0))]
    in_kz = any(s <= time_et <= e for s,e in KILL_ZONES)
    if not in_kz or not in_kill_zone:
        return _no_signal("FORGE-TS-08","ICT-03","Kill Zone OTE Entry",0.73,2.5,"Not in kill zone or no OTE.")
    dist = abs(current_price - ote_level) / atr if atr > 0 else 0
    e = ote_level
    s = e - atr if direction=="long" else e + atr
    t = e + atr*2.5 if direction=="long" else e - atr*2.5
    conf = max(0.60, 0.80 - dist*0.10)
    return _signal("FORGE-TS-08","ICT-03","Kill Zone OTE Entry",True,direction,e,s,t,0.73,2.5,conf,
                   f"Kill zone + OTE at {ote_level:.5f}. Institutional entry zone.")


# ── FORGE-TS-09: ICT-04 Breaker Block Retest ─────────────────────────────────
def ts09_breaker_block(is_breaker, breaker_high, breaker_low, current_price, atr, direction) -> TSSignal:
    if not is_breaker:
        return _no_signal("FORGE-TS-09","ICT-04","Breaker Block Retest",0.72,2.0,"No breaker block identified.")
    in_zone = breaker_low <= current_price <= breaker_high
    if not in_zone:
        return _no_signal("FORGE-TS-09","ICT-04","Breaker Block Retest",0.72,2.0,"Price not retesting breaker zone.")
    e = current_price
    s = breaker_low - atr*0.3 if direction=="long" else breaker_high + atr*0.3
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    return _signal("FORGE-TS-09","ICT-04","Breaker Block Retest",True,direction,e,s,t,0.72,2.0,0.75,
                   f"Breaker block retest [{breaker_low:.2f}–{breaker_high:.2f}]. Entry {e:.2f}.")


# ── FORGE-TS-10: ICT-05 Asian Range Raid and Reverse ─────────────────────────
def ts10_asian_raid(asian_high, asian_low, current_price, swept_asian, atr, direction) -> TSSignal:
    if not swept_asian:
        return _no_signal("FORGE-TS-10","ICT-05","Asian Range Raid & Reverse",0.71,2.5,"Asian range not yet swept.")
    e = current_price
    s = asian_high + atr*0.2 if direction=="short" else asian_low - atr*0.2
    t = e - atr*2.5 if direction=="short" else e + atr*2.5
    return _signal("FORGE-TS-10","ICT-05","Asian Range Raid & Reverse",True,direction,e,s,t,0.71,2.5,0.72,
                   f"Asian range [{asian_low:.5f}–{asian_high:.5f}] raided. Reversal entry {e:.5f}.")


# ── FORGE-TS-11: ICT-06 Premium/Discount Zone Filter ─────────────────────────
def ts11_premium_discount(current_price, range_high, range_low, direction) -> TSSignal:
    range_mid = (range_high + range_low) / 2
    in_premium = current_price > range_mid   # Above midpoint = premium (short)
    in_discount = current_price < range_mid  # Below midpoint = discount (long)
    aligned = (direction=="short" and in_premium) or (direction=="long" and in_discount)
    if not aligned:
        return _no_signal("FORGE-TS-11","ICT-06","Premium/Discount Zone",0.70,2.5,
                           f"Price not in {'premium' if direction=='short' else 'discount'} zone.")
    atr = (range_high - range_low) * 0.1
    e = current_price
    s = range_mid if direction=="short" else range_mid  # Stop at midpoint
    t = range_low - atr if direction=="short" else range_high + atr
    return _signal("FORGE-TS-11","ICT-06","Premium/Discount Zone",True,direction,e,s,t,0.70,2.5,0.68,
                   f"{'Premium' if in_premium else 'Discount'} zone. Entry {e:.2f}, target {t:.2f}.")


# ── FORGE-TS-12: ICT-07 FVG Inversion Play ───────────────────────────────────
def ts12_fvg_inversion(fvg_high, fvg_low, current_price, was_filled, now_inverted, atr, direction) -> TSSignal:
    if not was_filled or not now_inverted:
        return _no_signal("FORGE-TS-12","ICT-07","FVG Inversion Play",0.69,2.0,"FVG not filled and inverted.")
    in_zone = fvg_low <= current_price <= fvg_high
    if not in_zone:
        return _no_signal("FORGE-TS-12","ICT-07","FVG Inversion Play",0.69,2.0,"Price not in inverted FVG zone.")
    e = current_price
    s = fvg_low - atr*0.3 if direction=="long" else fvg_high + atr*0.3
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    return _signal("FORGE-TS-12","ICT-07","FVG Inversion Play",True,direction,e,s,t,0.69,2.0,0.66,
                   f"FVG [{fvg_low:.2f}–{fvg_high:.2f}] filled and inverted to support. Entry {e:.2f}.")


# ── FORGE-TS-13: ICT-08 Market Structure Break + OTE ─────────────────────────
def ts13_msb_ote(msb_confirmed, ote_level, current_price, atr, direction) -> TSSignal:
    if not msb_confirmed:
        return _no_signal("FORGE-TS-13","ICT-08","MSB + OTE",0.73,3.0,"No market structure break.")
    dist = abs(current_price - ote_level) / atr if atr > 0 else 0
    if dist > 0.8:
        return _no_signal("FORGE-TS-13","ICT-08","MSB + OTE",0.73,3.0,f"Price too far from OTE: {dist:.1f} ATR.")
    e = ote_level
    s = e - atr*1.0 if direction=="long" else e + atr*1.0
    t = e + atr*3.0 if direction=="long" else e - atr*3.0
    return _signal("FORGE-TS-13","ICT-08","MSB + OTE",True,direction,e,s,t,0.73,3.0,0.78,
                   f"MSB confirmed + OTE pullback to {ote_level:.2f}. High-confluence entry.")


# ── FORGE-TS-14: VOL-01 POC Magnetic Revert ──────────────────────────────────
def ts14_poc_revert(current_price, poc, vah, val, atr, direction) -> TSSignal:
    dist_from_poc = abs(current_price - poc) / atr if atr > 0 else 0
    if dist_from_poc < 1.5:
        return _no_signal("FORGE-TS-14","VOL-01","POC Magnetic Revert",0.74,1.8,"Price too close to POC.")
    at_extreme = current_price >= vah or current_price <= val
    if not at_extreme:
        return _no_signal("FORGE-TS-14","VOL-01","POC Magnetic Revert",0.74,1.8,"Not at value area extreme.")
    e = current_price
    s = e + atr*0.4 if direction=="short" else e - atr*0.4
    t = poc   # Target = POC (magnetic pull)
    return _signal("FORGE-TS-14","VOL-01","POC Magnetic Revert",True,direction,e,s,t,0.74,1.8,0.76,
                   f"POC magnetic reversion. Current {e:.2f} → POC {poc:.2f}. {dist_from_poc:.1f} ATR to target.")


# ── FORGE-TS-15: VOL-02 Value Area Edge Fade ─────────────────────────────────
def ts15_value_area_fade(current_price, vah, val, atr, direction) -> TSSignal:
    at_vah = abs(current_price - vah) / atr < 0.3 if atr > 0 else False
    at_val = abs(current_price - val) / atr < 0.3 if atr > 0 else False
    aligned = (direction=="short" and at_vah) or (direction=="long" and at_val)
    if not aligned:
        return _no_signal("FORGE-TS-15","VOL-02","Value Area Edge Fade",0.72,2.0,"Not at value area edge.")
    e = current_price
    edge = vah if at_vah else val
    s = edge + atr*0.3 if direction=="short" else edge - atr*0.3
    poc = (vah + val) / 2  # Approximate POC as midpoint
    t = poc
    return _signal("FORGE-TS-15","VOL-02","Value Area Edge Fade",True,direction,e,s,t,0.72,2.0,0.73,
                   f"Value area edge fade at {'VAH' if at_vah else 'VAL'} {edge:.2f}. Target POC {poc:.2f}.")


# ── FORGE-TS-16: VOL-03 Low Volume Node Express ──────────────────────────────
def ts16_lvn_express(current_price, lvn_level, next_hvn, atr, direction) -> TSSignal:
    at_lvn = abs(current_price - lvn_level) / atr < 0.4 if atr > 0 else False
    if not at_lvn:
        return _no_signal("FORGE-TS-16","VOL-03","LVN Express",0.73,2.5,"Not at low volume node.")
    e = current_price
    s = lvn_level - atr*0.5 if direction=="long" else lvn_level + atr*0.5
    t = next_hvn   # Express to next high volume node
    return _signal("FORGE-TS-16","VOL-03","LVN Express",True,direction,e,s,t,0.73,2.5,0.74,
                   f"LVN at {lvn_level:.2f} — price will express through low-volume area to HVN {next_hvn:.2f}.")


# ── FORGE-TS-17: VOL-04 High Volume Node Cluster Trade ───────────────────────
def ts17_hvn_cluster(current_price, hvn_levels, atr, direction) -> TSSignal:
    nearest_hvn = min(hvn_levels, key=lambda h: abs(h - current_price)) if hvn_levels else current_price
    at_hvn = abs(current_price - nearest_hvn) / atr < 0.3 if atr > 0 else False
    if not at_hvn:
        return _no_signal("FORGE-TS-17","VOL-04","HVN Cluster Trade",0.70,2.0,"Not at HVN cluster.")
    e = current_price
    s = nearest_hvn - atr if direction=="long" else nearest_hvn + atr
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    return _signal("FORGE-TS-17","VOL-04","HVN Cluster Trade",True,direction,e,s,t,0.70,2.0,0.68,
                   f"HVN cluster at {nearest_hvn:.2f}. Institutions transact here. Entry {e:.2f}.")


# ── FORGE-TS-18: VOL-05 Anchored VWAP Confluence ─────────────────────────────
def ts18_anchored_vwap(current_price, anchored_vwap, session_vwap, atr, direction) -> TSSignal:
    near_anchor = abs(current_price - anchored_vwap) / atr < 0.4 if atr > 0 else False
    both_align  = (direction=="long" and current_price > session_vwap and near_anchor) or \
                  (direction=="short" and current_price < session_vwap and near_anchor)
    if not both_align:
        return _no_signal("FORGE-TS-18","VOL-05","Anchored VWAP Confluence",0.71,2.0,"No AVWAP+VWAP confluence.")
    e = current_price
    s = anchored_vwap - atr*0.5 if direction=="long" else anchored_vwap + atr*0.5
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    return _signal("FORGE-TS-18","VOL-05","Anchored VWAP Confluence",True,direction,e,s,t,0.71,2.0,0.70,
                   f"AVWAP {anchored_vwap:.2f} + VWAP {session_vwap:.2f} confluence. Entry {e:.2f}.")


# ── FORGE-TS-19: ORD-01 Delta Divergence Reversal ────────────────────────────
def ts19_delta_divergence(price_making_new_extreme, delta_diverging, current_price, atr, direction) -> TSSignal:
    if not (price_making_new_extreme and delta_diverging):
        return _no_signal("FORGE-TS-19","ORD-01","Delta Divergence",0.75,2.5,"No delta divergence.")
    e = current_price
    s = e + atr if direction=="short" else e - atr
    t = e - atr*2.5 if direction=="short" else e + atr*2.5
    return _signal("FORGE-TS-19","ORD-01","Delta Divergence",True,direction,e,s,t,0.75,2.5,0.80,
                   f"Price extreme + delta divergence. Institutional absorption. Entry {e:.2f}.")


# ── FORGE-TS-20: ORD-02 Footprint Absorption Entry ───────────────────────────
def ts20_footprint_absorption(absorption_detected, absorption_level, current_price, atr, direction) -> TSSignal:
    if not absorption_detected:
        return _no_signal("FORGE-TS-20","ORD-02","Footprint Absorption",0.73,2.5,"No footprint absorption.")
    dist = abs(current_price - absorption_level) / atr if atr > 0 else 0
    if dist > 0.5:
        return _no_signal("FORGE-TS-20","ORD-02","Footprint Absorption",0.73,2.5,"Too far from absorption.")
    e = current_price
    s = absorption_level - atr*0.5 if direction=="long" else absorption_level + atr*0.5
    t = e + atr*2.5 if direction=="long" else e - atr*2.5
    return _signal("FORGE-TS-20","ORD-02","Footprint Absorption",True,direction,e,s,t,0.73,2.5,0.76,
                   f"Footprint absorption at {absorption_level:.2f}. Institutional stopping of sellers/buyers.")


# ── FORGE-TS-21: ORD-03 Order Block Stacking Breakout ────────────────────────
def ts21_ob_stacking(stacked_ob_count, breakout_confirmed, current_price, ob_level, atr, direction) -> TSSignal:
    if stacked_ob_count < 2 or not breakout_confirmed:
        return _no_signal("FORGE-TS-21","ORD-03","OB Stacking Breakout",0.71,2.0,"Need 2+ stacked OBs + breakout.")
    e = current_price
    s = ob_level - atr if direction=="long" else ob_level + atr
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    conf = min(0.85, 0.65 + stacked_ob_count * 0.05)
    return _signal("FORGE-TS-21","ORD-03","OB Stacking Breakout",True,direction,e,s,t,0.71,2.0,conf,
                   f"{stacked_ob_count} stacked OBs cleared. Breakout confirmed. Entry {e:.2f}.")


# ── FORGE-TS-22: ORD-04 Bid/Ask Imbalance Cascade ────────────────────────────
def ts22_imbalance_cascade(imbalance_ratio, direction_of_imbalance, current_price, atr, direction) -> TSSignal:
    if imbalance_ratio < 3.0:
        return _no_signal("FORGE-TS-22","ORD-04","Imbalance Cascade",0.70,2.0,f"Imbalance {imbalance_ratio:.1f}× < 3× min.")
    aligned = direction_of_imbalance == direction
    if not aligned:
        return _no_signal("FORGE-TS-22","ORD-04","Imbalance Cascade",0.70,2.0,"Imbalance direction mismatch.")
    e = current_price
    s = e - atr if direction=="long" else e + atr
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    conf = min(0.85, 0.60 + imbalance_ratio * 0.03)
    return _signal("FORGE-TS-22","ORD-04","Imbalance Cascade",True,direction,e,s,t,0.70,2.0,conf,
                   f"{imbalance_ratio:.1f}× bid/ask imbalance. Cascade effect expected. Entry {e:.2f}.")


# ── FORGE-TS-23: SES-01 NY Kill Zone Power Hour ──────────────────────────────
def ts23_ny_kill_zone(time_et: time, session_bias, gex_direction, current_price, vwap, atr,
                      prior_day_high, prior_day_low) -> TSSignal:
    in_kz = time(9,30) <= time_et <= time(11,0)
    if not in_kz:
        return _no_signal("FORGE-TS-23","SES-01","NY Kill Zone",0.74,2.5,f"Outside kill zone: {time_et}.")
    direction = "long" if (session_bias=="bullish" or gex_direction=="negative") else "short"
    e = max(current_price, vwap) if direction=="long" else min(current_price, vwap)
    s = min(prior_day_low, vwap-atr*0.3) if direction=="long" else max(prior_day_high, vwap+atr*0.3)
    t = e + (e-s)*2.5 if direction=="long" else e - (s-e)*2.5
    conf = 0.80 if gex_direction=="negative" else 0.65
    return _signal("FORGE-TS-23","SES-01","NY Kill Zone",True,direction,e,s,t,0.74,2.5,conf,
                   f"NY Kill Zone {time_et}. Bias: {session_bias}. GEX: {gex_direction}. Entry {e:.2f}.")


# ── FORGE-TS-24: SES-02 London-NY Overlap Momentum ───────────────────────────
def ts24_london_ny_overlap(time_et: time, trend_direction, london_high, london_low, current_price, atr) -> TSSignal:
    in_window = time(8,0) <= time_et <= time(11,30)
    if not in_window:
        return _no_signal("FORGE-TS-24","SES-02","London-NY Overlap",0.73,2.0,"Outside overlap window.")
    momentum_ok = (trend_direction=="bullish" and current_price > london_high) or \
                  (trend_direction=="bearish" and current_price < london_low)
    if not momentum_ok:
        return _no_signal("FORGE-TS-24","SES-02","London-NY Overlap",0.73,2.0,"No London range breakout.")
    direction = "long" if trend_direction=="bullish" else "short"
    e = current_price
    s = london_high - atr*0.2 if direction=="long" else london_low + atr*0.2
    t = e + atr*2.0 if direction=="long" else e - atr*2.0
    return _signal("FORGE-TS-24","SES-02","London-NY Overlap",True,direction,e,s,t,0.73,2.0,0.74,
                   f"London-NY overlap momentum. {trend_direction}. Entry {e:.2f}.")


# ── FORGE-TS-25: SES-03 First Hour Reversal Pattern ──────────────────────────
def ts25_first_hour_reversal(time_et: time, first_hour_high, first_hour_low, current_price, vwap, atr) -> TSSignal:
    in_window = time(10,30) <= time_et <= time(11,30)
    if not in_window:
        return _no_signal("FORGE-TS-25","SES-03","First Hour Reversal",0.70,2.0,"Outside reversal window.")
    at_fh_high = abs(current_price - first_hour_high) / atr < 0.3 if atr > 0 else False
    at_fh_low  = abs(current_price - first_hour_low)  / atr < 0.3 if atr > 0 else False
    if at_fh_high:
        direction = "short"
        e = current_price
        s = first_hour_high + atr*0.3
        t = vwap
    elif at_fh_low:
        direction = "long"
        e = current_price
        s = first_hour_low - atr*0.3
        t = vwap
    else:
        return _no_signal("FORGE-TS-25","SES-03","First Hour Reversal",0.70,2.0,"Not at first hour extreme.")
    return _signal("FORGE-TS-25","SES-03","First Hour Reversal",True,direction,e,s,t,0.70,2.0,0.68,
                   f"First hour reversal at {'high' if at_fh_high else 'low'} {e:.2f}. Target VWAP {vwap:.2f}.")


# ── FORGE-TS-26: SES-04 Pre-Close Institutional Positioning ──────────────────
def ts26_preclose_institutional(time_et: time, institutional_bias, current_price, vwap, atr) -> TSSignal:
    in_window = time(14,30) <= time_et <= time(15,30)
    if not in_window:
        return _no_signal("FORGE-TS-26","SES-04","Pre-Close Institutional",0.69,1.8,"Outside pre-close window.")
    if institutional_bias == "neutral":
        return _no_signal("FORGE-TS-26","SES-04","Pre-Close Institutional",0.69,1.8,"No institutional bias detected.")
    direction = "long" if institutional_bias=="bullish" else "short"
    e = current_price
    s = vwap - atr*0.4 if direction=="long" else vwap + atr*0.4
    t = e + atr*1.8 if direction=="long" else e - atr*1.8
    return _signal("FORGE-TS-26","SES-04","Pre-Close Institutional",True,direction,e,s,t,0.69,1.8,0.65,
                   f"Institutional pre-close positioning. {institutional_bias}. Entry {e:.2f}.")


# ── FORGE-TS-27: SES-05 Monday Gap Fill Strategy ─────────────────────────────
def ts27_monday_gap_fill(is_monday, gap_direction, gap_fill_target, current_price, atr, partial_fill_pct) -> TSSignal:
    if not is_monday:
        return _no_signal("FORGE-TS-27","SES-05","Monday Gap Fill",0.72,2.0,"Not Monday.")
    if partial_fill_pct >= 0.70:
        return _no_signal("FORGE-TS-27","SES-05","Monday Gap Fill",0.72,2.0,"Gap already 70%+ filled.")
    direction = "long" if gap_direction=="up_gap_filling_down" else "short"
    e = current_price
    s = e + atr*0.5 if direction=="short" else e - atr*0.5
    t = gap_fill_target
    return _signal("FORGE-TS-27","SES-05","Monday Gap Fill",True,direction,e,s,t,0.72,2.0,0.72,
                   f"Monday gap fill {partial_fill_pct:.0%} complete. Target: {gap_fill_target:.2f}.")


# ── FORGE-TS-28: INS-01 Unusual Options Flow Follow ──────────────────────────
def ts28_unusual_options_flow(flow_confirmed, catalyst_type, current_price, strike_price, atr, direction) -> TSSignal:
    if not flow_confirmed:
        return _no_signal("FORGE-TS-28","INS-01","Unusual Options Flow",0.75,3.0,"No unusual flow confirmed.")
    e = current_price
    s = e - atr*1.0 if direction=="long" else e + atr*1.0
    t = e + atr*3.0 if direction=="long" else e - atr*3.0
    return _signal("FORGE-TS-28","INS-01","Unusual Options Flow",True,direction,e,s,t,0.75,3.0,0.82,
                   f"Unusual {catalyst_type} flow confirmed. Institutional intelligence. Entry {e:.2f}, target {t:.2f}.")


# ── FORGE-TS-29: INS-02 Dark Pool Print Entry ────────────────────────────────
def ts29_dark_pool_print(print_detected, print_level, print_size_m, current_price, atr, direction) -> TSSignal:
    if not print_detected:
        return _no_signal("FORGE-TS-29","INS-02","Dark Pool Print",0.73,2.5,"No dark pool print detected.")
    if print_size_m < 1.0:  # Minimum $1M print
        return _no_signal("FORGE-TS-29","INS-02","Dark Pool Print",0.73,2.5,f"Print too small: ${print_size_m:.1f}M.")
    near_print = abs(current_price - print_level) / atr < 0.5 if atr > 0 else False
    if not near_print:
        return _no_signal("FORGE-TS-29","INS-02","Dark Pool Print",0.73,2.5,"Price too far from print level.")
    e = current_price
    s = print_level - atr*0.5 if direction=="long" else print_level + atr*0.5
    t = e + atr*2.5 if direction=="long" else e - atr*2.5
    conf = min(0.88, 0.68 + print_size_m * 0.02)
    return _signal("FORGE-TS-29","INS-02","Dark Pool Print",True,direction,e,s,t,0.73,2.5,conf,
                   f"Dark pool print ${print_size_m:.0f}M at {print_level:.2f}. Institutional accumulation.")


# ── FORGE-TS-30: INS-03 COT Extreme Reversal ─────────────────────────────────
def ts30_cot_extreme(commercial_net_position, extreme_threshold, current_price, vwap, atr, direction) -> TSSignal:
    is_extreme = abs(commercial_net_position) >= extreme_threshold
    if not is_extreme:
        return _no_signal("FORGE-TS-30","INS-03","COT Extreme Reversal",0.71,3.0,
                           f"COT not extreme: {commercial_net_position:+.0f} vs threshold {extreme_threshold:+.0f}.")
    # COT extremes signal trend reversals — commercials are the smart money
    cot_direction = "long" if commercial_net_position > 0 else "short"
    if cot_direction != direction:
        return _no_signal("FORGE-TS-30","INS-03","COT Extreme Reversal",0.71,3.0,"Trade direction vs COT signal mismatch.")
    e = current_price
    s = e - atr*1.0 if direction=="long" else e + atr*1.0
    t = e + atr*3.0 if direction=="long" else e - atr*3.0
    return _signal("FORGE-TS-30","INS-03","COT Extreme Reversal",True,direction,e,s,t,0.71,3.0,0.74,
                   f"COT extreme: commercials net {commercial_net_position:+.0f}. Institutional reversal setup.")


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY DISPATCHER — call any strategy by FORGE-TS ID
# ─────────────────────────────────────────────────────────────────────────────

STRATEGY_COUNT: int = 30

ALL_STRATEGY_IDS = [f"FORGE-TS-{i:02d}" for i in range(1, 31)]
