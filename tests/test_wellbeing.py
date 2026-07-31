# test_wellbeing.py
"""Tests for wellbeing integration: quiet_hours, BaseAgent.should_notify, WellbeingAgent."""
from __future__ import annotations

from datetime import datetime, timezone
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

from core.protocols import Message


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_settings(**overrides):
    s = MagicMock()
    s.quiet_hours_enabled = overrides.get("enabled", True)
    s.quiet_hours_windows = overrides.get(
        "windows",
        [
            {
                "name": "morning_routine",
                "start": overrides.get("morning_start", "07:00"),
                "end": overrides.get("morning_end", "09:30"),
                "allowed": overrides.get("morning_allowed", ["wellbeing-nudge"]),
            },
            {
                "name": "evening",
                "start": overrides.get("evening_start", "19:30"),
                "end": overrides.get("evening_end", "07:00"),
                "allowed": overrides.get(
                    "evening_allowed", ["wellbeing-nudge", "emergency"]
                ),
            },
        ],
    )
    s.emergency_keywords = overrides.get("emergency_keywords", ["server_down", "security"])
    s.wellbeing_location = overrides.get("location", "")
    s.wellbeing_wake_time = overrides.get("wake_time", "07:00")
    s.wellbeing_bedtime = overrides.get("bedtime", "23:00")
    s.telegram_allowed_chat_ids = overrides.get("chat_ids", ["123"])
    s.user_timezone = overrides.get("user_timezone", "UTC")
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

    def test_arbitrary_third_named_window(self):
        from core.quiet_hours import is_quiet_hours
        settings = _make_settings(
            windows=[
                {"name": "morning_routine", "start": "07:00", "end": "09:30", "allowed": []},
                {"name": "evening", "start": "19:30", "end": "07:00", "allowed": []},
                {"name": "focus_block", "start": "13:00", "end": "15:00", "allowed": ["urgent"]},
            ]
        )
        now = datetime(2024, 1, 1, 14, 0, 0)
        assert is_quiet_hours(settings, now) == "focus_block"

    def test_disabled_returns_none(self):
        from core.quiet_hours import is_quiet_hours
        settings = _make_settings(enabled=False)
        now = datetime(2024, 1, 1, 8, 0, 0)
        assert is_quiet_hours(settings, now) is None


class TestShouldNotify:
    def test_custom_window_allows_only_its_tags(self):
        from core.quiet_hours import should_notify
        settings = _make_settings(
            windows=[
                {"name": "focus_block", "start": "13:00", "end": "15:00", "allowed": ["urgent"]},
            ]
        )
        now = datetime(2024, 1, 1, 14, 0, 0)
        assert should_notify(settings, tag="urgent", now=now) is True
        assert should_notify(settings, tag="deploy-alert", now=now) is False

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
        storage.get_or_create_session = AsyncMock(return_value="wellbeing_123")
        storage.get_session_messages = AsyncMock(return_value=[])
        storage.save_message = AsyncMock()
        notifier = MagicMock()
        notifier.send = AsyncMock()
        return WellbeingAgent(settings=settings, storage=storage, notifier=notifier)

    def test_already_sent_today_false_when_empty(self):
        agent = self._make_agent()
        assert agent._already_sent_today({}, "morning_nudge_sent_at") is False

    def test_already_sent_today_true_for_todays_timestamp(self):
        agent = self._make_agent()
        from core.timezone import now_in_user_timezone
        state = {"morning_nudge_sent_at": now_in_user_timezone(agent.settings).isoformat()}
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

    async def test_build_morning_message_weekend_no_weather(self):
        agent = self._make_agent()
        with (
            patch.object(agent, "_get_weather", new_callable=AsyncMock, return_value=None),
            patch("random.choice", return_value="Morning. Routine when you're ready."),
        ):
            msg = await agent._build_morning_message(is_weekend=True)
        assert "Routine when you're ready" in msg

    async def test_build_morning_message_weekday_no_weather(self):
        agent = self._make_agent()
        with (
            patch.object(agent, "_get_weather", new_callable=AsyncMock, return_value=None),
            patch("random.choice", return_value="Morning. Good day for [run/yoga]."),
        ):
            msg = await agent._build_morning_message(is_weekend=False)
        assert "Good day for" in msg

    async def test_build_morning_message_weekday_with_weather(self):
        agent = self._make_agent()
        weather = {"temp": 12, "desc": "partly cloudy", "rainy": False}
        with (
            patch.object(agent, "_get_weather", new_callable=AsyncMock, return_value=weather),
            patch("random.choice", return_value="Morning. [temp]C, [condition]. Good day for [run/yoga]."),
        ):
            msg = await agent._build_morning_message(is_weekend=False)
        assert "12C" in msg
        assert "partly cloudy" in msg
        assert "run" in msg

    async def test_build_morning_message_uses_skill_pool(self):
        agent = self._make_agent()
        skill = dedent("""
        ## Weekday pool:
        - "Morning. [temp]C, [condition]. Skill weekday one."
        - "Morning. [temp]C, [condition]. Skill weekday two."
        ## Weather fallback
        - "Morning. Skill fallback."
        """)
        with (
            patch.object(agent, "_get_weather", new_callable=AsyncMock, return_value=None),
            patch("random.choice", return_value="Morning. Skill fallback."),
            patch.object(agent, "_load_skill_text", new_callable=AsyncMock, return_value=skill),
        ):
            msg = await agent._build_morning_message(is_weekend=False)
        assert "Skill fallback" in msg

    async def test_get_weather_no_location_returns_none(self):
        agent = self._make_agent(location="")
        assert await agent._get_weather() is None

    def test_parse_bullet_pool(self):
        from agents.wellbeing.agent import WellbeingAgent
        content = dedent("""
        ## Message Construction
        Weekday pool:
        - "Message one."
        - "Message two."
        ## Next section
        - "Ignored."
        """)
        assert WellbeingAgent._parse_bullet_pool(content, "Weekday pool:") == [
            "Message one.",
            "Message two.",
        ]

    def test_parse_numbered_pool(self):
        from agents.wellbeing.agent import WellbeingAgent
        content = dedent("""
        ## Message Construction
        1. "First message."
        2. "Second message."
        """)
        assert WellbeingAgent._parse_numbered_pool(content) == [
            "First message.",
            "Second message.",
        ]

    def test_parse_deflection_rules(self):
        from agents.wellbeing.agent import WellbeingAgent
        content = dedent("""
        ### Should deflect (not the agent's job)
        - Requests to send an immediate nudge → "That would skip the quiet-hours guard.
          Use /quiet to check your current settings."
        - Complex emotional support → "I'm a scheduled nudge bot."
        - Medical or health professional topics → "I'm not a doctor."
        """)
        agent = WellbeingAgent(settings=_make_settings(), storage=MagicMock(), notifier=MagicMock())
        rules = agent._parse_deflection_rules(content)
        assert len(rules) == 3
        keywords, response = rules[0]
        assert "send now" in keywords
        assert "That would skip the quiet-hours guard." in response

    def test_respond_settings_does_not_claim_to_update_preferences(self):
        agent = self._make_agent()
        text = agent._respond_settings("quiet hours")
        assert "Edit memory/context/preferences.md directly" in text
        assert "I can update it there" not in text


class TestWellbeingAgentHandle:
    def _make_agent(self, **setting_overrides):
        from agents.wellbeing.agent import WellbeingAgent
        settings = _make_settings(**setting_overrides)
        storage = MagicMock()
        storage.get_or_create_session = AsyncMock(return_value="wellbeing_123")
        storage.get_session_messages = AsyncMock(return_value=[])
        storage.save_message = AsyncMock()
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
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value={}),
            patch.object(agent, "_save_state", new_callable=AsyncMock),
            patch.object(agent, "_get_weather", new_callable=AsyncMock, return_value=None),
            patch.object(agent, "_load_skill_text", new_callable=AsyncMock, return_value=None),
            patch.object(agent, "should_notify", return_value=True),
            patch("random.choice", return_value="Morning. Good day for [run/yoga]."),
        ):
            response = await agent.handle(event)
        agent.notifier.send.assert_called_once()
        assert response.agent_name == "wellbeing"

    async def test_morning_nudge_skips_if_already_sent(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_morning_weekday")
        from core.timezone import now_in_user_timezone
        state = {"morning_nudge_sent_at": now_in_user_timezone(agent.settings).isoformat()}
        with (
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value=state),
            patch.object(agent, "should_notify", return_value=True),
        ):
            await agent.handle(event)
        agent.notifier.send.assert_not_called()

    async def test_morning_nudge_skips_during_quiet_hours(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_morning_weekday")
        with (
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value={}),
            patch.object(agent, "should_notify", return_value=False),
        ):
            await agent.handle(event)
        agent.notifier.send.assert_not_called()

    async def test_followup_skips_when_user_replied_today(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_followup")
        agent.storage.get_session_messages = AsyncMock(return_value=[
            Message(role="user", content="already up", agent="wellbeing", timestamp=datetime.now(timezone.utc))
        ])
        with (
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value={}),
            patch.object(agent, "_save_state", new_callable=AsyncMock),
            patch.object(agent, "should_notify", return_value=True),
        ):
            await agent.handle(event)
        agent.notifier.send.assert_not_called()

    async def test_followup_sends_when_user_has_not_replied(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_followup")
        agent.storage.get_session_messages = AsyncMock(return_value=[])
        with (
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value={}),
            patch.object(agent, "_save_state", new_callable=AsyncMock),
            patch.object(agent, "should_notify", return_value=True),
        ):
            await agent.handle(event)
        agent.notifier.send.assert_called_once()
        agent.notifier.send.assert_called_with("123", "Time to move.")

    async def test_followup_skips_on_weekends(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_followup")
        # 2024-01-06 is a Saturday
        with (
            patch("agents.wellbeing.agent.now_in_user_timezone") as mock_now,
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value={}),
            patch.object(agent, "should_notify", return_value=True),
        ):
            mock_now.return_value = datetime(2024, 1, 6, 8, 30, 0)
            await agent.handle(event)
        agent.notifier.send.assert_not_called()

    async def test_evening_uses_skill_message(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_evening")
        skill = dedent("""
        ## Message Construction
        1. "Evening skill one."
        2. "Evening skill two."
        """)
        with (
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value={}),
            patch.object(agent, "_save_state", new_callable=AsyncMock),
            patch.object(agent, "_load_skill_text", new_callable=AsyncMock, return_value=skill),
            patch.object(agent, "should_notify", return_value=True),
        ):
            response = await agent.handle(event)
        assert response.text in ["Evening skill one.", "Evening skill two."]
        agent.notifier.send.assert_called_once()

    async def test_bedtime_uses_skill_message(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_bedtime")
        skill = dedent("""
        ## Message Construction
        1. "Bedtime skill one."
        2. "Bedtime skill two."
        """)
        with (
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value={}),
            patch.object(agent, "_save_state", new_callable=AsyncMock),
            patch.object(agent, "_load_skill_text", new_callable=AsyncMock, return_value=skill),
            patch.object(agent, "should_notify", return_value=True),
        ):
            response = await agent.handle(event)
        assert response.text in ["Bedtime skill one.", "Bedtime skill two."]
        agent.notifier.send.assert_called_once()

    async def test_weekly_checkin_uses_seven_day_denominator(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_weekly")
        # 2024-01-01 is a Monday; today is Tuesday, so only 1 day recorded.
        state = {"weekly_stats": {"routine_days": ["2024-01-01"], "streak": 3}}
        with (
            patch("agents.wellbeing.agent.now_in_user_timezone") as mock_now,
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value=state),
            patch.object(agent, "_save_state", new_callable=AsyncMock),
            patch.object(agent, "should_notify", return_value=True),
        ):
            mock_now.return_value = datetime(2024, 1, 2, 9, 0, 0)
            response = await agent.handle(event)
        assert "1/7 days" in response.text

    async def test_weekly_checkin_resets_stats(self):
        agent = self._make_agent()
        event = self._make_event("wellbeing_weekly")
        state = {"weekly_stats": {"routine_days": ["2024-01-01"], "streak": 3}}
        saved = {}

        def capture_save(s):
            saved.update(s)

        with (
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value=state),
            patch.object(agent, "_save_state", new_callable=AsyncMock, side_effect=capture_save),
            patch.object(agent, "should_notify", return_value=True),
        ):
            await agent.handle(event)

        assert saved.get("weekly_stats", {}).get("routine_days") == []

    async def test_health_check_returns_true(self):
        agent = self._make_agent()
        assert await agent.health_check() is True


class TestWellbeingAgentSchedules:
    def _make_agent(self, **setting_overrides):
        from agents.wellbeing.agent import WellbeingAgent
        settings = _make_settings(**setting_overrides)
        storage = MagicMock()
        notifier = MagicMock()
        return WellbeingAgent(settings=settings, storage=storage, notifier=notifier)

    async def test_register_schedules_uses_wake_and_bedtime(self):
        from core.scheduler import scheduler
        agent = self._make_agent(wake_time="06:30", bedtime="22:00", chat_ids=["123"])
        bus = MagicMock()
        with patch.object(scheduler, "add_cron_job") as mock_add:
            await agent.register_schedules(bus)

        registered_crons = [call.kwargs["cron"] for call in mock_add.call_args_list]
        assert "30 6 * * 1-5" in registered_crons
        assert "30 7 * * 0,6" in registered_crons  # weekend = wake + 1 hour
        assert "0 22 * * *" in registered_crons

    async def test_register_schedules_defaults_when_invalid(self):
        from core.scheduler import scheduler
        agent = self._make_agent(wake_time="bad", bedtime="bad", chat_ids=["123"])
        bus = MagicMock()
        with patch.object(scheduler, "add_cron_job") as mock_add:
            await agent.register_schedules(bus)

        registered_crons = [call.kwargs["cron"] for call in mock_add.call_args_list]
        assert "0 7 * * 1-5" in registered_crons
        assert "0 8 * * 0,6" in registered_crons
        assert "0 23 * * *" in registered_crons


class TestWellbeingInteractive:
    def _make_agent(self, **setting_overrides):
        from agents.wellbeing.agent import WellbeingAgent
        settings = _make_settings(**setting_overrides)
        storage = MagicMock()
        storage.get_or_create_session = AsyncMock(return_value="wellbeing_123")
        storage.get_session_messages = AsyncMock(return_value=[])
        storage.save_message = AsyncMock()
        notifier = MagicMock()
        notifier.send = AsyncMock()
        return WellbeingAgent(settings=settings, storage=storage, notifier=notifier)

    def _make_event(self, text: str):
        from core.protocols import AgentEvent, EventType
        return AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="wellbeing",
            chat_id="123",
            text=text,
        )

    async def test_deflects_send_now_request(self):
        agent = self._make_agent()
        event = self._make_event("send now please")
        skill = dedent("""
        ### Should deflect (not the agent's job)
        - Requests to send an immediate nudge → "That would skip the quiet-hours guard.
          Use /quiet to check your current settings."
        """)
        with patch.object(agent, "_load_skill_text", new_callable=AsyncMock, return_value=skill):
            response = await agent.handle(event)
        assert "skip the quiet-hours guard" in response.text

    async def test_deflects_medical_topic(self):
        agent = self._make_agent()
        event = self._make_event("I need medical advice")
        skill = """
### Should deflect (not the agent's job)
- Medical or health professional topics → "I'm not a doctor."
"""
        with patch.object(agent, "_load_skill_text", new_callable=AsyncMock, return_value=skill):
            response = await agent.handle(event)
        assert "I'm not a doctor" in response.text

    async def test_responds_with_stats(self):
        agent = self._make_agent()
        event = self._make_event("how is my routine")
        state = {"weekly_stats": {"routine_days": ["2024-01-01", "2024-01-02"], "streak": 2}}
        with (
            patch("agents.wellbeing.agent.now_in_user_timezone") as mock_now,
            patch.object(agent, "_load_state", new_callable=AsyncMock, return_value=state),
        ):
            mock_now.return_value = datetime(2024, 1, 2, 14, 0, 0)
            response = await agent.handle(event)
        assert "2/7 days" in response.text
        assert "Streak: 2 weeks" in response.text

    async def test_unauthorized_interactive_returns_failure(self):
        agent = self._make_agent(chat_ids=["999"])
        event = self._make_event("hello")
        response = await agent.handle(event)
        assert response.text == "Unauthorized."
        assert response.success is False
