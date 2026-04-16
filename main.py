#!/usr/bin/env python3
"""
FairTracker — monitors FAIR vs LAST price divergence on Bybit, Binance, OKX
and sends/edits Telegram alerts.
"""
from __future__ import annotations
import asyncio, logging, sys
import aiohttp

from config import SCAN_INTERVAL
from exchanges import fetch_all
from tracker import Tracker
from telegram_bot import send_message, edit_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


async def main():
    log.info("FairTracker starting  (interval=%.1fs)", SCAN_INTERVAL)
    tracker = Tracker()

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                tickers = await fetch_all(session)
                log.info("Fetched %d tickers", len(tickers))
                actions = tracker.process(tickers)

                for act in actions:
                    if act.kind == "send":
                        msg_id = await send_message(session, act.text)
                        if msg_id:
                            state = tracker.active.get(act.key)
                            if state:
                                state.tg_message_id = msg_id
                        log.info("SENT alert for %s/%s  (msg_id=%s)", act.key[0], act.key[1], msg_id)
                        await asyncio.sleep(0.35)

                    elif act.kind == "edit" and act.message_id:
                        ok = await edit_message(session, act.message_id, act.text)
                        log.info("EDIT alert for %s/%s  ok=%s", act.key[0], act.key[1], ok)
                        await asyncio.sleep(0.35)

            except Exception as e:
                log.exception("Loop error: %s", e)
            await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped.")
        sys.exit(0)
