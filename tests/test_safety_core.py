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

from core.protocols import NotificationError
from core.safety import ActionType, ApprovalGate, PairingManager, Safety


class TestPairingManagerVerifyCode:
    def test_accepts_exact_code(self):
        pm = PairingManager(allowed_ids=[])
        assert pm.verify_code(pm.code) is True

    def test_is_case_insensitive(self):
        pm = PairingManager(allowed_ids=[])
        assert pm.verify_code(pm.code.upper()) is True

    def test_strips_whitespace(self):
        pm = PairingManager(allowed_ids=[])
        assert pm.verify_code(f"  {pm.code}  ") is True
        assert pm.verify_code(f"\n{pm.code}\t") is True

    def test_rejects_wrong_code(self):
        pm = PairingManager(allowed_ids=[])
        assert pm.verify_code("wrong-code") is False

    def test_rejects_empty_string(self):
        pm = PairingManager(allowed_ids=[])
        assert pm.verify_code("") is False
        assert pm.verify_code("   ") is False


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


class TestPairingManagerTrustedInterface:
    """#45: trust must be explicit (set at pair_directly()), not inferred
    from chat_id's string shape."""

    @pytest.mark.asyncio
    async def test_pair_directly_marks_chat_id_trusted(self):
        pm = PairingManager(allowed_ids=[])
        await pm.pair_directly("cli")
        assert pm.is_trusted_interface("cli") is True

    @pytest.mark.asyncio
    async def test_pair_directly_marks_a_numeric_looking_chat_id_trusted_too(self):
        # Trust must not depend on the chat_id happening to look like a
        # Telegram numeric ID or not — only on how it was paired.
        pm = PairingManager(allowed_ids=[])
        await pm.pair_directly("123456")
        assert pm.is_trusted_interface("123456") is True

    @pytest.mark.asyncio
    async def test_pair_directly_can_opt_out_of_trusted_interface(self):
        pm = PairingManager(allowed_ids=[])
        await pm.pair_directly("http_abc", trusted_interface=False)
        assert pm.is_paired("http_abc") is True
        assert pm.is_trusted_interface("http_abc") is False

    @pytest.mark.asyncio
    async def test_try_pair_does_not_mark_chat_id_trusted(self):
        pm = PairingManager(allowed_ids=[])
        await pm.try_pair("123", pm.code)
        assert pm.is_trusted_interface("123") is False

    def test_unknown_chat_id_is_not_trusted(self):
        pm = PairingManager(allowed_ids=[])
        assert pm.is_trusted_interface("never-seen") is False


class TestPairingManagerVerifyCodeWithLockout:
    @pytest.mark.asyncio
    async def test_accepts_correct_code_without_pairing(self):
        # Non-empty allowed_ids so is_paired() is not unconditionally True.
        pm = PairingManager(allowed_ids=["999"])
        assert await pm.verify_code_with_lockout("x", pm.code) is True
        assert pm.is_paired("x") is False

    @pytest.mark.asyncio
    async def test_rejects_wrong_code_and_counts_attempts(self):
        pm = PairingManager(allowed_ids=["999"])
        assert await pm.verify_code_with_lockout("x", "wrong") is False
        assert pm.attempts_remaining("x") == PairingManager.MAX_FAILED_ATTEMPTS - 1

    @pytest.mark.asyncio
    async def test_locks_after_max_failed_attempts(self):
        pm = PairingManager(allowed_ids=["999"])
        for _ in range(PairingManager.MAX_FAILED_ATTEMPTS):
            assert await pm.verify_code_with_lockout("x", "wrong") is False
        assert pm.is_locked("x") is True
        assert await pm.verify_code_with_lockout("x", pm.code) is False

    @pytest.mark.asyncio
    async def test_resets_attempts_on_correct_code(self):
        pm = PairingManager(allowed_ids=["999"])
        await pm.verify_code_with_lockout("x", "wrong")
        assert await pm.verify_code_with_lockout("x", pm.code) is True
        assert pm.attempts_remaining("x") == PairingManager.MAX_FAILED_ATTEMPTS


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


class TestPairingManagerConfigurableMaxFailedAttempts:
    def test_class_default_is_unaffected_by_instance_override(self):
        PairingManager(allowed_ids=[], max_failed_attempts=2)
        assert PairingManager.MAX_FAILED_ATTEMPTS == 5

    def test_instance_uses_default_when_not_overridden(self):
        pm = PairingManager(allowed_ids=[])
        assert pm.MAX_FAILED_ATTEMPTS == 5

    def test_instance_uses_override_value(self):
        pm = PairingManager(allowed_ids=[], max_failed_attempts=2)
        assert pm.MAX_FAILED_ATTEMPTS == 2

    @pytest.mark.asyncio
    async def test_lockout_triggers_at_overridden_threshold(self):
        pm = PairingManager(allowed_ids=[], max_failed_attempts=2)
        await pm.try_pair("123", "wrong-code")
        assert pm.is_locked("123") is False
        await pm.try_pair("123", "wrong-code")
        assert pm.is_locked("123") is True


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
            gate.resolve(approval_id, "123", approved=True)

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
            gate.resolve(approval_id, "123", approved=False)

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
            gate.resolve(approval_id, "123", approved=True)

        task = asyncio.create_task(approve_shortly())
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.DESTRUCTIVE
        )
        await task
        assert approved is True

    @pytest.mark.asyncio
    async def test_trusted_interface_auto_approves_without_waiting(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})
        approved = await gate.request_approval(
            chat_id="cli",
            description="do a thing",
            action_type=ActionType.WRITE_HIGH,
            trusted_interface=True,
        )
        assert approved is True

    @pytest.mark.asyncio
    async def test_numeric_chat_id_marked_trusted_still_auto_approves(self):
        """#45: trust must come from the explicit flag, not chat_id's shape —
        a numeric-looking chat_id marked trusted must still auto-approve."""
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})
        approved = await gate.request_approval(
            chat_id="123456",
            description="do a thing",
            action_type=ActionType.WRITE_HIGH,
            trusted_interface=True,
        )
        assert approved is True

    @pytest.mark.asyncio
    async def test_non_numeric_chat_id_without_trusted_flag_still_waits(self):
        """#45: a non-numeric chat_id is no longer auto-approved on shape
        alone — without the explicit trusted_interface flag it must go
        through the normal wait/timeout path like any other chat_id."""
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 0.05})
        approved = await gate.request_approval(
            chat_id="cli", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        assert approved is False  # timed out, not auto-approved


class TestApprovalGateConfigurableDefaultTimeout:
    @pytest.mark.asyncio
    async def test_unknown_action_type_times_out_at_overridden_default(self):
        # timeouts has no DESTRUCTIVE entry, and default_timeout is small —
        # if the override isn't honored this would hang for the module's
        # real 300s default instead of timing out almost immediately.
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={}, default_timeout=0.05)
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.DESTRUCTIVE
        )
        assert approved is False

    @pytest.mark.asyncio
    async def test_no_action_type_times_out_at_overridden_default(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={}, default_timeout=0.05)
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=None
        )
        assert approved is False


class TestApprovalGateDeliveryFailure:
    @pytest.mark.asyncio
    async def test_send_with_buttons_failure_returns_false_fast(self):
        notifier = AsyncMock()
        notifier.send_with_buttons = AsyncMock(
            side_effect=NotificationError("blocked", chat_id="123")
        )
        gate = ApprovalGate(notifier=notifier, timeouts={"WRITE_HIGH": 5})

        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )

        assert approved is False
        notifier.send_with_buttons.assert_awaited_once()
        # A plain-text fallback should be attempted.
        notifier.send.assert_awaited_once()
        # Pending state should be cleaned up immediately, not after a timeout.
        assert gate._pending == {}
        assert gate._results == {}

    @pytest.mark.asyncio
    async def test_plain_text_fallback_failure_is_swallowed(self):
        notifier = AsyncMock()
        notifier.send_with_buttons = AsyncMock(
            side_effect=NotificationError("blocked", chat_id="123")
        )
        notifier.send = AsyncMock(
            side_effect=NotificationError("also blocked", chat_id="123")
        )
        gate = ApprovalGate(notifier=notifier, timeouts={"WRITE_HIGH": 5})

        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )

        assert approved is False
        notifier.send_with_buttons.assert_awaited_once()
        notifier.send.assert_awaited_once()
        assert gate._pending == {}
        assert gate._results == {}

    @pytest.mark.asyncio
    async def test_successful_send_with_buttons_resolves_normally(self):
        notifier = AsyncMock()
        gate = ApprovalGate(notifier=notifier, timeouts={"WRITE_HIGH": 5})

        async def approve_shortly():
            await asyncio.sleep(0.01)
            approval_id = next(iter(gate._pending))
            gate.resolve(approval_id, "123", approved=True)

        task = asyncio.create_task(approve_shortly())
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        await task

        assert approved is True
        notifier.send_with_buttons.assert_awaited_once()


class TestApprovalGateAuthentication:
    @pytest.mark.asyncio
    async def test_resolve_with_wrong_chat_id_returns_false(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})

        async def wrong_chat_shortly():
            await asyncio.sleep(0.01)
            approval_id = next(iter(gate._pending))
            return gate.resolve(approval_id, "999", approved=True)

        task = asyncio.create_task(wrong_chat_shortly())
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        resolved = await task

        assert resolved is False
        assert approved is False

    @pytest.mark.asyncio
    async def test_resolve_unknown_approval_id_returns_false(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})

        async def resolve_unknown():
            await asyncio.sleep(0.01)
            return gate.resolve("nosuchid", "123", approved=True)

        task = asyncio.create_task(resolve_unknown())
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        resolved = await task

        assert resolved is False
        assert approved is False

    @pytest.mark.asyncio
    async def test_wrong_chat_id_does_not_block_correct_chat_resolution(self):
        gate = ApprovalGate(notifier=AsyncMock(), timeouts={"WRITE_HIGH": 5})

        async def attempt_and_resolve():
            await asyncio.sleep(0.01)
            approval_id = next(iter(gate._pending))
            # First attempt from the wrong chat must be rejected.
            assert gate.resolve(approval_id, "999", approved=True) is False
            # Correct chat can still resolve it afterwards.
            return gate.resolve(approval_id, "123", approved=True)

        task = asyncio.create_task(attempt_and_resolve())
        approved = await gate.request_approval(
            chat_id="123", description="do a thing", action_type=ActionType.WRITE_HIGH
        )
        resolved = await task

        assert resolved is True
        assert approved is True


class TestSafetyCheckActionTrustedInterface:
    """#45: Safety.check_action() must look trust up from PairingManager
    (via pair_directly()), not re-derive it from chat_id's shape."""

    @pytest.mark.asyncio
    async def test_directly_paired_chat_auto_approves_high_risk_action(self):
        safety = Safety(notifier=AsyncMock(), allowed_ids=[])
        await safety.pairing.pair_directly("cli")

        allowed = await safety.check_action(
            chat_id="cli",
            action_type=ActionType.WRITE_HIGH,
            autonomy_level="supervised",
            description="do a risky thing",
        )

        assert allowed is True

    @pytest.mark.asyncio
    async def test_directly_paired_numeric_chat_id_also_auto_approves(self):
        # Same as above but with a Telegram-shaped chat_id, proving the
        # decision no longer depends on string shape.
        safety = Safety(notifier=AsyncMock(), allowed_ids=[])
        await safety.pairing.pair_directly("555000")

        allowed = await safety.check_action(
            chat_id="555000",
            action_type=ActionType.WRITE_HIGH,
            autonomy_level="supervised",
            description="do a risky thing",
        )

        assert allowed is True

    @pytest.mark.asyncio
    async def test_non_trusted_chat_waits_and_times_out(self):
        # Paired via try_pair (Telegram-style), not pair_directly — must NOT
        # auto-approve even though allowed_ids=[] makes it "paired".
        safety = Safety(
            notifier=AsyncMock(),
            allowed_ids=[],
            approval_timeouts={"WRITE_HIGH": 0.05},
        )

        allowed = await safety.check_action(
            chat_id="cli",  # non-numeric shape, but never pair_directly()'d
            action_type=ActionType.WRITE_HIGH,
            autonomy_level="supervised",
            description="do a risky thing",
        )

        assert allowed is False  # timed out waiting for a button click

    @pytest.mark.asyncio
    async def test_directly_paired_but_not_trusted_chat_waits_for_approval(self):
        # HTTP sessions are paired directly but with trusted_interface=False,
        # so supervised high-risk actions must wait for explicit approval.
        safety = Safety(
            notifier=AsyncMock(),
            allowed_ids=[],
            approval_timeouts={"WRITE_HIGH": 0.05},
        )
        await safety.pairing.pair_directly("http_abc", trusted_interface=False)

        allowed = await safety.check_action(
            chat_id="http_abc",
            action_type=ActionType.WRITE_HIGH,
            autonomy_level="supervised",
            description="do a risky thing",
        )

        assert allowed is False  # timed out waiting for explicit approval
