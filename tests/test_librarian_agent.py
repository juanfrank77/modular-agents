"""Tests for LibrarianAgent: ingestion routing, distillation/persistence,
knowledge queries, weekly digest, and note helpers."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.librarian.agent import (
    _MAX_INGEST_CHARS,
    LibrarianAgent,
    _extract_actions,
    _slugify,
    _split_title,
)
from core.protocols import AgentEvent, EventType


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_settings(**overrides):
    s = MagicMock()
    s.quiet_hours_enabled = overrides.get("enabled", False)
    s.telegram_allowed_chat_ids = overrides.get("chat_ids", ["123"])
    s.librarian_agent_autonomy = "autonomous"
    s.tavily_api_key = ""
    return s


def _make_agent(tmp_path, **setting_overrides):
    settings = _make_settings(**setting_overrides)
    settings.memory_knowledge_dir = tmp_path / "knowledge"
    storage = MagicMock()
    storage.get_or_create_session = AsyncMock(return_value="sess-1")
    storage.save_message = AsyncMock()
    notifier = MagicMock()
    notifier.send = AsyncMock()
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value="TITLE: Test Note\n\n**Summary**: distilled.")
    memory = MagicMock()
    memory.get_context = AsyncMock(return_value="proj-a, proj-b")
    memory.build_context = AsyncMock(return_value=("", []))
    memory.save_solution = AsyncMock()
    agent = LibrarianAgent(
        settings=settings, storage=storage, notifier=notifier, llm=llm, memory=memory
    )
    agent._state_file = tmp_path / "state.json"
    agent._tools = _stub_tools()
    return agent


def _stub_tools(query_answer=None):
    graphify = MagicMock()
    graphify.available.return_value = query_answer is not None
    graphify.query = AsyncMock(return_value=query_answer)
    return SimpleNamespace(graphify=graphify)


def _user_event(text="", data=None, chat_id="123"):
    return AgentEvent(
        type=EventType.USER_MESSAGE,
        agent_name="librarian",
        chat_id=chat_id,
        text=text,
        data=data or {},
    )


def _prompt_content(agent) -> str:
    kwargs = agent.llm.complete.await_args.kwargs
    return kwargs["messages"][0].content


# ── Pure helpers ──────────────────────────────────────────────────────────

class TestNoteHelpers:
    def test_split_title_extracts_title_line(self):
        title, body = _split_title("TITLE: Pricing Playbook\n\n**Summary**: x")
        assert title == "Pricing Playbook"
        assert body == "**Summary**: x"

    def test_split_title_without_title_line(self):
        title, body = _split_title("just a body")
        assert title == "Untitled note"
        assert body == "just a body"

    def test_split_title_empty_title(self):
        title, body = _split_title("TITLE:\nbody line")
        assert title == "Untitled note"
        assert body == "body line"

    def test_slugify_strips_non_ascii_and_caps(self):
        assert _slugify("Héllo World! 123") == "h-llo-world-123"
        assert _slugify("a" * 100) == "a" * 60

    def test_extract_actions_caps_at_three(self):
        note = "**Next actions**\n" + "\n".join(f"- [ ] step {i}" for i in range(5))
        assert len(_extract_actions(note)) == 3

    def test_extract_actions_stops_at_non_bullet(self):
        note = "**Next actions**\n- [ ] one\n**Summary**: x\n- [ ] two"
        assert _extract_actions(note) == ["- [ ] one"]

    def test_extract_actions_no_section(self):
        assert _extract_actions("no actions here") == []


# ── Routing ───────────────────────────────────────────────────────────────

class TestRouting:
    async def test_file_path_routes_to_ingest_file(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._ingest_file = AsyncMock()
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF")
        resp = await agent.handle(_user_event(data={"file_path": str(f)}))
        agent._ingest_file.assert_awaited_once()
        called_path = agent._ingest_file.await_args.args[1]
        assert str(called_path) == str(f)

    async def test_url_routes_to_ingest_url(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._ingest_url = AsyncMock()
        await agent.handle(_user_event(text="check https://example.com/a later"))
        agent._ingest_url.assert_awaited_once()
        assert agent._ingest_url.await_args.args[1] == "https://example.com/a"

    async def test_plain_text_routes_to_query(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._handle_query = AsyncMock()
        await agent.handle(_user_event(text="what do I know about pricing"))
        agent._handle_query.assert_awaited_once()


# ── File ingestion ────────────────────────────────────────────────────────

class TestFileIngestion:
    async def test_missing_file(self, tmp_path):
        agent = _make_agent(tmp_path)
        resp = await agent.handle(_user_event(data={"file_path": str(tmp_path / "no.pdf")}))
        assert "File not found" in resp.text

    async def test_unsupported_suffix(self, tmp_path):
        agent = _make_agent(tmp_path)
        f = tmp_path / "data.xyz"
        f.write_text("x")
        resp = await agent.handle(_user_event(data={"file_path": str(f)}))
        assert "can't read" in resp.text

    async def test_empty_extraction_warns(self, tmp_path):
        agent = _make_agent(tmp_path)
        f = tmp_path / "empty.txt"
        f.write_text("   \n")
        resp = await agent.handle(_user_event(data={"file_path": str(f)}))
        assert "extracted no text" in resp.text

    async def test_audio_transcribed_locally(self, tmp_path, monkeypatch):
        agent = _make_agent(tmp_path)

        def fake_transcribe(path):
            return "spoken content about pricing plans"

        monkeypatch.setattr("core.transcription.transcribe", fake_transcribe)
        f = tmp_path / "note.ogg"
        f.write_bytes(b"OggS")
        resp = await agent.handle(_user_event(data={"file_path": str(f), "kind": "voice"}))
        assert "Saved to knowledge base" in resp.text
        assert "spoken content about pricing plans" in _prompt_content(agent)

    async def test_transcription_failure_surfaces(self, tmp_path, monkeypatch):
        from core.transcription import TranscriptionError

        agent = _make_agent(tmp_path)

        def failing_transcribe(path):
            raise TranscriptionError("model load failed")

        monkeypatch.setattr("core.transcription.transcribe", failing_transcribe)
        f = tmp_path / "note.ogg"
        f.write_bytes(b"OggS")
        resp = await agent.handle(_user_event(data={"file_path": str(f), "kind": "voice"}))
        assert "Couldn't process" in resp.text
        assert "model load failed" in resp.text

    async def test_text_file_distilled(self, tmp_path):
        agent = _make_agent(tmp_path)
        f = tmp_path / "article.md"
        f.write_text("Some interesting content about pricing.")
        resp = await agent.handle(_user_event(data={"file_path": str(f)}))
        assert "Saved to knowledge base" in resp.text
        assert "Test Note" in resp.text


# ── URL ingestion ─────────────────────────────────────────────────────────

class TestUrlIngestion:
    async def test_url_distilled_with_user_note(self, tmp_path, monkeypatch):
        agent = _make_agent(tmp_path)

        class FakeWebTool:
            def __init__(self, **kwargs):
                pass

            async def scrape(self, url):
                return "Article body about pricing strategy."

        monkeypatch.setattr("core.web_tool.WebTool", FakeWebTool)
        resp = await agent.handle(
            _user_event(text="check https://example.com/article")
        )
        assert "Saved to knowledge base" in resp.text
        # The URL is stripped from the user note that reaches the prompt
        # (it still appears once, legitimately, as the Source line)
        hint = _prompt_content(agent).split("Active projects")[0]
        assert 'said about this resource: "check"' in hint
        assert "https://example.com/article" not in hint.split("Source")[0]

    async def test_url_scrape_empty_warns(self, tmp_path, monkeypatch):
        agent = _make_agent(tmp_path)

        class FakeWebTool:
            def __init__(self, **kwargs):
                pass

            async def scrape(self, url):
                return ""

        monkeypatch.setattr("core.web_tool.WebTool", FakeWebTool)
        resp = await agent.handle(_user_event(text="https://example.com/empty"))
        assert "Couldn't fetch readable text" in resp.text


# ── Distillation + persistence ────────────────────────────────────────────

class TestDistillAndSave:
    async def test_note_file_index_and_state_written(self, tmp_path):
        agent = _make_agent(tmp_path)
        f = tmp_path / "res.txt"
        f.write_text("content")
        await agent.handle(_user_event(data={"file_path": str(f)}))

        notes = [p for p in agent._knowledge_dir.glob("*.md") if p.name != "INDEX.md"]
        assert len(notes) == 1
        assert notes[0].read_text(encoding="utf-8").startswith("# Test Note")
        assert notes[0].stem.endswith("test-note")

        index = (agent._knowledge_dir / "INDEX.md").read_text(encoding="utf-8")
        assert f"- {notes[0].stem}: Test Note" in index

        state = agent._load_state()
        assert state["notes"][notes[0].stem]["source"] == "res.txt"
        assert state["notes"][notes[0].stem]["surfaced_count"] == 0

        agent.memory.save_solution.assert_awaited_once()

    async def test_oversized_content_truncated(self, tmp_path):
        agent = _make_agent(tmp_path)
        f = tmp_path / "big.txt"
        f.write_text("x" * (_MAX_INGEST_CHARS + 5000))
        await agent.handle(_user_event(data={"file_path": str(f)}))
        prompt = _prompt_content(agent)
        assert "[... truncated ...]" in prompt
        assert len(prompt) < _MAX_INGEST_CHARS + 2000

    async def test_no_graphify_task_when_unavailable(self, tmp_path):
        agent = _make_agent(tmp_path)
        f = tmp_path / "res.txt"
        f.write_text("content")
        await agent.handle(_user_event(data={"file_path": str(f)}))
        agent.tools.graphify.update.assert_not_called()


# ── Knowledge queries ─────────────────────────────────────────────────────

class TestQueries:
    async def test_empty_knowledge_base(self, tmp_path):
        agent = _make_agent(tmp_path)
        resp = await agent.handle(_user_event(text="what do I know about pricing"))
        assert "knowledge base is empty" in resp.text

    async def test_matching_note_reaches_prompt(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._knowledge_dir.mkdir(parents=True)
        note = agent._knowledge_dir / "2026-01-01-pricing-notes.md"
        note.write_text("# Pricing Notes\n\nPricing is value-based.\n")
        resp = await agent.handle(_user_event(text="what do I know about pricing"))
        assert resp.success
        prompt = _prompt_content(agent)
        assert "Pricing is value-based." in prompt
        assert "Matching notes:" in prompt

    async def test_graph_answer_added_to_prompt(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._tools = _stub_tools(query_answer="Graph says: pricing -> value")
        agent._knowledge_dir.mkdir(parents=True)
        (agent._knowledge_dir / "INDEX.md").write_text("# Knowledge index\n\n- x: X\n")
        resp = await agent.handle(_user_event(text="pricing"))
        assert resp.success
        assert "<graph>" in _prompt_content(agent)


# ── Weekly digest ─────────────────────────────────────────────────────────

def _digest_event():
    return AgentEvent(
        type=EventType.SCHEDULED_TASK,
        agent_name="librarian",
        chat_id="123",
        data={"task": "librarian_weekly_digest"},
    )


class TestWeeklyDigest:
    async def test_no_notes_is_silent(self, tmp_path):
        agent = _make_agent(tmp_path)
        resp = await agent.handle(_digest_event())
        assert resp.text == ""
        agent.notifier.send.assert_not_awaited()

    async def test_least_surfaced_first_and_counter_incremented(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._knowledge_dir.mkdir(parents=True)
        (agent._knowledge_dir / "2026-01-01-alpha.md").write_text(
            "# Alpha Note\n\n**Next actions**\n- [ ] call bank\n"
        )
        (agent._knowledge_dir / "2026-02-02-beta.md").write_text("# Beta Note\n")
        agent._save_state(
            {
                "notes": {
                    "2026-01-01-alpha": {
                        "created": "2026-01-01T00:00:00+00:00",
                        "source": "a",
                        "surfaced_count": 1,
                        "last_surfaced": None,
                    },
                    "2026-02-02-beta": {
                        "created": "2026-02-02T00:00:00+00:00",
                        "source": "b",
                        "surfaced_count": 0,
                        "last_surfaced": None,
                    },
                }
            }
        )
        resp = await agent.handle(_digest_event())
        assert "Beta Note" in resp.text and "Alpha Note" in resp.text
        # Beta (0 surfaces) ranks before Alpha (1 surface)
        assert resp.text.index("Beta Note") < resp.text.index("Alpha Note")
        assert "- [ ] call bank" in resp.text

        state = agent._load_state()
        assert state["notes"]["2026-01-01-alpha"]["surfaced_count"] == 2
        assert state["notes"]["2026-02-02-beta"]["surfaced_count"] == 1
        agent.notifier.send.assert_awaited()

    async def test_missing_note_file_skipped(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._knowledge_dir.mkdir(parents=True)
        (agent._knowledge_dir / "2026-01-01-real.md").write_text("# Real Note\n")
        agent._save_state(
            {
                "notes": {
                    "2026-01-01-real": {
                        "created": "2026-01-01T00:00:00+00:00",
                        "source": "r",
                        "surfaced_count": 0,
                        "last_surfaced": None,
                    },
                    "2026-03-03-ghost": {
                        "created": "2026-03-03T00:00:00+00:00",
                        "source": "g",
                        "surfaced_count": 0,
                        "last_surfaced": None,
                    },
                }
            }
        )
        resp = await agent.handle(_digest_event())
        assert "Real Note" in resp.text
        assert "Ghost" not in resp.text


# ── Lifecycle ─────────────────────────────────────────────────────────────

class TestLifecycle:
    async def test_heartbeat_ok(self, tmp_path):
        agent = _make_agent(tmp_path)
        resp = await agent.handle(
            AgentEvent(type=EventType.HEARTBEAT_TICK, agent_name="librarian", chat_id="123")
        )
        assert resp.text == "HEARTBEAT_OK"

    async def test_unknown_scheduled_task_silent(self, tmp_path):
        agent = _make_agent(tmp_path)
        resp = await agent.handle(
            AgentEvent(
                type=EventType.SCHEDULED_TASK,
                agent_name="librarian",
                chat_id="123",
                data={"task": "nope"},
            )
        )
        assert resp.text == ""

    async def test_health_check_creates_knowledge_dir(self, tmp_path):
        agent = _make_agent(tmp_path)
        assert await agent.health_check() is True
        assert agent._knowledge_dir.exists()


# ── Authorization ─────────────────────────────────────────────────────────

class TestAuth:
    async def test_unauthorized_chat_rejected(self, tmp_path):
        agent = _make_agent(tmp_path, chat_ids=["999"])
        resp = await agent.handle(_user_event(text="hello", chat_id="123"))
        assert resp.success is False