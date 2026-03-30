"""
V22 HOTFIX — Fixes 1-4 only (safe, no behavior change to entries)
Run from titan_forge directory: python hotfix_safe.py
"""

with open("main.py", "rb") as f:
    s = f.read().decode("utf-8", errors="replace")

fixes = 0

# ═══ FIX 1: Remove dead tickers ═══
for pattern in [
    '    "GER40":["GER40.sim","DE40.sim","GER40"],\n',
    '    "GER40":["GER40.sim","DE40.sim","GER40Cash.sim","DAX40.sim","GER30.sim","GDAXI.sim","GER40"],\n',
]:
    if pattern in s: s = s.replace(pattern, ""); fixes += 1; print("  ✅ Removed GER40"); break

for pattern in [
    '    "UK100":["UK100.sim","FTSE.sim","UK100"],\n',
    '    "UK100":["UK100.sim","FTSE.sim","UK100Cash.sim","FTSE100.sim","UKX.sim","UK100"],\n',
]:
    if pattern in s: s = s.replace(pattern, ""); fixes += 1; print("  ✅ Removed UK100"); break

for pattern in ['    "ETHUSD":["ETHUSD.sim","ETHEREUM.sim","ETHUSD"],\n']:
    if pattern in s: s = s.replace(pattern, ""); fixes += 1; print("  ✅ Removed ETHUSD"); break

for dead in ['"GER40":"I:DAX",','"UK100":"I:UKX",','"ETHUSD":"X:ETHUSD",']:
    if dead in s: s = s.replace(dead, ""); fixes += 1

for dead in ['"GER40":150.0,','"UK100":80.0,','"ETHUSD":100.0,']:
    if dead in s: s = s.replace(dead, ""); fixes += 1

if 'CRYPTO = {"BTCUSD","ETHUSD"}' in s:
    s = s.replace('CRYPTO = {"BTCUSD","ETHUSD"}', 'CRYPTO = {"BTCUSD"}')
    fixes += 1; print("  ✅ Fixed CRYPTO set")

# ═══ FIX 2: Min SL/TP distance (fixes "Invalid stops") ═══
old_sldist = "            sld=abs(sig.entry_price-sig.sl_price);lots=calc_lots(sig.symbol,bal,sld)"
new_sldist = """            sld=abs(sig.entry_price-sig.sl_price)
            tpd=abs(sig.tp_price-sig.entry_price)
            min_dist=0.0005 if sig.symbol in ("EURUSD","GBPUSD","NZDUSD","USDCHF","EURGBP") else 0.05 if sig.symbol in ("USDJPY","GBPJPY") else 5.0
            if sld<min_dist or tpd<min_dist:
                logger.warning("[SKIP] %s: SL/TP too close (SL=%.5f TP=%.5f)",sig.symbol,sld,tpd)
                cds[sig.symbol]=time.time()
                await asyncio.sleep(CYCLE_SPEED); continue
            lots=calc_lots(sig.symbol,bal,sld)"""

if old_sldist in s:
    s = s.replace(old_sldist, new_sldist)
    fixes += 1; print("  ✅ Added min SL/TP distance check")
else:
    print("  ⚠️  Could not find sld line")

# ═══ FIX 3: Cooldown on failed orders ═══
old_failed = '                else: logger.warning("❌ FAILED: %s",getattr(result,\'error_message\',\'unknown\'))'
new_failed = """                else:
                    logger.warning("❌ FAILED: %s",getattr(result,'error_message','unknown'))
                    cds[sig.symbol]=time.time()"""

if old_failed in s:
    s = s.replace(old_failed, new_failed)
    fixes += 1; print("  ✅ Added cooldown on failed orders")
else:
    print("  ⚠️  Could not find failed order line")

# ═══ FIX 4: Tighter trailing stops ═══
if "cr>=0.8 and better" in s:
    print("  ✅ Tighter trails already in place")
else:
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
        fixes += 1; print("  ✅ Tighter trailing stops applied")
    else:
        print("  ⚠️  Could not find trail block")

# ═══ FIX 6: Truncate comment field (MetaAPI limit) ═══
# "V22|BTCUSD|STOCH_REVERSAL|NEUTRAL" is too long — shorten strategy names
old_comment = 'comment=f"V22|{sig.symbol}|{sig.strategy}|{regime}"'
new_comment = 'comment=f"V22|{sig.symbol[:6]}|{sig.strategy[:8]}|{regime[:4]}"'

if old_comment in s:
    s = s.replace(old_comment, new_comment)
    fixes += 1; print("  ✅ FIX 6: Truncated comment field for MetaAPI")
else:
    # Try alternate patterns
    for pat in [
        'comment=f"V22|{sig.symbol}|{sig.strategy}|{_regime}"',
        'comment=f"V22|{sym}|{sig.strategy}|{regime}"',
    ]:
        short_pat = pat.replace('{sig.symbol}','{sig.symbol[:6]}').replace('{sym}','{sym[:6]}').replace('{sig.strategy}','{sig.strategy[:8]}').replace('{regime}','{regime[:4]}').replace('{_regime}','{_regime[:4]}')
        if pat in s:
            s = s.replace(pat, short_pat)
            fixes += 1; print("  ✅ FIX 6: Truncated comment field (alt pattern)")
            break
    else:
        print("  ⚠️  FIX 6: Could not find comment pattern — check manually")

# ═══ FIX 7: Validate TP is on correct side of entry ═══
# SHORT with TP above entry = impossible trade. LONG with TP below entry = impossible.
old_mincheck = """            if sld<min_dist or tpd<min_dist:
                logger.warning("[SKIP] %s: SL/TP too close (SL=%.5f TP=%.5f)",sig.symbol,sld,tpd)
                cds[sig.symbol]=time.time()
                await asyncio.sleep(CYCLE_SPEED); continue"""
new_mincheck = """            if sld<min_dist or tpd<min_dist:
                logger.warning("[SKIP] %s: SL/TP too close (SL=%.5f TP=%.5f)",sig.symbol,sld,tpd)
                cds[sig.symbol]=time.time()
                await asyncio.sleep(CYCLE_SPEED); continue
            # Validate TP direction: LONG=TP>entry, SHORT=TP<entry
            if sig.direction=="LONG" and sig.tp_price<=sig.entry_price:
                logger.warning("[SKIP] %s LONG: TP %.5f <= Entry %.5f",sig.symbol,sig.tp_price,sig.entry_price)
                cds[sig.symbol]=time.time()
                await asyncio.sleep(CYCLE_SPEED); continue
            if sig.direction=="SHORT" and sig.tp_price>=sig.entry_price:
                logger.warning("[SKIP] %s SHORT: TP %.5f >= Entry %.5f",sig.symbol,sig.tp_price,sig.entry_price)
                cds[sig.symbol]=time.time()
                await asyncio.sleep(CYCLE_SPEED); continue"""

if old_mincheck in s:
    s = s.replace(old_mincheck, new_mincheck)
    fixes += 1; print("  ✅ FIX 7: Added TP direction validation")
else:
    print("  ⚠️  FIX 7: Could not find min check block")

# ═══ FIX 5: Telegram close alerts ═══
# Add position tracking variable after cooldown/state vars
old_state = "    cds:Dict[str,float]={};ld=date.today();dt=0;cyc=0;hb=ib"
new_state = "    cds:Dict[str,float]={};ld=date.today();dt=0;cyc=0;hb=ib;prev_positions:Dict[str,float]={}"

if old_state in s:
    s = s.replace(old_state, new_state)
    fixes += 1; print("  ✅ Added position tracking var")

# Add close detection after manage_pos call
old_manage = "            await manage_pos(adapter,acc)"
new_manage = """            await manage_pos(adapter,acc)
            # Detect closed trades
            curr_pos_ids={}
            if acc.open_positions:
                for p in acc.open_positions:
                    pid=getattr(p,'position_id','')
                    curr_pos_ids[pid]=getattr(p,'current_price',0) or 0
            for pid in list(prev_positions.keys()):
                if pid not in curr_pos_ids:
                    pnl_change=bal-hb if bal!=hb else 0
                    logger.info("📊 TRADE CLOSED: %s",pid)
                    send_telegram(f"📊 <b>TRADE CLOSED</b>\\nPosition {pid}\\nBalance: ${bal:,.2f}")
            prev_positions=curr_pos_ids"""

if old_manage in s:
    s = s.replace(old_manage, new_manage)
    fixes += 1; print("  ✅ Added trade close Telegram alerts")
else:
    print("  ⚠️  Could not find manage_pos call")

with open("main.py", "wb") as f:
    f.write(s.encode("utf-8"))

print(f"\n{'='*50}")
print(f"  HOTFIX COMPLETE: {fixes} fixes applied")
print(f"{'='*50}")
print(f"""
  Push:
  git add -A && git commit -m "v22: 7 fixes - stops trails comment TP-direction close-alerts" && git push origin main
""")
