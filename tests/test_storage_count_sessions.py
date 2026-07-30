# test_storage_count_sessions.py
"""Tests for Storage.count_sessions used by Memory._should_consolidate."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.storage import Storage


@pytest.fixture
async def db(tmp_path: Path) -> Storage:
    storage = Storage(tmp_path / "test.db")
    await storage.init()
    try:
        yield storage
    finally:
        await storage.close()


@pytest.mark.asyncio
class TestCountSessions:
    async def test_counts_distinct_sessions_for_agent(self, db):
        s1 = await db.get_or_create_session("chat_1", "business")
        s2 = await db.get_or_create_session("chat_2", "business")
        await db.save_message(s1, "user", "hello", "business")
        await db.save_message(s1, "user", "again", "business")
        await db.save_message(s2, "user", "hi there", "business")

        count = await db.count_sessions("business")

        assert count == 2

    async def test_does_not_count_other_agents(self, db):
        s1 = await db.get_or_create_session("chat_1", "business")
        s2 = await db.get_or_create_session("chat_2", "devops")
        await db.save_message(s1, "user", "hello", "business")
        await db.save_message(s2, "user", "hi there", "devops")

        count = await db.count_sessions("business")

        assert count == 1

    async def test_no_messages_returns_zero(self, db):
        count = await db.count_sessions("business")

        assert count == 0
