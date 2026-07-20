"""
test_safety_core.py
----------------------
Tests for the in-memory logic of PairingManager (lockout threshold) and
ApprovalGate (approval-timeout) in core/safety.py — distinct from
tests/test_safety_persistence.py, which covers StateStore write-through
and rehydration, not this behavior.

Run:
    python -m pytest tests/test_safety_core.py -x -q
"""

from __future__ import annotations

import asyncio

import pytest
from unittest.mock import AsyncMock

from core.safety import ActionType, ApprovalGate, PairingManager


class TestPairingManagerLockout:
    @pytest.mark.asyncio
    async def test_not_locked_before_max_failed_attempts(self):
        pm = PairingManager(allowed_ids=[])
        for _ in range(PairingManager.MAX_FAILED_ATTEMPTS - 1):
            await pm.try_pair("123", "wrong-code")
        assert pm.is_locked("123") is False

    @pytest.mark.asyncio
    async def test_locked_after_max_failed_attempts(self):
        pm = PairingManager(allowed_ids=[])
        for _ in range(PairingManager.MAX_FAILED_ATTEMPTS):
            await pm.try_pair("123", "wrong-code")
        assert pm.is_locked("123") is True

    @pytest.mark.asyncio
    async def test_locked_chat_cannot_pair_even_with_correct_code(self):
        # A non-empty allowed_ids excluding "123" is required here — an empty
        # list means "dev mode, no restrictions" and is_paired() always
        # returns True regardless of lock state.
        pm = PairingManager(allowed_ids=["999"])
        for _ in range(PairingManager.MAX_FAILED_ATTEMPTS):
            await pm.try_pair("123", "wrong-code")
        assert await pm.try_pair("123", pm.code) is False
        assert pm.is_paired("123") is False

    @pytest.mark.asyncio
    async def test_successful_pair_resets_failed_attempt_count(self):
        pm = PairingManager(allowed_ids=[])
        await pm.try_pair("123", "wrong-code")
        await pm.try_pair("123", "wrong-code")
        assert await pm.try_pair("123", pm.code) is True
        assert pm.is_locked("123") is False

    @pytest.mark.asyncio
    async def test_lockout_is_per_chat_id(self):
        pm = PairingManager(allowed_ids=[])
        for _ in range(PairingManager.MAX_FAILED_ATTEMPTS):
            await pm.try_pair("123", "wrong-code")
        assert pm.is_locked("123") is True
        assert pm.is_locked("456") is False
        assert await pm.try_pair("456", pm.code) is True


class TestPairingManagerAttemptsRemaining:
    @pytest.mark.asyncio
    async def test_full_attempts_remaining_for_unseen_chat(self):
        pm = PairingManager(allowed_ids=[])
        assert pm.attempts_remaining("never-tried") == PairingManager.MAX_FAILED_ATTEMPTS

    @pytest.mark.asyncio
    async def test_decrements_per_failed_attempt(self):
        pm = PairingManager(allowed_ids=[])
        await pm.try_pair("123", "wrong-code")
        assert pm.attempts_remaining("123") == PairingManager.MAX_FAILED_ATTEMPTS - 1
        await pm.try_pair("123", "wrong-code")
        assert pm.attempts_remaining("123") == PairingManager.MAX_FAILED_ATTEMPTS - 2

    @pytest.mark.asyncio
    async def test_zero_when_locked(self):
        pm = PairingManager(allowed_ids=[])
        for _ in range(PairingManager.MAX_FAILED_ATTEMPTS):
            await pm.try_pair("123", "wrong-code")
        assert pm.attempts_remaining("123") == 0

    @pytest.mark.asyncio
    async def test_resets_to_full_after_successful_pair(self):
        pm = PairingManager(allowed_ids=[])
        await pm.try_pair("123", "wrong-code")
        await pm.try_pair("123", pm.code)
        assert pm.attempts_remaining("123") == PairingManager.MAX_FAILED_ATTEMPTS


class TestApprovalGateTimeout:
    @pytest.mark.asyncio
    async def test_unresolved_approval_times_out_and_returns_false(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 0.05})
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        assert approved is False

    @pytest.mark.asyncio
    async def test_timeout_cleans_up_pending_and_result_state(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 0.05})
        await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        assert gate._pending == {}
        assert gate._results == {}

    @pytest.mark.asyncio
    async def test_resolve_before_timeout_returns_approved_result(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})

        async def approve_shortly():
            await asyncio.sleep(0.01)
            approval_id = next(iter(gate._pending))
            gate.resolve(approval_id, approved=True)

        task = asyncio.create_task(approve_shortly())
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        await task
        assert approved is True

    @pytest.mark.asyncio
    async def test_resolve_deny_before_timeout_returns_false(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})

        async def deny_shortly():
            await asyncio.sleep(0.01)
            approval_id = next(iter(gate._pending))
            gate.resolve(approval_id, approved=False)

        task = asyncio.create_task(deny_shortly())
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        await task
        assert approved is False

    @pytest.mark.asyncio
    async def test_falls_back_to_default_timeout_for_unknown_action_type(self):
        # No entry for DESTRUCTIVE in timeouts — must not raise, uses _DEFAULT_TIMEOUT.
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})

        async def approve_shortly():
            await asyncio.sleep(0.01)
            approval_id = next(iter(gate._pending))
            gate.resolve(approval_id, approved=True)

        task = asyncio.create_task(approve_shortly())
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.DESTRUCTIVE
        )
        await task
        assert approved is True

    @pytest.mark.asyncio
    async def test_non_numeric_chat_id_auto_approves_without_waiting(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})
        approved = await gate.request_approval(
            chat_id="cli", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        assert approved is True
