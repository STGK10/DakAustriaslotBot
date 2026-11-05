import asyncio
import logging
import os
import re
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

from portal import DakarPortal, CATEGORIES
from storage import Store

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BASE_URL = os.getenv("BOOKING_URL", "https://appointment.bmeia.gv.at/?Office=Dakar")
INTERVAL = max(120, int(os.getenv("CHECK_INTERVAL_SECONDS", "180")))
HEADLESS = os.getenv("HEADLESS", "true").lower() not in {"0", "false", "no"}
DB_PATH = os.getenv("DB_PATH", "/data/slots.sqlite3")
DEBUG_DIR = Path(os.getenv("DEBUG_DIR", "/data/debug"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("dakar-monitor")

if not BOT_TOKEN:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env")
if not ADMIN_CHAT_ID:
    raise RuntimeError("Set TELEGRAM_CHAT_ID in .env. Use /id once to discover your chat ID.")

store = Store(DB_PATH)
monitor_enabled = True
last_check: dict[str, str] = {}
last_error: dict[str, str] = {}
monitor_lock = asyncio.Lock()


def admin_only(update: Update) -> bool:
    return bool(update.effective_chat and str(update.effective_chat.id) == ADMIN_CHAT_ID)


async def reply(update: Update, message: str):
    if update.message:
        await update.message.reply_text(message, disable_web_page_preview=True)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply(update, f"Your Telegram chat ID is: {update.effective_chat.id if update.effective_chat else 'unknown'}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        await reply(update, "Not authorized. Add your chat ID to TELEGRAM_CHAT_ID in the server .env.")
        return
    await reply(
        update,
        "Dakar Appointment Monitor is ready.\n\n"
        "Monitoring: Residence permit + Legalization\n"
        "People: 1\n"
        f"Check interval: {INTERVAL}s per category (minimum 120s)\n\n"
        "/status — monitor status\n/check — check both categories now\n"
        "/residence — check residence permit\n/legalization — check legalization\n"
        "/stop — pause automatic checks\n/startmonitor — resume automatic checks\n"
        "/help — commands"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        await reply(update, "Not authorized.")
        return
    lines = [
        f"Automatic monitoring: {'ON' if monitor_enabled else 'PAUSED'}",
        f"Configured people: 1",
        f"Interval: {INTERVAL}s per category",
        f"Booking page: {BASE_URL}",
    ]
    for key, label in CATEGORIES.items():
        lines.append(f"\n{label}")
        lines.append(f"Last check: {last_check.get(key, 'not checked yet')}")
        if key in last_error:
            lines.append(f"Last error: {last_error[key]}")
    await reply(update, "\n".join(lines))


async def send_slot(context: ContextTypes.DEFAULT_TYPE, category: str, slot: dict):
    label = CATEGORIES[category]
    slot_id = slot.get("id") or f"{category}|{slot.get('date','')}|{slot.get('time','')}|{slot.get('text','')}"
    if not store.mark_if_new(category, slot_id, slot):
        return
    date = slot.get("date") or "Date shown on booking page"
    time = slot.get("time") or "Time shown on booking page"
    details = slot.get("text") or ""
    msg = (
        "🟢 POSSIBLE NEW APPOINTMENT SLOT\n\n"
        f"Category: {label}\n"
        "Location: Austrian Embassy, Dakar\n"
        "People: 1\n"
        f"Date: {date}\n"
        f"Time: {time}\n"
    )
    if details:
        msg += f"Portal details: {details[:350]}\n"
    msg += f"\nOpen the official booking page:\n{BASE_URL}\n\n"
    msg += "Please verify the slot in the official portal. Detection is not a reservation."
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, disable_web_page_preview=True)


async def check_category(context: ContextTypes.DEFAULT_TYPE, category: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        page = await browser.new_page(locale="fr-FR")
        portal = DakarPortal(page, BASE_URL, DEBUG_DIR)
        try:
            slots = await portal.check_category(category)
            last_check[category] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            last_error.pop(category, None)
            if slots:
                for slot in slots:
                    await send_slot(context, category, slot)
            else:
                log.info("%s: no candidate slots detected", category)
            return len(slots)
        except Exception as exc:
            last_error[category] = f"{type(exc).__name__}: {exc}"[:300]
            log.exception("Check failed for %s", category)
            raise
        finally:
            await browser.close()


async def run_checks(context: ContextTypes.DEFAULT_TYPE, category: Optional[str] = None):
    if monitor_lock.locked():
        if category is None:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="A check is already running.")
        return
    async with monitor_lock:
        categories = [category] if category else list(CATEGORIES.keys())
        for cat in categories:
            try:
                count = await check_category(context, cat)
                log.info("Finished %s: %d candidate(s)", cat, count)
            except Exception as exc:
                await context.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"⚠️ Could not check {CATEGORIES[cat]}.\n{type(exc).__name__}: {str(exc)[:250]}\n"
                         "The monitor will retry on its next cycle. Check /data/debug on the VPS."
                )


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        await reply(update, "Not authorized.")
        return
    await reply(update, "Checking both appointment categories now. I'll send a separate alert if a candidate slot is found.")
    await run_checks(context)


async def cmd_residence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        await reply(update, "Not authorized.")
        return
    await reply(update, "Checking Residence Permit now…")
    await run_checks(context, "residence")


async def cmd_legalization(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        await reply(update, "Not authorized.")
        return
    await reply(update, "Checking Legalization now…")
    await run_checks(context, "legalization")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitor_enabled
    if not admin_only(update):
        await reply(update, "Not authorized.")
        return
    monitor_enabled = False
    await reply(update, "Automatic monitoring paused. Manual /check still works.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global monitor_enabled
    if not admin_only(update):
        await reply(update, "Not authorized.")
        return
    monitor_enabled = True
    await reply(update, "Automatic monitoring resumed.")


async def scheduled_monitor(context: ContextTypes.DEFAULT_TYPE):
    if monitor_enabled:
        await run_checks(context)


async def post_init(app: Application):
    # First run after startup, then repeat. The minimum interval is deliberately conservative.
    app.job_queue.run_repeating(scheduled_monitor, interval=INTERVAL * 2, first=10, name="dakar-monitor")
    log.info("Bot started; checks every %ss per category", INTERVAL)


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("residence", cmd_residence))
    app.add_handler(CommandHandler("legalization", cmd_legalization))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("startmonitor", cmd_resume))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
