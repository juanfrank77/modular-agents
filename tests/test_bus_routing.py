"""
test_bus_routing.py
----------------------
Tests for core/bus.py's MessageBus._resolve_agent — explicit agent_name
priority, content-based classification for user messages, exclusion of
non-routable agents, and the stickiness/first-registered fallback chain.

Run:
    python -m pytest tests/test_bus_routing.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.bus import MessageBus
from core.protocols import AgentEvent, AgentResponse, EventType


class _FakeAgent:
    def __init__(self, name: str, description: str = "", routable: bool = True):
        self.name = name
        self.description = description
        self.routable = routable
        self.dispatch_calls: list[AgentEvent] = []

    async def dispatch(self, event: AgentEvent) -> AgentResponse:
        self.dispatch_calls.append(event)
        return AgentResponse(text="ok", agent_name=self.name)

    async def health_check(self) -> bool:
        return True


def _user_event(text: str, chat_id: str = "chat1", agent_name: str = "") -> AgentEvent:
    return AgentEvent(
        type=EventType.USER_MESSAGE, agent_name=agent_name, chat_id=chat_id, text=text
    )


class TestResolveAgent:
    @pytest.mark.asyncio
    async def test_explicit_agent_name_wins_even_with_classifier(self):
        bus = MessageBus(llm=object(), classifier_model="cheap-model")
        business, devops = _FakeAgent("business"), _FakeAgent("devops")
        bus.register(business)
        bus.register(devops)

        with patch(
            "core.bus.classify_agent", new=AsyncMock(return_value="business")
        ) as mock_classify:
            resolved = await bus._resolve_agent(
                _user_event("restart it", agent_name="devops")
            )
            assert resolved is devops
            mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_classifier_picks_agent_for_untagged_message(self):
        bus = MessageBus(llm=object(), classifier_model="cheap-model")
        business, devops = _FakeAgent("business"), _FakeAgent("devops")
        bus.register(business)
        bus.register(devops)

        with patch(
            "core.bus.classify_agent", new=AsyncMock(return_value="devops")
        ) as mock_classify:
            resolved = await bus._resolve_agent(_user_event("restart the server"))
            assert resolved is devops
            mock_classify.assert_awaited_once()
            candidates = mock_classify.call_args.args[1]
            assert candidates == {"business": "", "devops": ""}

    @pytest.mark.asyncio
    async def test_non_routable_agent_excluded_from_classifier_candidates(self):
        bus = MessageBus(llm=object(), classifier_model="cheap-model")
        business = _FakeAgent("business")
        echo = _FakeAgent("echo", routable=False)
        bus.register(business)
        bus.register(echo)

        with patch(
            "core.bus.classify_agent", new=AsyncMock(return_value="business")
        ) as mock_classify:
            await bus._resolve_agent(_user_event("hi"))
            candidates = mock_classify.call_args.args[1]
            assert "echo" not in candidates

    @pytest.mark.asyncio
    async def test_classifier_none_falls_back_to_stickiness(self):
        bus = MessageBus(llm=object(), classifier_model="cheap-model")
        business, devops = _FakeAgent("business"), _FakeAgent("devops")
        bus.register(business)
        bus.register(devops)
        bus._chat_agent_map["chat1"] = "devops"

        with patch("core.bus.classify_agent", new=AsyncMock(return_value=None)):
            resolved = await bus._resolve_agent(_user_event("hello"))
            assert resolved is devops

    @pytest.mark.asyncio
    async def test_no_llm_skips_classifier_uses_first_registered(self):
        bus = MessageBus()  # no llm wired
        business, devops = _FakeAgent("business"), _FakeAgent("devops")
        bus.register(business)
        bus.register(devops)

        with patch(
            "core.bus.classify_agent", new=AsyncMock(return_value="devops")
        ) as mock_classify:
            resolved = await bus._resolve_agent(_user_event("hello"))
            assert resolved is business
            mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_user_message_event_skips_classifier(self):
        bus = MessageBus(llm=object(), classifier_model="cheap-model")
        business = _FakeAgent("business")
        bus.register(business)
        bus._chat_agent_map["chat1"] = "business"

        with patch(
            "core.bus.classify_agent", new=AsyncMock(return_value="business")
        ) as mock_classify:
            event = AgentEvent(
                type=EventType.HEARTBEAT_TICK, agent_name="", chat_id="chat1"
            )
            await bus._resolve_agent(event)
            mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_agents_returns_none(self):
        bus = MessageBus()
        resolved = await bus._resolve_agent(_user_event("hello"))
        assert resolved is None


class TestChatAgentMapStickiness:
    @pytest.mark.asyncio
    async def test_scheduled_task_does_not_overwrite_sticky_agent(self):
        state_store = AsyncMock()
        bus = MessageBus(state_store=state_store)
        business, devops = _FakeAgent("business"), _FakeAgent("devops")
        bus.register(business)
        bus.register(devops)

        # User was last conversing with "business" in chat1.
        await bus.publish(_user_event("hello", chat_id="chat1", agent_name="business"))
        assert bus._chat_agent_map["chat1"] == "business"

        # An autonomous scheduled task for devops fires against the same chat.
        scheduled_event = AgentEvent(
            type=EventType.SCHEDULED_TASK,
            agent_name="devops",
            chat_id="chat1",
        )
        await bus.publish(scheduled_event)

        # Stickiness must still point at "business", not "devops" — both the
        # in-memory map and the state-store persistence call.
        assert bus._chat_agent_map["chat1"] == "business"
        state_store.save_chat_agent.assert_awaited_once_with("chat1", "business")


class TestAgentLock:
    """Tests for the agent lock/persist feature."""

    @pytest.mark.asyncio
    async def test_lock_routes_to_locked_agent_over_classifier(self):
        bus = MessageBus(llm=object(), classifier_model="cheap-model")
        business, devops, projects = _FakeAgent("business"), _FakeAgent("devops"), _FakeAgent("projects")
        bus.register(business)
        bus.register(devops)
        bus.register(projects)

        # Lock to "projects"
        assert await bus.lock_chat_agent("chat1", "projects") is True

        # Even if classifier would pick "devops", the lock wins
        with patch(
            "core.bus.classify_agent", new=AsyncMock(return_value="devops")
        ) as mock_classify:
            resolved = await bus._resolve_agent(_user_event("tell me about tools"))
            assert resolved is projects
            # Classifier should not be consulted when lock is active
            mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_explicit_tag_still_wins_over_lock(self):
        bus = MessageBus(llm=object(), classifier_model="cheap-model")
        business, devops = _FakeAgent("business"), _FakeAgent("devops")
        bus.register(business)
        bus.register(devops)

        # Lock to "business"
        await bus.lock_chat_agent("chat1", "business")

        # Explicit @tag should still override the lock
        resolved = await bus._resolve_agent(
            _user_event("restart server", agent_name="devops")
        )
        assert resolved is devops

    @pytest.mark.asyncio
    async def test_unlock_returns_to_normal_routing(self):
        bus = MessageBus(llm=object(), classifier_model="cheap-model")
        business, devops = _FakeAgent("business"), _FakeAgent("devops")
        bus.register(business)
        bus.register(devops)

        # Lock then unlock
        await bus.lock_chat_agent("chat1", "business")
        assert bus.get_chat_agent_lock("chat1") == "business"

        await bus.unlock_chat_agent("chat1")
        assert bus.get_chat_agent_lock("chat1") is None

        # Now classifier should be used again
        with patch(
            "core.bus.classify_agent", new=AsyncMock(return_value="devops")
        ) as mock_classify:
            resolved = await bus._resolve_agent(_user_event("deploy stuff"))
            assert resolved is devops
            mock_classify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lock_to_nonexistent_agent_returns_false(self):
        bus = MessageBus()
        business = _FakeAgent("business")
        bus.register(business)

        assert await bus.lock_chat_agent("chat1", "ghost") is False

    @pytest.mark.asyncio
    async def test_lock_updates_stickiness_map(self):
        bus = MessageBus()
        business, devops = _FakeAgent("business"), _FakeAgent("devops")
        bus.register(business)
        bus.register(devops)

        # Pre-existing stickiness to devops
        bus._chat_agent_map["chat1"] = "devops"

        # Lock to business should update stickiness
        await bus.lock_chat_agent("chat1", "business")
        assert bus._chat_agent_map["chat1"] == "business"
        assert bus.get_chat_agent_lock("chat1") == "business"

    @pytest.mark.asyncio
    async def test_lock_persists_to_state_store(self):
        state_store = AsyncMock()
        bus = MessageBus(state_store=state_store)
        business = _FakeAgent("business")
        bus.register(business)

        await bus.lock_chat_agent("chat1", "business")
        state_store.save_chat_agent.assert_awaited_with("chat1", "business")

    @pytest.mark.asyncio
    async def test_get_chat_agent_lock_returns_none_when_agent_unregistered(self):
        bus = MessageBus()
        business = _FakeAgent("business")
        bus.register(business)

        # Lock to business
        await bus.lock_chat_agent("chat1", "business")
        assert bus.get_chat_agent_lock("chat1") == "business"

        # Simulate agent being removed (e.g., reconfiguration)
        del bus._agents["business"]
        # Lock should return None since agent no longer exists
        assert bus.get_chat_agent_lock("chat1") is None