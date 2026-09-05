# test_memory_handoffs.py
"""Tests for Memory.save_handoff and Memory.get_recent_handoffs — structured
results from delegated tasks persisted to disk and reloaded on demand."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.memory import Memory, _HAS_YAML


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.memory_context_dir = tmp_path / "context"
    settings.memory_solutions_dir = tmp_path / "solutions"
    settings.message_retention_days = 0
    return settings


@pytest.mark.asyncio
class TestSaveHandoff:
    async def test_saves_yaml_file(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        handoff = {"topic": "research", "task": "do research", "result": "answer"}
        await memory.save_handoff("business", handoff)

        handoffs_dir = tmp_path / "solutions" / "business" / "handoffs"
        files = list(handoffs_dir.glob("*.yaml"))
        assert len(files) == 1
        content = files[0].read_text(encoding="utf-8")
        # We re-parse with yaml if available, else json-fallback has been
        # used; in either case the file must round-trip the input data.
        if _HAS_YAML:
            import yaml
            parsed = yaml.safe_load(content)
        else:
            parsed = json.loads(content)
        assert parsed["topic"] == "research"
        assert parsed["task"] == "do research"
        assert parsed["result"] == "answer"

    async def test_updates_index(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        await memory.save_handoff(
            "business", {"topic": "billing", "task": "fix invoices"}
        )

        index_path = tmp_path / "solutions" / "business" / "handoffs" / "INDEX.md"
        assert index_path.exists()
        text = index_path.read_text(encoding="utf-8")
        assert "billing" in text or "fix invoices" in text
        assert "_Last updated:" in text

    async def test_index_not_treated_as_solution(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        await memory.save_handoff(
            "business", {"topic": "billing", "task": "fix invoices"}
        )

        index_path = tmp_path / "solutions" / "business" / "handoffs" / "INDEX.md"
        assert index_path.exists()

        # INDEX.md must never load as a solution (relevance injection guard).
        assert memory._load_solution_file(index_path) is None

        # And rebuild_index must not index it as a solution entry.
        (tmp_path / "context").mkdir(parents=True, exist_ok=True)
        await memory.rebuild_index()
        rebuilt = (tmp_path / "context" / "MEMORY.md").read_text(encoding="utf-8")
        assert "solutions/business/handoffs/INDEX" not in rebuilt

    async def test_fallback_to_json_when_yaml_unavailable(self, tmp_path, monkeypatch):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        # Force the module's _HAS_YAML to False so save_handoff uses JSON.
        monkeypatch.setattr("core.memory._HAS_YAML", False)

        await memory.save_handoff(
            "business", {"topic": "ops", "task": "deploy", "result": "ok"}
        )

        handoffs_dir = tmp_path / "solutions" / "business" / "handoffs"
        json_files = list(handoffs_dir.glob("*.yaml"))
        assert len(json_files) == 1
        # Filename still ends with .yaml per spec, but content is JSON.
        raw = json_files[0].read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["topic"] == "ops"
        assert parsed["result"] == "ok"

    async def test_creates_handoffs_directory(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        handoffs_dir = tmp_path / "solutions" / "business" / "handoffs"
        assert not handoffs_dir.exists()

        await memory.save_handoff("business", {"topic": "x", "task": "y"})

        assert handoffs_dir.exists()
        assert handoffs_dir.is_dir()


@pytest.mark.asyncio
class TestGetRecentHandoffs:
    async def test_reads_recent_files(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        await memory.save_handoff(
            "business", {"topic": "first", "task": "first task"}
        )
        await memory.save_handoff(
            "business", {"topic": "second", "task": "second task"}
        )
        await memory.save_handoff(
            "business", {"topic": "third", "task": "third task"}
        )

        handoffs = await memory.get_recent_handoffs("business", limit=3)

        assert len(handoffs) == 3
        topics = [h["topic"] for h in handoffs]
        # Sorted by filename desc → most recent first.
        assert topics[0] == "third"
        assert topics[-1] == "first"

    async def test_limit_enforcement(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        for i in range(7):
            await memory.save_handoff(
                "business", {"topic": f"t{i}", "task": f"task {i}"}
            )

        handoffs = await memory.get_recent_handoffs("business", limit=3)

        assert len(handoffs) == 3

    async def test_skips_unparseable_files(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        # Save a valid one and a clearly invalid YAML/JSON file.
        await memory.save_handoff("business", {"topic": "ok", "task": "valid"})

        handoffs_dir = tmp_path / "solutions" / "business" / "handoffs"
        # An older timestamp guarantees the bad file sorts first (descending)
        # and will be skipped instead of returned.
        (handoffs_dir / "20200101T000000_000000_bogus.yaml").write_text(
            "{not valid yaml: [unclosed",
            encoding="utf-8",
        )

        handoffs = await memory.get_recent_handoffs("business", limit=5)

        # Only the valid one should be returned; the bogus file is skipped.
        assert len(handoffs) == 1
        assert handoffs[0]["topic"] == "ok"

    async def test_empty_directory(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        handoffs = await memory.get_recent_handoffs("business", limit=5)

        assert handoffs == []

    async def test_nonexistent_directory(self, tmp_path):
        memory = Memory(
            storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path)
        )

        # No save_handoff called → directory doesn't exist.
        handoffs = await memory.get_recent_handoffs("ghost", limit=5)

        assert handoffs == []
