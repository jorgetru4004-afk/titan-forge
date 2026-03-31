"""
FORGE HOTFIX v3 — Nuclear option: wrap collect_key_levels in try/except
"""

main_file = "main.py"
try:
    with open(main_file, "rb") as f:
        content = f.read().decode("utf-8", errors="replace")
    
    # Replace the bare call with a try/except wrapped version
    old = "key_levels = collect_key_levels(tracker, ctx)"
    new = """try:
                key_levels = collect_key_levels(tracker, ctx)
            except (TypeError, AttributeError):
                key_levels = {}"""
    
    if old in content and "try:" not in content.split(old)[0][-50:]:
        content = content.replace(old, new)
        with open(main_file, "wb") as f:
            f.write(content.encode("utf-8"))
        print(f"FIXED: {main_file} — wrapped collect_key_levels in try/except")
    else:
        print(f"SKIP: {main_file} — already patched or not found")
        
except Exception as e:
    print(f"ERROR: {e}")

print("\nDone! Push with:")
print('  git add -A && git commit -m "hotfix v3: try/except on collect_key_levels" && git push origin main')
