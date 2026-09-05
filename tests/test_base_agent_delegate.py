"""
test_base_agent_delegate.py
---------------------------
Tests for BaseAgent.delegate() — inter-agent delegation through the bus.

Run:
    python3 -m pytest tests/test_base_agent_delegate.py -x -q
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.echo.agent import EchoAgent
from core.bus import MessageBus
from core.protocols import AgentEvent, AgentResponse, EventType


def _make_agent(bus=None, model=""):
    settings = MagicMock()
    settings.echo_agent_model = model
    settings.telegram_allowed_chat_ids = []
    notifier = MagicMock()
    notifier.send = AsyncMock()
    storage = MagicMock()
    storage.get_or_create_session = AsyncMock(return_value="session1")
    storage.save_message = AsyncMock()
    storage.search_history = AsyncMock(return_value=[])
    return EchoAgent(
        settings=settings,
        storage=storage,
        notifier=notifier,
        bus=bus,
    )


class _HangingEchoAgent(EchoAgent):
    """EchoAgent variant whose handle() sleeps to simulate a hung target."""

    async def handle(self, event):  # type: ignore[override]
        await asyncio.sleep(5)
        return AgentResponse(text="unreachable", agent_name=self.name)


def _make_hanging_agent(bus):
    settings = MagicMock()
    settings.echo_agent_model = ""
    settings.telegram_allowed_chat_ids = []
    notifier = MagicMock()
    notifier.send = AsyncMock()
    storage = MagicMock()
    storage.get_or_create_session = AsyncMock(return_value="session1")
    storage.save_message = AsyncMock()
    storage.search_history = AsyncMock(return_value=[])
    return _HangingEchoAgent(
        settings=settings,
        storage=storage,
        notifier=notifier,
        bus=bus,
    )


class TestDelegation:
    @pytest.mark.asyncio
    async def test_happy_path_delegates_and_returns_response(self):
        bus = MessageBus()
        parent = _make_agent(bus=bus)
        # Register a second agent with a distinct name to receive the delegated event.
        target = _make_agent(bus=bus)
        target.name = "helper"
        target.description = "helper agent"
        delivered: list[AgentEvent] = []
        original_dispatch = target.dispatch

        async def _capturing_dispatch(event):
            delivered.append(event)
            return await original_dispatch(event)

        target.dispatch = _capturing_dispatch  # type: ignore[assignment]
        bus.register(parent)
        bus.register(target)

        incoming = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="echo",
            chat_id="chat1",
            text="please help",
        )

        response = await parent.delegate(incoming, "helper")

        assert response.success is True
        assert len(delivered) == 1
        event = delivered[0]
        assert event.agent_name == "helper"
        assert event.origin_agent == "echo"
        assert event.correlation_id != ""
        assert event.parent_event_id != ""

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self):
        bus = MessageBus()
        parent = _make_agent(bus=bus)
        # Swap parent's name so it doesn't clash with target
        target = _make_hanging_agent(bus=bus)
        target.name = "helper"
        target.description = "helper agent"
        bus.register(parent)
        bus.register(target)

        incoming = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="echo",
            chat_id="chat1",
            text="please help",
        )

        response = await parent.delegate(
            incoming, "helper", timeout_seconds=0.05
        )

        assert response.success is False
        assert "timed out" in response.text.lower()
        assert response.agent_name == "helper"

    @pytest.mark.asyncio
    async def test_missing_target_agent_returns_failure(self):
        # Empty bus: publish() returns None because no agents are registered.
        bus = MessageBus()
        parent = _make_agent(bus=bus)
        # Note: do NOT register parent — we want publish() to fail to resolve.

        incoming = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="echo",
            chat_id="chat1",
            text="please help",
        )

        response = await parent.delegate(incoming, "ghost")

        assert response.success is False
        assert "not found" in response.text.lower() or "failed to respond" in response.text.lower()
        assert response.agent_name == "ghost"

    @pytest.mark.asyncio
    async def test_response_propagates(self):
        bus = MessageBus()
        parent = _make_agent(bus=bus)
        target = _make_agent(bus=bus)
        target.name = "helper"
        target.description = "helper agent"
        bus.register(parent)
        bus.register(target)

        incoming = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="echo",
            chat_id="chat1",
            text="please help",
        )

        response = await parent.delegate(incoming, "helper")

        # EchoAgent's handle() calls reply(), which sends through the notifier
        # and returns AgentResponse(text=<original text>, agent_name=self.name).
        assert "please help" in response.text
        assert response.agent_name == "helper"
        assert response.success is True

    @pytest.mark.asyncio
    async def test_bus_none_returns_failure(self):
        parent = _make_agent(bus=None)

        incoming = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="echo",
            chat_id="chat1",
            text="please help",
        )

        response = await parent.delegate(incoming, "helper")

        assert response.success is False
        assert response.agent_name == "helper"

    @pytest.mark.asyncio
    async def test_handoff_propagated_in_response(self):
        bus = MessageBus()

        # Build a parent that delegates but doesn't itself handle.
        parent = _make_agent(bus=bus)
        target = _make_agent(bus=bus)
        target.name = "helper"
        target.description = "helper agent"

        async def _returning_handle(event):
            return AgentResponse(
                text="done",
                agent_name="helper",
                handoff={"status": "ok", "items": [1, 2, 3]},
            )

        target.handle = _returning_handle  # type: ignore[assignment]
        bus.register(parent)
        bus.register(target)

        incoming = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="echo",
            chat_id="chat1",
            text="please help",
        )

        response = await parent.delegate(incoming, "helper")

        assert response.success is True
        assert response.handoff == {"status": "ok", "items": [1, 2, 3]}


class _FakeAgent:
    """Minimal bus-compatible worker for stickiness routing tests."""

    name = "worker"
    description = "worker agent"
    routable = True

    async def dispatch(self, event):
        return AgentResponse(text="done", agent_name=self.name)

    async def health_check(self):
        return True


class TestDelegationDoesNotHijackStickiness:
    @pytest.mark.asyncio
    async def test_delegate_does_not_overwrite_chat_agent_map(self):
        bus = MessageBus()
        parent = _make_agent(bus=bus)
        worker = _FakeAgent()
        bus.register(parent)
        bus.register(worker)  # ty: ignore[invalid-argument-type]

        incoming = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="echo",
            chat_id="chat1",
            text="please help",
        )

        await parent.delegate(incoming, "worker")

        # Delegated sub-task (origin_agent set) must not overwrite the
        # chat's sticky agent mapping.
        assert "chat1" not in bus._chat_agent_map

        # Normal user message without origin still sets stickiness.
        direct = AgentEvent(
            type=EventType.USER_MESSAGE,
            agent_name="worker",
            chat_id="chat1",
            text="direct message",
        )
        await bus.publish(direct)

        assert bus._chat_agent_map["chat1"] == "worker"
