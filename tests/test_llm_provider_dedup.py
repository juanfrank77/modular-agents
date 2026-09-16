"""
test_llm_provider_dedup.py
----------------------------
Tests that the refactored provider base classes preserve behavior:
- summarize() delegates to complete() with a fixed system prompt for all
  four providers (dedup target).
- KiloLLM and OpenRouterLLM share a common base class.
- OpenRouterLLM still sends its distinguishing extra_headers.

Run:
    python -m pytest tests/test_llm_provider_dedup.py -x -q
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.llm import (
    AnthropicLLM,
    KiloLLM,
    OllamaLLM,
    OpenRouterLLM,
    SummaryFailedError,
    _OpenAICompatibleLLM,
)
from core.protocols import Message, ToolDef


class TestSharedBase:
    def test_kilo_and_openrouter_share_openai_compatible_base(self):
        assert issubclass(KiloLLM, _OpenAICompatibleLLM)
        assert issubclass(OpenRouterLLM, _OpenAICompatibleLLM)

    def test_openrouter_sends_distinguishing_headers(self):
        llm = OpenRouterLLM(api_key="key")
        assert llm._extra_headers == {
            "HTTP-Referer": "https://github.com/juanfrank77/modular-agents",
            "X-Title": "Modular Agents",
        }

    def test_kilo_has_no_extra_headers(self):
        llm = KiloLLM(api_key="key")
        assert not llm._extra_headers


class TestSummarizeDedup:
    @pytest.mark.asyncio
    async def test_kilo_summarize_delegates_to_complete(self):
        llm = KiloLLM(api_key="key")
        llm.complete = AsyncMock(return_value=SimpleNamespace(text="summary"))
        result = await llm.summarize([Message(role="user", content="hi")])
        assert result == "summary"
        llm.complete.assert_awaited_once()
        _, kwargs = llm.complete.call_args
        assert kwargs["max_tokens"] == 512
        assert "summar" in kwargs["system"].lower()

    @pytest.mark.asyncio
    async def test_openrouter_summarize_delegates_to_complete(self):
        llm = OpenRouterLLM(api_key="key")
        llm.complete = AsyncMock(return_value=SimpleNamespace(text="summary"))
        result = await llm.summarize([Message(role="user", content="hi")])
        assert result == "summary"

    @pytest.mark.asyncio
    async def test_anthropic_summarize_delegates_to_complete(self):
        with patch("core.llm.AsyncAnthropic"):
            llm = AnthropicLLM(api_key="key")
        llm.complete = AsyncMock(return_value=SimpleNamespace(text="summary"))
        result = await llm.summarize([Message(role="user", content="hi")])
        assert result == "summary"

    @pytest.mark.asyncio
    async def test_ollama_summarize_delegates_to_complete(self):
        llm = OllamaLLM(base_url="http://localhost:11434")
        llm.complete = AsyncMock(return_value=SimpleNamespace(text="summary"))
        result = await llm.summarize([Message(role="user", content="hi")])
        assert result == "summary"
        await llm.close()

    @pytest.mark.asyncio
    async def test_summarize_retries_on_empty_text(self, monkeypatch):
        monkeypatch.setattr("core.llm.asyncio.sleep", AsyncMock())
        llm = KiloLLM(api_key="key")
        llm.complete = AsyncMock(side_effect=[
            SimpleNamespace(text=""),
            SimpleNamespace(text=""),
            SimpleNamespace(text="summary"),
        ])
        result = await llm.summarize([Message(role="user", content="hi")])
        assert result == "summary"
        assert llm.complete.await_count == 3

    @pytest.mark.asyncio
    async def test_summarize_fails_loud_after_empty_retries(self, monkeypatch):
        monkeypatch.setattr("core.llm.asyncio.sleep", AsyncMock())
        llm = KiloLLM(api_key="key")
        llm.complete = AsyncMock(return_value=SimpleNamespace(text=""))
        with pytest.raises(SummaryFailedError):
            await llm.summarize([Message(role="user", content="hi")])
        assert llm.complete.await_count == 3

    @pytest.mark.asyncio
    async def test_summarize_includes_tool_descriptions_in_system_prompt(self):
        llm = KiloLLM(api_key="key")
        llm.complete = AsyncMock(return_value=SimpleNamespace(text="summary"))
        tools = [
            ToolDef(
                name="LIST_ISSUES",
                description="List GitHub issues for a repository.",
                parameters={},
            ),
            ToolDef(
                name="GET_STATUS",
                description="Check Railway deployment status.",
                parameters={},
            ),
        ]
        result = await llm.summarize(
            [Message(role="user", content="hi")], tools=tools
        )
        assert result == "summary"
        llm.complete.assert_awaited_once()
        _, kwargs = llm.complete.call_args
        assert "LIST_ISSUES" in kwargs["system"]
        assert "GET_STATUS" in kwargs["system"]
        assert "GitHub issues" in kwargs["system"]
        assert "Railway deployment status" in kwargs["system"]
