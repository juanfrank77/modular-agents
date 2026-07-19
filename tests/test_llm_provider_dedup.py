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
    _OpenAICompatibleLLM,
)
from core.protocols import Message


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
