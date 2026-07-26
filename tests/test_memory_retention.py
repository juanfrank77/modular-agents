"""Tests for retention pruning wired into Memory.get_session_context."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from core.memory import Memory
from core.storage import Storage


async def _set_ts(db_path: Path, content: str, ts: datetime) -> None:
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "UPDATE messages SET ts = ? WHERE content = ?", (ts.isoformat(), content)
        )
        await conn.commit()


def _make_settings(tmp_path: Path, retention_days: int) -> MagicMock:
    settings = MagicMock()
    settings.memory_context_dir = tmp_path / "context"
    settings.memory_solutions_dir = tmp_path / "solutions"
    settings.message_retention_days = retention_days
    return settings


@pytest.mark.asyncio
class TestGetSessionContextRetention:
    async def test_prunes_messages_older_than_retention_window(self, tmp_path):
        storage = Storage(tmp_path / "test.db")
        await storage.init()
        session_id = await storage.create_session("business")
        await storage.save_message(session_id, "user", "ancient", "business")
        await storage.save_message(session_id, "user", "recent", "business")
        await _set_ts(
            tmp_path / "test.db", "ancient",
            datetime.now(timezone.utc) - timedelta(days=100),
        )

        memory = Memory(storage=storage, llm=AsyncMock(), settings=_make_settings(tmp_path, 90))
        result = await memory.get_session_context(session_id, "business")

        assert [m.content for m in result] == ["recent"]
        # Pruning also removed the row from underlying storage, not just the view.
        remaining = await storage.get_session_messages(session_id)
        assert [m.content for m in remaining] == ["recent"]

    async def test_zero_retention_days_disables_pruning(self, tmp_path):
        storage = Storage(tmp_path / "test.db")
        await storage.init()
        session_id = await storage.create_session("business")
        await storage.save_message(session_id, "user", "ancient", "business")
        await _set_ts(
            tmp_path / "test.db", "ancient",
            datetime.now(timezone.utc) - timedelta(days=1000),
        )

        memory = Memory(storage=storage, llm=AsyncMock(), settings=_make_settings(tmp_path, 0))
        result = await memory.get_session_context(session_id, "business")

        assert [m.content for m in result] == ["ancient"]

    async def test_no_old_messages_is_unaffected(self, tmp_path):
        storage = Storage(tmp_path / "test.db")
        await storage.init()
        session_id = await storage.create_session("business")
        await storage.save_message(session_id, "user", "fresh", "business")

        memory = Memory(storage=storage, llm=AsyncMock(), settings=_make_settings(tmp_path, 90))
        result = await memory.get_session_context(session_id, "business")

        assert [m.content for m in result] == ["fresh"]

    async def test_prune_failure_does_not_abort_session_context(self, tmp_path):
        """A transient DB error (e.g. 'database is locked') during pruning
        must be swallowed, not propagated — retention pruning is best-effort
        and should never abort a live conversation turn."""
        storage = Storage(tmp_path / "test.db")
        await storage.init()
        session_id = await storage.create_session("business")
        await storage.save_message(session_id, "user", "hello", "business")
        storage.delete_messages_older_than = AsyncMock(
            side_effect=Exception("database is locked")
        )

        memory = Memory(storage=storage, llm=AsyncMock(), settings=_make_settings(tmp_path, 90))
        result = await memory.get_session_context(session_id, "business")

        assert [m.content for m in result] == ["hello"]
