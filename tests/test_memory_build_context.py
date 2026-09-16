# test_memory_build_context.py
"""Tests for Memory.build_context's empty-task behavior — it must not fall
back to loading every context file (the old, unbounded "load everything"
path); it should behave exactly like get_relevant_context("")."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.llm import SummaryFailedError
from core.memory import Memory
from core.protocols import ToolDef
from core.storage import Storage


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.memory_context_dir = tmp_path / "context"
    settings.memory_solutions_dir = tmp_path / "solutions"
    settings.message_retention_days = 0
    return settings


@pytest.fixture
async def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.mark.asyncio
class TestBuildContextEmptyTask:
    async def test_empty_task_loads_always_load_file(self, tmp_path):
        context_dir = tmp_path / "context"
        context_dir.mkdir()
        (context_dir / "preferences.md").write_text(
            "<!-- topic-always-load -->\nTimezone: UTC"
        )

        storage = Storage(tmp_path / "test.db")
        await storage.init()
        session_id = await storage.get_or_create_session("chat_1", "business")

        memory = Memory(storage=storage, llm=MagicMock(), settings=_make_settings(tmp_path))
        markdown_context, _ = await memory.build_context(session_id, "business", task="")

        assert "Timezone: UTC" in markdown_context

    async def test_empty_task_does_not_load_keyword_only_file(self, tmp_path):
        context_dir = tmp_path / "context"
        context_dir.mkdir()
        (context_dir / "projects.md").write_text(
            "<!-- topic-keywords: deploy, railway -->\nProject data here."
        )

        storage = Storage(tmp_path / "test.db")
        await storage.init()
        session_id = await storage.get_or_create_session("chat_1", "business")

        memory = Memory(storage=storage, llm=MagicMock(), settings=_make_settings(tmp_path))
        markdown_context, _ = await memory.build_context(session_id, "business", task="")

        assert "Project data here." not in markdown_context

    async def test_compaction_failure_returns_full_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.memory._COMPACTION_THRESHOLD", 10)

        storage = Storage(tmp_path / "test.db")
        await storage.init()
        session_id = await storage.get_or_create_session("chat_1", "business")

        for i in range(25):
            await storage.save_message(session_id, "user", f"message {i} " * 10, "business")

        llm = MagicMock()
        llm.summarize = AsyncMock(side_effect=SummaryFailedError("model down"))
        memory = Memory(storage=storage, llm=llm, settings=_make_settings(tmp_path))

        history = await memory.get_session_context(session_id, "business")

        assert len(history) == 25
        llm.summarize.assert_awaited_once()


@pytest.mark.asyncio
class TestToolAwareSummarization:
    async def test_build_context_passes_tools_to_summarize(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.memory._COMPACTION_THRESHOLD", 10)

        storage = Storage(tmp_path / "test.db")
        await storage.init()
        session_id = await storage.get_or_create_session("chat_1", "devops")

        for i in range(25):
            await storage.save_message(session_id, "user", f"message {i} " * 10, "devops")

        tools = [
            ToolDef(
                name="LIST_ISSUES",
                description="List GitHub issues.",
                parameters={},
            )
        ]

        llm = MagicMock()
        llm.summarize = AsyncMock(return_value="a summary")
        memory = Memory(storage=storage, llm=llm, settings=_make_settings(tmp_path))

        await memory.build_context(session_id, "devops", task="check issues", tools=tools)

        llm.summarize.assert_awaited_once()
        _, kwargs = llm.summarize.call_args
        assert kwargs["tools"] == tools
