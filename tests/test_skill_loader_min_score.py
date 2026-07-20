"""
test_skill_loader_min_score.py
----------------------------------
Tests for SkillLoader's configurable min_score — the relevance-filtering
threshold was previously a hardcoded module constant (_MIN_SCORE = 0.05)
with no way to override it per instance.

Run:
    python -m pytest tests/test_skill_loader_min_score.py -x -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.skill_loader import SkillLoader, _MIN_SCORE


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    # Task tokens will be {"schedule", "meeting", "tomorrow", "morning"} (4).
    # This skill shares only "meeting" — overlap 1/4 = 0.25.
    (d / "low-overlap.md").write_text("This skill is about meeting notes and follow-ups.")
    return d


class TestDefaultMinScore:
    def test_default_matches_module_constant(self):
        loader = SkillLoader()
        assert loader._min_score == _MIN_SCORE

    @pytest.mark.asyncio
    async def test_includes_skill_above_default_threshold(self, skills_dir):
        loader = SkillLoader()
        results = await loader.find_relevant(
            "schedule a meeting tomorrow morning", skills_dir
        )
        assert len(results) == 1


class TestOverriddenMinScore:
    def test_instance_uses_override_value(self):
        loader = SkillLoader(min_score=0.5)
        assert loader._min_score == 0.5

    @pytest.mark.asyncio
    async def test_excludes_skill_below_overridden_threshold(self, skills_dir):
        loader = SkillLoader(min_score=0.5)
        results = await loader.find_relevant(
            "schedule a meeting tomorrow morning", skills_dir
        )
        assert results == []
