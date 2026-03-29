"""
FORGE HOTFIX v2 — Fixes ALL None comparison crashes in forge_target.py
and adds cycle_speed default to main.py
"""
import re

# Fix forge_target.py — guard ALL bare comparisons with > or < 
target_file = "forge_target.py"
try:
    with open(target_file, "rb") as f:
        content = f.read().decode("utf-8", errors="replace")
    
    # Find all "if VARIABLE > 0:" patterns and add None guard
    # Match: "if somevar > 0:" or "if somevar < 0:" etc
    patterns = [
        (r'if (\w+) > (\d+):', r'if \1 is not None and \1 > \2:'),
        (r'if (\w+) < (\d+):', r'if \1 is not None and \1 < \2:'),
        (r'if (\w+) > (\d+\.\d+):', r'if \1 is not None and \1 > \2:'),
        (r'if (\w+) < (\d+\.\d+):', r'if \1 is not None and \1 < \2:'),
        (r'if (\w+) >= (\d+):', r'if \1 is not None and \1 >= \2:'),
        (r'if (\w+) <= (\d+):', r'if \1 is not None and \1 <= \2:'),
    ]
    
    changed = False
    for old_pat, new_pat in patterns:
        new_content = re.sub(old_pat, new_pat, content)
        if new_content != content:
            changed = True
            content = new_content
    
    # Also handle "if VARIABLE is not None and VARIABLE is not None and" (double guard)
    content = content.replace("is not None and is not None and", "is not None and")
    
    if changed:
        with open(target_file, "wb") as f:
            f.write(content.encode("utf-8"))
        print(f"FIXED: {target_file} — added None guards to ALL comparisons")
    else:
        print(f"SKIP: {target_file} — already patched")
        
except Exception as e:
    print(f"ERROR on {target_file}: {e}")

# Fix main.py — add cycle_speed default
main_file = "main.py"
try:
    with open(main_file, "rb") as f:
        content = f.read().decode("utf-8", errors="replace")
    
    if "cycle_speed = 60" not in content:
        # Add cycle_speed default right after the function definition
        old = "async def live_trading_loop("
        if old in content:
            idx = content.index(old)
            # Find the closing ")" and then ":"
            paren_count = 0
            i = idx + len(old)
            while i < len(content):
                if content[i] == '(':
                    paren_count += 1
                elif content[i] == ')':
                    if paren_count == 0:
                        break
                    paren_count -= 1
                i += 1
            # Find the ":" after ")"
            colon_idx = content.index(":", i)
            newline_idx = content.index("\n", colon_idx)
            
            content = content[:newline_idx + 1] + "    cycle_speed = 60  # hotfix default\n" + content[newline_idx + 1:]
            
            with open(main_file, "wb") as f:
                f.write(content.encode("utf-8"))
            print(f"FIXED: {main_file} — added cycle_speed default")
        else:
            print(f"SKIP: {main_file} — function not found")
    else:
        print(f"SKIP: {main_file} — already patched")
        
except Exception as e:
    print(f"ERROR on {main_file}: {e}")

print("\nDone! Push with:")
print('  git add -A && git commit -m "hotfix v2: all None guards + cycle_speed" && git push origin main')
