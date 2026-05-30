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

# ── BaseAgent.should_notify tests ─────────────────────────────────────────

class TestBaseAgentShouldNotify:
    def _make_agent(self, **setting_overrides):
        from agents.echo.agent import EchoAgent
        settings = _make_settings(**setting_overrides)
        storage = MagicMock()
        notifier = MagicMock()
        return EchoAgent(settings=settings, storage=storage, notifier=notifier)

    def test_midday_returns_true(self):
        agent = self._make_agent()
        now = datetime(2024, 1, 1, 14, 0, 0)
        assert agent.should_notify("deploy-alert", _now=now) is True

    def test_morning_quiet_blocks_non_allowed(self):
        agent = self._make_agent()
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert agent.should_notify("deploy-alert", _now=now) is False

    def test_emergency_flag_always_passes(self):
        agent = self._make_agent()
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert agent.should_notify("anything", is_emergency=True, _now=now) is True
# ── WellbeingAgent tests ──────────────────────────────────────────────────────

class TestWellbeingAgentHelpers:
    def _make_agent(self, **setting_overrides):
        from agents.wellbeing.agent import WellbeingAgent
        settings = _make_settings(**setting_overrides)
        storage = MagicMock()
        notifier = MagicMock()
        notifier.send = AsyncMock()
        return WellbeingAgent(settings=settings, storage=storage, notifier=notifier)

    def test_already_sent_today_false_when_empty(self):
        agent = self._make_agent()
        assert agent._already_sent_today({}, "morning_nudge_sent_at") is False

    def test_already_sent_today_true_for_todays_timestamp(self):
        agent = self._make_agent()
        state = {"morning_nudge_sent_at": datetime.now().isoformat()}
        assert agent._already_sent_today(state, "morning_nudge_sent_at") is True

    def test_already_sent_today_false_for_yesterday(self):
        from datetime import date, timedelta
        agent = self._make_agent()
        yesterday = (date.today() - timedelta(days=1)).isoformat() + "T09:00:00"
        state = {"morning_nudge_sent_at": yesterday}
        assert agent._already_sent_today(state, "morning_nudge_sent_at") is False

    def test_pick_message_returns_one_of_the_options(self):
        agent = self._make_agent()
        messages = ["A", "B", "C"]
        result = agent._pick_message(messages)
        assert result in messages

    def test_suggest_activity_no_weather(self):
        agent = self._make_agent()
        assert agent._suggest_activity(None) == "run or yoga"

    def test_suggest_activity_rainy_returns_yoga(self):
        agent = self._make_agent()
        assert agent._suggest_activity({"rainy": True, "temp": 15}) == "yoga"

    def test_suggest_activity_cold_returns_yoga(self):
        agent = self._make_agent()
        assert agent._suggest_activity({"rainy": False, "temp": -10}) == "yoga"

    def test_suggest_activity_good_weather_returns_run(self):
        agent = self._make_agent()
        assert agent._suggest_activity({"rainy": False, "temp": 18}) == "run"

    def test_build_morning_message_weekend_no_weather(self):
        agent = self._make_agent()
        with patch.object(agent, "_get_weather", return_value=None):
            msg = agent._build_morning_message(is_weekend=True)
        assert "Routine when you're ready" in msg

    def test_build_morning_message_weekday_no_weather(self):
        agent = self._make_agent()
        with patch.object(agent, "_get_weather", return_value=None):
            msg = agent._build_morning_message(is_weekend=False)
        assert "Good day for" in msg

    def test_build_morning_message_weekday_with_weather(self):
        agent = self._make_agent()
        weather = {"temp": 12, "desc": "partly cloudy", "rainy": False}
        with patch.object(agent, "_get_weather", return_value=weather):
            msg = agent._build_morning_message(is_weekend=False)
        assert "12C" in msg
        assert "run" in msg

    def test_get_weather_no_location_returns_none(self):
        agent = self._make_agent(location="")
        assert agent._get_weather() is None


class TestWellbeingAgentHandle:
    def _make_agent(self, **setting_overrides):
        from agents.wellbeing.agent import WellbeingAgent
        settings = _make_settings(**setting_overrides)
        storage = MagicMock()
        notifier = MagicMock()
        notifier.send = AsyncMock()
        return WellbeingAgent(settings=settings, storage=storage, notifier=notifier)

    def _make_event(self, task: str):
        from core.protocols import AgentEvent, EventType
        return AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="wellbeing",
            chat_id="123",
            data={"task": task},
        )

    async def test_morning_nudge_sends_message(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_morning_weekday")
        with (
            patch.object(agent, "_load_state", return_value={}),
            patch.object(agent, "_save_state"),
            patch.object(agent, "_get_weather", return_value=None),
            patch.object(agent, "should_notify", return_value=True),
        ):
            response = await agent.handle(event)
        agent.notifier.send.assert_called_once()
        assert response.agent_name == "wellbeing"

    async def test_morning_nudge_skips_if_already_sent(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_morning_weekday")
        state = {"morning_nudge_sent_at": datetime.now().isoformat()}
        with (
            patch.object(agent, "_load_state", return_value=state),
            patch.object(agent, "should_notify", return_value=True),
        ):
            await agent.handle(event)
        agent.notifier.send.assert_not_called()

    async def test_morning_nudge_skips_during_quiet_hours(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_morning_weekday")
        with (
            patch.object(agent, "_load_state", return_value={}),
            patch.object(agent, "should_notify", return_value=False),
        ):
            await agent.handle(event)
        agent.notifier.send.assert_not_called()

    async def test_weekly_checkin_resets_stats(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_weekly")
        state = {"weekly_stats": {"routine_days": ["2024-01-01"], "streak": 3}}
        saved = {}

        def capture_save(s):
            saved.update(s)

        with (
            patch.object(agent, "_load_state", return_value=state),
            patch.object(agent, "_save_state", side_effect=capture_save),
            patch.object(agent, "should_notify", return_value=True),
        ):
            await agent.handle(event)

        assert saved.get("weekly_stats", {}).get("routine_days") == []

    async def test_health_check_returns_true(self):
        agent = self._make_agent()
        assert await agent.health_check() is True
