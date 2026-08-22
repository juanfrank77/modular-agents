"""
test_base_agent_reply.py
---------------------------
Tests for BaseAgent.reply()'s prominent "**🤖 agent**" formatting (item 6, "which agent am
I talking to") and BaseAgent.resolve_model()'s chat-override precedence
(item 6, "/model mutates the global default for everyone").

Run:
    python -m pytest tests/test_base_agent_reply.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.echo.agent import EchoAgent
from core.protocols import AgentEvent, EventType


def _make_agent(bus=None, model=""):
    settings = MagicMock()
    settings.echo_agent_model = model
    settings.telegram_allowed_chat_ids = []
    notifier = MagicMock()
    notifier.send = AsyncMock()
    return EchoAgent(
        settings=settings,
        storage=MagicMock(),
        notifier=notifier,
        bus=bus,
    )


class TestReplyPrefix:
    @pytest.mark.asyncio
    async def test_notifier_receives_prominent_agent_formatting(self):
        agent = _make_agent()
        event = AgentEvent(type=EventType.USER_MESSAGE, agent_name="echo", chat_id="123", text="hi")

        await agent.reply(event, "hello there")

        # New format: **🤖 Agent Name**\n\nresponse text
        agent.notifier.send.assert_awaited_once_with("123", "**🤖 echo**\n\nhello there")

    @pytest.mark.asyncio
    async def test_returned_response_text_is_unprefixed(self):
        agent = _make_agent()
        event = AgentEvent(type=EventType.USER_MESSAGE, agent_name="echo", chat_id="123", text="hi")

        response = await agent.reply(event, "hello there")

        assert response.text == "hello there"
        assert response.agent_name == "echo"


class TestResolveModel:
    def test_no_bus_falls_back_to_agent_model(self):
        agent = _make_agent(model="claude-haiku-4.6")
        assert agent.resolve_model("123") == "claude-haiku-4.6"

    def test_bus_with_no_override_falls_back_to_agent_model(self):
        bus = MagicMock()
        bus.get_chat_model.return_value = ""
        agent = _make_agent(bus=bus, model="claude-haiku-4.6")
        assert agent.resolve_model("123") == "claude-haiku-4.6"

    def test_chat_override_wins_over_agent_model(self):
        bus = MagicMock()
        bus.get_chat_model.return_value = "claude-opus-4.6"
        agent = _make_agent(bus=bus, model="claude-haiku-4.6")
        assert agent.resolve_model("123") == "claude-opus-4.6"

    def test_no_override_and_no_agent_model_returns_empty_string(self):
        agent = _make_agent(model="")
        assert agent.resolve_model("123") == ""
