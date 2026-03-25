"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                        NEXUS CAPITAL — TITAN FORGE                          ║
║                   firm_rules.py — Firm Identity Registry                    ║
║                                                                              ║
║  Minimal module providing FirmID enum for mt5_adapter.py compatibility.    ║
║  Full firm intelligence lives in forge_firm.py.                            ║
║                                                                              ║
║  Jorge Trujillo — Founder | Claude — AI Architect | March 2026              ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
from enum import Enum


class FirmID(str, Enum):
    """Prop firm identifiers. String enum so it serializes cleanly."""
    FTMO           = "FTMO"
    APEX           = "APEX"
    DNA_FUNDED     = "DNA_FUNDED"
    FIVEPERCENTERS = "FIVEPERCENTERS"
    TOPSTEP        = "TOPSTEP"  # PERMANENTLY EXCLUDED — but ID exists for completeness
