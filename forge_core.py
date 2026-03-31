"""
FORGE CORE — Telegram alerts only.
Stripped of all V21 modules (session management, setup weights, price cache,
instrument tracker, signal verdict, news blackout, evidence, market context).
"""

import logging
import os
import ssl
import urllib.request

logger = logging.getLogger("titan_forge.core")

# ── TELEGRAM ──
_TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5264397522")

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def send_telegram(text: str) -> None:
    if not _TELEGRAM_BOT_TOKEN:
        logger.debug("[TELEGRAM] No bot token — skipping.")
        return
    try:
        import urllib.parse
        url = (f"https://api.telegram.org/bot{_TELEGRAM_BOT_TOKEN}/sendMessage?"
               f"chat_id={_TELEGRAM_CHAT_ID}&parse_mode=HTML&"
               f"text={urllib.parse.quote(text[:4000])}")
        req = urllib.request.Request(url, headers={"User-Agent": "TITAN-FORGE"})
        with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
            resp.read()
    except Exception as e:
        logger.warning("[TELEGRAM] Send failed (non-fatal): %s", e)
