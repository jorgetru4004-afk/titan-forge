"""
Adds back the old function stubs that v21 main.py imports
"""
target = "forge_genesis.py"
try:
    with open(target, "rb") as f:
        content = f.read().decode("utf-8", errors="replace")
    
    if "auto_evolve" not in content:
        stub = '''

# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY STUBS — v21 main.py imports these
# ═══════════════════════════════════════════════════════════════════════════════

def auto_evolve(*args, **kwargs):
    """Legacy stub for v21 compatibility. No-op."""
    return {}

def get_calibrated_wr(*args, **kwargs):
    """Legacy stub for v21 compatibility. Returns None."""
    return None
'''
        content += stub
        with open(target, "wb") as f:
            f.write(content.encode("utf-8"))
        print(f"FIXED: Added auto_evolve + get_calibrated_wr stubs")
    else:
        print("SKIP: stubs already present")
except Exception as e:
    print(f"ERROR: {e}")

print("Push with:")
print('  git add -A && git commit -m "hotfix: legacy stubs for v21 compat" && git push origin main')
