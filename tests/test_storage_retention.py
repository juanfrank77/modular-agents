"""Tests for Storage.delete_messages_older_than — time-based message pruning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from core.storage import Storage


@pytest.fixture
async def db(tmp_path: Path) -> Storage:
    storage = Storage(tmp_path / "test.db")
    await storage.init()
    return storage


async def _set_ts(db_path: Path, msg_id: str, ts: datetime) -> None:
    """Test-only helper: back-date a message's ts, since save_message always
    stamps datetime.now()."""
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "UPDATE messages SET ts = ? WHERE id = ?", (ts.isoformat(), msg_id)
        )
        await conn.commit()


async def _message_id(db_path: Path, content: str) -> str:
    async with aiosqlite.connect(str(db_path)) as conn:
        cursor = await conn.execute(
            "SELECT id FROM messages WHERE content = ?", (content,)
        )
        row = await cursor.fetchone()
        return row[0]


@pytest.mark.asyncio
class TestDeleteMessagesOlderThan:
    async def test_deletes_rows_older_than_cutoff(self, db, tmp_path):
        db_path = tmp_path / "test.db"
        session_id = await db.create_session("business")
        await db.save_message(session_id, "user", "ancient message", "business")
        await db.save_message(session_id, "user", "recent message", "business")

        old_id = await _message_id(db_path, "ancient message")
        await _set_ts(db_path, old_id, datetime.now(timezone.utc) - timedelta(days=100))

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        deleted = await db.delete_messages_older_than(session_id, cutoff)

        assert deleted == 1
        remaining = await db.get_session_messages(session_id)
        assert [m.content for m in remaining] == ["recent message"]

    async def test_does_not_delete_rows_at_or_after_cutoff(self, db, tmp_path):
        db_path = tmp_path / "test.db"
        session_id = await db.create_session("business")
        await db.save_message(session_id, "user", "borderline message", "business")

        old_id = await _message_id(db_path, "borderline message")
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        await _set_ts(db_path, old_id, cutoff + timedelta(seconds=1))

        deleted = await db.delete_messages_older_than(session_id, cutoff)

        assert deleted == 0
        remaining = await db.get_session_messages(session_id)
        assert len(remaining) == 1

    async def test_no_matching_rows_is_a_noop(self, db):
        session_id = await db.create_session("business")
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)

        deleted = await db.delete_messages_older_than(session_id, cutoff)

        assert deleted == 0

    async def test_deleted_rows_removed_from_fts(self, db, tmp_path):
        db_path = tmp_path / "test.db"
        session_id = await db.create_session("business")
        await db.save_message(session_id, "user", "old searchable text", "business")

        old_id = await _message_id(db_path, "old searchable text")
        await _set_ts(db_path, old_id, datetime.now(timezone.utc) - timedelta(days=100))

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        await db.delete_messages_older_than(session_id, cutoff)

        results = await db.search_history("searchable")
        assert results == []

    async def test_only_deletes_for_matching_session(self, db, tmp_path):
        db_path = tmp_path / "test.db"
        s1 = await db.create_session("business")
        s2 = await db.create_session("devops")
        await db.save_message(s1, "user", "old business message", "business")
        await db.save_message(s2, "user", "old devops message", "devops")

        s1_msg_id = await _message_id(db_path, "old business message")
        s2_msg_id = await _message_id(db_path, "old devops message")
        old_ts = datetime.now(timezone.utc) - timedelta(days=100)
        await _set_ts(db_path, s1_msg_id, old_ts)
        await _set_ts(db_path, s2_msg_id, old_ts)

        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        deleted = await db.delete_messages_older_than(s1, cutoff)

        assert deleted == 1
        assert len(await db.get_session_messages(s1)) == 0
        assert len(await db.get_session_messages(s2)) == 1
