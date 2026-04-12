"""Browser Agent skill — intelligent web automation powered by Playwright.

Goes beyond simple web_research: navigates pages, fills forms, clicks buttons,
extracts data from complex SPAs, and follows multi-step web workflows.

Uses accessibility tree parsing for robust element identification.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

# Lazy-load playwright
_browser = None
_context = None


async def _get_browser():
    global _browser, _context
    if _browser is None:
        try:
            from playwright.async_api import async_playwright
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(headless=True)
            _context = await _browser.new_context(
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            )
        except Exception as e:
            logger.error("Browser agent: failed to launch — %s", e)
            raise
    return _context


class BrowserAgentSkill(BaseSkill):
    name = "browser"
    description = "Web browser automation — navigate, click, fill forms, extract data, take screenshots"
    RISK_MAP = {
        "navigate": "EXTERNAL",
        "click": "EXTERNAL",
        "fill": "EXTERNAL",
        "extract_text": "READ",
        "extract_links": "READ",
        "screenshot": "READ",
        "execute_js": "EXTERNAL",
        "get_page_structure": "READ",
        "close": "READ",
        "wait": "READ",
        "multi_step": "EXTERNAL",
    }

    def __init__(self):
        self._pages: dict[str, Any] = {}  # tab_id -> Page
        self._current_tab: str = "default"

    async def execute(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        method = getattr(self, f"do_{action}", None)
        if method is None:
            return {"error": f"Unknown browser action: {action}"}
        try:
            return await method(**params)
        except Exception as e:
            logger.error("Browser.%s failed: %s", action, e)
            return {"error": str(e)}

    async def _get_page(self, tab: str | None = None) -> Any:
        tab = tab or self._current_tab
        if tab not in self._pages:
            ctx = await _get_browser()
            page = await ctx.new_page()
            self._pages[tab] = page
        return self._pages[tab]

    async def do_navigate(self, url: str, tab: str = None, wait_for: str = "load") -> dict:
        """Navigate to a URL. wait_for: 'load', 'domcontentloaded', 'networkidle'."""
        page = await self._get_page(tab)
        response = await page.goto(url, wait_until=wait_for, timeout=30000)
        status = response.status if response else 0
        title = await page.title()
        return {
            "status": "ok",
            "url": page.url,
            "title": title,
            "http_status": status,
        }

    async def do_click(self, selector: str, tab: str = None) -> dict:
        """Click an element by CSS selector or text content."""
        page = await self._get_page(tab)
        try:
            await page.click(selector, timeout=5000)
        except Exception:
            # Try by text content
            await page.get_by_text(selector).first.click(timeout=5000)
        await page.wait_for_timeout(500)
        return {"status": "clicked", "selector": selector, "url": page.url}

    async def do_fill(self, selector: str, value: str, tab: str = None) -> dict:
        """Fill a form field with a value."""
        page = await self._get_page(tab)
        try:
            await page.fill(selector, value, timeout=5000)
        except Exception:
            # Try by placeholder/label
            await page.get_by_placeholder(selector).first.fill(value, timeout=5000)
        return {"status": "filled", "selector": selector}

    async def do_extract_text(self, selector: str = "body", tab: str = None) -> dict:
        """Extract text content from the page or a specific element."""
        page = await self._get_page(tab)
        if selector == "body":
            text = await page.inner_text("body")
        else:
            text = await page.inner_text(selector)
        # Trim to reasonable size
        if len(text) > 5000:
            text = text[:5000] + "\n... (truncated)"
        return {"status": "ok", "text": text, "url": page.url}

    async def do_extract_links(self, tab: str = None) -> dict:
        """Extract all links from the current page."""
        page = await self._get_page(tab)
        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                text: a.innerText.trim().substring(0, 100),
                href: a.href,
            })).filter(l => l.text && l.href.startsWith('http'))
        """)
        return {"status": "ok", "links": links[:50], "count": len(links)}

    async def do_screenshot(self, path: str = "", full_page: bool = False, tab: str = None) -> dict:
        """Take a screenshot of the current page."""
        from pathlib import Path as P
        page = await self._get_page(tab)
        if not path:
            path = str(P(__file__).resolve().parent.parent / "data" / "screenshots" / "browser_screenshot.png")
        P(path).parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=path, full_page=full_page)
        return {"status": "ok", "path": path}

    async def do_execute_js(self, code: str, tab: str = None) -> dict:
        """Execute JavaScript in the page context."""
        page = await self._get_page(tab)
        result = await page.evaluate(code)
        return {"status": "ok", "result": str(result)[:2000] if result else None}

    async def do_get_page_structure(self, tab: str = None) -> dict:
        """Get the accessibility tree of the page — useful for understanding page layout."""
        page = await self._get_page(tab)
        # Get simplified DOM structure
        structure = await page.evaluate("""
            () => {
                function getTree(el, depth = 0) {
                    if (depth > 3) return null;
                    const tag = el.tagName?.toLowerCase();
                    if (!tag || ['script','style','noscript','svg','path'].includes(tag)) return null;
                    const node = { tag };
                    if (el.id) node.id = el.id;
                    if (el.className && typeof el.className === 'string')
                        node.class = el.className.split(' ').slice(0,3).join(' ');
                    const text = el.childNodes.length === 1 && el.childNodes[0].nodeType === 3
                        ? el.childNodes[0].textContent?.trim() : '';
                    if (text && text.length < 100) node.text = text;
                    if (['input','textarea','select'].includes(tag)) {
                        node.type = el.type || '';
                        node.name = el.name || '';
                        node.placeholder = el.placeholder || '';
                    }
                    if (tag === 'a') node.href = el.href || '';
                    if (tag === 'button' || el.role === 'button') node.role = 'button';
                    const children = Array.from(el.children)
                        .map(c => getTree(c, depth + 1))
                        .filter(Boolean);
                    if (children.length) node.children = children;
                    return node;
                }
                return getTree(document.body);
            }
        """)
        # Truncate if too large
        text = json.dumps(structure, ensure_ascii=False)
        if len(text) > 8000:
            text = text[:8000] + "... (truncated)"
        return {"status": "ok", "structure": structure, "url": page.url}

    async def do_wait(self, selector: str = None, timeout: int = 5000, tab: str = None) -> dict:
        """Wait for an element to appear or a fixed timeout."""
        page = await self._get_page(tab)
        if selector:
            await page.wait_for_selector(selector, timeout=timeout)
            return {"status": "ok", "found": selector}
        else:
            await page.wait_for_timeout(timeout)
            return {"status": "ok", "waited_ms": timeout}

    async def do_multi_step(self, steps: str, tab: str = None) -> dict:
        """Execute multiple browser steps in sequence.

        steps should be a JSON array of actions:
        [{"action": "navigate", "url": "..."}, {"action": "click", "selector": "..."}]
        """
        try:
            step_list = json.loads(steps) if isinstance(steps, str) else steps
        except json.JSONDecodeError:
            return {"error": "Invalid JSON in steps parameter"}

        results = []
        for i, step in enumerate(step_list):
            action = step.pop("action", "")
            if not action:
                results.append({"step": i, "error": "No action specified"})
                continue
            step["tab"] = tab
            result = await self.execute(action, step)
            results.append({"step": i, "action": action, **result})
            if result.get("error"):
                break

        return {"status": "ok", "steps_completed": len(results), "results": results}

    async def do_close(self, tab: str = None) -> dict:
        """Close a browser tab."""
        tab = tab or self._current_tab
        page = self._pages.pop(tab, None)
        if page:
            await page.close()
        return {"status": "closed", "tab": tab}
