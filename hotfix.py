"""
FORGE v21 HOTFIX — Run this once to fix the VWAP crash
"""
import os

# Fix 1: forge_target.py — add None guard on vwap
target_file = "forge_target.py"
if os.path.exists(target_file):
    with open(target_file, "r") as f:
        content = f.read()
    
    # Replace all instances of "if vwap > 0:" with null-safe version
    old = "if vwap > 0:"
    new = "if vwap is not None and vwap > 0:"
    
    if old in content:
        content = content.replace(old, new)
        with open(target_file, "w") as f:
            f.write(content)
        print(f"FIXED: {target_file} — added None guard on vwap")
    else:
        print(f"SKIP: {target_file} — already patched or pattern not found")
else:
    print(f"ERROR: {target_file} not found")

# Fix 2: main.py — add default cycle_speed
main_file = "main.py"
if os.path.exists(main_file):
    with open(main_file, "r") as f:
        content = f.read()
    
    # Add cycle_speed default at the start of live_trading_loop
    old_func = "async def live_trading_loop("
    if old_func in content and "cycle_speed = 60" not in content:
        # Find the function and add default after its first line
        idx = content.index(old_func)
        # Find the next newline after the function def line (after the ":")
        colon_idx = content.index(":", idx + len(old_func))
        newline_idx = content.index("\n", colon_idx)
        
        # Insert cycle_speed default
        content = content[:newline_idx + 1] + "    cycle_speed = 60  # default fallback\n" + content[newline_idx + 1:]
        
        with open(main_file, "w") as f:
            f.write(content)
        print(f"FIXED: {main_file} — added cycle_speed default")
    else:
        print(f"SKIP: {main_file} — already patched or pattern not found")
else:
    print(f"ERROR: {main_file} not found")

print("\nDone! Now run:")
print("  git add -A")
print('  git commit -m "hotfix: VWAP None guard + cycle_speed default"')
print("  git push origin main")
