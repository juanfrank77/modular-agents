# test_memory_topic_config.py
"""Tests for per-file topic config (topic-always-load / topic-keywords HTML
comments) replacing the hardcoded _TOPIC_KEYWORDS dict in core/memory.py."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.memory import Memory, _parse_topic_meta


def _make_memory(tmp_path: Path) -> Memory:
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    settings = MagicMock()
    settings.memory_context_dir = context_dir
    settings.memory_solutions_dir = tmp_path / "solutions"
    return Memory(storage=MagicMock(), llm=MagicMock(), settings=settings)


class TestParseTopicMeta:
    def test_always_load_flag(self):
        always_load, keywords = _parse_topic_meta("<!-- topic-always-load -->\ncontent")
        assert always_load is True
        assert keywords == []

    def test_keywords_parsed(self):
        _, keywords = _parse_topic_meta("<!-- topic-keywords: repo, deploy, github -->")
        assert keywords == ["repo", "deploy", "github"]

    def test_no_declaration(self):
        always_load, keywords = _parse_topic_meta("# Just a file\nNo declarations here.")
        assert always_load is False
        assert keywords == []


@pytest.mark.asyncio
class TestGetRelevantContext:
    async def test_always_load_file_included_regardless_of_task(self, tmp_path):
        memory = _make_memory(tmp_path)
        (tmp_path / "context" / "preferences.md").write_text(
            "<!-- topic-always-load -->\nTimezone: UTC"
        )

        result = await memory.get_relevant_context("anything at all")
        assert "Preferences" in result
        assert "Timezone: UTC" in result

    async def test_keyword_file_loaded_on_match(self, tmp_path):
        memory = _make_memory(tmp_path)
        (tmp_path / "context" / "projects.md").write_text(
            "<!-- topic-keywords: deploy, railway -->\nProject data here."
        )

        result = await memory.get_relevant_context("please deploy the app")
        assert "Projects" in result
        assert "Project data here." in result

    async def test_keyword_file_skipped_without_match(self, tmp_path):
        memory = _make_memory(tmp_path)
        (tmp_path / "context" / "projects.md").write_text(
            "<!-- topic-keywords: deploy, railway -->\nProject data here."
        )

        result = await memory.get_relevant_context("what's the weather")
        assert "Project data here." not in result

    async def test_undeclared_file_never_auto_loaded(self, tmp_path):
        memory = _make_memory(tmp_path)
        (tmp_path / "context" / "reader_profile.md").write_text(
            "No declaration here.\nCurrent focus: shipping NINA."
        )

        result = await memory.get_relevant_context("shipping NINA update")
        assert "shipping NINA" not in result

    async def test_new_topic_file_needs_no_core_change(self, tmp_path):
        """A brand-new context file becomes loadable purely by adding the
        HTML-comment declaration inside it — no _TOPIC_KEYWORDS edit."""
        memory = _make_memory(tmp_path)
        (tmp_path / "context" / "team.md").write_text(
            "<!-- topic-keywords: teammate, standup -->\nTeam roster here."
        )

        result = await memory.get_relevant_context("who's on standup today")
        assert "Team roster here." in result
