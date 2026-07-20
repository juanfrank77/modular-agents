"""
test_openai_provider.py
--------------------------
Tests for OpenAILLM — a direct OpenAI API key provider built on the shared
_OpenAICompatibleLLM base (same pattern as KiloLLM/OpenRouterLLM).

Run:
    python -m pytest tests/test_openai_provider.py -x -q
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.llm import OpenAILLM, _OpenAICompatibleLLM
from core.protocols import Message, ToolDef


def _openai_tool_call(id_: str, name: str, arguments: dict):
    return SimpleNamespace(
        id=id_,
        type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


class TestOpenAILLMConstruction:
    def test_shares_openai_compatible_base(self):
        assert issubclass(OpenAILLM, _OpenAICompatibleLLM)

    def test_supports_tools_is_true(self):
        llm = OpenAILLM(api_key="key")
        assert llm.supports_tools is True

    def test_no_extra_headers(self):
        llm = OpenAILLM(api_key="key")
        assert not llm._extra_headers

    def test_client_uses_default_openai_base_url(self):
        llm = OpenAILLM(api_key="key")
        # AsyncOpenAI defaults base_url to OpenAI's API when none is passed —
        # confirms OpenAILLM doesn't override it (unlike Kilo/OpenRouter).
        assert str(llm._client.base_url).rstrip("/") == "https://api.openai.com/v1"


class TestOpenAILLMToolCalling:
    @pytest.mark.asyncio
    async def test_complete_with_tools_returns_tool_call(self):
        llm = OpenAILLM(api_key="key")

        message = SimpleNamespace(
            content=None,
            tool_calls=[_openai_tool_call("call_1", "MERGE_PR", {"number": 42})],
        )
        mock_response = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
        llm._client.chat.completions.create = AsyncMock(return_value=mock_response)

        tool_defs = [
            ToolDef(
                name="MERGE_PR",
                description="Merge a PR.",
                parameters={"type": "object", "properties": {}, "required": []},
            )
        ]
        result = await llm.complete(
            messages=[Message(role="user", content="merge pr 42")],
            system="sys",
            tools=tool_defs,
        )

        assert result.tool_calls[0].name == "MERGE_PR"
        call_kwargs = llm._client.chat.completions.create.call_args.kwargs
        assert call_kwargs["parallel_tool_calls"] is False
        assert "extra_headers" not in call_kwargs

    @pytest.mark.asyncio
    async def test_summarize_delegates_to_complete(self):
        llm = OpenAILLM(api_key="key")
        llm.complete = AsyncMock(return_value=SimpleNamespace(text="summary"))
        result = await llm.summarize([Message(role="user", content="hi")])
        assert result == "summary"
