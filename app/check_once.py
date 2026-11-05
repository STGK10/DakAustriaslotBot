import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from portal import DakarPortal, CATEGORIES
from storage import Store

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dakar-monitor.once")

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
BASE_URL = os.getenv("BOOKING_URL", "https://appointment.bmeia.gv.at/?Office=Dakar")
DB_PATH = os.getenv("DB_PATH", "data/slots.sqlite3")
DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "data/debug"))


def telegram_send(text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=25) as response:
        body = json.loads(response.read().decode())
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API error: {body}")


async def main():
    store = Store(DB_PATH)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(locale="fr-FR")
        portal = DakarPortal(page, BASE_URL, DEBUG_DIR)
        try:
            for category, label in CATEGORIES.items():
                try:
                    candidates = await portal.check_category(category)
                    log.info("%s: %d candidate slot(s)", label, len(candidates))
                    for slot in candidates:
                        slot_id = slot.get("id") or f"{category}|{slot.get('date','')}|{slot.get('time','')}|{slot.get('text','')}"
                        if not store.mark_if_new(category, slot_id, slot):
                            continue
                        msg = (
                            "🟢 POSSIBLE NEW APPOINTMENT SLOT\n\n"
                            f"Category: {label}\n"
                            "Location: Austrian Embassy, Dakar\n"
                            "People: 1\n"
                            f"Date: {slot.get('date') or 'See booking page'}\n"
                            f"Time: {slot.get('time') or 'See booking page'}\n"
                            f"Details: {(slot.get('text') or '')[:300]}\n\n"
                            f"Official booking page:\n{BASE_URL}\n\n"
                            "Please verify availability on the official portal. This is not a reservation."
                        )
                        telegram_send(msg)
                except Exception as exc:
                    log.exception("Failed checking %s", label)
                    telegram_send(
                        f"⚠️ Dakar monitor could not check {label}.\n"
                        f"{type(exc).__name__}: {str(exc)[:250]}\n"
                        "The scheduled monitor will retry on its next run."
                    )
        finally:
            await browser.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
