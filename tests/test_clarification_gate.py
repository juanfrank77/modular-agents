"""
test_clarification_gate.py
--------------------------
Tests for core/safety.ClarificationGate.

Run:
    python3 -m pytest tests/test_clarification_gate.py -x -q
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from core.protocols import NotificationError
from core.safety import ClarificationGate


_FIXED_UUID = uuid.UUID("12345678-1234-5678-1234-567812345678")


def _make_notifier() -> AsyncMock:
    notifier = AsyncMock()
    notifier.send_with_buttons = AsyncMock()
    notifier.send = AsyncMock()
    return notifier


@pytest.fixture
def fixed_uuid():
    with patch("core.safety.uuid.uuid4", return_value=_FIXED_UUID):
        yield


@pytest.mark.asyncio
class TestClarificationGateAsk:
    async def test_confirm_sends_yes_no_buttons(self, fixed_uuid):
        notifier = _make_notifier()
        gate = ClarificationGate(notifier, default_timeout=0.1)

        # Resolve after a short delay so ask() can proceed.
        async def _resolve_later():
            await asyncio.sleep(0.02)
            assert gate.resolve("12345678", "chat1", "yes")

        asyncio.create_task(_resolve_later())
        answer = await gate.ask(
            chat_id="chat1",
            question="Delete the stale branch?",
            question_type="confirm",
        )

        assert answer == "yes"
        notifier.send_with_buttons.assert_awaited_once()
        _, kwargs = notifier.send_with_buttons.call_args
        assert kwargs["chat_id"] == "chat1"
        assert kwargs["text"] == "Delete the stale branch?"
        assert len(kwargs["buttons"]) == 2
        assert kwargs["buttons"][0] == ("Yes", "clarify:12345678:yes")
        assert kwargs["buttons"][1] == ("No", "clarify:12345678:no")

    async def test_choice_sends_choice_buttons(self, fixed_uuid):
        notifier = _make_notifier()
        gate = ClarificationGate(notifier, default_timeout=0.1)

        async def _resolve_later():
            await asyncio.sleep(0.02)
            assert gate.resolve("12345678", "chat1", "1")

        asyncio.create_task(_resolve_later())
        answer = await gate.ask(
            chat_id="chat1",
            question="Deploy where?",
            question_type="choice",
            choices=["staging", "production"],
        )

        assert answer == "production"
        _, kwargs = notifier.send_with_buttons.call_args
        assert kwargs["buttons"] == [
            ("staging", "clarify:12345678:0"),
            ("production", "clarify:12345678:1"),
        ]

    async def test_text_sends_plain_message_and_returns_default(self, fixed_uuid):
        notifier = _make_notifier()
        gate = ClarificationGate(notifier, default_timeout=0.05)

        answer = await gate.ask(
            chat_id="chat1",
            question="Any notes?",
            question_type="text",
            default="none",
        )

        assert answer == "none"
        notifier.send.assert_awaited_once_with("chat1", "Any notes?")
        notifier.send_with_buttons.assert_not_awaited()

    async def test_timeout_returns_default(self, fixed_uuid):
        notifier = _make_notifier()
        gate = ClarificationGate(notifier, default_timeout=0.02)

        answer = await gate.ask(
            chat_id="chat1",
            question="Deploy where?",
            question_type="choice",
            choices=["staging", "production"],
            default="staging",
        )

        assert answer == "staging"

    async def test_delivery_failure_returns_default(self, fixed_uuid):
        notifier = _make_notifier()
        notifier.send_with_buttons = AsyncMock(
            side_effect=NotificationError("network down", chat_id="chat1")
        )
        gate = ClarificationGate(notifier, default_timeout=0.1)

        answer = await gate.ask(
            chat_id="chat1",
            question="Deploy where?",
            question_type="confirm",
            default="no",
        )

        assert answer == "no"


@pytest.mark.asyncio
class TestClarificationGateResolve:
    async def test_resolve_wrong_chat_returns_false(self, fixed_uuid):
        notifier = _make_notifier()
        gate = ClarificationGate(notifier, default_timeout=0.1)

        async def _resolve_later():
            await asyncio.sleep(0.02)
            assert gate.resolve("12345678", "wrong_chat", "yes") is False

        asyncio.create_task(_resolve_later())
        answer = await gate.ask(
            chat_id="chat1", question="X?", question_type="confirm"
        )
        assert answer == ""  # timeout because resolve was rejected

    async def test_resolve_unknown_id_returns_false(self):
        notifier = _make_notifier()
        gate = ClarificationGate(notifier)
        assert gate.resolve("unknown", "chat1", "yes") is False
