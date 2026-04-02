"""
core/memory.py
--------------
Three-layer memory system:
  Layer 1 (SQLite):   Conversation history via Storage
  Layer 2 (Markdown): Context files - index always loaded, topic files on-demand
  Layer 3 (grep):     Session transcripts searched with targeted queries only

Key design principles: 
  - MEMORY.md is an index of pointers, not a content dump. Always injected.
  - Topic files (preferences, personal, projects, solutions) loaded on-demand
    based on relevance to the current task — not all at once.
  - Strict write discipline: save_solution() enforces a size cap and updates
    the index after every write.
  - consolidate() rewrites memory files in the background — merges duplicates,
    removes contradictions, prunes stale content. Never blocks a response.

Usage:
    from core.memory import Memory
    mem = Memory(storage=storage, llm=llm, settings=settings)
    context, history = await mem.build_context(session_id, "business", task="deploy checklist")
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.logger import get_logger
from core.protocols import Message

if TYPE_CHECKING:
    from core.config import Settings
    from core.protocols import LLMProvider
    from core.storage import Storage

log = get_logger("memory")

# Token estimation
_CHARS_PER_TOKEN = 4
_COMPACTION_THRESHOLD = 8000  # tokens before session compaction kicks in
_KEEP_RECENT = 20             # messages to keep intact during compaction

# Solution file  constraints
_MAX_SOLUTION_TOKENS = 500     # ~2000 chars — enforced on every save
_MAX_SOLUTION_CHARS = _MAX_SOLUTION_TOKENS * _CHARS_PER_TOKEN

# Consolidation triggers
_CONSOLIDATION_MIN_SESSIONS = 5   # minimum sessions before consolidation runs
_CONSOLIDATION_MIN_HOURS = 24     # minimum hours between consolidation runs

# Memory filename
_INDEX_FILE = "MEMORY.md"

# Topic files always considered relevant regardless of task
_ALWAYS_LOAD = {"preferences"}

# Keywords that trigger loading each topic file
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "personal":    ["who am i", "background", "about me", "personal", "context"],
    "projects":    ["project", "repo", "deploy", "railway", "github", "startup",
                    "newsletter", "saas", "priority", "task", "status", "deadline"]
}

class Memory:
    def __init__(self, storage: "Storage", llm: "LLMProvider", settings: "Settings") -> None:
        self._storage = storage
        self._llm = llm
        self._context_dir = settings.memory_context_dir
        self._solutions_dir = settings.memory_solutions_dir
        self._consolidation_lock = asyncio.Lock()

    # ── Layer 1: SQLite (delegates to Storage) ──

    async def save_message(
        self, session_id: str, role: str, content: str, agent: str
    ) -> None:
        await self._storage.save_message(session_id, role, content, agent)

    async def search_history(
        self, query: str, agent: str | None = None, limit: int = 10
    ) -> list[Message]:
        return await self._storage.search_history(query, agent, limit)

    # ── Layer 2: Markdown index + topic files ──

    async def get_index(self) -> str:
        """
        Read MEMORY.md — the always-loaded index.
        Returns empty string if it doesn't exist yet (first run).
        """
        path = self._context_dir / _INDEX_FILE
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    async def get_context(self, key: str) -> str:
        """
        Read a single topic file by key.
        e.g. 'preferences' → memory/context/preferences.md
        """
        path = self._context_dir / f"{key}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    async def get_relevant_context(self, task: str) -> str:
        """
        Smart context loader: always loads MEMORY.md index + preferences,
        then loads additional topic files only if the task is relevant to them.

        This replaces the old "load everything" approach in build_context().
        """
        parts: list[str] = []

        # Always load: index
        index = await self.get_index()
        if index.strip():
            parts.append(f"## Memory index\n{index.strip()}")

        # Always load: preferences
        for key in _ALWAYS_LOAD:
            content = await self.get_context(key)
            if content.strip():
                parts.append(f"## {key.title()}\n{content.strip()}")

        # Conditionally load: other topic files
        task_lower = task.lower()
        for topic, keywords in _TOPIC_KEYWORDS.items():
            if any(kw in task_lower for kw in keywords):
                content = await self.get_context(topic)
                if content.strip():
                    parts.append(f"## {topic.title()}\n{content.strip()}")
                    log.info(
                        "Topic file loaded",
                        event="topic_loaded",
                        topic=topic,
                        task_preview=task[:60],
                    )

        # Load relevant solutions for this agent/task
        solutions = await self._get_relevant_solutions(task)
        if solutions:
            parts.append(f"## Relevant solutions\n{solutions}")

        return "\n\n".join(parts)

    async def save_solution(self, agent: str, topic: str, content: str) -> None:
        """
        Save a solution file with size enforcement and index update.
 
        Write discipline:
          1. Truncate content to _MAX_SOLUTION_CHARS if needed
          2. Write to agents/<agent>/<topic>.md
          3. Update MEMORY.md index entry
        """
        agent_dir = self._solutions_dir / agent
        agent_dir.mkdir(parents=True, exist_ok=True)

        # Enforce size cap - truncate at a sentence boundary if possible
        if len(content) > _MAX_SOLUTION_CHARS:
            truncated = content[:_MAX_SOLUTION_CHARS]
            # Try to end at a sentence boundary
            last_period = truncated.rfind(". ")
            if last_period > _MAX_SOLUTION_CHARS * 0.7:
                truncated = truncated[:last_period + 1]
            content = truncated + "\n\n_[truncated to fit memory constraints]_"
            log.info(
                "Solution truncated",
                event="solution_truncated",
                agent=agent,
                topic=topic,
                original_chars=len(content),
            )

        # Write the solution file
        path = agent_dir / f"{topic}.md"
        path.write_text(content, encoding="utf-8")
        log.info("Solution saved", event="solution_saved", agent=agent, topic=topic)

        # Update the index
        await self._update_index_entry(
            key=f"solutions/{agent}/{topic}",
            summary=_extract_summary(content)
        )

    async def _get_relevant_solutions(self, task: str) -> str:
        """
        Load solution files whose filenames or first lines match the task.
        Returns concatenated content of matching solutions, or empty string.
        """
        if not self._solutions_dir.exists():
            return ""

        task_words = set(re.findall(r"\w+", task.lower()))
        matched: list[str] = []

        for solution_file in self._solutions_dir.rglob("*.md"):
            # Match on filename tokens
            file_words = set(re.findall(r"\w+", solution_file.stem.lower()))
            if task_words & file_words:  # any overlap
                content = solution_file.read_text(encoding="utf-8").strip()
                if content:
                    matched.append(
                        f"### {solution_file.stem}\n{content}"
                    )

        return "\n\n".join(matched[:3])  # max 3 solutions per call
    # ── Index management ─────────────────────
 
    async def _update_index_entry(self, key: str, summary: str) -> None:
        """
        Update or insert a single entry in MEMORY.md.
        Format: - key: summary (~150 chars max)
        Never dumps full content into the index.
        """
        index_path = self._context_dir / _INDEX_FILE
        summary_short = summary[:150].replace("\n", " ").strip()
        new_entry = f"- {key}: {summary_short}"
 
        if not index_path.exists():
            index_path.write_text(
                f"# Memory index\n_Last updated: {_now()}_\n\n{new_entry}\n",
                encoding="utf-8",
            )
            return
 
        content = index_path.read_text(encoding="utf-8")
 
        # Update existing entry or append new one
        pattern = re.compile(rf"^- {re.escape(key)}:.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(new_entry, content)
        else:
            content = content.rstrip() + f"\n{new_entry}\n"
 
        # Update timestamp
        content = re.sub(
            r"_Last updated:.*_",
            f"_Last updated: {_now()}_",
            content,
        )
 
        index_path.write_text(content, encoding="utf-8")
        log.info("Index updated", event="index_update", key=key)
 
    async def rebuild_index(self) -> None:
        """
        Rebuild MEMORY.md from scratch by scanning all context and solution files.
        Called at the end of consolidate() to ensure the index stays accurate.
        """
        entries: list[str] = []
 
        # Index context files
        if self._context_dir.exists():
            for md_file in sorted(self._context_dir.glob("*.md")):
                if md_file.name == _INDEX_FILE:
                    continue
                content = md_file.read_text(encoding="utf-8").strip()
                if content:
                    summary = _extract_summary(content)
                    entries.append(f"- context/{md_file.stem}: {summary[:150]}")
 
        # Index solution files
        if self._solutions_dir.exists():
            for sol_file in sorted(self._solutions_dir.rglob("*.md")):
                content = sol_file.read_text(encoding="utf-8").strip()
                if content:
                    # Build relative key: solutions/agent/topic
                    try:
                        rel = sol_file.relative_to(self._solutions_dir)
                        key = f"solutions/{rel.with_suffix('')}"
                    except ValueError:
                        key = f"solutions/{sol_file.stem}"
                    summary = _extract_summary(content)
                    entries.append(f"- {key}: {summary[:150]}")
 
        index_content = (
            f"# Memory index\n"
            f"_Last updated: {_now()}_\n"
            f"_This file is always loaded. Topic files are loaded on-demand._\n\n"
            + "\n".join(entries)
            + "\n"
        )
 
        index_path = self._context_dir / _INDEX_FILE
        index_path.write_text(index_content, encoding="utf-8")
        log.info(
            "Index rebuilt",
            event="index_rebuilt",
            entries=len(entries),
        )
 
    # ── Consolidation ─────────────────────────
 
    async def consolidate(self, agent: str, force: bool = False) -> bool:
        """
        Background memory rewrite — fires after sufficient sessions have
        accumulated. Merges duplicates, removes contradictions, prunes stale
        content, converts vague entries to specific ones.
 
        Runs in the background (fire-and-forget from agents). Never blocks
        a response. Uses a lock to prevent concurrent runs.
 
        Returns True if consolidation ran, False if skipped.
        """
        if self._consolidation_lock.locked():
            log.info("Consolidation already running", event="consolidate_skip")
            return False
 
        if not force:
            should_run = await self._should_consolidate(agent)
            if not should_run:
                return False
 
        async with self._consolidation_lock:
            log.info("Consolidation starting", event="consolidate_start", agent=agent)
            try:
                await self._run_consolidation(agent)
                await self.rebuild_index()
                log.info("Consolidation complete", event="consolidate_done", agent=agent)
                return True
            except Exception as e:
                log.error(
                    "Consolidation failed",
                    event="consolidate_error",
                    agent=agent,
                    error=str(e),
                )
                return False
 
    async def _should_consolidate(self, agent: str) -> bool:
        """
        Check if consolidation should run based on session count and
        time since last consolidation.
        """
        # Check session count
        recent = await self._storage.search_history(
            "_", agent=agent, limit=_CONSOLIDATION_MIN_SESSIONS
        )
        if len(recent) < _CONSOLIDATION_MIN_SESSIONS:
            return False
 
        # Check time since last consolidation (stored as a marker in the index)
        index = await self.get_index()
        match = re.search(r"_Last consolidated: (.+?)_", index)
        if match:
            try:
                last_run = datetime.fromisoformat(match.group(1).strip())
                hours_since = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600
                if hours_since < _CONSOLIDATION_MIN_HOURS:
                    return False
            except ValueError:
                pass  # malformed date — proceed with consolidation
 
        return True
 
    async def _run_consolidation(self, agent: str) -> None:
        """
        The actual consolidation work. Rewrites each solution file for the
        given agent using the LLM to merge, deduplicate, and prune.
        """
        agent_dir = self._solutions_dir / agent
        if not agent_dir.exists():
            return
 
        solution_files = list(agent_dir.glob("*.md"))
        if not solution_files:
            return
 
        # Consolidate each solution file individually
        for sol_file in solution_files:
            content = sol_file.read_text(encoding="utf-8").strip()
            if not content:
                continue
 
            consolidated = await self._llm.complete(
                messages=[Message(
                    role="user",
                    content=(
                        f"Rewrite this knowledge/solution file to be more useful:\n\n"
                        f"{content}\n\n"
                        f"Rules:\n"
                        f"- Merge any duplicate or redundant information\n"
                        f"- Remove anything that contradicts a more recent entry\n"
                        f"- Convert vague statements to specific, actionable ones\n"
                        f"- Prune anything that is unlikely to be useful in future\n"
                        f"- Keep the result under {_MAX_SOLUTION_CHARS} characters\n"
                        f"- Preserve the markdown format\n"
                        f"- Return only the rewritten content, no explanation"
                    ),
                )],
                system=(
                    "You are a memory consolidation system. Rewrite knowledge files "
                    "to be concise, specific, and free of redundancy. "
                    "Return only the rewritten content."
                ),
                max_tokens=512,
            )
 
            if consolidated.strip():
                sol_file.write_text(consolidated.strip(), encoding="utf-8")
                log.info(
                    "Solution consolidated",
                    event="solution_consolidated",
                    file=sol_file.name,
                )
 
        # Mark consolidation time in the index
        await self._mark_consolidated()
 
    async def _mark_consolidated(self) -> None:
        """Update the consolidation timestamp in MEMORY.md."""
        index_path = self._context_dir / _INDEX_FILE
        if not index_path.exists():
            return
 
        content = index_path.read_text(encoding="utf-8")
        marker = f"_Last consolidated: {_now()}_"
 
        if "_Last consolidated:" in content:
            content = re.sub(r"_Last consolidated:.*_", marker, content)
        else:
            # Add after the last updated line
            content = content.replace(
                f"_Last updated: {_now()}_",
                f"_Last updated: {_now()}_\n{marker}",
            )
 
        index_path.write_text(content, encoding="utf-8")
 
    # ── Session context with auto-compaction ──

    async def get_session_context(
        self, session_id: str, agent: str
    ) -> list[Message]:
        """
        Get session messages with auto-compaction.
        If estimated tokens exceed threshold, summarize old messages
        and keep only the last N.
        """
        messages = await self._storage.get_session_messages(session_id, limit=100)

        if not messages:
            return []

        estimated_tokens = sum(len(m.content) for m in messages) // _CHARS_PER_TOKEN

        if estimated_tokens > _COMPACTION_THRESHOLD and len(messages) > _KEEP_RECENT:
            old_messages = messages[:-_KEEP_RECENT]
            recent_messages = messages[-_KEEP_RECENT:]

            log.info(
                "Compacting session",
                event="session_compact",
                session_id=session_id,
                old_count=len(old_messages),
                estimated_tokens=estimated_tokens,
            )

            summary = await self._llm.summarize(old_messages)

            # Return summary as a system-ish user message + recent history
            summary_msg = Message(
                role="user",
                content=f"[Previous conversation summary: {summary}]",
                agent=agent,
            )
            return [summary_msg] + recent_messages

        return messages

    # ── Main entry point for agents ──

    async def build_context(
        self, session_id: str, agent: str, task: str = ""
    ) -> tuple[str, list[Message]]:
        """
        Main context builder. Returns (markdown_context, compacted_history).
        markdown_context is the concatenation of all relevant context files.
        Pass task= for smarter topic file selection. Falls back to loading
        all context files if task is empty (backwards compatible).
        """
        if task:
            markdown_context = await self.get_relevant_context(task)
        else: 
            # Backwards compatible: load all context files
            parts: list[str] = []
            index = await self.get_index()
            if index.strip():
                parts.append(f"## Memory index\n{index.strip()}")
            if self._context_dir.exists():
                for md_file in sorted(self._context_dir.glob("*.md")):
                    if md_file.name == _INDEX_FILE:
                        continue
                    content = md_file.read_text(encoding="utf-8").strip()
                    if content:
                        parts.append(f"## {md_file.stem}\n{content}")

            markdown_context = "\n\n".join(parts)

        # Get compacted history
        history = await self.get_session_context(session_id, agent)
        return markdown_context, history

    # ── Fire-and-forget consolidation helper ──
    def schedule_consolidation(self, agent: str) -> None:
        """
        Schedule a background consolidation without awaiting it.
        Call this from agents after saving a solution — it won't block.

        Usage:
            await self.memory.save_solution(agent, topic, content)
            self.memory.schedule_consolidation(agent)
        """
        asyncio.create_task(
            self.consolidate(agent),
            name=f"consolidate_{agent}",
        )


# ── Helpers ───────────────────────────────────
 
def _extract_summary(content: str) -> str:
    """
    Extract a one-line summary from markdown content.
    Prefers the first non-heading, non-empty line.
    """
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("_"):
            return line[:150]
    return content[:150].replace("\n", " ")
 
 
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
 
