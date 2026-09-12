"""
tests/test_base_reply_thinking_tags.py
---------------------------------------
Regression tests for thinking-tag leakage: models that leak chain-of-
thought tags (`<think>`, `<mm:think>`, `<think>...</think>` blocks, etc.)
into their final text must have those stripped before the text is saved
to session history or delivered to the user. Found live: minimax-m3
responses stored raw `</mm:think>` markers into messages and Telegram.

Run:
    python -m pytest tests/test_base_reply_thinking_tags.py -x -q
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.base import BaseAgent
from core.protocols import AgentEvent, EventType


def _make_agent() -> BaseAgent:
    class _StubAgent(BaseAgent):
        async def handle(self, event):
            return None

        async def health_check(self):
            return True

    agent = _StubAgent.__new__(_StubAgent)  # skip __init__ wiring
    agent.name = "test"
    agent.emoji = "🧪"
    agent.notifier = MagicMock()
    agent.notifier.send = AsyncMock()
    return agent


def _event() -> AgentEvent:
    return AgentEvent(
        type=EventType.USER_MESSAGE,
        agent_name="test",
        chat_id="1",
        text="hi",
    )


class TestThinkingTagStripping:
    def test_leading_closed_tag_is_removed(self):
        agent = _make_agent()
        asyncio.run(
            agent.reply(_event(), "</mm:think>Good. Reconciled:\n\nDone.")
        )
        sent = agent.notifier.send.call_args.args[1]
        assert "</mm:think>" not in sent
        assert "Good" in sent

    def test_closed_open_block_is_removed(self):
        agent = _make_agent()
        asyncio.run(
            agent.reply(_event(), "<think>secret reasoning</think>Answer here.")
        )
        sent = agent.notifier.send.call_args.args[1]
        assert "<think>" not in sent
        assert "secret reasoning" not in sent
        assert "Answer here." in sent

    def test_agent_response_text_is_also_clean(self):
        agent = _make_agent()
        response = asyncio.run(
            agent.reply(_event(), "</mm:think>Clean answer.")
        )
        assert "</mm:think>" not in response.text
        assert response.text == "Clean answer."

    def test_text_without_tags_unchanged(self):
        agent = _make_agent()
        response = asyncio.run(agent.reply(_event(), "Plain reply."))
        assert response.text == "Plain reply."

    def test_header_is_still_prepended(self):
        agent = _make_agent()
        response = asyncio.run(agent.reply(_event(), "</mm:think>Hello"))
        assert agent.notifier.send.call_args.args[1].startswith("**🧪 test**")

    def test_only_leading_and_wrapped_tags_removed(self):
        """A tag mentioned mid-sentence as literal content is left alone."""
        agent = _make_agent()
        response = asyncio.run(
            agent.reply(_event(), "The tag </mm:think> leaked earlier.")
        )
        assert response.text == "The tag </mm:think> leaked earlier."