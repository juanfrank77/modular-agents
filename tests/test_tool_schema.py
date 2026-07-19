"""
test_tool_schema.py
--------------------
Tests for core/tool_schema.py — builds ToolDef entries (for LLM tool-calling)
from an ActionSpec-shaped registry.

Run:
    python -m pytest tests/test_tool_schema.py -x -q
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.tool_schema import build_tool_defs


@dataclass
class _FakeActionSpec:
    """Minimal stand-in matching the fields build_tool_defs reads off ActionSpec."""
    required: list[str]
    defaults: dict[str, str]
    schema: dict[str, dict]
    description: str
    describe: Callable[[dict], str] = lambda a: ""
    execute: Callable[..., Any] = None


class TestBuildToolDefs:
    def test_builds_one_tool_def_per_action(self):
        actions = {
            "MERGE_PR": _FakeActionSpec(
                required=["number", "repo"],
                defaults={"method": "rebase"},
                schema={
                    "number": {"type": "integer", "description": "PR number"},
                    "repo": {"type": "string", "description": "owner/repo"},
                    "method": {"type": "string", "enum": ["merge", "squash", "rebase"]},
                },
                description="Enable auto-merge for a GitHub pull request.",
            ),
            "CREATE_ISSUE": _FakeActionSpec(
                required=["repo", "title"],
                defaults={"body": ""},
                schema={
                    "repo": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                },
                description="Create a GitHub issue.",
            ),
        }

        tool_defs = build_tool_defs(actions)

        assert len(tool_defs) == 2
        by_name = {td.name: td for td in tool_defs}

        merge = by_name["MERGE_PR"]
        assert merge.description == "Enable auto-merge for a GitHub pull request."
        assert merge.parameters["type"] == "object"
        assert merge.parameters["required"] == ["number", "repo"]
        assert merge.parameters["properties"]["number"] == {
            "type": "integer", "description": "PR number"
        }
        assert merge.parameters["properties"]["method"]["enum"] == [
            "merge", "squash", "rebase"
        ]

        issue = by_name["CREATE_ISSUE"]
        assert issue.parameters["required"] == ["repo", "title"]

    def test_empty_registry_returns_empty_list(self):
        assert build_tool_defs({}) == []
