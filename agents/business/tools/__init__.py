from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core.composio_tool import ComposioTool
from core.config import Settings
from core.local_file_tool import LocalFileTool
from core.logger import get_logger

if TYPE_CHECKING:
    from agents.business.tools.calendar import LocalCalendarTool
    from agents.business.tools.gmail import GmailTool

log = get_logger("business_tools")


class BusinessToolsUnavailable(Exception):
    """Raised when business tools cannot be initialized.

    Retained for API compatibility; ``build_tools`` no longer raises it —
    a missing Google connection degrades to ``gmail=None`` instead.
    """
    pass


@dataclass
class BusinessTools:
    gmail: "GmailTool | None"
    calendar: "LocalCalendarTool"
    local_file: LocalFileTool


def build_tools(settings: Settings) -> BusinessTools:
    """Build and return BusinessTools instance.

    The calendar tool is local SQLite (``memory/calendar.db`` by convention) —
    it never touches Composio and is built unconditionally. Gmail still runs
    through Composio: if ``COMPOSIO_API_KEY`` is unset or the
    ``composio-anthropic`` SDK cannot initialise, ``gmail`` is ``None`` and
    Gmail actions fail with a scoped error while calendar and local-file
    tools keep working. The calendar must not be held hostage by Gmail
    availability.
    """
    from agents.business.tools.gmail import GmailTool
    from agents.business.tools.calendar import LocalCalendarTool

    # Local calendar DB. getattr fallback keeps lightweight test fakes working.
    calendar_db_path = getattr(settings, "calendar_db_path", Path("memory/calendar.db"))

    gmail: GmailTool | None = None
    if not settings.composio_api_key:
        log.warning(
            "Gmail unavailable — COMPOSIO_API_KEY not set; calendar unaffected",
            event="gmail_unavailable",
        )
    else:
        user_id = settings.composio_user_id if settings.composio_user_id else "default"
        try:
            composio = ComposioTool(api_key=settings.composio_api_key, user_id=user_id)
            gmail = GmailTool(composio=composio)
        except Exception as e:
            log.warning(
                "Gmail unavailable — Composio failed to initialise; calendar unaffected",
                event="gmail_unavailable",
                error=str(e),
            )

    log.info(
        "Business tools built",
        event="business_tools_built",
        gmail_available=gmail is not None,
    )
    return BusinessTools(
        gmail=gmail,
        calendar=LocalCalendarTool(db_path=str(calendar_db_path)),
        local_file=LocalFileTool(allowed_paths=settings.local_file_paths),
    )
