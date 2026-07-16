from __future__ import annotations

from dataclasses import dataclass

from core.composio_tool import ComposioTool
from core.config import Settings
from core.logger import get_logger

log = get_logger("business_tools")


class BusinessToolsUnavailable(Exception):
    """Raised when business tools cannot be initialized."""
    pass


@dataclass
class BusinessTools:
    gmail: "GmailTool"
    calendar: "CalendarTool"


def build_tools(settings: Settings) -> BusinessTools:
    """Build and return BusinessTools instance.

    Raises:
        BusinessToolsUnavailable: If api_key is missing or ComposioTool raises RuntimeError.
    """
    from agents.business.tools.gmail import GmailTool
    from agents.business.tools.calendar import CalendarTool

    if not settings.composio_api_key:
        raise BusinessToolsUnavailable(
            "Google account not connected — COMPOSIO_API_KEY is not set"
        )

    user_id = settings.composio_user_id if settings.composio_user_id else "default"

    try:
        composio = ComposioTool(api_key=settings.composio_api_key, user_id=user_id)
    except RuntimeError as e:
        raise BusinessToolsUnavailable(str(e)) from e

    log.info("Business tools built", event="business_tools_built")
    return BusinessTools(
        gmail=GmailTool(composio=composio),
        calendar=CalendarTool(composio=composio),
    )