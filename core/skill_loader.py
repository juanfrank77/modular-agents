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
from pathlib import Path

from core.logger import get_logger
from core.text_match import tokenize

log = get_logger("skills")

_MIN_SCORE = 0.05

_SKILL_XML_TEMPLATE = """<skill>
{content}
</skill>"""


class SkillLoader:
    def __init__(self, min_score: float = _MIN_SCORE) -> None:
        self._min_score = min_score
        # path -> (mtime, content, tokens) — avoids re-reading and
        # re-tokenizing unchanged skill files on every message.
        self._cache: dict[Path, tuple[float, str, set[str]]] = {}

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return tokenize(text)

    def _load_skill_file(self, md_file: Path) -> tuple[str, set[str]] | None:
        """Load (and cache) a single skill file's content + tokens, keyed
        by mtime so edits are picked up without a stale cache hit."""
        try:
            mtime = md_file.stat().st_mtime
        except OSError:
            return None

        cached = self._cache.get(md_file)
        if cached is not None and cached[0] == mtime:
            return cached[1], cached[2]

        try:
            content = md_file.read_text(encoding="utf-8").strip()
        except Exception:
            return None
        if not content:
            self._cache.pop(md_file, None)
            return None

        result = (content, tokenize(content))
        self._cache[md_file] = (mtime, *result)
        return result

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
                loaded = self._load_skill_file(md_file)
                if loaded is None:
                    continue
                content, skill_tokens = loaded
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
                loaded = self._load_skill_file(md_file)
                if loaded:
                    results.append(_SKILL_XML_TEMPLATE.format(content=loaded[0]))
            return results

        return await asyncio.to_thread(_read_all)
