"""
test_protocols_tool_calling.py
-------------------------------
Tests for the new tool-calling data types in core/protocols.py.

Run:
    python -m pytest tests/test_protocols_tool_calling.py -x -q
"""

from __future__ import annotations

from core.protocols import LLMResult, ToolCall, ToolDef, ToolResultInput


class TestToolDef:
    def test_construction(self):
        td = ToolDef(
            name="MERGE_PR",
            description="Enable auto-merge for a GitHub pull request.",
            parameters={"type": "object", "properties": {}, "required": []},
        )
        assert td.name == "MERGE_PR"
        assert td.parameters["type"] == "object"


class TestToolCall:
    def test_construction(self):
        tc = ToolCall(id="call_1", name="MERGE_PR", args={"number": 42})
        assert tc.id == "call_1"
        assert tc.args["number"] == 42


class TestToolResultInput:
    def test_construction(self):
        tr = ToolResultInput(tool_call_id="call_1", content="✅ done")
        assert tr.tool_call_id == "call_1"
        assert tr.content == "✅ done"


class TestLLMResult:
    def test_defaults(self):
        result = LLMResult()
        assert result.text == ""
        assert result.tool_calls == []
        assert result.raw_assistant is None

    def test_text_only(self):
        result = LLMResult(text="hello")
        assert result.text == "hello"
        assert result.tool_calls == []

    def test_with_tool_calls(self):
        tc = ToolCall(id="call_1", name="MERGE_PR", args={"number": 42})
        result = LLMResult(tool_calls=[tc], raw_assistant={"raw": True})
        assert result.tool_calls == [tc]
        assert result.raw_assistant == {"raw": True}

    def test_tool_calls_default_is_not_shared(self):
        # Regression guard: mutable default must not leak between instances.
        LLMResult().tool_calls.append(ToolCall(id="x", name="Y", args={}))
        assert LLMResult().tool_calls == []
