"""
test_bus_persistence.py
---------------------------
Tests for MessageBus's chat->agent continuity persistence via StateStore.

Run:
    python -m pytest tests/test_bus_persistence.py -x -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.bus import MessageBus
from core.protocols import AgentEvent, AgentResponse, EventType
from core.state_store import StateStore


@pytest.fixture
async def store(tmp_path: Path) -> StateStore:
    s = StateStore(tmp_path / "state.db")
    await s.init()
    return s


class _FakeAgent:
    def __init__(self, name: str):
        self.name = name
        self.description = ""
        self.routable = True

    async def dispatch(self, event: AgentEvent) -> AgentResponse:
        return AgentResponse(text="ok", agent_name=self.name)

    async def health_check(self) -> bool:
        return True


class TestChatAgentMapWriteThrough:
    @pytest.mark.asyncio
    async def test_publish_writes_through(self, store: StateStore):
        bus = MessageBus(state_store=store)
        bus.register(_FakeAgent("business"))

        event = AgentEvent(
            type=EventType.USER_MESSAGE, agent_name="business", chat_id="123", text="hi"
        )
        await bus.publish(event)

        assert await store.load_chat_agent_map() == {"123": "business"}


class TestChatAgentMapRehydration:
    @pytest.mark.asyncio
    async def test_load_restores_stickiness_fallback(self, store: StateStore):
        await store.save_chat_agent("123", "devops")
        bus = MessageBus(state_store=store)
        bus.register(_FakeAgent("business"))
        bus.register(_FakeAgent("devops"))

        await bus.load_chat_agent_map()

        # No llm wired, no explicit agent_name -> falls back straight to
        # stickiness, which should now be pre-populated from the store.
        event = AgentEvent(type=EventType.USER_MESSAGE, agent_name="", chat_id="123", text="hi")
        resolved = await bus._resolve_agent(event)
        assert resolved.name == "devops"

    @pytest.mark.asyncio
    async def test_no_state_store_is_noop(self):
        bus = MessageBus()
        await bus.load_chat_agent_map()  # must not raise
        assert bus._chat_agent_map == {}


class TestChatModelMap:
    @pytest.mark.asyncio
    async def test_set_chat_model_writes_through_and_updates_get(self, store: StateStore):
        bus = MessageBus(state_store=store)

        await bus.set_chat_model("123", "claude-opus-4.6")

        assert bus.get_chat_model("123") == "claude-opus-4.6"
        assert await store.load_chat_model_map() == {"123": "claude-opus-4.6"}

    @pytest.mark.asyncio
    async def test_get_chat_model_returns_empty_string_when_unset(self):
        bus = MessageBus()
        assert bus.get_chat_model("never_seen") == ""

    @pytest.mark.asyncio
    async def test_clear_chat_model_removes_override(self, store: StateStore):
        bus = MessageBus(state_store=store)
        await bus.set_chat_model("123", "claude-opus-4.6")

        await bus.clear_chat_model("123")

        assert bus.get_chat_model("123") == ""
        assert await store.load_chat_model_map() == {}

    @pytest.mark.asyncio
    async def test_load_chat_model_map_rehydrates_from_store(self, store: StateStore):
        await store.save_chat_model("123", "claude-haiku-4.6")
        bus = MessageBus(state_store=store)

        await bus.load_chat_model_map()

        assert bus.get_chat_model("123") == "claude-haiku-4.6"

    @pytest.mark.asyncio
    async def test_load_chat_model_map_no_state_store_is_noop(self):
        bus = MessageBus()
        await bus.load_chat_model_map()  # must not raise
        assert bus.get_chat_model("123") == ""