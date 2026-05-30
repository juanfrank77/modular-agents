"""
core/quiet_hours.py
-------------------
Message gating for quiet-hours boundaries.

Two public functions:
    is_quiet_hours(settings, now=None) -> str | None
    should_notify(settings, tag="", is_emergency=False, now=None) -> bool

No file I/O. All config comes from the Settings object.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config import Settings


def _parse_time(t_str: str) -> time:
    h, m = t_str.split(":")
    return time(int(h), int(m))


def _in_window(now_time: time, start_str: str, end_str: str) -> bool:
    """True if now_time is within [start, end). Handles overnight windows."""
    start = _parse_time(start_str)
    end = _parse_time(end_str)
    if start <= end:
        return start <= now_time < end
    return now_time >= start or now_time < end


def is_quiet_hours(settings: "Settings", now: datetime | None = None) -> str | None:
    """Return the active quiet-hours window name, or None if outside all windows."""
    if not settings.quiet_hours_enabled:
        return None
    now_time = (now or datetime.now()).time()
    windows = {
        "morning_routine": (settings.quiet_hours_morning_start, settings.quiet_hours_morning_end),
        "evening": (settings.quiet_hours_evening_start, settings.quiet_hours_evening_end),
    }
    for name, (start, end) in windows.items():
        if _in_window(now_time, start, end):
            return name
    return None


def should_notify(
    settings: "Settings",
    tag: str = "",
    is_emergency: bool = False,
    now: datetime | None = None,
) -> bool:
    """Central gate. Returns True if the message is allowed through."""
    if is_emergency:
        return True
    if not settings.quiet_hours_enabled:
        return True
    if any(kw in tag.lower() for kw in settings.emergency_keywords):
        return True
    window_name = is_quiet_hours(settings, now)
    if window_name is None:
        return True
    allowed_map = {
        "morning_routine": settings.quiet_hours_morning_allowed,
        "evening": settings.quiet_hours_evening_allowed,
    }
    return tag in allowed_map.get(window_name, [])