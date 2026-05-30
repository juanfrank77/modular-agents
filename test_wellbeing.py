# test_wellbeing.py
"""Tests for wellbeing integration: quiet_hours, BaseAgent.should_notify, WellbeingAgent."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_settings(**overrides):
    s = MagicMock()
    s.quiet_hours_enabled = overrides.get("enabled", True)
    s.quiet_hours_morning_start = overrides.get("morning_start", "07:00")
    s.quiet_hours_morning_end = overrides.get("morning_end", "09:30")
    s.quiet_hours_morning_allowed = overrides.get("morning_allowed", ["wellbeing-nudge"])
    s.quiet_hours_evening_start = overrides.get("evening_start", "19:30")
    s.quiet_hours_evening_end = overrides.get("evening_end", "07:00")
    s.quiet_hours_evening_allowed = overrides.get("evening_allowed", ["wellbeing-nudge", "emergency"])
    s.emergency_keywords = overrides.get("emergency_keywords", ["server_down", "security"])
    s.wellbeing_location = overrides.get("location", "")
    s.wellbeing_wake_time = overrides.get("wake_time", "07:00")
    s.wellbeing_bedtime = overrides.get("bedtime", "23:00")
    s.telegram_allowed_chat_ids = overrides.get("chat_ids", ["123"])
    return s


# ── core/quiet_hours.py tests ─────────────────────────────────────────────

class TestIsQuietHours:
    def test_midday_is_not_quiet(self):
        from core.quiet_hours import is_quiet_hours
        settings = _make_settings()
        now = datetime(2024, 1, 1, 14, 0, 0)
        assert is_quiet_hours(settings, now) is None

    def test_morning_routine_window(self):
        from core.quiet_hours import is_quiet_hours
        settings = _make_settings()
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert is_quiet_hours(settings, now) == "morning_routine"

    def test_evening_window_before_midnight(self):
        from core.quiet_hours import is_quiet_hours
        settings = _make_settings()
        now = datetime(2024, 1, 1, 22, 0, 0)
        assert is_quiet_hours(settings, now) == "evening"

    def test_evening_window_early_morning_overnight(self):
        from core.quiet_hours import is_quiet_hours
        settings = _make_settings()
        now = datetime(2024, 1, 1, 3, 0, 0)
        assert is_quiet_hours(settings, now) == "evening"

    def test_disabled_returns_none(self):
        from core.quiet_hours import is_quiet_hours
        settings = _make_settings(enabled=False)
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert is_quiet_hours(settings, now) is None


class TestShouldNotify:
    def test_midday_any_tag_allowed(self):
        from core.quiet_hours import should_notify
        settings = _make_settings()
        now = datetime(2024, 1, 1, 14, 0, 0)
        assert should_notify(settings, tag="deploy-alert", now=now) is True

    def test_morning_routine_blocks_non_allowed_tag(self):
        from core.quiet_hours import should_notify
        settings = _make_settings()
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert should_notify(settings, tag="deploy-alert", now=now) is False

    def test_morning_routine_allows_wellbeing_nudge(self):
        from core.quiet_hours import should_notify
        settings = _make_settings()
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert should_notify(settings, tag="wellbeing-nudge", now=now) is True

    def test_emergency_flag_bypasses_quiet_hours(self):
        from core.quiet_hours import should_notify
        settings = _make_settings()
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert should_notify(settings, tag="anything", is_emergency=True, now=now) is True

    def test_emergency_keyword_in_tag_bypasses_quiet_hours(self):
        from core.quiet_hours import should_notify
        settings = _make_settings()
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert should_notify(settings, tag="server_down", now=now) is True

    def test_disabled_always_allows(self):
        from core.quiet_hours import should_notify
        settings = _make_settings(enabled=False)
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert should_notify(settings, tag="deploy-alert", now=now) is True