"""
core/storage.py
---------------
Async SQLite wrapper for conversation session history.
Agents never touch the DB directly — they call methods here.

Schema:
    sessions(id, agent, started_at, summary)
    messages(id, session_id, agent, role, content, ts)

Usage:
    from core.storage import Storage
    db = Storage(settings.db_path)
    await db.init()
    await db.save_message("sess_123", "user", "hello", "business")
    history = await db.search_history("morning briefing", agent="business")
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from core.logger import get_logger
from core.protocols import Message

log = get_logger("storage")


class Storage:
    def __init__(self, db_path: Path):
        self._path = db_path
        self._db_path_str = str(db_path)

    async def init(self) -> None:
        """Create tables if they don't exist. Call once at startup."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path_str) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    agent       TEXT NOT NULL,
                    started_at  TEXT NOT NULL,
                    summary     TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id          TEXT PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    agent       TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    ts          TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_messages_agent
                    ON messages(agent);
            """)
            await db.commit()
        log.info("Storage initialised", event="storage_init", path=self._db_path_str)

    # ── Sessions ───────────────────────────────

    async def create_session(self, agent: str) -> str:
        session_id = str(uuid.uuid4())
        async with aiosqlite.connect(self._db_path_str) as db:
            await db.execute(
                "INSERT INTO sessions (id, agent, started_at) VALUES (?, ?, ?)",
                (session_id, agent, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        return session_id

    async def get_or_create_session(self, chat_id: str, agent: str) -> str:
        """
        Returns the most recent open session for this chat+agent,
        or creates a new one. Using chat_id as a stable session key
        means one ongoing conversation per chat per agent.
        """
        session_id = f"{agent}_{chat_id}"
        async with aiosqlite.connect(self._db_path_str) as db:
            cursor = await db.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cursor.fetchone()
            if not row:
                await db.execute(
                    "INSERT INTO sessions (id, agent, started_at) VALUES (?, ?, ?)",
                    (session_id, agent, datetime.now(timezone.utc).isoformat()),
                )
                await db.commit()
        return session_id

    # ── Messages ──────────────────────────────

    async def save_message(
        self, session_id: str, role: str, content: str, agent: str
    ) -> None:
        msg_id = str(uuid.uuid4())
        async with aiosqlite.connect(self._db_path_str) as db:
            await db.execute(
                "INSERT INTO messages (id, session_id, agent, role, content, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, session_id, agent, role, content, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()

    async def get_session_messages(
        self, session_id: str, limit: int = 50
    ) -> list[Message]:
        async with aiosqlite.connect(self._db_path_str) as db:
            cursor = await db.execute(
                "SELECT role, content, agent, ts FROM messages "
                "WHERE session_id = ? ORDER BY ts DESC LIMIT ?",
                (session_id, limit),
            )
            rows = await cursor.fetchall()
        # Return in chronological order
        return [
            Message(role=r[0], content=r[1], agent=r[2],
                    timestamp=datetime.fromisoformat(r[3]))
            for r in list(rows)[::-1]
        ]

    async def search_history(
        self, query: str, agent: str | None = None, limit: int = 10
    ) -> list[Message]:
        """Simple keyword search across message content."""
        like = f"%{query}%"
        async with aiosqlite.connect(self._db_path_str) as db:
            if agent:
                cursor = await db.execute(
                    "SELECT role, content, agent, ts FROM messages "
                    "WHERE content LIKE ? AND agent = ? "
                    "ORDER BY ts DESC LIMIT ?",
                    (like, agent, limit),
                )
            else:
                cursor = await db.execute(
                    "SELECT role, content, agent, ts FROM messages "
                    "WHERE content LIKE ? ORDER BY ts DESC LIMIT ?",
                    (like, limit),
                )
            rows = await cursor.fetchall()
        return [
            Message(role=r[0], content=r[1], agent=r[2],
                    timestamp=datetime.fromisoformat(r[3]))
            for r in rows
        ]

    async def save_session_summary(self, session_id: str, summary: str) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await db.execute(
                "UPDATE sessions SET summary = ? WHERE id = ?",
                (summary, session_id),
            )
            await db.commit()
