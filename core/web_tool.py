"""
core/web_tool.py
----------------
Web access utilities for agents: search via Tavily and HTML scraping via httpx
+ BeautifulSoup.

Usage:
    from core.web_tool import WebTool

    tool = WebTool(search_api_key="tvly-...")
    results = await tool.search("Python asyncio tutorial", max_results=5)
    text = await tool.scrape("https://example.com")
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from core.logger import get_logger

log = get_logger("web_tool")

_MAX_SCRAPE_CHARS = 20480  # 20 KB


class WebTool:
    """Web search and scraping helper for agents.

    Provides async access to the Tavily Search API and HTML scraping via
    ``httpx`` + ``BeautifulSoup``.  Both methods degrade gracefully on error:
    they log a warning and return an empty result rather than raising.
    """

    def __init__(self, search_api_key: str = "", timeout: int = 10) -> None:
        self._search_api_key = search_api_key
        self._timeout = timeout
        log.debug("WebTool initialised", has_api_key=bool(search_api_key))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Search the web using the Tavily Search API.

        Each result dict contains at least ``title``, ``url``, and ``content``
        keys.  If ``search_api_key`` was not provided, a warning is logged and
        an empty list is returned immediately.

        Args:
            query: The search query string.
            max_results: Maximum number of results to return.  Defaults to 10.

        Returns:
            List of result dicts, or ``[]`` if the key is missing or an error
            occurs.
        """
        if not self._search_api_key:
            log.warning(
                "search called without API key — returning empty results",
                event="search_no_key",
                query=query,
            )
            return []

        if not query or not query.strip():
            log.warning("search called with empty query", event="search_empty_query")
            return []

        def _sync_search() -> list[dict]:
            from tavily import TavilyClient  # type: ignore[import-untyped]

            client = TavilyClient(api_key=self._search_api_key)
            response = client.search(query, max_results=max_results)
            return response.get("results", [])

        try:
            results: list[dict] = await asyncio.to_thread(_sync_search)
            log.debug(
                "search complete",
                event="search_done",
                query=query,
                count=len(results),
            )
            return results
        except Exception as exc:
            log.warning(
                "search failed",
                event="search_error",
                query=query,
                error=str(exc),
            )
            return []

    async def scrape(self, url: str) -> str:
        """Fetch a URL and return its visible text content.

        Strips ``<script>`` and ``<style>`` tags before extracting text.
        Output is capped at 20 KB (20 480 chars); a truncation notice is
        appended when the cap is reached.  On any HTTP or network error a
        warning is logged and ``""`` is returned.

        Args:
            url: The URL to fetch and parse.

        Returns:
            Visible page text (up to 20 KB), or ``""`` on error.
        """
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            log.warning("scrape rejected non-http URL", event="scrape_invalid_url", url=url)
            return ""

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, follow_redirects=False)
                if response.is_redirect:
                    log.warning("scrape blocked redirect", event="scrape_redirect_blocked", url=url)
                    return ""
                response.raise_for_status()
                html = response.text
        except httpx.HTTPStatusError as exc:
            log.warning(
                "scrape HTTP error",
                event="scrape_http_error",
                url=url,
                status_code=exc.response.status_code,
                error=str(exc),
            )
            return ""
        except httpx.RequestError as exc:
            log.warning(
                "scrape network error",
                event="scrape_network_error",
                url=url,
                error=str(exc),
            )
            return ""

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()

        text = soup.get_text(separator="\n")
        # Collapse excessive blank lines
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)

        if len(text) > _MAX_SCRAPE_CHARS:
            log.debug(
                "scrape result truncated",
                event="scrape_truncated",
                url=url,
                original_chars=len(text),
            )
            text = text[:_MAX_SCRAPE_CHARS] + "\n\n[... truncated: content exceeded 20 KB limit ...]"

        log.debug("scrape complete", event="scrape_done", url=url, chars=len(text))
        return text
