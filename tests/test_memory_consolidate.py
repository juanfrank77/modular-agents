# test_memory_consolidate.py
"""Tests for Memory._should_consolidate's session-count gate (bug #32).

Under FTS5's default unicode61 tokenizer, "_" is a separator and produces no
searchable token, so the old `search_history("_", ...)` placeholder always
returned zero results and consolidation could never fire. _should_consolidate
must use Storage.count_sessions() instead.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.memory import Memory, _CONSOLIDATION_MIN_SESSIONS
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
class TestShouldConsolidate:
    async def test_fires_once_enough_sessions_exist(self, tmp_path, storage):
        memory = Memory(storage=storage, llm=MagicMock(), settings=_make_settings(tmp_path))

        for i in range(_CONSOLIDATION_MIN_SESSIONS):
            session_id = await storage.get_or_create_session(f"chat_{i}", "business")
            await storage.save_message(session_id, "user", f"message {i}", "business")

        assert await memory._should_consolidate("business") is True

    async def test_does_not_fire_below_session_threshold(self, tmp_path, storage):
        memory = Memory(storage=storage, llm=MagicMock(), settings=_make_settings(tmp_path))

        for i in range(_CONSOLIDATION_MIN_SESSIONS - 1):
            session_id = await storage.get_or_create_session(f"chat_{i}", "business")
            await storage.save_message(session_id, "user", f"message {i}", "business")

        assert await memory._should_consolidate("business") is False

    async def test_no_sessions_does_not_fire(self, tmp_path, storage):
        memory = Memory(storage=storage, llm=MagicMock(), settings=_make_settings(tmp_path))

        assert await memory._should_consolidate("business") is False
