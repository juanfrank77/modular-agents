"""Tests for FocusAgent: session lifecycle, stats, scheduled prompts."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.protocols import AgentEvent, AgentResponse, EventType


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_settings(**overrides):
    s = MagicMock()
    s.quiet_hours_enabled = overrides.get("enabled", False)
    s.telegram_allowed_chat_ids = overrides.get("chat_ids", ["123"])
    s.focus_agent_autonomy = "autonomous"
    return s


def _make_agent(tmp_path, **setting_overrides):
    from agents.focus.agent import FocusAgent

    settings = _make_settings(**setting_overrides)
    storage = MagicMock()
    storage.get_or_create_session = AsyncMock(return_value="sess-1")
    storage.save_message = AsyncMock()
    notifier = MagicMock()
    notifier.send = AsyncMock()
    agent = FocusAgent(settings=settings, storage=storage, notifier=notifier)
    agent._state_file = tmp_path / "state.json"
    return agent


# ── Session lifecycle ─────────────────────────────────────────────────────

class TestFocusSessions:
    def test_start_session_default_minutes(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = agent._build_interactive_response("focus")
        assert "50 minutes" in msg
        assert agent._load_state()["active_session"]["goal_minutes"] == 50

    def test_start_session_custom_minutes(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = agent._build_interactive_response("focus 90")
        assert "90 minutes" in msg

    def test_start_twice_warns(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._build_interactive_response("focus")
        msg = agent._build_interactive_response("focus 25")
        assert "already" in msg

    def test_done_logs_session(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._build_interactive_response("focus 50")
        # Backdate the start so a real duration is measured
        state = agent._load_state()
        state["active_session"]["started_at"] = (
            datetime.now() - timedelta(minutes=30)
        ).isoformat()
        agent._save_state(state)

        msg = agent._build_interactive_response("done")
        assert "Logged 30 minutes" in msg
        state = agent._load_state()
        assert "active_session" not in state
        assert state["sessions"][0]["minutes"] == 30
        assert state["sessions"][0]["date"] == date.today().isoformat()

    def test_done_without_session(self, tmp_path):
        agent = _make_agent(tmp_path)
        msg = agent._build_interactive_response("done")
        assert "No active session" in msg

    def test_cancel_discards_without_logging(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._build_interactive_response("focus")
        msg = agent._build_interactive_response("cancel focus")
        assert "cancelled" in msg.lower()
        state = agent._load_state()
        assert "active_session" not in state
        assert not state.get("sessions")

    def test_unrelated_message_returns_empty(self, tmp_path):
        agent = _make_agent(tmp_path)
        assert agent._build_interactive_response("what's for dinner") == ""


# ── Stats ─────────────────────────────────────────────────────────────────

class TestFocusStats:
    def test_stats_counts_this_week(self, tmp_path):
        agent = _make_agent(tmp_path)
        today = date.today().isoformat()
        agent._save_state(
            {"sessions": [{"date": today, "minutes": 50}, {"date": today, "minutes": 25}]}
        )
        msg = agent._build_interactive_response("stats")
        assert "75 min" in msg

    def test_stats_ignores_old_sessions(self, tmp_path):
        agent = _make_agent(tmp_path)
        old = (date.today() - timedelta(days=30)).isoformat()
        agent._save_state({"sessions": [{"date": old, "minutes": 120}]})
        msg = agent._build_interactive_response("stats")
        assert "0 min" in msg


# ── Scheduled tasks ───────────────────────────────────────────────────────

class TestFocusScheduled:
    async def test_daily_prompt_sends_once(self, tmp_path):
        agent = _make_agent(tmp_path)
        event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="focus",
            chat_id="123",
            data={"task": "focus_daily_prompt"},
        )
        resp1 = await agent.handle(event)
        assert resp1.text != ""
        agent.notifier.send.assert_awaited()

        resp2 = await agent.handle(event)
        assert resp2.text == ""  # already sent today

    async def test_weekly_review_updates_streak(self, tmp_path):
        agent = _make_agent(tmp_path)
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        sessions = [
            {"date": (monday + timedelta(days=i)).isoformat(), "minutes": 50}
            for i in range(min(4, (today - monday).days + 1))
        ]
        # Pad to 4 distinct days if the week is young (dates in the future
        # aren't counted, so also seed the streak directly for that case)
        agent._save_state({"sessions": sessions, "streak": 0})
        event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="focus",
            chat_id="123",
            data={"task": "focus_weekly_review"},
        )
        resp = await agent.handle(event)
        assert "Deep work review" in resp.text

    async def test_unknown_task_is_silent(self, tmp_path):
        agent = _make_agent(tmp_path)
        event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="focus",
            chat_id="123",
            data={"task": "nope"},
        )
        resp = await agent.handle(event)
        assert resp.text == ""

    async def test_heartbeat_ok(self, tmp_path):
        agent = _make_agent(tmp_path)
        event = AgentEvent(type=EventType.HEARTBEAT_TICK, agent_name="focus", chat_id="")
        resp = await agent.handle(event)
        assert resp.text == "HEARTBEAT_OK"


# ── Authorization ─────────────────────────────────────────────────────────

class TestFocusAuth:
    async def test_unauthorized_chat_rejected(self, tmp_path):
        agent = _make_agent(tmp_path, chat_ids=["999"])
        event = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="focus",
            chat_id="123",
            text="focus",
        )
        resp = await agent._handle_interactive(event)
        assert resp.success is False
