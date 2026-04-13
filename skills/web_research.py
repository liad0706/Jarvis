"""Web research skill -- search the web with DuckDuckGo and fetch/summarize pages.

חיפוש באינטרנט באמצעות DuckDuckGo (ללא מפתח API) ושליפת תוכן מדפי אינטרנט.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote_plus

from core.skill_base import BaseSkill

logger = logging.getLogger(__name__)

# Limits
MAX_RESULTS_CAP = 20
MAX_PAGE_TEXT = 50_000
SUMMARY_LENGTH = 2000
REQUEST_TIMEOUT = 15

# User-Agent to avoid being blocked
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class WebResearchSkill(BaseSkill):
    """Search the web, fetch pages, and summarize content."""

    name = "web_research"
    description = (
        "Search the web using DuckDuckGo, fetch web pages, "
        "and summarize their content. No API key required."
    )

    RISK_MAP = {
        "search": "low",
        "fetch_page": "low",
        "summarize_url": "low",
    }

    async def execute(self, action: str, params: dict | None = None) -> dict:
        method = getattr(self, f"do_{action}", None)
        if not method:
            return {"error": f"Unknown action: {action}"}
        try:
            return await method(**(params or {}))
        except Exception as e:
            logger.exception("web_research.%s failed", action)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_bs4():
        """Import BeautifulSoup, raising a clear error if missing."""
        try:
            from bs4 import BeautifulSoup
            return BeautifulSoup
        except ImportError:
            raise ImportError(
                "beautifulsoup4 is required. Install with: pip install beautifulsoup4"
            )

    @staticmethod
    def _get_requests():
        import requests
        return requests

    def _make_session(self):
        requests = self._get_requests()
        session = requests.Session()
        session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9,he;q=0.8",
        })
        return session

    def _extract_text(self, html: str) -> str:
        """Extract readable text from HTML, stripping scripts/styles."""
        BeautifulSoup = self._get_bs4()
        soup = BeautifulSoup(html, "html.parser")

        # Remove script, style, nav, footer, header elements
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "iframe"]):
            tag.decompose()

        # Try to find the main content area
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find("div", {"role": "main"})
            or soup.find("div", {"id": "content"})
            or soup.find("div", {"class": "content"})
            or soup.body
            or soup
        )

        text = main.get_text(separator="\n", strip=True)

        # Collapse multiple blank lines
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        text = "\n".join(lines)

        if len(text) > MAX_PAGE_TEXT:
            text = text[:MAX_PAGE_TEXT] + "\n... (truncated)"

        return text

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    async def do_search(self, query: str, max_results: int = 5) -> dict:
        """Search the web using DuckDuckGo. Returns results with title, URL, and snippet. חיפוש באינטרנט."""
        max_results = min(int(max_results), MAX_RESULTS_CAP)
        logger.info("Web search: %s (max %d)", query, max_results)

        def _search():
            requests = self._get_requests()
            BeautifulSoup = self._get_bs4()

            session = self._make_session()
            resp = session.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "b": ""},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []

            for item in soup.select(".result"):
                if len(results) >= max_results:
                    break

                title_tag = item.select_one(".result__a")
                snippet_tag = item.select_one(".result__snippet")

                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                href = title_tag.get("href", "")

                # DuckDuckGo wraps URLs in a redirect; extract the actual URL
                if "uddg=" in href:
                    from urllib.parse import urlparse, parse_qs
                    parsed = parse_qs(urlparse(href).query)
                    href = parsed.get("uddg", [href])[0]

                snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

                results.append({
                    "title": title,
                    "url": href,
                    "snippet": snippet,
                })

            return results

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, _search)

        return {
            "status": "ok",
            "query": query,
            "count": len(results),
            "results": results,
        }

    async def do_fetch_page(self, url: str) -> dict:
        """Fetch a web page and extract its main text content. שליפת תוכן מדף אינטרנט."""
        logger.info("Fetching page: %s", url)

        def _fetch():
            session = self._make_session()
            resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            return resp.text, resp.url

        loop = asyncio.get_event_loop()
        html, final_url = await loop.run_in_executor(None, _fetch)
        text = self._extract_text(html)

        return {
            "status": "ok",
            "url": str(final_url),
            "text_length": len(text),
            "text": text,
        }

    async def do_summarize_url(self, url: str) -> dict:
        """Fetch a web page and return the first ~2000 characters of clean text as a summary. סיכום דף אינטרנט."""
        logger.info("Summarizing URL: %s", url)

        result = await self.do_fetch_page(url)
        if "error" in result:
            return result

        full_text = result["text"]
        summary = full_text[:SUMMARY_LENGTH]
        if len(full_text) > SUMMARY_LENGTH:
            # Try to cut at a sentence boundary
            last_period = summary.rfind(".")
            if last_period > SUMMARY_LENGTH // 2:
                summary = summary[: last_period + 1]
            summary += "\n... (content continues)"

        return {
            "status": "ok",
            "url": result["url"],
            "full_length": len(full_text),
            "summary_length": len(summary),
            "summary": summary,
        }
