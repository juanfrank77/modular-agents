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