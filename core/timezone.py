"""
core/timezone.py
----------------
Single source of truth for the user's configured timezone.

Storage continues to use UTC. Scheduler and quiet-hours gating both use the
timezone configured by USER_TIMEZONE so cron firing and notification gating
agree on the same wall-clock time.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from core.config import Settings

DEFAULT_TIMEZONE = "UTC"


def load_timezone(timezone_name: str | None = None) -> "ZoneInfo":
    """
    Return a ZoneInfo for *timezone_name*.

    Falls back to UTC and logs a warning if the name is unknown or the
    system is missing the IANA timezone database.
    """
    from zoneinfo import ZoneInfo

    name = (timezone_name or DEFAULT_TIMEZONE).strip()
    if not name:
        name = DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception as e:
        from core.logger import get_logger

        log = get_logger("timezone")
        log.warning(
            "Unknown timezone, falling back to UTC",
            event="timezone_unknown",
            requested=name,
            error=str(e),
        )
        return ZoneInfo(DEFAULT_TIMEZONE)


def _resolve_timezone(settings_or_name: str | "Settings") -> "ZoneInfo":
    """Accept a timezone name string or a Settings object."""
    if isinstance(settings_or_name, str):
        return load_timezone(settings_or_name)
    tz_name = getattr(settings_or_name, "user_timezone", None)
    return load_timezone(tz_name)


def now_in_user_timezone(settings_or_name: str | "Settings") -> datetime:
    """
    Return the current wall-clock time in the user's configured timezone.

    Accepts either a Settings object with a ``user_timezone`` attribute, or a
    timezone name string.
    """
    tz = _resolve_timezone(settings_or_name)
    return datetime.now(tz)


def as_user_timezone(
    dt: datetime,
    settings_or_name: str | "Settings",
) -> datetime:
    """
    Convert *dt* to the user's configured timezone.

    If *dt* is naive, it is assumed to already be in the user's timezone.
    """
    tz = _resolve_timezone(settings_or_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)
