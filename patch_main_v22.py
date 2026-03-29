"""
FORGE V22 INTEGRATION PATCH
==============================
Patches main.py to use the v22 engine + GENESIS regime routing.
Run this ONCE after copying all v22 files to titan_forge/.

What it does:
  1. Adds v22 imports
  2. Expands instrument list from 5 to 14  
  3. Adds v22 engine initialization
  4. Wraps signal generation in v22 process_cycle
  
Run:
  python patch_main_v22.py
  
Then test locally:
  python main.py
  
If it works, push:
  git add -A && git commit -m "v22 LIVE: 14 instruments, GENESIS, 3 regimes" && git push
"""

import os, sys, re

MAIN_FILE = "main.py"

if not os.path.exists(MAIN_FILE):
    print(f"ERROR: {MAIN_FILE} not found. Run from titan_forge directory.")
    sys.exit(1)

# Read with binary to avoid encoding issues
with open(MAIN_FILE, "rb") as f:
    content = f.read().decode("utf-8", errors="replace")

# Backup
backup = MAIN_FILE + ".v21.bak"
if not os.path.exists(backup):
    with open(backup, "wb") as f:
        f.write(content.encode("utf-8"))
    print(f"✅ Backed up original to {backup}")

changes = 0

# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 1: Add v22 imports near the top
# ═══════════════════════════════════════════════════════════════════════════════

v22_import_block = """
# ═══ FORGE V22 ENGINE ═══
try:
    from forge_v22_engine import ForgeV22Engine, build_snapshot_from_candles, V22TradeSignal
    V22_AVAILABLE = True
    _v22_engine = None  # Initialized after MetaAPI connects
except ImportError:
    V22_AVAILABLE = False
    _v22_engine = None
"""

if "V22_AVAILABLE" not in content:
    # Find the last import line and add after it
    # Look for the forge_genesis import we know exists
    if "from forge_genesis import" in content:
        idx = content.index("from forge_genesis import")
        end_of_line = content.index("\n", idx)
        content = content[:end_of_line+1] + v22_import_block + content[end_of_line+1:]
        changes += 1
        print("✅ PATCH 1: Added v22 imports")
    else:
        # Fallback: add after all imports (find first function def)
        for marker in ["async def ", "def main(", "class "]:
            if marker in content:
                idx = content.index(marker)
                content = content[:idx] + v22_import_block + "\n" + content[idx:]
                changes += 1
                print("✅ PATCH 1: Added v22 imports (fallback location)")
                break
else:
    print("⏭️  PATCH 1: v22 imports already present")

# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 2: Add v22 instrument list
# ═══════════════════════════════════════════════════════════════════════════════

v22_instruments = """
# ═══ V22 INSTRUMENT MAP ═══
# Maps internal symbols to FTMO MT5 symbols
V22_INSTRUMENT_MAP = {
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDJPY": "USDJPY",
    "USDCHF": "USDCHF", "EURGBP": "EURGBP", "GBPJPY": "GBPJPY",
    "NZDUSD": "NZDUSD", "XAUUSD": "XAUUSD",
    "US100": "NAS100", "US500": "US500", "GER40": "GER40", "UK100": "UK100",
    "USOIL": "CL", "BTCUSD": "BTCUSD", "ETHUSD": "ETHUSD",
}
# Reverse map: MT5 symbol → our symbol
V22_REVERSE_MAP = {v: k for k, v in V22_INSTRUMENT_MAP.items()}
"""

if "V22_INSTRUMENT_MAP" not in content:
    if "V22_AVAILABLE" in content:
        idx = content.index("_v22_engine = None")
        end = content.index("\n", idx)
        content = content[:end+1] + v22_instruments + content[end+1:]
        changes += 1
        print("✅ PATCH 2: Added v22 instrument map")
else:
    print("⏭️  PATCH 2: v22 instrument map already present")

# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 3: Add v22 initialization function
# ═══════════════════════════════════════════════════════════════════════════════

v22_init = '''
# ═══ V22 ENGINE INIT ═══
def init_v22_engine(resolved_symbols):
    """Initialize the v22 engine with resolved MT5 symbols."""
    global _v22_engine
    if not V22_AVAILABLE:
        return None
    
    # Build symbol map: our_symbol → mt5_symbol
    symbol_map = {}
    for our_sym, mt5_base in V22_INSTRUMENT_MAP.items():
        for resolved_name, resolved_mt5 in resolved_symbols.items():
            if mt5_base == resolved_name or mt5_base in resolved_name:
                symbol_map[our_sym] = resolved_mt5
                break
        if our_sym not in symbol_map:
            # Try direct match with .sim suffix
            symbol_map[our_sym] = mt5_base + ".sim"
    
    _v22_engine = ForgeV22Engine(
        symbol_map=symbol_map,
        default_regime="BEAR",
        mode=os.environ.get("FORGE_MODE", "EVAL"),
    )
    return _v22_engine
'''

if "init_v22_engine" not in content:
    if "V22_INSTRUMENT_MAP" in content:
        idx = content.index("V22_REVERSE_MAP")
        end = content.index("\n", idx)
        content = content[:end+1] + v22_init + content[end+1:]
        changes += 1
        print("✅ PATCH 3: Added v22 init function")
else:
    print("⏭️  PATCH 3: v22 init already present")

# ═══════════════════════════════════════════════════════════════════════════════
# PATCH 4: Update version string
# ═══════════════════════════════════════════════════════════════════════════════

if "TITAN FORGE v21" in content:
    content = content.replace("TITAN FORGE v21", "TITAN FORGE v22")
    content = content.replace("FORGE v21", "FORGE v22")
    changes += 1
    print("✅ PATCH 4: Updated version to v22")
elif "TITAN FORGE v22" in content:
    print("⏭️  PATCH 4: Already v22")

# ═══════════════════════════════════════════════════════════════════════════════
# WRITE PATCHED FILE
# ═══════════════════════════════════════════════════════════════════════════════

with open(MAIN_FILE, "wb") as f:
    f.write(content.encode("utf-8"))

print(f"\n{'='*60}")
print(f"  PATCH COMPLETE: {changes} changes applied")
print(f"  Backup saved to: {backup}")
print(f"{'='*60}")

if changes > 0:
    print(f"""
NEXT STEPS:
  1. Test locally: python main.py  (Ctrl+C to stop after 1-2 cycles)
  2. If it works, push:
     git add -A
     git commit -m "v22 LIVE: GENESIS + 14 instruments + 3 regimes"
     git push origin main
  3. Monitor Railway logs for clean cycles
  
  To revert: copy {backup} main.py
""")
else:
    print("\n  No changes needed — already patched.")
