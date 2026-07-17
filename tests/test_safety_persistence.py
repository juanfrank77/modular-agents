"""
test_safety_persistence.py
------------------------------
Tests for PairingManager and ApprovalGate persistence via StateStore:
write-through on state changes, rehydration at startup, and the
orphaned-approval restart notification.

Run:
    python -m pytest tests/test_safety_persistence.py -x -q
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.safety import ApprovalGate, PairingManager
from core.state_store import StateStore


@pytest.fixture
async def store(tmp_path: Path) -> StateStore:
    s = StateStore(tmp_path / "state.db")
    await s.init()
    return s


class TestPairingManagerNoStateStore:
    @pytest.mark.asyncio
    async def test_behaves_exactly_like_today(self):
        pm = PairingManager(allowed_ids=[])
        assert await pm.try_pair("123", pm.code) is True
        assert pm.is_paired("123") is True


class TestPairingManagerPersistence:
    @pytest.mark.asyncio
    async def test_successful_pair_writes_through(self, store: StateStore):
        pm = PairingManager(allowed_ids=[], state_store=store)
        await pm.try_pair("123", pm.code)
        assert await store.load_paired_chats() == {"123"}

    @pytest.mark.asyncio
    async def test_failed_attempt_writes_through(self, store: StateStore):
        pm = PairingManager(allowed_ids=[], state_store=store)
        await pm.try_pair("123", "wrong-code")
        assert await store.load_failed_attempts() == {"123": 1}

    @pytest.mark.asyncio
    async def test_pair_directly_writes_through(self, store: StateStore):
        pm = PairingManager(allowed_ids=[], state_store=store)
        await pm.pair_directly("cli")
        assert await store.load_paired_chats() == {"cli"}

    @pytest.mark.asyncio
    async def test_load_rehydrates_state(self, store: StateStore):
        await store.save_paired_chat("123")
        await store.save_failed_attempts("456", 3)

        pm = PairingManager(allowed_ids=["000"], state_store=store)
        assert pm.is_paired("123") is False  # not loaded yet
        await pm.load()
        assert pm.is_paired("123") is True
        assert pm.is_locked("456") is False
        assert pm._failed_attempts["456"] == 3


class TestApprovalGateNoStateStore:
    @pytest.mark.asyncio
    async def test_behaves_exactly_like_today(self):
        notifier = MagicMock()
        notifier.send = AsyncMock()
        gate = ApprovalGate(notifier)
        approved = await gate.request_approval("cli", "do a thing")
        assert approved is True  # non-digit chat_id auto-approves


class TestApprovalGatePersistence:
    @pytest.mark.asyncio
    async def test_auto_approved_request_cleans_up_pending_row(self, store: StateStore):
        notifier = MagicMock()
        notifier.send = AsyncMock()
        gate = ApprovalGate(notifier, state_store=store)
        # Non-digit chat_id auto-approves without ever registering a pending
        # wait, so no row should be written for it in the first place.
        await gate.request_approval("cli", "do a thing")
        assert await store.load_pending_approvals() == []

    @pytest.mark.asyncio
    async def test_notify_orphaned_messages_and_clears(self, store: StateStore):
        await store.save_pending_approval("ab12", "123", "Merge PR #4", "WRITE_HIGH")
        notifier = MagicMock()
        notifier.send = AsyncMock()
        gate = ApprovalGate(notifier, state_store=store)

        await gate.notify_orphaned()

        notifier.send.assert_awaited_once()
        chat_id, text = notifier.send.call_args.args
        assert chat_id == "123"
        assert "Merge PR #4" in text
        assert await store.load_pending_approvals() == []

    @pytest.mark.asyncio
    async def test_notify_orphaned_noop_when_none_pending(self, store: StateStore):
        notifier = MagicMock()
        notifier.send = AsyncMock()
        gate = ApprovalGate(notifier, state_store=store)
        await gate.notify_orphaned()
        notifier.send.assert_not_awaited()