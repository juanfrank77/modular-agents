# test_quiet_hours_timezone.py
"""Tests that quiet_hours respects the user_timezone setting."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock


from core.quiet_hours import is_quiet_hours, should_notify


def _make_settings(user_timezone: str = "UTC", **overrides):
    s = MagicMock()
    s.user_timezone = user_timezone
    s.quiet_hours_enabled = overrides.get("enabled", True)
    s.quiet_hours_windows = overrides.get(
        "windows",
        [
            {
                "name": "morning_routine",
                "start": "07:00",
                "end": "09:30",
                "allowed": ["wellbeing-nudge"],
            },
            {
                "name": "evening",
                "start": "19:30",
                "end": "07:00",
                "allowed": ["wellbeing-nudge", "emergency"],
            },
        ],
    )
    s.emergency_keywords = ["server_down", "security"]
    return s


def test_is_quiet_hours_converts_aware_utc_to_user_timezone():
    """Denver is UTC-7 in January; quiet windows are 07:00-09:30 and 19:30-09:30."""
    settings = _make_settings(user_timezone="America/Denver")
    now = datetime(2024, 1, 15, 19, 0, 0, tzinfo=timezone.utc)
    # 19:00 UTC == 12:00 MST, between the two quiet windows.
    assert is_quiet_hours(settings, now) is None
    now = datetime(2024, 1, 15, 15, 0, 0, tzinfo=timezone.utc)
    # 15:00 UTC == 08:00 MST, inside the morning routine window.
    assert is_quiet_hours(settings, now) == "morning_routine"


def test_should_notify_uses_user_timezone_for_aware_now():
    settings = _make_settings(user_timezone="America/Denver")
    # 08:00 MST is quiet morning, so deploy-alert is blocked.
    now = datetime(2024, 1, 15, 15, 0, 0, tzinfo=timezone.utc)
    assert should_notify(settings, tag="deploy-alert", now=now) is False
    # 14:00 MST is not quiet, so deploy-alert is allowed.
    now = datetime(2024, 1, 15, 21, 0, 0, tzinfo=timezone.utc)
    assert should_notify(settings, tag="deploy-alert", now=now) is True


def test_naive_now_is_assumed_to_be_in_user_timezone():
    settings = _make_settings(user_timezone="America/Denver")
    # 08:00 naive, interpreted as 08:00 Denver, inside morning routine.
    now = datetime(2024, 1, 15, 8, 0, 0)
    assert is_quiet_hours(settings, now) == "morning_routine"
