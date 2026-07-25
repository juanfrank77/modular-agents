"""
core/skill_loader.py
--------------------
Reads .md skill files from an agent's skills directory.
Scores skills by keyword overlap with the incoming task.

Usage:
    from core.skill_loader import SkillLoader
    loader = SkillLoader()
    skills = await loader.find_relevant("schedule a meeting", Path("agents/business/skills"))

NOTE: Skill content is wrapped in <skill> XML delimiters to prevent prompt injection.
The LLM is instructed to treat content inside these tags as DATA, not instructions.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from core.logger import get_logger

log = get_logger("skills")

_MIN_SCORE = 0.05

_SKILL_XML_TEMPLATE = """<skill>
{content}
</skill>"""


class SkillLoader:
    def __init__(self, min_score: float = _MIN_SCORE) -> None:
        self._min_score = min_score

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Simple word tokenization — lowercase, alpha-only, 2+ chars."""
        return {w for w in re.findall(r"[a-z]{2,}", text.lower())}

    def _load_skill_file(self, md_file: Path) -> str | None:
        """Load a single skill file, returning content or None if empty."""
        try:
            content = md_file.read_text(encoding="utf-8").strip()
            return content if content else None
        except Exception:
            return None

    async def find_relevant(
        self, task: str, skills_dir: Path | str, max_skills: int = 3
    ) -> list[str]:
        """Return top-N skill contents ranked by word overlap with the task."""
        skills_dir = Path(skills_dir)
        if not skills_dir.exists():
            return []

        task_tokens = self._tokenize(task)
        if not task_tokens:
            return await self.load_all(skills_dir)

        # Run blocking file I/O in a thread
        def _scan_and_score():
            scored: list[tuple[float, str]] = []
            for md_file in skills_dir.glob("*.md"):
                content = self._load_skill_file(md_file)
                if content is None:
                    continue
                skill_tokens = self._tokenize(content)
                if not skill_tokens:
                    continue
                overlap = len(task_tokens & skill_tokens)
                score = overlap / len(task_tokens)
                if score >= self._min_score:
                    scored.append((score, content))
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored

        scored = await asyncio.to_thread(_scan_and_score)
        top_matches = scored[:max_skills]
        results = [_SKILL_XML_TEMPLATE.format(content=content) for _, content in top_matches]

        log.info(
            "Skills loaded",
            event="skills_loaded",
            count=len(results),
            total_available=len(scored),
        )
        return results

    async def load_all(self, skills_dir: Path | str) -> list[str]:
        """Load all skill files (for agents with few skills)."""
        skills_dir = Path(skills_dir)
        if not skills_dir.exists():
            return []

        def _read_all():
            results = []
            for md_file in sorted(skills_dir.glob("*.md")):
                content = self._load_skill_file(md_file)
                if content:
                    results.append(_SKILL_XML_TEMPLATE.format(content=content))
            return results

        return await asyncio.to_thread(_read_all)
