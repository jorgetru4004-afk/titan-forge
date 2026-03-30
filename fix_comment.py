s=open("main.py","r").read()
old='comment=f"V22|{sig.symbol}|{sig.strategy.value}|{regime}"'
new='comment=f"V22|{sig.symbol[:6]}|{sig.strategy.value[:8]}|{regime[:4]}"'
if old in s:
    s=s.replace(old,new)
    open("main.py","w").write(s)
    print("Fixed: comment field truncated")
else:
    print("Pattern not found - check main.py manually")
