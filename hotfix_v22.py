"""
V22 HOTFIX — Tighter trails + smart exits + remove dead tickers
Run from titan_forge directory: python hotfix_v22.py
"""
import re

with open("main.py", "rb") as f:
    s = f.read().decode("utf-8", errors="replace")

changes = 0

# ═══════════════════════════════════════════════════════════════════
# FIX 1: Remove GER40, UK100, ETHUSD (not available on FTMO OANDA)
# ═══════════════════════════════════════════════════════════════════

for dead in [
    '    "GER40":["GER40.sim","DE40.sim","GER40"],',
    '    "UK100":["UK100.sim","FTSE.sim","UK100"],',
    '    "ETHUSD":["ETHUSD.sim","ETHEREUM.sim","ETHUSD"],',
]:
    if dead in s:
        s = s.replace(dead + "\n", "")
        changes += 1

# Also remove from POLYGON_MAP
for dead in [
    '"GER40":"I:DAX",', '"UK100":"I:UKX",', '"ETHUSD":"X:ETHUSD",',
]:
    if dead in s:
        s = s.replace(dead, "")
        changes += 1

# Remove from ATR_FB
for dead in ['"GER40":150.0,', '"UK100":80.0,', '"ETHUSD":100.0,']:
    if dead in s:
        s = s.replace(dead, "")
        changes += 1

# Remove ETHUSD from CRYPTO set
if '"ETHUSD"' in s:
    s = s.replace('CRYPTO = {"BTCUSD","ETHUSD"}', 'CRYPTO = {"BTCUSD"}')
    changes += 1

print(f"  FIX 1: Removed dead tickers ({changes} changes)")

# ═══════════════════════════════════════════════════════════════════
# FIX 2: Tighter trailing stops
# ═══════════════════════════════════════════════════════════════════

old_trail = """            ns=None
            if cr>=0.5 and better(be): ns=be
            if cr>=1.5 and better(be+risk*0.5 if il else be-risk*0.5): ns=be+risk*0.5 if il else be-risk*0.5
            if cr>=2.0 and better(be+risk*1.0 if il else be-risk*1.0): ns=be+risk*1.0 if il else be-risk*1.0
            if cr>=3.0 and better(be+risk*2.0 if il else be-risk*2.0): ns=be+risk*2.0 if il else be-risk*2.0"""

new_trail = """            ns=None
            if cr>=0.5 and better(be): ns=be
            if cr>=0.8 and better(be+risk*0.2 if il else be-risk*0.2): ns=be+risk*0.2 if il else be-risk*0.2
            if cr>=1.0 and better(be+risk*0.3 if il else be-risk*0.3): ns=be+risk*0.3 if il else be-risk*0.3
            if cr>=1.3 and better(be+risk*0.5 if il else be-risk*0.5): ns=be+risk*0.5 if il else be-risk*0.5
            if cr>=1.5 and better(be+risk*0.7 if il else be-risk*0.7): ns=be+risk*0.7 if il else be-risk*0.7
            if cr>=2.0 and better(be+risk*1.2 if il else be-risk*1.2): ns=be+risk*1.2 if il else be-risk*1.2
            if cr>=2.5 and better(be+risk*1.7 if il else be-risk*1.7): ns=be+risk*1.7 if il else be-risk*1.7
            if cr>=3.0 and better(be+risk*2.3 if il else be-risk*2.3): ns=be+risk*2.3 if il else be-risk*2.3"""

if old_trail in s:
    s = s.replace(old_trail, new_trail)
    changes += 1
    print("  FIX 2: Tighter trailing stops ✅")
else:
    print("  FIX 2: Trail block not found — checking alternate format")
    # Try to find it by the cr>=0.5 pattern
    if "cr>=0.5 and better(be)" in s and "cr>=1.5 and better" in s:
        print("  FIX 2: Found trail logic but format differs — manual edit needed")
    else:
        print("  FIX 2: WARNING — could not find trailing stop code")

# ═══════════════════════════════════════════════════════════════════
# FIX 3: Add smart exit detection function
# ═══════════════════════════════════════════════════════════════════

smart_exit_code = '''
# ═══ SMART EXIT DETECTION ═══
def should_smart_exit(snap, direction, current_r):
    """Detect momentum exhaustion — EXIT only, never blocks entries.
    Returns (should_exit, reason) — only triggers when in profit (current_r > 0.5)"""
    if current_r < 0.5:
        return False, ""
    
    # 1. RSI divergence: price in our favor but RSI reversing
    if direction == "SHORT" and snap.rsi < 35 and snap.rsi > snap.stoch_d:
        if current_r >= 1.0:
            return True, "RSI oversold bounce"
    if direction == "LONG" and snap.rsi > 65 and snap.rsi < snap.stoch_d:
        if current_r >= 1.0:
            return True, "RSI overbought reversal"
    
    # 2. Stochastic crossing against us
    if direction == "SHORT" and snap.stoch_k > snap.stoch_d and snap.stoch_k_prev < snap.stoch_d_prev:
        if current_r >= 0.8:
            return True, "Stoch bullish cross on SHORT"
    if direction == "LONG" and snap.stoch_k < snap.stoch_d and snap.stoch_k_prev > snap.stoch_d_prev:
        if current_r >= 0.8:
            return True, "Stoch bearish cross on LONG"
    
    # 3. Price hit opposite BB band (stretched too far)
    if direction == "SHORT" and snap.bid <= snap.bb_lower and current_r >= 1.0:
        return True, "Hit lower BB band"
    if direction == "LONG" and snap.ask >= snap.bb_upper and current_r >= 1.0:
        return True, "Hit upper BB band"
    
    # 4. Reversal candle pattern (big wick against us)
    if len(snap.closes) >= 2 and len(snap.opens) >= 2:
        last_body = abs(float(snap.closes[-1]) - float(snap.opens[-1]))
        last_range = float(snap.highs[-1]) - float(snap.lows[-1])
        if last_range > 0:
            wick_ratio = 1.0 - (last_body / last_range)
            if wick_ratio > 0.7 and current_r >= 1.0:  # Doji or hammer
                if direction == "SHORT" and float(snap.closes[-1]) > float(snap.opens[-1]):
                    return True, "Bullish reversal candle"
                if direction == "LONG" and float(snap.closes[-1]) < float(snap.opens[-1]):
                    return True, "Bearish reversal candle"
    
    return False, ""

'''

if "should_smart_exit" not in s:
    # Insert before the manage_pos function
    marker = "async def manage_pos(adapter,account):"
    if marker in s:
        idx = s.index(marker)
        s = s[:idx] + smart_exit_code + s[idx:]
        changes += 1
        print("  FIX 3: Smart exit detection added ✅")
else:
    print("  FIX 3: Smart exit already present")

# ═══════════════════════════════════════════════════════════════════
# FIX 4: Wire smart exit into position management
# ═══════════════════════════════════════════════════════════════════

# Add smart exit check inside manage_pos, after trailing stop logic
old_trail_end = """            if ns:
                try: await adapter.modify_position(pos.position_id,new_stop_loss=round(ns,5)); logger.info("[TRAIL] %s %.1fR→SL=%.5f",pos.position_id,cr,ns)
                except Exception as e: logger.error("[TRAIL] %s: %s",pos.position_id,e)
        except: pass"""

new_trail_end = """            if ns:
                try: await adapter.modify_position(pos.position_id,new_stop_loss=round(ns,5)); logger.info("[TRAIL] %s %.1fR→SL=%.5f",pos.position_id,cr,ns)
                except Exception as e: logger.error("[TRAIL] %s: %s",pos.position_id,e)
            # Smart exit: check if momentum is dying
            if cr >= 0.8:
                inst_name = str(getattr(pos,'instrument','') or getattr(pos,'symbol',''))
                pos_dir = "LONG" if il else "SHORT"
                for sym,mt in _resolved.items():
                    if mt and mt in inst_name:
                        cd = get_candles(sym)
                        if cd:
                            sn = make_snap(sym, cd, cur, cur)
                            if sn:
                                should_exit, reason = should_smart_exit(sn, pos_dir, cr)
                                if should_exit:
                                    try:
                                        await adapter.close_position(pos.position_id)
                                        logger.info("🧠 SMART EXIT: %s %.1fR — %s", pos.position_id, cr, reason)
                                        send_telegram(f"🧠 <b>SMART EXIT</b>\\n{sym} {pos_dir} closed at +{cr:.1f}R\\nReason: {reason}")
                                    except Exception as e:
                                        logger.error("[SMART_EXIT] %s: %s", pos.position_id, e)
                        break
        except: pass"""

if old_trail_end in s:
    s = s.replace(old_trail_end, new_trail_end)
    changes += 1
    print("  FIX 4: Smart exit wired into position management ✅")
else:
    print("  FIX 4: Could not find trail end block — check manually")

# ═══════════════════════════════════════════════════════════════════
# WRITE
# ═══════════════════════════════════════════════════════════════════

with open("main.py", "wb") as f:
    f.write(s.encode("utf-8"))

print(f"\n{'='*50}")
print(f"  HOTFIX COMPLETE: {changes} changes")
print(f"{'='*50}")
print(f"\nTrailing stop progression (NEW):")
print(f"  +0.5R → breakeven")
print(f"  +0.8R → lock +0.2R")
print(f"  +1.0R → lock +0.3R")
print(f"  +1.3R → lock +0.5R")
print(f"  +1.5R → lock +0.7R")
print(f"  +2.0R → lock +1.2R")
print(f"  +2.5R → lock +1.7R")
print(f"  +3.0R → lock +2.3R")
print(f"\nSmart exit triggers (EXIT ONLY):")
print(f"  - RSI oversold/overbought bounce")
print(f"  - Stochastic crossing against trade")
print(f"  - Price hits opposite BB band")
print(f"  - Reversal candle pattern (doji/hammer)")
print(f"\nPush:")
print(f'  git add -A && git commit -m "v22 hotfix: tight trails + smart exit + remove dead tickers" && git push origin main')
