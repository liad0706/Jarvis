"""Calmark appointment booking skill - uses Playwright to call Calmark's internal API.

Session persistence: cookies and login state are saved to data/calmark_session.json
so you only need to log in once. The session survives Jarvis restarts.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from core.skill_base import BaseSkill
from config import get_settings

logger = logging.getLogger(__name__)

CALMARK_PAGE_URL = "https://calmark.co.il/p/YRVLZ"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_FILE = PROJECT_ROOT / "data" / "calmark_session.json"
CALMARK_SCREENSHOT_DIR = PROJECT_ROOT / "data" / "calmark_screenshots"

# Service catalog scraped from the page
SERVICES = {
    "תספורת גבר + זקן": {"id": 31719, "price": 100},
    "תספורת גבר ללא זקן": {"id": 31720, "price": 80},
    "תספורת חייל+זקן": {"id": 31721, "price": 70},
    "תספורת שיער ארוך": {"id": 31722, "price": 120},
    "תספורת ילד": {"id": 31723, "price": 70},
    "סידור זקן": {"id": 31724, "price": 40},
}
DEFAULT_SERVICE = "תספורת גבר + זקן"
# Cap slots per day in tool JSON so the follow-up LLM round does not hang on huge context
_MAX_SLOTS_PER_DAY_IN_TOOL_RESULT = 24


class AppointmentBookerSkill(BaseSkill):
    name = "appointment"
    description = (
        "Book appointments at your barber (Yishi Peretz) on Calmark. Check availability, list services, book, login. "
        "Vision (qwen3-vl): use capture_calmark_page or pass include_vision=true on check_availability / book_appointment "
        "to attach a Playwright viewport screenshot for the model."
    )

    def __init__(self):
        self.settings = get_settings()
        self.barber_name = self.settings.kamarlek_barber_name
        self.barber_url = self.settings.calmark_barber_url
        self._page = None
        self._browser = None
        self._pw = None
        self._logged_in = False

    async def _get_browser(self):
        """Backward-compatible browser bootstrap helper."""
        await self._ensure_session()
        return self._browser

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        return await method(**(params or {}))

    # ── Session management ──────────────────────────────────────────

    def _save_cookies(self, cookies: list):
        """Save browser cookies to disk for session persistence."""
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved %d cookies to %s", len(cookies), SESSION_FILE)

    def _load_cookies(self) -> list | None:
        """Load saved cookies from disk."""
        if SESSION_FILE.exists():
            try:
                cookies = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
                logger.info("Loaded %d cookies from %s", len(cookies), SESSION_FILE)
                return cookies
            except Exception:
                logger.warning("Failed to load cookies, starting fresh")
        return None

    async def _ensure_session(self):
        """Ensure we have a live Playwright page with Calmark loaded and service selected."""
        if self._page:
            try:
                await self._page.evaluate("1+1")
                await self._dismiss_blocking_overlays()
                await self._advance_staff_step()
                return
            except Exception:
                self._page = None

        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        launch_kw: dict = {"headless": self.settings.calmark_playwright_headless}
        sm = getattr(self.settings, "calmark_playwright_slow_mo_ms", 0) or 0
        if sm > 0:
            launch_kw["slow_mo"] = sm
        self._browser = await self._pw.chromium.launch(**launch_kw)

        # Create context with saved cookies if available
        context = await self._browser.new_context(
            locale="he-IL",
            viewport={"width": 1280, "height": 720},
        )

        # Restore cookies from previous session
        saved_cookies = self._load_cookies()
        if saved_cookies:
            await context.add_cookies(saved_cookies)
            self._logged_in = True

        self._page = await context.new_page()
        page_url = self.barber_url or CALMARK_PAGE_URL
        await self._page.goto(page_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        await self._dismiss_blocking_overlays()

        # Service + staff are applied per-request via _sync_booking_flow(service_name)

        # Check if we're logged in
        login_status = await self._page.evaluate('''() => {
            return typeof loggedInUser !== 'undefined' && loggedInUser ? true : false;
        }''')
        self._logged_in = login_status or (saved_cookies is not None)

        logger.info("Calmark session ready (logged_in=%s)", self._logged_in)

    async def _persist_session(self):
        """Save current cookies so session survives restart."""
        if self._page:
            context = self._page.context
            cookies = await context.cookies()
            self._save_cookies(cookies)

    async def _screenshot_calmark_viewport(self) -> str | None:
        """Save viewport PNG for vision attachment; returns filesystem path."""
        page = self._page
        if not page:
            return None
        try:
            CALMARK_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
            path = CALMARK_SCREENSHOT_DIR / "last_viewport.png"
            await page.screenshot(path=str(path), full_page=False)
            return str(path)
        except Exception as e:
            logger.warning("Calmark viewport screenshot failed: %s", e)
            return None

    async def _with_calmark_vision(self, result: dict, include_vision: bool) -> dict:
        """Attach vision_attach_path when requested and the Playwright page is alive."""
        if not include_vision or not self._page:
            return result
        shot = await self._screenshot_calmark_viewport()
        if not shot:
            return result
        out = dict(result)
        out["vision_attach_path"] = shot
        return out

    # ── UI sync (Calmark API can return slots while the DOM still says "choose service") ──

    async def _dismiss_blocking_overlays(self):
        """Close review popups / modals that block the booking flow."""
        page = self._page
        if not page:
            return
        try:
            for _ in range(4):
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.15)
        except Exception:
            pass
        for sel in (
            ".leave-review-button-close",
            ".review-reminder-close",
            "[class*='review-reminder'] i.fa-times",
            "[class*='review-reminder'] .fa-xmark",
            ".login-dialog-wrapper .fa-times",
            ".login-dialog-wrapper .fa-xmark",
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=400):
                    await loc.click()
                    await asyncio.sleep(0.25)
            except Exception:
                continue
        for label in ("סגור", "Close", "×"):
            try:
                btn = page.get_by_role("button", name=label, exact=True)
                if await btn.count() > 0:
                    b = btn.first
                    if await b.is_visible(timeout=400):
                        await b.click()
                        await asyncio.sleep(0.25)
            except Exception:
                pass

    async def _ui_blocked_waiting_for_service(self) -> bool:
        """True when Calmark shows the hint to pick a service (UI not aligned with AJAX state)."""
        page = self._page
        if not page:
            return False
        try:
            hint = page.get_by_text("בחרו שירות כדי להמשיך", exact=False)
            if await hint.count() == 0:
                return False
            return await hint.first.is_visible(timeout=600)
        except Exception:
            return False

    async def _sync_booking_flow(self, service_name: str) -> None:
        """Click service + continue + staff in the real UI, then mirror selectedServices in JS."""
        page = self._page
        if not page:
            return

        await self._dismiss_blocking_overlays()

        service = SERVICES.get(service_name, SERVICES[DEFAULT_SERVICE])
        sid = int(service["id"])

        # Calmark builds rows with servicesId (DOM often lowercase) and toggles .selectedService on click.
        try:
            rows = page.locator(".inputItem.select-service")
            n = await rows.count()
            clicked = False
            for i in range(n):
                cell = rows.nth(i)
                if not await cell.is_visible(timeout=600):
                    continue
                raw = await cell.get_attribute("servicesid")
                if raw is None:
                    raw = await cell.get_attribute("servicesId")
                if raw == str(sid):
                    await cell.click()
                    clicked = True
                    await asyncio.sleep(0.45)
                    break
            if not clicked:
                by_name = page.locator(".inputItem.select-service").filter(has_text=service_name)
                if await by_name.count() > 0 and await by_name.first.is_visible(timeout=1500):
                    await by_name.first.click()
                    await asyncio.sleep(0.45)
                else:
                    logger.warning("Could not click service row for id %s; setting JS only", sid)
        except Exception as e:
            logger.warning("Service row click issue: %s", e)

        await page.evaluate(f"selectedServices = [{sid}]")

        # Handler is on #acceptServices (reads .selectedService from DOM); wrapper alone may not fire.
        try:
            acc = page.locator("#acceptServices").first
            if await acc.is_visible(timeout=2500):
                await acc.click()
            else:
                wrap = page.locator("#acceptServicesWrapper #acceptServices").first
                if await wrap.is_visible(timeout=1500):
                    await wrap.click()
        except Exception as e:
            logger.warning("acceptServices click: %s", e)

        await asyncio.sleep(1.5)
        await self._advance_staff_step()
        await page.evaluate(f"selectedServices = [{sid}]")

    async def _select_service(self, service_name: str):
        """Keep JS in sync; prefer _sync_booking_flow before API calls."""
        service = SERVICES.get(service_name, SERVICES[DEFAULT_SERVICE])
        await self._page.evaluate(f"selectedServices = [{service['id']}]")

    async def _advance_staff_step(self):
        """Match Calmark Page.js: staff lives in #employees; rows are .inputItem[employeeId]; no acceptEmployees btn."""
        page = self._page
        if not page:
            return

        emp = page.locator("#employees")
        cal = page.locator("#pageCalendarWrapper")

        for _ in range(60):
            try:
                if await cal.is_visible(timeout=250):
                    if not await emp.is_visible(timeout=200):
                        logger.info("Calmark: calendar without staff popup (AutoSelectEmployee)")
                        return
            except Exception:
                pass
            try:
                if await emp.is_visible(timeout=300):
                    break
            except Exception:
                pass
            await asyncio.sleep(0.2)
        else:
            logger.warning("Calmark: #employees did not show in time")
            return

        try:
            await page.locator("#employees .inputItemContainer .inputItem").first.wait_for(
                state="visible", timeout=12000
            )
        except Exception as e:
            logger.warning("Calmark: no staff rows in #employees: %s", e)
            return

        clicked = False
        for attr in ('[employeeid="-1"]', '[employeeId="-1"]'):
            try:
                auto = page.locator(f"#employees .inputItemContainer .inputItem{attr}")
                if await auto.count() > 0 and await auto.first.is_visible(timeout=800):
                    await auto.first.click()
                    clicked = True
                    logger.info("Calmark: chose auto staff (employeeId -1)")
                    await asyncio.sleep(0.6)
                    break
            except Exception:
                continue

        if not clicked:
            rows = page.locator("#employees .inputItemContainer .inputItem")
            n = await rows.count()
            for i in range(n):
                row = rows.nth(i)
                try:
                    if not await row.is_visible(timeout=500):
                        continue
                    eid = await row.get_attribute("employeeid")
                    if eid is None:
                        eid = await row.get_attribute("employeeId")
                    if eid is None:
                        continue
                    await row.click()
                    logger.info("Calmark: clicked staff employeeId=%s", eid)
                    clicked = True
                    await asyncio.sleep(0.6)
                    break
                except Exception:
                    continue

        if not clicked:
            logger.warning("Calmark: could not click any staff row")
            return

        try:
            await emp.wait_for(state="hidden", timeout=20000)
        except Exception:
            logger.warning("Calmark: #employees still visible after pick")

    # ── API helpers ─────────────────────────────────────────────────

    async def _get_slots(self, date_str: str) -> list[str]:
        """Get available time slots for a date (DD/MM/YYYY). Mirrors Calmark Page.js WebMethods."""
        result = await self._page.evaluate(f'''() => {{
            return new Promise((resolve) => {{
                var d = '{date_str} 00:00';
                var wl = (typeof businessData !== 'undefined' && businessData)
                    ? businessData.EnableWaitingList : false;
                var se = (typeof selectedEmployee !== 'undefined') ? selectedEmployee : -1;
                var anyEmp = (se == -1 || se === '-1');

                function parseTimes(data) {{
                    var parsed = JSON.parse(data.d);
                    var u0 = parsed[0];
                    if (u0 && u0.UnoccupiedTime && Array.isArray(u0.UnoccupiedTime))
                        return u0.UnoccupiedTime;
                    if (Array.isArray(u0)) return u0;
                    return [];
                }}

                if (anyEmp) {{
                    $.ajax({{
                        type: 'POST',
                        url: '/Pages/Page.aspx/GetTimeForAppointment',
                        contentType: 'application/json; charset=utf-8',
                        data: JSON.stringify({{
                            businessId: businessId,
                            services: selectedServices,
                            date: d,
                            waitingList: wl
                        }}),
                        dataType: 'json',
                        success: function(data) {{
                            var parsed = JSON.parse(data.d);
                            var times = parsed[0] || [];
                            resolve({{ok: true, times: times}});
                        }},
                        error: function(xhr, status, err) {{
                            resolve({{ok: false, error: String(err)}});
                        }}
                    }});
                }} else {{
                    $.ajax({{
                        type: 'POST',
                        url: '/Pages/Page.aspx/GetTimeAndDateForAppointmentByServiceAndEmployee',
                        contentType: 'application/json; charset=utf-8',
                        data: JSON.stringify({{
                            businessId: businessId,
                            services: selectedServices,
                            employeeId: se,
                            date: d,
                            waitingList: wl
                        }}),
                        dataType: 'json',
                        success: function(data) {{
                            resolve({{ok: true, times: parseTimes(data)}});
                        }},
                        error: function(xhr, status, err) {{
                            resolve({{ok: false, error: String(err)}});
                        }}
                    }});
                }}
                setTimeout(() => resolve({{ok: false, error: 'timeout'}}), 10000);
            }});
        }}''')

        if not result.get("ok"):
            return []

        slots = []
        for t in result.get("times", []):
            if "T" in str(t):
                slots.append(str(t).split("T")[1][:5])
            else:
                slots.append(str(t))
        return slots

    # ── Skill actions ───────────────────────────────────────────────

    async def do_list_services(self) -> dict:
        """List available services and prices at the barber."""
        services_list = [
            {"name": name, "price": f"₪{info['price']}"}
            for name, info in SERVICES.items()
        ]
        return {
            "status": "ok",
            "services": services_list,
            "message": f"{len(services_list)} services available at {self.barber_name}",
        }

    async def do_capture_calmark_page(self, service: str = "") -> dict:
        """Take a screenshot of the Calmark page inside Playwright and attach it for the vision model. Use when the user asks what you see on Calmark, or to debug UI vs API. If service is a known Hebrew name, sync that service in the UI first."""
        try:
            await self._get_browser()
        except Exception as e:
            return {"error": f"Failed to connect to Calmark: {e}"}

        service_name = service if service in SERVICES else DEFAULT_SERVICE
        await self._sync_booking_flow(service_name)

        path = await self._screenshot_calmark_viewport()
        if not path:
            return {"error": "Could not capture Calmark page (no viewport)."}
        return {
            "status": "ok",
            "barber": self.barber_name,
            "service": service_name,
            "message": "צילום דף Calmark (viewport) מצורף למודל ראייה.",
            "vision_attach_path": path,
        }

    async def do_check_availability(
        self,
        date: str = "",
        service: str = "",
        max_days: int = 7,
        first_available_only: bool = True,
        include_vision: bool = False,
    ) -> dict:
        """Check available time slots. date: DD/MM/YYYY, or empty to scan upcoming days. service: Hebrew name. first_available_only: if true (default), stop at the first day that has slots — much faster for 'מתי יש תור'. Set false for a full week overview. max_days: cap on how many days to scan (default 7). include_vision: if true, attach a Calmark viewport screenshot for vision models (e.g. qwen3-vl) — useful when UI/API disagree or the user asks what you see."""
        async def finish(r: dict) -> dict:
            return await self._with_calmark_vision(r, include_vision)

        try:
            await self._get_browser()
        except Exception as e:
            return await finish({"error": f"Failed to connect to Calmark: {e}"})

        service_name = service if service in SERVICES else DEFAULT_SERVICE
        await self._sync_booking_flow(service_name)

        try:
            md = max(1, min(14, int(max_days)))
        except (ValueError, TypeError):
            md = 7

        if date:
            dates_to_check = [date]
            first_available_only = False
        else:
            dates_to_check = [
                (datetime.now() + timedelta(days=i)).strftime("%d/%m/%Y")
                for i in range(md)
            ]

        async def _collect_availability() -> dict[str, list[str]]:
            out: dict[str, list[str]] = {}
            for check_date in dates_to_check:
                slots = await self._get_slots(check_date)
                if slots:
                    out[check_date] = slots
                    if first_available_only:
                        break
            return out

        availability = await _collect_availability()
        total = sum(len(s) for s in availability.values())

        if await self._ui_blocked_waiting_for_service():
            logger.warning("Calmark UI still shows 'select service' — reloading and resyncing once")
            await self._page.goto(
                self.barber_url or CALMARK_PAGE_URL,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await asyncio.sleep(2)
            await self._dismiss_blocking_overlays()
            await self._sync_booking_flow(service_name)
            availability = await _collect_availability()
            total = sum(len(s) for s in availability.values())

        if await self._ui_blocked_waiting_for_service() and total > 0:
            return await finish({
                "status": "error",
                "error": "ui_api_mismatch",
                "barber": self.barber_name,
                "service": service_name,
                "availability": {},
                "total_slots": 0,
                "message": "ה-API החזיר תורים אבל במסך עדיין מופיע 'בחרו שירות כדי להמשיך' — לא סומכים על התוצאה. "
                           "סגור חלונות (ביקורת/התחברות) ונסה שוב, או בחר שירות ידנית בדפדפן.",
            })

        ui_ok = not await self._ui_blocked_waiting_for_service()

        truncated_note: dict[str, int] = {}
        availability_for_llm: dict[str, list] = {}
        for d, slots in availability.items():
            if len(slots) > _MAX_SLOTS_PER_DAY_IN_TOOL_RESULT:
                availability_for_llm[d] = slots[:_MAX_SLOTS_PER_DAY_IN_TOOL_RESULT]
                truncated_note[d] = len(slots)
            else:
                availability_for_llm[d] = slots

        today_str = datetime.now().strftime("%d/%m/%Y")
        if total > 0:
            # Build explicit per-date summary so the LLM cannot hallucinate dates
            date_lines = []
            for d, slots in availability_for_llm.items():
                trunc = truncated_note.get(d)
                count_note = f" ({trunc} total)" if trunc else ""
                preview = ", ".join(slots[:6])
                if len(slots) > 6:
                    preview += f" ... (+{len(slots) - 6} more)"
                day_label = " (TODAY)" if d == today_str else ""
                date_lines.append(f"  {d}{day_label}: {preview}{count_note}")
            msg = (
                f"⚠ EXACT dates from Calmark API — report these dates AS-IS, do NOT substitute today's date.\n"
                f"Today is {today_str}.\n"
                f"Available slots ({total} total):\n" + "\n".join(date_lines)
            )
        else:
            msg = "No available slots for the requested dates"
        if total == 0 and not ui_ok:
            msg += (
                " (הממשק עדיין מבקש לבחור שירות — ייתכן חלון חוסם או דף לא מסונכרן; לא אומת זמינות אמיתית.)"
            )

        return await finish({
            "status": "ok",
            "barber": self.barber_name,
            "service": service_name,
            "price": f"₪{SERVICES[service_name]['price']}",
            "availability": availability_for_llm,
            "total_slots": total,
            "slots_per_day_full_count": truncated_note or None,
            "logged_in": self._logged_in,
            "ui_flow_ok": ui_ok,
            "message": msg,
        })

    async def do_search_barber(self, name: str = "") -> dict:
        """Compatibility action used by older callers/tests."""
        target_name = (name or self.barber_name).strip() or self.barber_name
        try:
            await self._get_browser()
        except Exception as e:
            return {"error": f"Failed to connect to Calmark: {e}"}

        return {
            "status": "ok",
            "barber": target_name,
            "url": self.barber_url or CALMARK_PAGE_URL,
            "message": f"Barber page is ready for {target_name}.",
        }

    async def do_login(self, phone: str = "") -> dict:
        """Log in to Calmark with your phone number. You'll receive an SMS code."""
        if not phone:
            return {
                "status": "need_info",
                "message": "Please provide your phone number to log in to Calmark.",
            }

        try:
            await self._get_browser()
        except Exception as e:
            return {"error": f"Failed to connect to Calmark: {e}"}

        try:
            # Open the login dialog via JS (it's hidden by default)
            await self._page.evaluate("ShowLoginDialog()")
            await asyncio.sleep(2)

            # Fill phone number in #login-phone
            await self._page.locator("#login-phone").fill(phone)
            await asyncio.sleep(1)

            # Click the continue button in login part 1
            await self._page.locator("#login-dialog-part-button-1").click()
            await asyncio.sleep(5)

            # Check which part is now visible (password or SMS code)
            visible_part = await self._page.evaluate('''() => {
                if (document.getElementById('login-dialog-part-2')?.style.display !== 'none'
                    && document.getElementById('login-dialog-part-2')?.offsetParent !== null)
                    return 'password';
                if (document.getElementById('login-dialog-part-8')?.style.display !== 'none'
                    && document.getElementById('login-dialog-part-8')?.offsetParent !== null)
                    return 'sms_code';
                // Check all parts
                for (let i = 1; i <= 9; i++) {
                    let el = document.getElementById('login-dialog-part-' + i);
                    if (el && el.offsetParent !== null && el.style.display !== 'none') return 'part_' + i;
                }
                return 'unknown';
            }''')

            await self._persist_session()

            if visible_part == "password":
                return {
                    "status": "need_password",
                    "message": f"Calmark says you already have an account. Please provide your password using verify_login.",
                }
            elif visible_part == "sms_code":
                return {
                    "status": "sms_sent",
                    "message": f"SMS code sent to {phone}. Use verify_login with the code.",
                }
            else:
                return {
                    "status": "sms_sent",
                    "visible_state": visible_part,
                    "message": f"Login started for {phone}. Check your SMS and use verify_login with the code.",
                }
        except Exception as e:
            return {"error": f"Login failed: {e}"}

    async def do_verify_login(self, code: str = "") -> dict:
        """Verify login with the SMS code or password you received."""
        if not code:
            return {"status": "need_info", "message": "Please provide the SMS code or your password."}

        if not self._page:
            return {"error": "No active login session. Call login first."}

        try:
            # Check which part is visible - password (part 2) or SMS code (part 8)
            visible_part = await self._page.evaluate('''() => {
                let p2 = document.getElementById('login-dialog-part-2');
                let p8 = document.getElementById('login-dialog-part-8');
                if (p2 && p2.offsetParent !== null) return 'password';
                if (p8 && p8.offsetParent !== null) return 'sms_code';
                return 'unknown';
            }''')

            if visible_part == "password":
                # Fill password in #login-password
                await self._page.locator("#login-password").fill(code)
                await asyncio.sleep(1)
                await self._page.locator("#login-dialog-part-button-2").click()
            elif visible_part == "sms_code":
                # Fill SMS code in #login-forgot-password
                await self._page.locator("#login-forgot-password").fill(code)
                await asyncio.sleep(1)
                await self._page.locator("#login-dialog-part-button-8").click()
            else:
                # Try both fields
                for field_id in ["#login-password", "#login-forgot-password"]:
                    try:
                        if await self._page.locator(field_id).is_visible():
                            await self._page.locator(field_id).fill(code)
                            break
                    except Exception:
                        continue

            await asyncio.sleep(5)

            # Check if login succeeded - dialog should close
            dialog_visible = await self._page.evaluate('''() => {
                let dialog = document.querySelector('.login-dialog-wrapper, [class*="login-dialog"]');
                return dialog ? dialog.offsetParent !== null : false;
            }''')

            # Save cookies - the important part for persistence!
            await self._persist_session()

            if not dialog_visible:
                self._logged_in = True
                return {
                    "status": "logged_in",
                    "message": "Successfully logged in to Calmark! Session saved - you won't need to log in again.",
                }
            else:
                return {
                    "status": "retry",
                    "message": "Login dialog still open. The code may be incorrect. Try again.",
                }
        except Exception as e:
            return {"error": f"Verification failed: {e}"}

    async def do_book_appointment(
        self,
        date: str = "",
        time_slot: str = "",
        service: str = "",
        include_vision: bool = False,
    ) -> dict:
        """Book an appointment. date: DD/MM/YYYY, time_slot: HH:MM, service: Hebrew name. include_vision: attach a Calmark viewport screenshot for the vision model after the booking attempt."""
        async def finish(r: dict) -> dict:
            return await self._with_calmark_vision(r, include_vision)

        if not date or not time_slot:
            return await finish({
                "status": "need_info",
                "message": "Please specify date (DD/MM/YYYY) and time (HH:MM). "
                           "Use check_availability first to see available slots.",
            })

        try:
            await self._get_browser()
        except Exception as e:
            return await finish({"error": f"Failed to connect to Calmark: {e}"})

        if not self._logged_in:
            return await finish({
                "status": "need_login",
                "message": "You need to log in first. Use login with your phone number.",
            })

        service_name = service if service in SERVICES else DEFAULT_SERVICE
        await self._sync_booking_flow(service_name)

        start_date = f"{date} {time_slot}"

        result = await self._page.evaluate(f'''() => {{
            return new Promise((resolve) => {{
                $.ajax({{
                    type: 'POST',
                    url: '/Pages/Page.aspx/ScheduleAppointment',
                    contentType: 'application/json; charset=utf-8',
                    data: JSON.stringify({{
                        businessId: businessId,
                        services: selectedServices,
                        employeeId: selectedEmployee,
                        notes: '',
                        startDate: '{start_date}',
                        source: null,
                        referrer: '',
                        targetSource: null,
                        url: window.location.href
                    }}),
                    dataType: 'json',
                    success: function(data) {{
                        resolve({{ok: true, data: data.d}});
                    }},
                    error: function(xhr, status, err) {{
                        resolve({{ok: false, error: String(err), status: xhr.status}});
                    }}
                }});
                setTimeout(() => resolve({{ok: false, error: 'timeout'}}), 10000);
            }});
        }}''')

        if result.get("ok"):
            # Save session after successful booking
            await self._persist_session()
            return await finish({
                "status": "booked",
                "barber": self.barber_name,
                "service": service_name,
                "date": date,
                "time": time_slot,
                "price": f"₪{SERVICES[service_name]['price']}",
                "message": f"Appointment booked with {self.barber_name} on {date} at {time_slot} ({service_name})",
            })
        else:
            return await finish({
                "status": "error",
                "error": result.get("error", "Unknown error"),
                "message": "Booking failed. Try logging in again with the login action.",
            })

    async def do_open_page(self) -> dict:
        """Open the barber's Calmark page in your browser."""
        import webbrowser
        await asyncio.to_thread(webbrowser.open, self.barber_url)
        return {
            "status": "ok",
            "url": self.barber_url,
            "message": f"Opened {self.barber_name}'s page in your browser.",
        }
