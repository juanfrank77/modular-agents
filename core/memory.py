"""
core/memory.py
--------------
Two-layer memory system:
  Layer 1 (SQLite): Conversation history via Storage
  Layer 2 (Markdown): Static context from memory/context/*.md files

Usage:
    from core.memory import Memory
    mem = Memory(storage=storage, llm=llm, settings=settings)
    context, history = await mem.build_context(session_id, "business")
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import get_logger
from core.protocols import Message

if TYPE_CHECKING:
    from core.config import Settings
    from core.llm import AnthropicLLM
    from core.storage import Storage

log = get_logger("memory")

# Rough token estimation: ~4 chars per token
_CHARS_PER_TOKEN = 4
_COMPACTION_THRESHOLD = 8000  # tokens
_KEEP_RECENT = 20


class Memory:
    def __init__(self, storage: "Storage", llm: "AnthropicLLM", settings: "Settings") -> None:
        self._storage = storage
        self._llm = llm
        self._context_dir = settings.memory_context_dir
        self._solutions_dir = settings.memory_solutions_dir

    # ── Layer 1: SQLite (delegates to Storage) ──

    async def save_message(
        self, session_id: str, role: str, content: str, agent: str
    ) -> None:
        await self._storage.save_message(session_id, role, content, agent)

    async def search_history(
        self, query: str, agent: str | None = None, limit: int = 10
    ) -> list[Message]:
        return await self._storage.search_history(query, agent, limit)

    # ── Layer 2: Markdown context files ──

    async def get_context(self, key: str) -> str:
        """Read a markdown context file (e.g. 'preferences' → memory/context/preferences.md)."""
        path = self._context_dir / f"{key}.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    async def save_solution(self, agent: str, topic: str, content: str) -> None:
        """Save a solution/knowledge file for an agent."""
        agent_dir = self._solutions_dir / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        path = agent_dir / f"{topic}.md"
        path.write_text(content, encoding="utf-8")
        log.info("Solution saved", event="solution_saved", agent=agent, topic=topic)

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

    async def build_context(
        self, session_id: str, agent: str
    ) -> tuple[str, list[Message]]:
        """
        Convenience method: returns (markdown_context, compacted_history).
        markdown_context is the concatenation of all relevant context files.
        """
        # Load all markdown context files
        context_parts: list[str] = []
        if self._context_dir.exists():
            for md_file in sorted(self._context_dir.glob("*.md")):
                content = md_file.read_text(encoding="utf-8").strip()
                if content:
                    context_parts.append(f"## {md_file.stem}\n{content}")

        markdown_context = "\n\n".join(context_parts)

        # Get compacted history
        history = await self.get_session_context(session_id, agent)

        return markdown_context, history
