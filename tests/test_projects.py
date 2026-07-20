# test_projects.py
"""Tests for the ProjectsAgent (momentum tracking + weekly kickoff)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import agents.projects.agent as projects_module
from agents.projects.agent import (
    ProjectsAgent,
    _days_since,
    _parse_update,
    _project_names,
)
from core.protocols import AgentEvent, EventType, LLMResult

_PROJECTS_MD = """# Projects

## Active Projects

### NINA
- Status: In progress

### Newsletter
- Status: In progress
"""


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_settings(tmp_path: Path):
    s = MagicMock()
    s.telegram_allowed_chat_ids = ["123"]
    s.projects_agent_autonomy = "supervised"
    s.memory_context_dir = tmp_path / "context"
    return s


def _make_memory(projects_md: str = _PROJECTS_MD):
    memory = AsyncMock()
    memory.get_context = AsyncMock(return_value=projects_md)
    memory.build_context = AsyncMock(return_value=("", []))
    return memory


def _make_agent(cls, tmp_path: Path, llm_response: str = ""):
    settings = _make_settings(tmp_path)
    storage = AsyncMock()
    storage.get_or_create_session = AsyncMock(return_value="sess1")
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=LLMResult(text=llm_response))
    safety = AsyncMock()
    safety.check_action = AsyncMock(return_value=True)
    agent = cls(
        settings=settings,
        storage=storage,
        notifier=AsyncMock(),
        llm=llm,
        memory=_make_memory(),
        safety=safety,
        skill_loader=None,
        bus=None,
    )
    return agent


def _event(text: str = "", data: dict | None = None, agent_name: str = "") -> AgentEvent:
    return AgentEvent(
        type=EventType.USER_MESSAGE,
        agent_name=agent_name,
        chat_id="123",
        text=text,
        data=data or {},
    )


# ── Projects helper functions ─────────────────────────────────────────────

class TestProjectsHelpers:
    def test_parse_update(self):
        project, note = _parse_update("PROJECT: NINA\nNOTE: shipped onboarding")
        assert project == "NINA"
        assert note == "shipped onboarding"

    def test_parse_update_garbage(self):
        assert _parse_update("no structure here") == ("", "")

    def test_project_names(self):
        assert _project_names(_PROJECTS_MD) == ["NINA", "Newsletter"]

    def test_days_since_none(self):
        from datetime import datetime, timezone
        assert _days_since(None, datetime.now(timezone.utc)) is None


# ── ProjectsAgent ─────────────────────────────────────────────────────────

class TestProjectsAgent:
    async def test_log_progress(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr(projects_module, "_STATE_FILE", state_file)
        agent = _make_agent(
            ProjectsAgent, tmp_path, llm_response="PROJECT: NINA\nNOTE: Shipped onboarding flow"
        )
        (tmp_path / "context").mkdir(parents=True)
        (tmp_path / "context" / "projects.md").write_text(_PROJECTS_MD)

        resp = await agent.handle(_event(text="update: NINA — shipped the onboarding flow"))

        assert "Logged" in resp.text
        state = json.loads(state_file.read_text())
        assert "NINA" in state["projects"]
        assert state["projects"]["NINA"]["log"][-1]["note"] == "Shipped onboarding flow"
        projects_md = (tmp_path / "context" / "projects.md").read_text()
        assert "## Progress log" in projects_md
        assert "NINA: Shipped onboarding flow" in projects_md

    async def test_log_progress_unparseable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(projects_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(ProjectsAgent, tmp_path, llm_response="I have no idea")

        resp = await agent.handle(_event(text="update: something vague"))
        assert "couldn't tell which project" in resp.text

    async def test_momentum_summary_flags_stale(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr(projects_module, "_STATE_FILE", state_file)
        agent = _make_agent(ProjectsAgent, tmp_path)
        state_file.write_text(json.dumps({
            "projects": {
                "NINA": {
                    "last_update": "2026-06-01T00:00:00+00:00",
                    "log": [{"date": "2026-06-01", "note": "old work"}],
                }
            }
        }))

        summary = agent._momentum_summary(_PROJECTS_MD)
        assert "STALE" in summary
        assert "Newsletter: no updates logged yet" in summary

    async def test_project_chat_uses_llm(self, tmp_path, monkeypatch):
        monkeypatch.setattr(projects_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(ProjectsAgent, tmp_path, llm_response="Focus on NINA today.")

        resp = await agent.handle(_event(text="what should I work on?"))
        assert resp.text == "Focus on NINA today."
        agent.llm.complete.assert_awaited()

    async def test_weekly_kickoff_sends_to_all_chats(self, tmp_path, monkeypatch):
        monkeypatch.setattr(projects_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(ProjectsAgent, tmp_path, llm_response="1. NINA first.")

        event = AgentEvent(
            type=EventType.SCHEDULED_TASK, agent_name="projects",
            chat_id="123", data={"task": "projects_weekly_kickoff"},
        )
        resp = await agent.handle(event)
        assert resp.text == "1. NINA first."
        agent.notifier.send.assert_awaited_once()
        assert "Weekly Kickoff" in agent.notifier.send.await_args.args[1]

    async def test_unauthorized_chat_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(projects_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(ProjectsAgent, tmp_path)

        event = _event(text="status")
        event.chat_id = "999"
        resp = await agent.handle(event)
        assert not resp.success
