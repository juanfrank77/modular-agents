# test_storage_search.py
"""Tests for Storage.search_history's FTS5-backed full-text search."""
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
class TestSearchHistory:
    async def test_finds_matching_message(self, db):
        session_id = await db.get_or_create_session("chat_1", "business")
        await db.save_message(session_id, "user", "let's schedule the morning briefing", "business")
        await db.save_message(session_id, "user", "unrelated content about lunch", "business")

        results = await db.search_history("briefing")
        assert len(results) == 1
        assert "briefing" in results[0].content

    async def test_filters_by_agent(self, db):
        s1 = await db.get_or_create_session("chat_1", "business")
        s2 = await db.get_or_create_session("chat_2", "devops")
        await db.save_message(s1, "user", "deploy the newsletter", "business")
        await db.save_message(s2, "user", "deploy the API service", "devops")

        results = await db.search_history("deploy", agent="devops")
        assert len(results) == 1
        assert results[0].agent == "devops"

    async def test_no_match_returns_empty(self, db):
        session_id = await db.get_or_create_session("chat_1", "business")
        await db.save_message(session_id, "user", "hello world", "business")

        results = await db.search_history("nonexistent")
        assert results == []

    async def test_empty_query_returns_empty(self, db):
        session_id = await db.get_or_create_session("chat_1", "business")
        await db.save_message(session_id, "user", "hello world", "business")

        assert await db.search_history("   ") == []

    async def test_query_with_special_characters_does_not_raise(self, db):
        session_id = await db.get_or_create_session("chat_1", "business")
        await db.save_message(session_id, "user", "cost is $5 (roughly)", "business")

        # FTS5 operators / quotes in the raw query must not break MATCH syntax.
        results = await db.search_history('"weird" AND OR NOT (query)')
        assert results == []  # no crash, just no match

    async def test_backfills_existing_rows_on_reinit(self, tmp_path):
        db_path = tmp_path / "backfill.db"
        storage = Storage(db_path)
        await storage.init()
        session_id = await storage.get_or_create_session("chat_1", "business")
        await storage.save_message(session_id, "user", "pre-existing note about taxes", "business")

        # Re-init simulates the FTS table being added to an existing DB.
        storage2 = Storage(db_path)
        await storage2.init()
        try:
            results = await storage2.search_history("taxes")
            assert len(results) == 1
        finally:
            await storage2.close()
