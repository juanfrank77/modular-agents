# test_knowledge_projects.py
"""Tests for the LibrarianAgent (knowledge ingestion) and ProjectsAgent."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import agents.librarian.agent as librarian_module
import agents.projects.agent as projects_module
from agents.librarian.agent import LibrarianAgent, _extract_actions, _slugify, _split_title
from agents.projects.agent import (
    ProjectsAgent,
    _days_since,
    _parse_update,
    _project_names,
)
from core.protocols import AgentEvent, EventType

_PROJECTS_MD = """# Projects

## Active Projects

### NINA
- Status: In progress

### Newsletter
- Status: In progress
"""

_DISTILLED = """TITLE: Pricing Anchors That Work

**Source**: pricing.txt
**Summary**: Anchoring shapes willingness to pay.
**Key ideas**:
- Show the expensive tier first
**Next actions**:
- [ ] Draft a three-tier pricing table for NINA
**Related projects**: NINA
"""


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_settings(tmp_path: Path):
    s = MagicMock()
    s.telegram_allowed_chat_ids = ["123"]
    s.librarian_agent_autonomy = "autonomous"
    s.projects_agent_autonomy = "supervised"
    s.memory_knowledge_dir = tmp_path / "knowledge"
    s.memory_context_dir = tmp_path / "context"
    s.memory_inbox_dir = tmp_path / "inbox"
    s.openai_api_key = ""
    s.tavily_api_key = ""
    s.ingest_max_file_mb = 20
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
    llm.complete = AsyncMock(return_value=llm_response)
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
    if cls is LibrarianAgent:
        # Stub out graphify so tests never shell out to the real CLI
        graphify = MagicMock()
        graphify.available = MagicMock(return_value=False)
        graphify.has_graph = MagicMock(return_value=False)
        graphify.query = AsyncMock(return_value=None)
        graphify.update = AsyncMock(return_value=True)
        agent._tools = MagicMock(graphify=graphify)
    return agent


def _event(text: str = "", data: dict | None = None, agent_name: str = "") -> AgentEvent:
    return AgentEvent(
        type=EventType.USER_MESSAGE,
        agent_name=agent_name,
        chat_id="123",
        text=text,
        data=data or {},
    )


# ── Librarian helper functions ────────────────────────────────────────────

class TestLibrarianHelpers:
    def test_split_title(self):
        title, body = _split_title("TITLE: My Note\n\n**Summary**: hi")
        assert title == "My Note"
        assert body.startswith("**Summary**")

    def test_split_title_missing(self):
        title, body = _split_title("just some text")
        assert title == "Untitled note"
        assert body == "just some text"

    def test_slugify(self):
        assert _slugify("Pricing Anchors That Work!") == "pricing-anchors-that-work"

    def test_extract_actions(self):
        actions = _extract_actions(_DISTILLED)
        assert actions == ["- [ ] Draft a three-tier pricing table for NINA"]


# ── LibrarianAgent ingestion ──────────────────────────────────────────────

class TestLibrarianIngestion:
    async def test_ingest_text_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path, llm_response=_DISTILLED)

        src = tmp_path / "pricing.txt"
        src.write_text("Anchoring is powerful for pricing pages.")

        resp = await agent.handle(
            _event(data={"file_path": str(src), "kind": "document"})
        )

        assert resp.success
        notes = list((tmp_path / "knowledge").glob("*-pricing-anchors-that-work.md"))
        assert len(notes) == 1
        assert "Pricing Anchors That Work" in notes[0].read_text()
        index = (tmp_path / "knowledge" / "INDEX.md").read_text()
        assert "pricing-anchors-that-work" in index
        state = json.loads((tmp_path / "state.json").read_text())
        assert len(state["notes"]) == 1
        agent.memory.save_solution.assert_awaited_once()
        agent.notifier.send.assert_awaited()

    async def test_unsupported_extension(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path)
        src = tmp_path / "video.avi"
        src.write_bytes(b"\x00\x01")

        await agent.handle(_event(data={"file_path": str(src)}))

        sent = agent.notifier.send.await_args.args[1]
        assert "can't read" in sent

    async def test_voice_without_openai_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path)
        src = tmp_path / "note.ogg"
        src.write_bytes(b"\x00\x01")

        await agent.handle(_event(data={"file_path": str(src), "kind": "voice"}))

        sent = agent.notifier.send.await_args.args[1]
        assert "OPENAI_API_KEY" in sent

    async def test_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path)

        await agent.handle(_event(data={"file_path": str(tmp_path / "ghost.pdf")}))

        sent = agent.notifier.send.await_args.args[1]
        assert "not found" in sent

    async def test_query_empty_knowledge_base(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path)

        await agent.handle(_event(text="what do I know about pricing?"))

        sent = agent.notifier.send.await_args.args[1]
        assert "empty" in sent


class TestLibrarianDigest:
    async def test_weekly_digest_resurfaces_least_seen(self, tmp_path, monkeypatch):
        state_file = tmp_path / "state.json"
        monkeypatch.setattr(librarian_module, "_STATE_FILE", state_file)
        agent = _make_agent(LibrarianAgent, tmp_path)

        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        (kdir / "2026-07-01-pricing.md").write_text(
            "# Pricing note\n\n**Next actions**:\n- [ ] Try anchoring\n"
        )
        state_file.write_text(json.dumps({
            "notes": {
                "2026-07-01-pricing": {
                    "created": "2026-07-01T00:00:00+00:00",
                    "source": "x", "surfaced_count": 0, "last_surfaced": None,
                }
            }
        }))

        event = AgentEvent(
            type=EventType.SCHEDULED_TASK, agent_name="librarian",
            chat_id="123", data={"task": "librarian_weekly_digest"},
        )
        resp = await agent.handle(event)

        assert "Pricing note" in resp.text
        assert "Try anchoring" in resp.text
        state = json.loads(state_file.read_text())
        assert state["notes"]["2026-07-01-pricing"]["surfaced_count"] == 1

    async def test_digest_with_no_notes_is_silent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path)

        event = AgentEvent(
            type=EventType.SCHEDULED_TASK, agent_name="librarian",
            chat_id="123", data={"task": "librarian_weekly_digest"},
        )
        resp = await agent.handle(event)
        assert resp.text == ""
        agent.notifier.send.assert_not_awaited()


# ── Librarian graphify integration ────────────────────────────────────────

class TestLibrarianGraphify:
    async def test_ingest_triggers_graph_update_when_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path, llm_response=_DISTILLED)
        agent._tools.graphify.available.return_value = True

        src = tmp_path / "pricing.txt"
        src.write_text("Anchoring is powerful.")
        await agent.handle(_event(data={"file_path": str(src), "kind": "document"}))
        # Let the fire-and-forget update task run
        import asyncio
        await asyncio.sleep(0)

        agent._tools.graphify.update.assert_awaited_once()

    async def test_ingest_skips_graph_update_when_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path, llm_response=_DISTILLED)

        src = tmp_path / "pricing.txt"
        src.write_text("Anchoring is powerful.")
        await agent.handle(_event(data={"file_path": str(src), "kind": "document"}))

        agent._tools.graphify.update.assert_not_awaited()

    async def test_query_includes_graph_context(self, tmp_path, monkeypatch):
        monkeypatch.setattr(librarian_module, "_STATE_FILE", tmp_path / "state.json")
        agent = _make_agent(LibrarianAgent, tmp_path, llm_response="Answer.")
        agent._tools.graphify.query = AsyncMock(return_value="pricing ↔ anchoring ↔ NINA")

        kdir = tmp_path / "knowledge"
        kdir.mkdir(parents=True)
        (kdir / "INDEX.md").write_text("# Knowledge index\n\n- pricing: Pricing note\n")

        await agent.handle(_event(text="what do I know about pricing?"))

        prompt = agent.llm.complete.await_args.kwargs["messages"][-1].content
        assert "<graph>" in prompt
        assert "pricing ↔ anchoring ↔ NINA" in prompt

    def test_graphify_tool_paths(self, tmp_path):
        from agents.librarian.tools.graphify import GraphifyTool

        tool = GraphifyTool(tmp_path)
        assert not tool.has_graph()
        (tmp_path / "graphify-out").mkdir()
        (tmp_path / "graphify-out" / "graph.json").write_text("{}")
        assert tool.has_graph()

    async def test_graphify_tool_query_none_without_graph(self, tmp_path):
        from agents.librarian.tools.graphify import GraphifyTool

        tool = GraphifyTool(tmp_path)
        assert await tool.query("anything") is None


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
