"""
test_devops_actions.py
------------------------
Tests for agents/devops/actions.py — the ActionSpec registry that maps
approved ACTION: lines to real DevOpsTools calls.

Run:
    python -m pytest tests/test_devops_actions.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.devops.actions import ACTIONS, MissingRequiredArg, resolve_args
from agents.devops.tools import DevOpsTools
from agents.devops.tools.cli_runner import ToolError


def _fake_tools(**overrides) -> DevOpsTools:
    tools = DevOpsTools(github=AsyncMock(), railway=AsyncMock())
    for attr, value in overrides.items():
        target, method = attr.split(".")
        setattr(getattr(tools, target), method, value)
    return tools


class TestResolveArgs:
    def test_merges_defaults_under_parsed_args(self):
        spec = ACTIONS["MERGE_PR"]
        resolved = resolve_args(spec, {"number": "42", "repo": "org/x"})
        assert resolved == {"number": "42", "repo": "org/x", "method": "rebase"}

    def test_parsed_value_overrides_default(self):
        spec = ACTIONS["MERGE_PR"]
        resolved = resolve_args(spec, {"number": "42", "repo": "org/x", "method": "squash"})
        assert resolved["method"] == "squash"

    def test_missing_required_arg_raises(self):
        spec = ACTIONS["MERGE_PR"]
        with pytest.raises(MissingRequiredArg) as exc_info:
            resolve_args(spec, {"repo": "org/x"})
        assert "number" in str(exc_info.value)


class TestMergePr:
    def test_describe_reflects_resolved_default(self):
        spec = ACTIONS["MERGE_PR"]
        resolved = resolve_args(spec, {"number": "42", "repo": "org/x"})
        assert spec.describe(resolved) == "Merge PR #42 in org/x (rebase)"

    @pytest.mark.asyncio
    async def test_execute_calls_github_merge_pr(self):
        spec = ACTIONS["MERGE_PR"]
        tools = _fake_tools()
        tools.github.merge_pr = AsyncMock(
            return_value={"repo": "org/x", "number": 42, "merged": True, "output": ""}
        )
        result = await spec.execute(tools, {"number": "42", "repo": "org/x", "method": "rebase"})
        tools.github.merge_pr.assert_called_once_with(number=42, repo="org/x", method="rebase")
        assert result == "✅ Auto-merge enabled for PR #42 in org/x (rebase)"

    @pytest.mark.asyncio
    async def test_execute_propagates_tool_error(self):
        spec = ACTIONS["MERGE_PR"]
        tools = _fake_tools()
        tools.github.merge_pr = AsyncMock(
            side_effect=ToolError("github", ["gh", "pr", "merge"], "not mergeable", 1)
        )
        with pytest.raises(ToolError):
            await spec.execute(tools, {"number": "42", "repo": "org/x", "method": "rebase"})


class TestCreateIssue:
    def test_describe(self):
        spec = ACTIONS["CREATE_ISSUE"]
        resolved = resolve_args(spec, {"repo": "org/x", "title": "Flaky CI"})
        assert spec.describe(resolved) == "Create issue in org/x: Flaky CI"

    @pytest.mark.asyncio
    async def test_execute_calls_github_create_issue(self):
        spec = ACTIONS["CREATE_ISSUE"]
        tools = _fake_tools()
        tools.github.create_issue = AsyncMock(
            return_value={"repo": "org/x", "title": "Flaky CI", "url": "https://github.com/org/x/issues/9"}
        )
        resolved = resolve_args(spec, {"repo": "org/x", "title": "Flaky CI"})
        result = await spec.execute(tools, resolved)
        tools.github.create_issue.assert_called_once_with(repo="org/x", title="Flaky CI", body="")
        assert result == "✅ Created issue in org/x: Flaky CI → https://github.com/org/x/issues/9"


class TestDeployProd:
    def test_describe_with_service(self):
        spec = ACTIONS["DEPLOY_PROD"]
        resolved = resolve_args(spec, {"service": "api"})
        assert spec.describe(resolved) == "Deploy api → production"

    def test_describe_without_service(self):
        spec = ACTIONS["DEPLOY_PROD"]
        resolved = resolve_args(spec, {})
        assert spec.describe(resolved) == "Deploy default service → production"

    @pytest.mark.asyncio
    async def test_execute_calls_railway_deploy(self):
        spec = ACTIONS["DEPLOY_PROD"]
        tools = _fake_tools()
        tools.railway.deploy = AsyncMock(
            return_value={"service": "api", "environment": "production", "triggered": True, "detached": True, "output": ""}
        )
        resolved = resolve_args(spec, {"service": "api"})
        result = await spec.execute(tools, resolved)
        tools.railway.deploy.assert_called_once_with(service="api", environment="production")
        assert result == "✅ Deploy triggered: api → production"


class TestDeployStaging:
    @pytest.mark.asyncio
    async def test_execute_calls_railway_deploy_with_staging(self):
        spec = ACTIONS["DEPLOY_STAGING"]
        tools = _fake_tools()
        tools.railway.deploy = AsyncMock(
            return_value={"service": "api", "environment": "staging", "triggered": True, "detached": True, "output": ""}
        )
        resolved = resolve_args(spec, {"service": "api"})
        result = await spec.execute(tools, resolved)
        tools.railway.deploy.assert_called_once_with(service="api", environment="staging")
        assert result == "✅ Deploy triggered: api → staging"


class TestDbRollback:
    def test_describe(self):
        spec = ACTIONS["DB_ROLLBACK"]
        resolved = resolve_args(spec, {"deployment_id": "abc123", "service": "api"})
        assert spec.describe(resolved) == "Roll back api to abc123"

    @pytest.mark.asyncio
    async def test_execute_calls_railway_rollback(self):
        spec = ACTIONS["DB_ROLLBACK"]
        tools = _fake_tools()
        tools.railway.rollback = AsyncMock(
            return_value={"deployment_id": "abc123", "service": "api", "environment": "production", "rolled_back": True, "output": ""}
        )
        resolved = resolve_args(spec, {"deployment_id": "abc123", "service": "api", "environment": "production"})
        result = await spec.execute(tools, resolved)
        tools.railway.rollback.assert_called_once_with(
            deployment_id="abc123", service="api", environment="production"
        )
        assert result == "✅ Rolled back api to abc123"

    def test_missing_deployment_id_raises(self):
        spec = ACTIONS["DB_ROLLBACK"]
        with pytest.raises(MissingRequiredArg):
            resolve_args(spec, {"service": "api"})


class TestActionSpecHasToolSchema:
    def test_every_action_has_schema_and_description(self):
        for name, spec in ACTIONS.items():
            assert spec.description, f"{name} missing description"
            assert isinstance(spec.schema, dict), f"{name} missing schema dict"
            for key in spec.required:
                assert key in spec.schema, f"{name} required key '{key}' missing from schema"

    def test_merge_pr_schema_shape(self):
        spec = ACTIONS["MERGE_PR"]
        assert spec.schema["number"]["type"] == "integer"
        assert spec.schema["repo"]["type"] == "string"
        assert spec.schema["method"]["enum"] == ["merge", "squash", "rebase"]