"""
core/skill_loader.py
--------------------
Reads .md skill files from an agent's skills directory.
Scores skills by keyword overlap with the incoming task.

Usage:
    from core.skill_loader import SkillLoader
    loader = SkillLoader()
    skills = loader.find_relevant("schedule a meeting", Path("agents/business/skills"))
"""

from __future__ import annotations

import re
from pathlib import Path

from core.logger import get_logger

log = get_logger("skills")

_MIN_SCORE = 0.05


class SkillLoader:
    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Simple word tokenization — lowercase, alpha-only, 2+ chars."""
        return {w for w in re.findall(r"[a-z]{2,}", text.lower())}

    def find_relevant(
        self, task: str, skills_dir: Path, max_skills: int = 3
    ) -> list[str]:
        """Return top-N skill contents ranked by word overlap with the task."""
        if not skills_dir.exists():
            return []

        task_tokens = self._tokenize(task)
        if not task_tokens:
            return self.load_all(skills_dir)

        scored: list[tuple[float, str]] = []
        for md_file in skills_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            skill_tokens = self._tokenize(content)
            if not skill_tokens:
                continue
            overlap = len(task_tokens & skill_tokens)
            score = overlap / len(task_tokens)
            if score >= _MIN_SCORE:
                scored.append((score, content))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = [content for _, content in scored[:max_skills]]
        log.info(
            "Skills loaded",
            event="skills_loaded",
            count=len(results),
            total_available=len(scored),
        )
        return results

    def load_all(self, skills_dir: Path) -> list[str]:
        """Load all skill files (for agents with few skills)."""
        if not skills_dir.exists():
            return []
        results = []
        for md_file in sorted(skills_dir.glob("*.md")):
            content = md_file.read_text(encoding="utf-8").strip()
            if content:
                results.append(content)
        return results
