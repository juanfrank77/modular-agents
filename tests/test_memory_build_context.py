# test_memory_build_context.py
"""Tests for Memory.build_context's empty-task behavior — it must not fall
back to loading every context file (the old, unbounded "load everything"
path); it should behave exactly like get_relevant_context("")."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.memory import Memory
from core.storage import Storage


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.memory_context_dir = tmp_path / "context"
    settings.memory_solutions_dir = tmp_path / "solutions"
    settings.message_retention_days = 0
    return settings


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
        session_id = await storage.create_session("business")

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
        session_id = await storage.create_session("business")

        memory = Memory(storage=storage, llm=MagicMock(), settings=_make_settings(tmp_path))
        markdown_context, _ = await memory.build_context(session_id, "business", task="")

        assert "Project data here." not in markdown_context
