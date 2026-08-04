"""
tests/test_projects_actions.py
------------------------------
Tests for agents/projects/actions.py — the ActionSpec registry for the
Projects agent's web search and local file tools.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.projects.actions import ACTIONS, MissingRequiredArg, resolve_args
from agents.projects.tools import ProjectsTools


def _fake_tools(**overrides) -> ProjectsTools:
    tools = ProjectsTools(web=AsyncMock(), local_file=AsyncMock())
    for attr, value in overrides.items():
        target, method = attr.split(".")
        setattr(getattr(tools, target), method, value)
    return tools


class TestResolveArgs:
    def test_merges_defaults_under_parsed_args(self):
        spec = ACTIONS["WEB_SEARCH"]
        resolved = resolve_args(spec, {"query": "asyncio"})
        assert resolved == {"query": "asyncio", "max_results": "5"}

    def test_missing_required_arg_raises(self):
        spec = ACTIONS["WEB_SEARCH"]
        with pytest.raises(MissingRequiredArg) as exc_info:
            resolve_args(spec, {"max_results": "3"})
        assert "query" in str(exc_info.value)


class TestWebSearch:
    def test_describe(self):
        spec = ACTIONS["WEB_SEARCH"]
        resolved = resolve_args(spec, {"query": "asyncio"})
        assert spec.describe(resolved) == "Web search: asyncio"

    @pytest.mark.asyncio
    async def test_execute_calls_web_search(self):
        spec = ACTIONS["WEB_SEARCH"]
        tools = _fake_tools()
        tools.web.search = AsyncMock(return_value=[
            {"title": "Docs", "url": "https://x", "content": "Guide"}
        ])
        resolved = resolve_args(spec, {"query": "asyncio"})
        result = await spec.execute(tools, resolved)

        tools.web.search.assert_called_once_with("asyncio", max_results=5)
        assert "Docs" in result
        assert "https://x" in result

    @pytest.mark.asyncio
    async def test_execute_no_results(self):
        spec = ACTIONS["WEB_SEARCH"]
        tools = _fake_tools()
        tools.web.search = AsyncMock(return_value=[])
        resolved = resolve_args(spec, {"query": "asyncio"})
        result = await spec.execute(tools, resolved)

        assert "No web results found" in result


class TestReadLocalFile:
    def test_describe(self):
        spec = ACTIONS["READ_LOCAL_FILE"]
        resolved = resolve_args(spec, {"path": "notes/project.md"})
        assert spec.describe(resolved) == "Read local file notes/project.md"

    @pytest.mark.asyncio
    async def test_execute_calls_local_file_read(self):
        spec = ACTIONS["READ_LOCAL_FILE"]
        tools = _fake_tools()
        tools.local_file.read_file = AsyncMock(
            return_value={"path": "/notes/project.md", "content": "notes"}
        )
        resolved = resolve_args(spec, {"path": "notes/project.md"})
        result = await spec.execute(tools, resolved)

        tools.local_file.read_file.assert_called_once_with("notes/project.md")
        assert "notes" in result

    @pytest.mark.asyncio
    async def test_execute_reports_error(self):
        spec = ACTIONS["READ_LOCAL_FILE"]
        tools = _fake_tools()
        tools.local_file.read_file = AsyncMock(
            return_value={"path": "notes/project.md", "error": "Access denied"}
        )
        resolved = resolve_args(spec, {"path": "notes/project.md"})
        result = await spec.execute(tools, resolved)

        assert "Could not read" in result
        assert "Access denied" in result


class TestActionSpecHasToolSchema:
    def test_every_action_has_schema_and_description(self):
        for name, spec in ACTIONS.items():
            assert spec.description, f"{name} missing description"
            assert isinstance(spec.schema, dict), f"{name} missing schema dict"
            for key in spec.required:
                assert key in spec.schema, f"{name} required key '{key}' missing from schema"
