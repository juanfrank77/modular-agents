"""
core/composio_tool.py
---------------------
Async wrapper around the Composio SDK for external app access (Gmail, Calendar,
and any other connected app).

Usage:
    from core.composio_tool import ComposioTool

    tool = ComposioTool(api_key="...", user_id="alice")
    result = await tool.execute("GMAIL_FETCH_EMAILS", max_results=5)
    schemas = await tool.get_tools(["gmail", "googlecalendar"])
    matches = await tool.search_tools("send an email")
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.logger import get_logger

log = get_logger("composio_tool")

# Optional import — Composio may not be installed in every environment.
try:
    from composio_anthropic import ComposioToolSet  # type: ignore[import-untyped]
    _COMPOSIO_AVAILABLE = True
except ImportError:
    _COMPOSIO_AVAILABLE = False
    ComposioToolSet = None  # type: ignore[assignment,misc]


class ComposioTool:
    """Async wrapper around the Composio SDK.

    All heavy Composio SDK calls are synchronous under the hood; this class
    dispatches them to a thread via ``asyncio.to_thread()`` so callers can
    ``await`` them without blocking the event loop.

    Args:
        api_key: Composio API key used to authenticate requests.
        user_id: Entity / user identifier scoped to the connected accounts.
                 Defaults to ``"default"``.

    Raises:
        RuntimeError: If ``composio-anthropic`` is not installed.
    """

    def __init__(self, api_key: str, user_id: str = "default") -> None:
        if not _COMPOSIO_AVAILABLE:
            raise RuntimeError(
                "composio-anthropic is not installed. "
                "Run: uv pip install composio-anthropic"
            )
        self._user_id = user_id
        self._toolset: Any = ComposioToolSet(api_key=api_key, entity_id=user_id)
        log.debug(
            "ComposioTool initialised",
            event="composio_init",
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, tool_slug: str, **arguments: Any) -> dict:
        """Execute a Composio tool action and return the result dict.

        Wraps the synchronous SDK call via ``asyncio.to_thread()`` so it does
        not block the event loop.

        Args:
            tool_slug: The Composio action slug (e.g. ``"GMAIL_FETCH_EMAILS"``).
            **arguments: Keyword arguments forwarded to the action payload.

        Returns:
            Result dict returned by the Composio SDK.  On error, returns a dict
            with an ``"error"`` key containing the exception message.
        """
        log.debug(
            "execute action",
            event="composio_execute",
            tool_slug=tool_slug,
        )

        def _sync_execute() -> dict:
            return self._toolset.execute_action(
                action=tool_slug,
                params=arguments,
                entity_id=self._user_id,
            )

        try:
            result: dict = await asyncio.to_thread(_sync_execute)
            log.debug(
                "execute complete",
                event="composio_execute_done",
                tool_slug=tool_slug,
            )
            return result
        except Exception as exc:
            log.warning(
                "execute failed",
                event="composio_execute_error",
                tool_slug=tool_slug,
                error=str(exc),
            )
            return {"error": str(exc)}

    async def get_tools(self, toolkits: list[str]) -> list[dict]:
        """Return tool schemas for the given toolkit names.

        The schemas are suitable for direct consumption by an LLM as part of a
        tool-calling prompt.

        Args:
            toolkits: List of Composio app/toolkit names
                      (e.g. ``["gmail", "googlecalendar"]``).

        Returns:
            List of tool schema dicts, or ``[]`` on error.
        """
        log.debug(
            "get_tools",
            event="composio_get_tools",
            toolkits=toolkits,
        )

        def _sync_get_tools() -> list[dict]:
            return self._toolset.get_tools(apps=toolkits)

        try:
            schemas: list[dict] = await asyncio.to_thread(_sync_get_tools)
            log.debug(
                "get_tools complete",
                event="composio_get_tools_done",
                count=len(schemas),
            )
            return schemas
        except Exception as exc:
            log.error(
                "get_tools failed",
                event="composio_get_tools_error",
                toolkits=toolkits,
                error=str(exc),
            )
            return [{"error": str(exc)}]

    async def search_tools(self, query: str) -> list[dict]:
        """Semantic search over all available Composio tools.

        Useful for dynamically discovering which tool slug to use for a given
        task described in natural language.

        Args:
            query: A natural-language description of the desired action
                   (e.g. ``"send an email"``).

        Returns:
            List of matching tool schema dicts, or ``[]`` on error.
        """
        if not query or not query.strip():
            log.warning(
                "search_tools called with empty query",
                event="composio_search_empty",
            )
            return []

        log.debug(
            "search_tools",
            event="composio_search_tools",
            query=query,
        )

        def _sync_search() -> list[dict]:
            return self._toolset.find_actions_by_use_case(use_case=query)

        try:
            results: list[dict] = await asyncio.to_thread(_sync_search)
            log.debug(
                "search_tools complete",
                event="composio_search_tools_done",
                query=query,
                count=len(results),
            )
            return results
        except Exception as exc:
            log.error(
                "search_tools failed",
                event="composio_search_tools_error",
                query=query,
                error=str(exc),
            )
            return [{"error": str(exc)}]
