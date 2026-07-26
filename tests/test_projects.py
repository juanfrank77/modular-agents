# test_projects.py
"""Tests for the ProjectsAgent (momentum tracking + weekly kickoff)."""
from __future__ import annotations

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

    def test_parse_progress_log_empty_without_section(self):
        assert projects_module._parse_progress_log("# Projects\n\nNo log here.") == {}

    def test_parse_progress_log_single_entry(self):
        md = (
            "# Projects\n\n## Progress log\n"
            "- 2026-07-20 · NINA: Shipped onboarding flow\n"
        )
        result = projects_module._parse_progress_log(md)
        assert result == {"NINA": [("2026-07-20", "Shipped onboarding flow")]}

    def test_parse_progress_log_multiple_entries_same_project_ordered(self):
        md = (
            "# Projects\n\n## Progress log\n"
            "- 2026-07-18 · NINA: Wrote the spec\n"
            "- 2026-07-20 · NINA: Shipped onboarding flow\n"
        )
        result = projects_module._parse_progress_log(md)
        assert result["NINA"] == [
            ("2026-07-18", "Wrote the spec"),
            ("2026-07-20", "Shipped onboarding flow"),
        ]

    def test_parse_progress_log_interleaved_projects(self):
        md = (
            "# Projects\n\n## Progress log\n"
            "- 2026-07-18 · NINA: Wrote the spec\n"
            "- 2026-07-19 · Newsletter: Sent issue 12\n"
            "- 2026-07-20 · NINA: Shipped onboarding flow\n"
        )
        result = projects_module._parse_progress_log(md)
        assert result["NINA"] == [
            ("2026-07-18", "Wrote the spec"),
            ("2026-07-20", "Shipped onboarding flow"),
        ]
        assert result["Newsletter"] == [("2026-07-19", "Sent issue 12")]


# ── ProjectsAgent ─────────────────────────────────────────────────────────

class TestProjectsAgent:
    async def test_log_progress(self, tmp_path, monkeypatch):
        agent = _make_agent(
            ProjectsAgent, tmp_path, llm_response="PROJECT: NINA\nNOTE: Shipped onboarding flow"
        )
        (tmp_path / "context").mkdir(parents=True)
        (tmp_path / "context" / "projects.md").write_text(_PROJECTS_MD)

        resp = await agent.handle(_event(text="update: NINA — shipped the onboarding flow"))

        assert "Logged" in resp.text
        projects_md = (tmp_path / "context" / "projects.md").read_text()
        assert "## Progress log" in projects_md
        assert "NINA: Shipped onboarding flow" in projects_md

    async def test_log_progress_unparseable(self, tmp_path, monkeypatch):
        agent = _make_agent(ProjectsAgent, tmp_path, llm_response="I have no idea")

        resp = await agent.handle(_event(text="update: something vague"))
        assert "couldn't tell which project" in resp.text

    async def test_log_progress_write_failure_is_reported_honestly(self, tmp_path, monkeypatch):
        agent = _make_agent(
            ProjectsAgent, tmp_path, llm_response="PROJECT: NINA\nNOTE: Shipped onboarding flow"
        )
        agent.safety.check_action = AsyncMock(return_value=False)
        (tmp_path / "context").mkdir(parents=True)
        (tmp_path / "context" / "projects.md").write_text(_PROJECTS_MD)

        resp = await agent.handle(_event(text="update: NINA — shipped the onboarding flow"))

        assert "couldn't save this update" in resp.text

    async def test_momentum_summary_flags_stale(self, tmp_path, monkeypatch):
        agent = _make_agent(ProjectsAgent, tmp_path)
        projects_md_with_log = (
            _PROJECTS_MD + "\n## Progress log\n- 2026-06-01 · NINA: old work\n"
        )

        summary = agent._momentum_summary(projects_md_with_log)
        assert "STALE" in summary
        assert "Newsletter: no updates logged yet" in summary

    async def test_project_chat_uses_llm(self, tmp_path, monkeypatch):
        agent = _make_agent(ProjectsAgent, tmp_path, llm_response="Focus on NINA today.")

        resp = await agent.handle(_event(text="what should I work on?"))
        assert resp.text == "Focus on NINA today."
        agent.llm.complete.assert_awaited()

    async def test_weekly_kickoff_sends_to_all_chats(self, tmp_path, monkeypatch):
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
        agent = _make_agent(ProjectsAgent, tmp_path)

        event = _event(text="status")
        event.chat_id = "999"
        resp = await agent.handle(event)
        assert not resp.success

    async def test_agent_write_never_touches_user_content(self, tmp_path):
        agent = _make_agent(
            ProjectsAgent, tmp_path, llm_response="PROJECT: NINA\nNOTE: Shipped onboarding flow"
        )
        (tmp_path / "context").mkdir(parents=True)
        path = tmp_path / "context" / "projects.md"
        path.write_text(_PROJECTS_MD)
        user_authored_part = _PROJECTS_MD.split("## Progress log")[0]

        from datetime import datetime, timezone
        await agent._append_progress_line(
            "123", "NINA", "Shipped onboarding flow", datetime.now(timezone.utc)
        )

        updated = path.read_text()
        # The agent may normalize trailing whitespace when it creates the
        # section, but it must never change, drop, or reorder user-authored
        # lines above the heading.
        assert updated.split("## Progress log")[0].rstrip() == user_authored_part.rstrip()
