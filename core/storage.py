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
    db = Storage(settings.db_path, settings.db_encryption_key)
    await db.init()
    await db.save_message("sess_123", "user", "hello", "business")
    history = await db.search_history("morning briefing", agent="business")
    await db.close()
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from core.db import apply_encryption_key
from core.logger import get_logger
from core.protocols import Message

log = get_logger("storage")


def _fts_phrase(query: str) -> str:
    """Wrap a raw user query as an FTS5 quoted phrase so operators
    (AND/OR/NOT/NEAR, unbalanced quotes, etc.) can't break the MATCH syntax."""
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


class Storage:
    def __init__(self, db_path: Path, encryption_key: str = ""):
        self._path = db_path
        self._db_path_str = str(db_path)
        self._encryption_key = encryption_key
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> None:
        """Create tables if they don't exist. Call once at startup.

        This opens a single long-lived SQLite connection that is reused for
        every subsequent query, avoiding the cost of re-connecting and
        re-deriving the SQLCipher key on every operation.
        """
        if self._db is not None:
            return

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path_str)
        await apply_encryption_key(self._db, self._encryption_key)
        # Wait up to 5s if another connection holds a write lock.
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.executescript("""
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

            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                id UNINDEXED,
                agent UNINDEXED,
                role UNINDEXED,
                ts UNINDEXED
            );

            CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(content, id, agent, role, ts)
                VALUES (new.content, new.id, new.agent, new.role, new.ts);
            END;

            CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
                DELETE FROM messages_fts WHERE id = old.id;
            END;
        """)
        # Backfill any rows written before the FTS index existed.
        await self._db.execute("""
            INSERT INTO messages_fts(content, id, agent, role, ts)
            SELECT content, id, agent, role, ts FROM messages
            WHERE id NOT IN (SELECT id FROM messages_fts)
        """)
        await self._db.commit()
        log.info("Storage initialised", event="storage_init", path=self._db_path_str)

    async def close(self) -> None:
        """Close the long-lived database connection."""
        if self._db is not None:
            try:
                await self._db.close()
            except Exception:
                pass
            self._db = None
            log.info("Storage closed", event="storage_close", path=self._db_path_str)

    # ── Sessions ───────────────────────────────

    async def create_session(self, agent: str) -> str:
        session_id = str(uuid.uuid4())
        if self._db is None:
            raise RuntimeError("Storage not initialised; call init() first")
        await self._db.execute(
            "INSERT INTO sessions (id, agent, started_at) VALUES (?, ?, ?)",
            (session_id, agent, datetime.now(timezone.utc).isoformat()),
        )
        await self._db.commit()
        return session_id

    async def get_or_create_session(self, chat_id: str, agent: str) -> str:
        """
        Returns the most recent open session for this chat+agent,
        or creates a new one. Using chat_id as a stable session key
        means one ongoing conversation per chat per agent.

        The insert uses ``INSERT OR IGNORE`` so concurrent callers racing
        to create the same session resolve atomically instead of failing
        with a uniqueness constraint error.
        """
        session_id = f"{agent}_{chat_id}"
        if self._db is None:
            raise RuntimeError("Storage not initialised; call init() first")
        await self._db.execute(
            "INSERT OR IGNORE INTO sessions (id, agent, started_at) VALUES (?, ?, ?)",
            (session_id, agent, datetime.now(timezone.utc).isoformat()),
        )
        await self._db.commit()
        return session_id

    # ── Messages ──────────────────────────────

    async def save_message(
        self, session_id: str, role: str, content: str, agent: str
    ) -> None:
        msg_id = str(uuid.uuid4())
        if self._db is None:
            raise RuntimeError("Storage not initialised; call init() first")
        await self._db.execute(
            "INSERT INTO messages (id, session_id, agent, role, content, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, session_id, agent, role, content, datetime.now(timezone.utc).isoformat()),
        )
        await self._db.commit()

    async def get_session_messages(
        self, session_id: str, limit: int = 50
    ) -> list[Message]:
        if self._db is None:
            raise RuntimeError("Storage not initialised; call init() first")
        cursor = await self._db.execute(
            "SELECT role, content, agent, ts FROM messages "
            "WHERE session_id = ? ORDER BY ts DESC LIMIT ?",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [
            Message(role=r[0], content=r[1], agent=r[2],
                    timestamp=datetime.fromisoformat(r[3]))
            for r in list(rows)[::-1]
        ]

    async def search_history(
        self, query: str, agent: str | None = None, limit: int = 10
    ) -> list[Message]:
        """Full-text search across message content, ranked by relevance (FTS5 bm25)."""
        if not query.strip():
            return []
        if self._db is None:
            raise RuntimeError("Storage not initialised; call init() first")
        match = _fts_phrase(query)
        if agent:
            cursor = await self._db.execute(
                "SELECT role, content, agent, ts FROM messages_fts "
                "WHERE messages_fts MATCH ? AND agent = ? "
                "ORDER BY rank LIMIT ?",
                (match, agent, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT role, content, agent, ts FROM messages_fts "
                "WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            )
        rows = await cursor.fetchall()
        return [
            Message(role=r[0], content=r[1], agent=r[2],
                    timestamp=datetime.fromisoformat(r[3]))
            for r in rows
        ]

    async def save_session_summary(self, session_id: str, summary: str) -> None:
        if self._db is None:
            raise RuntimeError("Storage not initialised; call init() first")
        await self._db.execute(
            "UPDATE sessions SET summary = ? WHERE id = ?",
            (summary, session_id),
        )
        await self._db.commit()

    async def delete_messages_older_than(
        self, session_id: str, cutoff: datetime
    ) -> int:
        """Delete messages in this session older than cutoff. Returns the
        number of rows deleted. The messages_fts_ad trigger keeps the FTS
        index in sync automatically."""
        if self._db is None:
            raise RuntimeError("Storage not initialised; call init() first")
        cursor = await self._db.execute(
            "DELETE FROM messages WHERE session_id = ? AND ts < ?",
            (session_id, cutoff.isoformat()),
        )
        await self._db.commit()
        return cursor.rowcount
