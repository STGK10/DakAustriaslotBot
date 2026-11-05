import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Any
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

log = logging.getLogger("dakar-monitor.portal")

CATEGORIES = {
    "residence": "Titre de séjour / Residence title / Aufenthaltstitel",
    "legalization": "Légalisation / Legalisation / Beglaubigungen",
}

# Optional overrides in .env. Set these only after inspecting the actual calendar DOM.
# Example: SLOT_SELECTOR="button.appointment-available"
SLOT_SELECTOR = None


class DakarPortal:
    def __init__(self, page: Page, base_url: str, debug_dir: Path):
        self.page = page
        self.base_url = base_url
        self.debug_dir = debug_dir

    async def _save_debug(self, tag: str):
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            await self.page.screenshot(path=str(self.debug_dir / f"{stamp}-{tag}.png"), full_page=True)
            (self.debug_dir / f"{stamp}-{tag}.html").write_text(await self.page.content(), encoding="utf-8")
            (self.debug_dir / f"{stamp}-{tag}.txt").write_text(await self.page.locator("body").inner_text(), encoding="utf-8")
        except Exception:
            log.exception("Could not save debug snapshot")

    async def _select_category(self, category: str):
        wanted = CATEGORIES[category]
        selects = self.page.locator("select")
        count = await selects.count()
        for i in range(count):
            select = selects.nth(i)
            options = await select.locator("option").all_text_contents()
            match = next((o for o in options if wanted.casefold() in o.casefold()
                          or (category == "residence" and "aufenthaltstitel" in o.casefold())
                          or (category == "legalization" and "beglaubigungen" in o.casefold())), None)
            if match:
                await select.select_option(label=match.strip())
                return
        raise RuntimeError(f"Could not find category dropdown option for: {wanted}")

    async def _select_one_person(self):
        selects = self.page.locator("select")
        for i in range(await selects.count()):
            select = selects.nth(i)
            options = await select.locator("option").all_text_contents()
            for option in options:
                normalized = option.strip().casefold()
                if normalized in {"1", "1 person", "1 personne", "1 person(s)", "eine person"}:
                    try:
                        await select.select_option(label=option.strip())
                        return
                    except Exception:
                        pass
        # Some versions render a numeric input rather than a select.
        number = self.page.locator('input[type="number"]')
        if await number.count():
            await number.first.fill("1")
            return
        # If the page has no explicit people selector, do not invent one.
        log.warning("No explicit people selector found; continuing with portal default")

    async def _continue_safe(self):
        # Only click navigation controls. Never click buttons labelled book/reserve/submit/confirm.
        patterns = [
            re.compile(r"^(ensuite|suivant|continuer|continue|next|weiter|fortfahren)\b", re.I),
        ]
        for pattern in patterns:
            candidates = self.page.get_by_role("button", name=pattern)
            if await candidates.count():
                await candidates.first.click()
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                await self.page.wait_for_timeout(900)
                return True
            links = self.page.get_by_role("link", name=pattern)
            if await links.count():
                await links.first.click()
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                await self.page.wait_for_timeout(900)
                return True
        # Some ASP.NET pages use input submit controls named "Ensuite".
        inputs = self.page.locator('input[type="submit"], input[type="button"]')
        for i in range(await inputs.count()):
            el = inputs.nth(i)
            value = (await el.get_attribute("value") or "").strip()
            if value.casefold() in {"ensuite", "suivant", "continue", "next", "weiter"}:
                await el.click()
                await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
                await self.page.wait_for_timeout(900)
                return True
        return False

    async def _enter_calendar(self, category: str):
        await self.page.goto(self.base_url, wait_until="domcontentloaded", timeout=45000)
        await self.page.wait_for_timeout(1200)
        await self._select_category(category)
        await self._select_one_person()
        # The uploaded guide shows an "Ensuite" step, followed by another "Ensuite".
        # Bound the navigation and click only explicit next/continue controls.
        for _ in range(6):
            if await self._calendar_looks_visible():
                return
            if not await self._continue_safe():
                break
        if await self._calendar_looks_visible():
            return
        await self._save_debug(f"{category}-calendar-not-reached")
        raise RuntimeError(
            "Could not confidently reach the appointment calendar. "
            "A debug screenshot/HTML was saved; portal steps may have changed."
        )

    async def _calendar_looks_visible(self) -> bool:
        text = (await self.page.locator("body").inner_text()).casefold()
        calendar_words = ["calendrier", "calendar", "termin", "appointment", "rendez-vous", "date"]
        date_pattern = re.search(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", text)
        return sum(word in text for word in calendar_words) >= 2 or bool(date_pattern)

    async def _extract_slots(self) -> list[dict[str, Any]]:
        # Strongest mode: user-configured CSS selector, set after inspecting the live calendar.
        selector = SLOT_SELECTOR
        elements = self.page.locator(selector) if selector else self.page.locator(
            '[data-date], [data-start], .available, .availability, .free, '
            '.appointment-available, .slot-available, td:not(.disabled) a, '
            'button:not([disabled]), a[aria-label]'
        )
        count = min(await elements.count(), 500)
        found = []
        seen = set()
        for i in range(count):
            el = elements.nth(i)
            try:
                if not await el.is_visible():
                    continue
                text = " ".join((await el.inner_text()).split())
                aria = (await el.get_attribute("aria-label") or "").strip()
                title = (await el.get_attribute("title") or "").strip()
                data_date = (await el.get_attribute("data-date") or "").strip()
                data_start = (await el.get_attribute("data-start") or "").strip()
                cls = (await el.get_attribute("class") or "").casefold()
                disabled = await el.get_attribute("disabled")
                aria_disabled = (await el.get_attribute("aria-disabled") or "").casefold()
                if disabled is not None or aria_disabled == "true":
                    continue
                combined = " ".join(x for x in [data_date, data_start, text, aria, title] if x)
                if not combined or len(combined) > 300:
                    continue
                # Conservative heuristic: candidate must have a date or time, or an explicit
                # availability marker/class. Navigation controls and month labels are excluded.
                has_date = bool(re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", combined))
                has_time = bool(re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", combined))
                availability_marker = any(x in cls for x in ["available", "availability", "free", "slot"])
                if not (has_date or has_time or availability_marker):
                    continue
                if re.search(r"(previous|next|suivant|précédent|month|mois)", combined, re.I) and not (has_date or has_time):
                    continue
                key = (data_date or combined).casefold()
                if key in seen:
                    continue
                seen.add(key)
                date_match = re.search(r"\b\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?\b", combined)
                time_match = re.search(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b", combined)
                found.append({
                    "id": key,
                    "date": date_match.group(0) if date_match else data_date or "",
                    "time": time_match.group(0) if time_match else "",
                    "text": combined,
                })
            except Exception:
                continue
        return found

    async def check_category(self, category: str) -> list[dict[str, Any]]:
        await self._enter_calendar(category)
        await self._save_debug(f"{category}-calendar")
        slots = await self._extract_slots()
        # Keep the result explicitly marked as candidates; human verification is required.
        return slots
