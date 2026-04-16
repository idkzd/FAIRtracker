"""
Telegram notifier — send & edit alerts via Bot API.
"""
from __future__ import annotations
import logging, aiohttp
from typing import Optional
from config import TG_BOT_TOKEN, TG_CHAT_ID

log = logging.getLogger("telegram")


async def send_message(session: aiohttp.ClientSession, text: str) -> Optional[int]:
    """Send a new message. Returns message_id or None."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log.warning("TG_BOT_TOKEN / TG_CHAT_ID not set — skipping")
        return None
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
            if r.status == 200 and data.get("ok"):
                return data["result"]["message_id"]
            log.error("TG send error %s: %s", r.status, data)
            return None
    except Exception as e:
        log.error("TG send exception: %s", e)
        return None


async def edit_message(session: aiohttp.ClientSession, message_id: int, text: str) -> bool:
    """Edit an existing message. Returns True on success."""
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/editMessageText"
    payload = {
        "chat_id": TG_CHAT_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
            data = await r.json()
            if r.status == 200 and data.get("ok"):
                return True
            if "message is not modified" in str(data):
                return True
            log.error("TG edit error %s: %s", r.status, data)
            return False
    except Exception as e:
        log.error("TG edit exception: %s", e)
        return False
