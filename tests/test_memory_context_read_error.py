"""
test_memory_context_read_error.py
----------------------------------
Tests for the F2 fix (docs/specs/known-failure-modes.md): a context file that
EXISTS but is unreadable must be reported as a read error, not treated as an
empty context. An empty (nonexistent) context is a legitimate "nothing here"
state; a read failure is a system fault the agent must surface.

Run:
    python -m pytest tests/test_memory_context_read_error.py -x -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agents.projects.agent import ProjectsAgent
from core.memory import Memory


@pytest.fixture
def memory(tmp_path: Path) -> Memory:
    # Memory's constructor needs a Storage, LLMProvider, and Settings — but
    # get_context only reads from disk, so we pass mocks/placeholders here.
    return Memory(
        storage=None,  # type: ignore[arg-type]
        llm=None,  # type: ignore[arg-type]
        settings=type("S", (), {"memory_context_dir": tmp_path / "context"}),
    )


class TestGetContextReadError:
    """F2: an existing-but-unreadable file must surface an error marker."""

    async def test_missing_file_returns_empty(self, memory):
        # Nonexistent file is a legitimate empty state — no error marker.
        result = await memory.get_context("projects")
        assert result == ""

    async def test_unreadable_file_returns_error_marker(self, memory):
        path = memory.settings.memory_context_dir / "projects.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Projects\n- a: 1d ago", encoding="utf-8")
        os.chmod(path, 0)
        try:
            result = await memory.get_context("projects")
        finally:
            os.chmod(path, 0o644)  # allow cleanup
        assert result.startswith("[CONTEXT READ ERROR:")
        assert "projects.md" in result


class TestMomentumSummaryReadError:
    """F2: _momentum_summary must not report "(no projects tracked)" on error."""

    def test_error_marker_is_surfaced(self):
        projects_md = "[CONTEXT READ ERROR: projects.md could not be read: Permission denied]"
        result = ProjectsAgent._momentum_summary(None, projects_md)
        assert "Could not read" in result
        assert "Permission denied" in result
        assert "no projects tracked" not in result

    def test_normal_content_still_parsed(self):
        projects_md = "# Projects\n- work: updated 1d ago"
        result = ProjectsAgent._momentum_summary(None, projects_md)
        assert "no projects tracked" not in result

    def test_truly_empty_is_not_mistaken_for_error(self):
        # An empty-but-valid file is a legitimate "nothing tracked" state.
        assert ProjectsAgent._momentum_summary(None, "") == "(no projects tracked)"
