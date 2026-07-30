# test_memory_solutions.py
"""Tests for Memory._get_relevant_solutions — filename-based matching with
stemming/stopwords and mtime-keyed content caching."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.memory import Memory
from core.storage import Storage


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.memory_context_dir = tmp_path / "context"
    settings.memory_context_dir.mkdir()
    settings.memory_solutions_dir = tmp_path / "solutions"
    settings.memory_solutions_dir.mkdir()
    settings.message_retention_days = 0
    return settings


@pytest.fixture
async def storage(tmp_path: Path):
    s = Storage(tmp_path / "test.db")
    await s.init()
    try:
        yield s
    finally:
        await s.close()


@pytest.mark.asyncio
class TestGetRelevantSolutions:
    async def test_matches_solution_by_filename(self, tmp_path):
        memory = Memory(storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path))
        (tmp_path / "solutions" / "meeting_notes.md").write_text("# Notes\nTake notes.")

        result = await memory._get_relevant_solutions("meeting tomorrow")

        assert "<solution>" in result
        assert "meeting_notes" in result
        assert "Take notes." in result

    async def test_stemming_folds_plural_into_filename(self, tmp_path):
        memory = Memory(storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path))
        (tmp_path / "solutions" / "meeting_notes.md").write_text("# Notes\nTake notes.")

        result = await memory._get_relevant_solutions("meetings tomorrow")

        assert "meeting_notes" in result

    async def test_stopwords_do_not_match(self, tmp_path):
        memory = Memory(storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path))
        (tmp_path / "solutions" / "the.md").write_text("ignored")

        result = await memory._get_relevant_solutions("the meeting")

        assert "the.md" not in result
        assert result == ""

    async def test_no_match_returns_empty(self, tmp_path):
        memory = Memory(storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path))
        (tmp_path / "solutions" / "meeting_notes.md").write_text("# Notes\nTake notes.")

        result = await memory._get_relevant_solutions("deploy to railway")

        assert result == ""

    async def test_caches_content_until_mtime_changes(self, tmp_path):
        memory = Memory(storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path))
        solution_file = tmp_path / "solutions" / "meeting_notes.md"
        solution_file.write_text("original")

        result1 = await memory._get_relevant_solutions("meeting")
        assert "original" in result1
        assert len(memory._solution_cache) == 1

        # Overwrite the file: the cached entry must be invalidated by the new mtime.
        solution_file.write_text("updated")
        result2 = await memory._get_relevant_solutions("meeting")
        assert "updated" in result2
        assert "original" not in result2

    async def test_limits_to_three_solutions(self, tmp_path):
        memory = Memory(storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path))
        for i in range(5):
            (tmp_path / "solutions" / f"meeting_{i}.md").write_text(f"content {i}")

        result = await memory._get_relevant_solutions("meeting")
        xml_blocks = result.count("<solution>")

        assert xml_blocks == 3
