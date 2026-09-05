"""
core/state_store.py
----------------------
Async SQLite persistence for operational state that must survive a
process restart: pairing, pending approvals, HTTP sessions, and the
bus's chat->agent continuity map. Shares a database file with
core/storage.py (and its SQLCipher key, when configured) but owns its
own tables.

Usage:
    from core.state_store import StateStore
    state_store = StateStore(settings.db_path, settings.db_encryption_key)
    await state_store.init()
    await state_store.save_paired_chat("123456")
    paired = await state_store.load_paired_chats()
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite

from core.db import apply_encryption_key
from core.logger import get_logger

log = get_logger("state_store")


class StateStore:
    def __init__(self, db_path: Path, encryption_key: str = "") -> None:
        self._path = db_path
        self._db_path_str = str(db_path)
        self._encryption_key = encryption_key

    async def init(self) -> None:
        """Create tables if they don't exist. Call once at startup."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS paired_chats (
                    chat_id    TEXT PRIMARY KEY,
                    paired_at  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pairing_failed_attempts (
                    chat_id   TEXT PRIMARY KEY,
                    attempts  INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_approvals (
                    approval_id  TEXT PRIMARY KEY,
                    chat_id      TEXT NOT NULL,
                    description  TEXT NOT NULL,
                    action_type  TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS http_sessions (
                    token       TEXT PRIMARY KEY,
                    chat_id     TEXT NOT NULL,
                    created_at  REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_agent_map (
                    chat_id     TEXT PRIMARY KEY,
                    agent_name  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_model_map (
                    chat_id  TEXT PRIMARY KEY,
                    model    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_profiles (
                    name            TEXT PRIMARY KEY,
                    description     TEXT NOT NULL,
                    emoji           TEXT NOT NULL DEFAULT '🤖',
                    autonomy_level  TEXT NOT NULL DEFAULT 'supervised',
                    routable        INTEGER NOT NULL DEFAULT 1,
                    enabled         INTEGER NOT NULL DEFAULT 1,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL
                );
            """)
            await db.commit()
        log.info("StateStore initialised", event="state_store_init", path=self._db_path_str)

    # ── paired_chats ───────────────────────────

    async def save_paired_chat(self, chat_id: str) -> None:
        import datetime
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "INSERT OR REPLACE INTO paired_chats (chat_id, paired_at) VALUES (?, ?)",
                (chat_id, datetime.datetime.now(datetime.timezone.utc).isoformat()),
            )
            await db.commit()

    async def delete_paired_chat(self, chat_id: str) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute("DELETE FROM paired_chats WHERE chat_id = ?", (chat_id,))
            await db.commit()

    async def load_paired_chats(self) -> set[str]:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute("SELECT chat_id FROM paired_chats")
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    # ── pairing_failed_attempts ────────────────

    async def save_failed_attempts(self, chat_id: str, attempts: int) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "INSERT OR REPLACE INTO pairing_failed_attempts (chat_id, attempts) VALUES (?, ?)",
                (chat_id, attempts),
            )
            await db.commit()

    async def delete_failed_attempts(self, chat_id: str) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "DELETE FROM pairing_failed_attempts WHERE chat_id = ?", (chat_id,)
            )
            await db.commit()

    async def load_failed_attempts(self) -> dict[str, int]:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute("SELECT chat_id, attempts FROM pairing_failed_attempts")
            rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    # ── pending_approvals ───────────────────────

    async def save_pending_approval(
        self, approval_id: str, chat_id: str, description: str, action_type: str
    ) -> None:
        import datetime
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "INSERT OR REPLACE INTO pending_approvals "
                "(approval_id, chat_id, description, action_type, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    approval_id,
                    chat_id,
                    description,
                    action_type,
                    datetime.datetime.now(datetime.timezone.utc).isoformat(),
                ),
            )
            await db.commit()

    async def delete_pending_approval(self, approval_id: str) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "DELETE FROM pending_approvals WHERE approval_id = ?", (approval_id,)
            )
            await db.commit()

    async def load_pending_approvals(self) -> list[dict]:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute(
                "SELECT approval_id, chat_id, description, action_type FROM pending_approvals"
            )
            rows = await cursor.fetchall()
        return [
            {
                "approval_id": row[0],
                "chat_id": row[1],
                "description": row[2],
                "action_type": row[3],
            }
            for row in rows
        ]

    async def clear_pending_approvals(self) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute("DELETE FROM pending_approvals")
            await db.commit()

    # ── http_sessions ───────────────────────────

    async def save_http_session(self, token: str, chat_id: str, created_at: float) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "INSERT OR REPLACE INTO http_sessions (token, chat_id, created_at) VALUES (?, ?, ?)",
                (token, chat_id, created_at),
            )
            await db.commit()

    async def delete_http_session(self, token: str) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute("DELETE FROM http_sessions WHERE token = ?", (token,))
            await db.commit()

    async def clear_http_sessions(self) -> int:
        """Delete all HTTP sessions. Returns the number of rows removed."""
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute("DELETE FROM http_sessions")
            await db.commit()
        return cursor.rowcount

    async def load_http_sessions(self) -> dict[str, tuple[str, float]]:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute("SELECT token, chat_id, created_at FROM http_sessions")
            rows = await cursor.fetchall()
        return {row[0]: (row[1], row[2]) for row in rows}

    # ── chat_agent_map ──────────────────────────

    async def save_chat_agent(self, chat_id: str, agent_name: str) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "INSERT OR REPLACE INTO chat_agent_map (chat_id, agent_name) VALUES (?, ?)",
                (chat_id, agent_name),
            )
            await db.commit()

    async def load_chat_agent_map(self) -> dict[str, str]:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute("SELECT chat_id, agent_name FROM chat_agent_map")
            rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    # ── chat_model_map ───────────────────────────

    async def save_chat_model(self, chat_id: str, model: str) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "INSERT OR REPLACE INTO chat_model_map (chat_id, model) VALUES (?, ?)",
                (chat_id, model),
            )
            await db.commit()

    async def delete_chat_model(self, chat_id: str) -> None:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute("DELETE FROM chat_model_map WHERE chat_id = ?", (chat_id,))
            await db.commit()

    async def load_chat_model_map(self) -> dict[str, str]:
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute("SELECT chat_id, model FROM chat_model_map")
            rows = await cursor.fetchall()
        return {row[0]: row[1] for row in rows}

    # ── agent_profiles ───────────────────────────

    async def save_agent_profile(self, profile) -> None:
        """Insert or replace an agent profile."""
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute(
                "INSERT OR REPLACE INTO agent_profiles "
                "(name, description, emoji, autonomy_level, routable, enabled, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    profile.name,
                    profile.description,
                    profile.emoji,
                    profile.autonomy_level,
                    1 if profile.routable else 0,
                    1 if profile.enabled else 0,
                    profile.created_at or now,
                    now,
                ),
            )
            await db.commit()

    async def load_agent_profile(self, name: str):
        """Load a single agent profile by name, or None."""
        from core.protocols import AgentProfile
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute(
                "SELECT name, description, emoji, autonomy_level, routable, enabled, created_at, updated_at "
                "FROM agent_profiles WHERE name = ?",
                (name,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return AgentProfile(
            name=row[0],
            description=row[1],
            emoji=row[2],
            autonomy_level=row[3],
            routable=bool(row[4]),
            enabled=bool(row[5]),
            created_at=row[6],
            updated_at=row[7],
        )

    async def load_all_agent_profiles(self) -> list:
        """Load all agent profiles."""
        from core.protocols import AgentProfile
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            cursor = await db.execute(
                "SELECT name, description, emoji, autonomy_level, routable, enabled, created_at, updated_at "
                "FROM agent_profiles"
            )
            rows = await cursor.fetchall()
        return [
            AgentProfile(
                name=row[0],
                description=row[1],
                emoji=row[2],
                autonomy_level=row[3],
                routable=bool(row[4]),
                enabled=bool(row[5]),
                created_at=row[6],
                updated_at=row[7],
            )
            for row in rows
        ]

    async def delete_agent_profile(self, name: str) -> None:
        """Delete an agent profile by name."""
        async with aiosqlite.connect(self._db_path_str) as db:
            await apply_encryption_key(db, self._encryption_key)
            await db.execute("DELETE FROM agent_profiles WHERE name = ?", (name,))
            await db.commit()