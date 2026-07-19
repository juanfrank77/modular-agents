"""
test_llm_tool_calling.py
--------------------------
Tests for structured tool-calling support in core/llm.py providers.

Run:
    python -m pytest tests/test_llm_tool_calling.py -x -q
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.llm import AnthropicLLM
from core.protocols import Message, ToolDef, ToolResultInput


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(id_: str, name: str, input_: dict):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


class TestAnthropicToolCalling:
    @pytest.mark.asyncio
    async def test_supports_tools_is_true(self):
        with patch("core.llm.AsyncAnthropic"):
            llm = AnthropicLLM(api_key="key")
        assert llm.supports_tools is True

    @pytest.mark.asyncio
    async def test_complete_without_tools_returns_text_only(self):
        with patch("core.llm.AsyncAnthropic") as mock_client_cls:
            llm = AnthropicLLM(api_key="key")
            mock_response = SimpleNamespace(
                content=[_text_block("Hello there")],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )
            llm._client.messages.create = AsyncMock(return_value=mock_response)

            result = await llm.complete(
                messages=[Message(role="user", content="hi")],
                system="You are helpful.",
            )

        assert result.text == "Hello there"
        assert result.tool_calls == []
        call_kwargs = llm._client.messages.create.call_args.kwargs
        assert "tools" not in call_kwargs

    @pytest.mark.asyncio
    async def test_complete_with_tools_returns_tool_call(self):
        with patch("core.llm.AsyncAnthropic"):
            llm = AnthropicLLM(api_key="key")
            mock_response = SimpleNamespace(
                content=[_tool_use_block("call_1", "MERGE_PR", {"number": 42, "repo": "org/x"})],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )
            llm._client.messages.create = AsyncMock(return_value=mock_response)

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

        assert result.text == ""
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].id == "call_1"
        assert result.tool_calls[0].name == "MERGE_PR"
        assert result.tool_calls[0].args == {"number": 42, "repo": "org/x"}
        assert result.raw_assistant == [mock_response.content[0]]

        call_kwargs = llm._client.messages.create.call_args.kwargs
        assert call_kwargs["tools"] == [
            {"name": "MERGE_PR", "description": "Merge a PR.",
             "input_schema": {"type": "object", "properties": {}, "required": []}}
        ]

    @pytest.mark.asyncio
    async def test_complete_with_tool_result_appends_continuation_messages(self):
        with patch("core.llm.AsyncAnthropic"):
            llm = AnthropicLLM(api_key="key")
            mock_response = SimpleNamespace(
                content=[_text_block("Done, merged it.")],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            )
            llm._client.messages.create = AsyncMock(return_value=mock_response)

            raw_assistant = [_tool_use_block("call_1", "MERGE_PR", {"number": 42})]
            result = await llm.complete(
                messages=[Message(role="user", content="merge pr 42")],
                system="sys",
                tool_result=ToolResultInput(tool_call_id="call_1", content="✅ merged"),
                raw_assistant=raw_assistant,
            )

        assert result.text == "Done, merged it."
        call_kwargs = llm._client.messages.create.call_args.kwargs
        sent_messages = call_kwargs["messages"]
        assert sent_messages[-2] == {"role": "assistant", "content": raw_assistant}
        assert sent_messages[-1] == {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": "✅ merged",
            }],
        }
        assert "tools" not in call_kwargs
