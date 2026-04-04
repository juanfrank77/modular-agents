"""
agents/business/tools/calendar.py
-----------------------------------
Google Calendar integration for the Business agent via Composio.

Usage:
    from core.composio_tool import ComposioTool
    from agents.business.tools.calendar import CalendarTool

    composio = ComposioTool(api_key="...", user_id="alice")
    cal = CalendarTool(composio=composio)

    events = await cal.list_events(max_results=5)
    result = await cal.create_event(
        title="Team Sync",
        start="2026-04-05T10:00:00Z",
        end="2026-04-05T11:00:00Z",
        description="Weekly check-in",
    )
    block  = await cal.block_time(
        title="Deep Work",
        start="2026-04-05T14:00:00Z",
        end="2026-04-05T16:00:00Z",
    )
"""

from __future__ import annotations

from core.composio_tool import ComposioTool
from core.logger import get_logger

log = get_logger("calendar_tool")


class CalendarTool:
    """Google Calendar operations powered by Composio.

    Each method maps to a single Composio action slug and returns the raw
    result dict produced by the SDK.  On error the dict will contain an
    ``"error"`` key (handled transparently by :meth:`ComposioTool.execute`).

    Args:
        composio: An initialised :class:`~core.composio_tool.ComposioTool`
                  instance shared across tools.
    """

    def __init__(self, composio: ComposioTool) -> None:
        self._composio = composio
        log.debug("CalendarTool initialised", event="calendar_init")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_events(self, max_results: int = 10) -> list[dict]:
        """Fetch upcoming events from the user's primary Google Calendar.

        Args:
            max_results: Maximum number of events to return.  Defaults to 10.

        Returns:
            List of event dicts, or a single-item list containing an error
            dict if the action fails.
        """
        log.debug(
            "list_events",
            event="calendar_list_events",
            max_results=max_results,
        )
        result = await self._composio.execute(
            "GOOGLECALENDAR_LIST_EVENTS",
            max_results=max_results,
        )
        if "error" in result:
            log.warning(
                "list_events failed",
                event="calendar_list_events_error",
                error=result["error"],
            )
            return [result]
        events: list[dict] = result.get("items", result.get("data", [result]))
        log.debug(
            "list_events complete",
            event="calendar_list_events_done",
            count=len(events),
        )
        return events

    async def _create_event_raw(
        self,
        title: str,
        start: str,
        end: str,
        description: str = "",
    ) -> dict:
        """Private helper that calls the Composio create-event action directly.

        Args:
            title: Event title / summary (already formatted by the caller).
            start: Start datetime in ISO 8601 format.
            end: End datetime in ISO 8601 format.
            description: Optional event description.  Defaults to ``""``.

        Returns:
            Result dict from Composio (contains ``"id"`` on success,
            or ``"error"`` on failure).
        """
        result = await self._composio.execute(
            "GOOGLECALENDAR_CREATE_EVENT",
            summary=title,
            start=start,
            end=end,
            description=description,
        )
        return result

    async def create_event(
        self,
        title: str,
        start: str,
        end: str,
        description: str = "",
    ) -> dict:
        """Create a new event on the user's primary Google Calendar.

        Args:
            title: Event title / summary.
            start: Start datetime in ISO 8601 format (e.g. ``"2026-04-05T10:00:00Z"``).
            end: End datetime in ISO 8601 format.
            description: Optional event description.  Defaults to ``""``.

        Returns:
            Result dict from Composio (contains ``"id"`` on success,
            or ``"error"`` on failure).
        """
        log.debug(
            "create_event",
            event="calendar_create_event",
            title=title,
            start=start,
            end=end,
        )
        result = await self._create_event_raw(title, start, end, description)
        if "error" in result:
            log.warning(
                "create_event failed",
                event="calendar_create_event_error",
                title=title,
                error=result["error"],
            )
        else:
            log.debug(
                "create_event complete",
                event="calendar_create_event_done",
                title=title,
            )
        return result

    async def block_time(self, title: str, start: str, end: str) -> dict:
        """Block off time on the calendar with a prefixed title.

        Creates a calendar event with the title prefixed by ``"Blocked: "`` to
        indicate the time should not be scheduled over.

        Args:
            title: Description of what the block is for.
            start: Start datetime in ISO 8601 format.
            end: End datetime in ISO 8601 format.

        Returns:
            Result dict from Composio (contains ``"id"`` on success,
            or ``"error"`` on failure).
        """
        blocked_title = f"Blocked: {title}"
        log.debug(
            "block_time",
            event="calendar_block_time",
            title=blocked_title,
            start=start,
            end=end,
        )
        result = await self._create_event_raw(blocked_title, start, end)
        if "error" in result:
            log.warning(
                "block_time failed",
                event="calendar_block_time_error",
                title=blocked_title,
                error=result["error"],
            )
        else:
            log.debug(
                "block_time complete",
                event="calendar_block_time_done",
                title=blocked_title,
            )
        return result
