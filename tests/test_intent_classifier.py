"""
test_intent_classifier.py
---------------------------
Tests for core/intent_classifier.py — the cheap LLM-based agent router
used by MessageBus for untagged user messages.

Run:
    python -m pytest tests/test_intent_classifier.py -x -q
"""

from __future__ import annotations

import pytest

from core.intent_classifier import classify_agent


class _FakeLLM:
    def __init__(self, reply: str = "", raise_exc: Exception | None = None):
        self._reply = reply
        self._raise_exc = raise_exc
        self.calls: list[tuple] = []

    async def complete(self, messages, system, model="", max_tokens=1024):
        self.calls.append((messages, system, model, max_tokens))
        if self._raise_exc:
            raise self._raise_exc
        return self._reply

    async def summarize(self, messages):
        return ""


_AGENTS = {
    "business": "Handles calendar, email, task management.",
    "devops": "Handles GitHub, deployments, infrastructure.",
}


class TestClassifyAgent:
    @pytest.mark.asyncio
    async def test_exact_match(self):
        llm = _FakeLLM(reply="devops")
        result = await classify_agent(
            "restart the prod server", _AGENTS, llm, "cheap-model"
        )
        assert result == "devops"

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self):
        llm = _FakeLLM(reply="Business")
        result = await classify_agent(
            "what's on my calendar today", _AGENTS, llm, "cheap-model"
        )
        assert result == "business"

    @pytest.mark.asyncio
    async def test_reply_wrapped_in_extra_text(self):
        llm = _FakeLLM(reply="Agent: devops.")
        result = await classify_agent("deploy to prod", _AGENTS, llm, "cheap-model")
        assert result == "devops"

    @pytest.mark.asyncio
    async def test_unmatched_reply_returns_none(self):
        llm = _FakeLLM(reply="librarian")
        result = await classify_agent("some message", _AGENTS, llm, "cheap-model")
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self):
        llm = _FakeLLM(raise_exc=RuntimeError("timeout"))
        result = await classify_agent("some message", _AGENTS, llm, "cheap-model")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_agents_returns_none_without_calling_llm(self):
        llm = _FakeLLM(reply="devops")
        result = await classify_agent("some message", {}, llm, "cheap-model")
        assert result is None
        assert llm.calls == []

    @pytest.mark.asyncio
    async def test_passes_model_and_text_through(self):
        llm = _FakeLLM(reply="devops")
        await classify_agent("deploy now", _AGENTS, llm, "haiku-cheap")
        messages, system, model, max_tokens = llm.calls[0]
        assert model == "haiku-cheap"
        assert messages[0].content == "deploy now"
        assert "business" in system
        assert "devops" in system