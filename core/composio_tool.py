"""
core/composio_tool.py
---------------------
Async wrapper around the Composio SDK for external app access (Gmail, Calendar,
and any other connected app).

Uses the current-generation Composio SDK (``composio`` >= 0.30 with the
``composio-anthropic`` provider package). The legacy ``ComposioToolSet`` API
was removed upstream; this wrapper keeps the project's own interface stable:
``ComposioTool(api_key, user_id)`` with ``execute`` / ``get_tools`` /
``search_tools``.

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
    from composio import Composio
    from composio_anthropic import AnthropicProvider

    _COMPOSIO_AVAILABLE = True
except ImportError:
    _COMPOSIO_AVAILABLE = False
    Composio = None  # type: ignore[assignment,misc]
    AnthropicProvider = None  # type: ignore[assignment,misc]


class ComposioTool:
    """Async wrapper around the Composio SDK.

    The current-generation SDK is synchronous; this class dispatches every
    call to a thread via ``asyncio.to_thread()`` so callers can ``await``
    them without blocking the event loop.

    Args:
        api_key: Composio project API key used to authenticate requests.
        user_id: User identifier scoped to the connected accounts.
                 Defaults to ``"default"``.

    Raises:
        RuntimeError: If ``composio`` / ``composio-anthropic`` is not installed.
    """

    def __init__(
        self,
        api_key: str,
        user_id: str = "default",
        toolkit_versions: dict[str, str] | None = None,
    ) -> None:
        """Create the wrapper.

        Args:
            api_key: Composio project API key.
            user_id: User identifier scoped to the connected accounts.
            toolkit_versions: Optional ``{toolkit_slug: version}`` map for
                reproducible tool execution (e.g. ``{"gmail": "20251103_01"}``).
                When omitted, tools run on the toolkit's latest version —
                matching the legacy SDK's behaviour — via
                ``dangerously_skip_version_check`` on every execution.
        """
        if not _COMPOSIO_AVAILABLE:
            raise RuntimeError(
                "composio / composio-anthropic is not installed. "
                "Run: uv pip install composio composio-anthropic"
            )
        self._user_id = user_id
        sdk_kwargs: dict[str, Any] = {"api_key": api_key, "provider": AnthropicProvider()}
        if toolkit_versions:
            sdk_kwargs["toolkit_versions"] = toolkit_versions
        self._sdk: Any = Composio(**sdk_kwargs)
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
            tool_slug: The Composio tool slug (e.g. ``"GMAIL_FETCH_EMAILS"``).
            **arguments: Keyword arguments forwarded to the tool payload.

        Returns:
            The tool's ``data`` dict on success. On failure, returns a dict
            with an ``"error"`` key containing the failure message.
        """
        log.debug(
            "execute action",
            event="composio_execute",
            tool_slug=tool_slug,
        )

        def _sync_execute() -> dict:
            # The new SDK refuses 'latest' (its default toolkit version) for
            # manual execution unless the check is explicitly skipped. The
            # legacy SDK always executed the latest version, so we preserve
            # that behaviour; pass `toolkit_versions` to __init__ to pin
            # specific versions instead.
            response = self._sdk.tools.execute(
                tool_slug,
                dict(arguments),
                user_id=self._user_id,
                dangerously_skip_version_check=True,
            )
            # ToolExecutionResponse is a TypedDict: {data, error, successful}.
            if not response.get("successful") or response.get("error"):
                return {
                    "error": response.get("error") or "tool execution failed"
                }
            return response.get("data") or {}

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
            toolkits: List of Composio toolkit names
                      (e.g. ``["gmail", "googlecalendar"]``).

        Returns:
            List of tool schema dicts, or a single-item list with an
            ``"error"`` key on failure.
        """
        log.debug(
            "get_tools",
            event="composio_get_tools",
            toolkits=toolkits,
        )

        def _sync_get_tools() -> list[dict]:
            return self._sdk.tools.get(self._user_id, toolkits=toolkits)

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
        """Search available Composio tools by natural-language use case.

        Useful for dynamically discovering which tool slug to use for a given
        task described in natural language.

        Args:
            query: A natural-language description of the desired action
                   (e.g. ``"send an email"``).

        Returns:
            List of matching tool schema dicts, or a single-item list with an
            ``"error"`` key on failure.
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
            return self._sdk.tools.get(self._user_id, search=query)

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
