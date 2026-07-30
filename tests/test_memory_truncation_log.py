# test_memory_truncation_log.py
"""Tests for save_solution's truncation log field (bug #42).

When content exceeds _MAX_SOLUTION_CHARS, save_solution truncates it and
reassigns `content` to the truncated string *before* logging
`original_chars=len(content)` — so the log reports the post-truncation
length under a field named "original_chars". The log must report the
length of the content as it was *before* truncation.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.memory import Memory, _MAX_SOLUTION_CHARS


def _make_settings(tmp_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.memory_context_dir = tmp_path / "context"
    settings.memory_context_dir.mkdir()
    settings.memory_solutions_dir = tmp_path / "solutions"
    settings.memory_solutions_dir.mkdir()
    settings.message_retention_days = 0
    return settings


@pytest.mark.asyncio
class TestSaveSolutionTruncationLog:
    async def test_logs_pre_truncation_length(self, tmp_path, monkeypatch):
        memory = Memory(storage=MagicMock(), llm=MagicMock(), settings=_make_settings(tmp_path))

        original_content = "x" * (_MAX_SOLUTION_CHARS + 500)
        assert len(original_content) > _MAX_SOLUTION_CHARS

        captured = {}

        def fake_info(msg, **kwargs):
            if kwargs.get("event") == "solution_truncated":
                captured.update(kwargs)

        monkeypatch.setattr("core.memory.log.info", fake_info)

        await memory.save_solution("business", "big_topic", original_content)

        assert captured.get("original_chars") == len(original_content)
        assert captured["original_chars"] > _MAX_SOLUTION_CHARS
