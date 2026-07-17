"""
test_state_store.py
----------------------
Tests for core/state_store.py — SQLite-backed persistence for pairing,
pending approvals, HTTP sessions, and bus chat->agent continuity.

Run:
    python -m pytest tests/test_state_store.py -x -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.state_store import StateStore


@pytest.fixture
async def store(tmp_path: Path) -> StateStore:
    s = StateStore(tmp_path / "state.db")
    await s.init()
    return s


class TestPairedChats:
    @pytest.mark.asyncio
    async def test_save_and_load(self, store: StateStore):
        await store.save_paired_chat("123")
        await store.save_paired_chat("456")
        assert await store.load_paired_chats() == {"123", "456"}

    @pytest.mark.asyncio
    async def test_delete(self, store: StateStore):
        await store.save_paired_chat("123")
        await store.delete_paired_chat("123")
        assert await store.load_paired_chats() == set()

    @pytest.mark.asyncio
    async def test_empty_by_default(self, store: StateStore):
        assert await store.load_paired_chats() == set()


class TestFailedAttempts:
    @pytest.mark.asyncio
    async def test_save_and_load(self, store: StateStore):
        await store.save_failed_attempts("123", 3)
        assert await store.load_failed_attempts() == {"123": 3}

    @pytest.mark.asyncio
    async def test_overwrite(self, store: StateStore):
        await store.save_failed_attempts("123", 1)
        await store.save_failed_attempts("123", 2)
        assert await store.load_failed_attempts() == {"123": 2}

    @pytest.mark.asyncio
    async def test_delete(self, store: StateStore):
        await store.save_failed_attempts("123", 3)
        await store.delete_failed_attempts("123")
        assert await store.load_failed_attempts() == {}


class TestPendingApprovals:
    @pytest.mark.asyncio
    async def test_save_and_load(self, store: StateStore):
        await store.save_pending_approval("ab12", "123", "Merge PR #4", "WRITE_HIGH")
        rows = await store.load_pending_approvals()
        assert rows == [
            {
                "approval_id": "ab12",
                "chat_id": "123",
                "description": "Merge PR #4",
                "action_type": "WRITE_HIGH",
            }
        ]

    @pytest.mark.asyncio
    async def test_delete(self, store: StateStore):
        await store.save_pending_approval("ab12", "123", "desc", "EXECUTE")
        await store.delete_pending_approval("ab12")
        assert await store.load_pending_approvals() == []

    @pytest.mark.asyncio
    async def test_clear_all(self, store: StateStore):
        await store.save_pending_approval("a1", "123", "desc1", "EXECUTE")
        await store.save_pending_approval("a2", "456", "desc2", "WRITE_HIGH")
        await store.clear_pending_approvals()
        assert await store.load_pending_approvals() == []


class TestHttpSessions:
    @pytest.mark.asyncio
    async def test_save_and_load(self, store: StateStore):
        await store.save_http_session("tok1", "http_abcd1234", 1700000000.0)
        assert await store.load_http_sessions() == {
            "tok1": ("http_abcd1234", 1700000000.0)
        }

    @pytest.mark.asyncio
    async def test_delete(self, store: StateStore):
        await store.save_http_session("tok1", "http_abcd1234", 1700000000.0)
        await store.delete_http_session("tok1")
        assert await store.load_http_sessions() == {}


class TestChatAgentMap:
    @pytest.mark.asyncio
    async def test_save_and_load(self, store: StateStore):
        await store.save_chat_agent("123", "devops")
        assert await store.load_chat_agent_map() == {"123": "devops"}

    @pytest.mark.asyncio
    async def test_overwrite(self, store: StateStore):
        await store.save_chat_agent("123", "devops")
        await store.save_chat_agent("123", "business")
        assert await store.load_chat_agent_map() == {"123": "business"}