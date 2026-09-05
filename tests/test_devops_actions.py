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
    tools = DevOpsTools(github=AsyncMock(), railway=AsyncMock(), local_file=AsyncMock())
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


class TestListIssues:
    def test_describe_all_repos(self):
        spec = ACTIONS["LIST_ISSUES"]
        resolved = resolve_args(spec, {})
        assert spec.describe(resolved) == "List open issues in all project repos"

    def test_describe_specific_repo(self):
        spec = ACTIONS["LIST_ISSUES"]
        resolved = resolve_args(spec, {"repo": "org/x"})
        assert spec.describe(resolved) == "List open issues in org/x"

    def test_describe_with_label(self):
        spec = ACTIONS["LIST_ISSUES"]
        resolved = resolve_args(spec, {"repo": "org/x", "label": "bug"})
        assert spec.describe(resolved) == "List open issues in org/x with label bug"

    @pytest.mark.asyncio
    async def test_execute_calls_github_list_issues(self):
        spec = ACTIONS["LIST_ISSUES"]
        tools = _fake_tools()
        tools.github.list_issues = AsyncMock(return_value=[
            {"repo": "org/x", "number": 3, "title": "Flaky CI", "state": "OPEN", "url": "https://github.com/org/x/issues/3"}
        ])
        resolved = resolve_args(spec, {"repo": "org/x"})
        result = await spec.execute(tools, resolved)
        tools.github.list_issues.assert_called_once_with(repo="org/x", state="open", label=None, limit=20)
        assert "Flaky CI" in result
        assert "https://github.com/org/x/issues/3" in result

    @pytest.mark.asyncio
    async def test_execute_omitted_repo_becomes_none(self):
        spec = ACTIONS["LIST_ISSUES"]
        tools = _fake_tools()
        tools.github.list_issues = AsyncMock(return_value=[])
        resolved = resolve_args(spec, {})
        await spec.execute(tools, resolved)
        tools.github.list_issues.assert_called_once_with(repo=None, state="open", label=None, limit=20)

    @pytest.mark.asyncio
    async def test_execute_shows_error_entries(self):
        spec = ACTIONS["LIST_ISSUES"]
        tools = _fake_tools()
        tools.github.list_issues = AsyncMock(return_value=[{"repo": "org/bad", "error": "boom"}])
        resolved = resolve_args(spec, {})
        result = await spec.execute(tools, resolved)
        assert "error: boom" in result


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


class TestGetStatus:
    def test_describe_with_service(self):
        spec = ACTIONS["GET_STATUS"]
        resolved = resolve_args(spec, {"service": "api", "environment": "production"})
        assert spec.describe(resolved) == "Get Railway status for api (production)"

    def test_describe_without_service(self):
        spec = ACTIONS["GET_STATUS"]
        resolved = resolve_args(spec, {})
        assert spec.describe(resolved) == "Get Railway status for default service (default environment)"

    @pytest.mark.asyncio
    async def test_execute_calls_railway_get_status(self):
        spec = ACTIONS["GET_STATUS"]
        tools = _fake_tools()
        tools.railway.get_status = AsyncMock(return_value={"status": "ACTIVE", "service": "api", "environment": "production"})
        resolved = resolve_args(spec, {"service": "api", "environment": "production"})
        result = await spec.execute(tools, resolved)
        tools.railway.get_status.assert_called_once_with(service="api", environment="production")
        assert result == "✅ Railway status: ACTIVE (api / production)"

    @pytest.mark.asyncio
    async def test_execute_propagates_tool_error(self):
        spec = ACTIONS["GET_STATUS"]
        tools = _fake_tools()
        tools.railway.get_status = AsyncMock(side_effect=ToolError("railway", ["railway", "status"], "not authenticated", 1))
        with pytest.raises(ToolError):
            await spec.execute(tools, {"service": "api", "environment": "production"})


class TestFetchLogs:
    def test_describe(self):
        spec = ACTIONS["FETCH_LOGS"]
        resolved = resolve_args(spec, {"service": "api", "lines": "50"})
        assert spec.describe(resolved) == "Fetch 50 logs for api (default environment)"

    @pytest.mark.asyncio
    async def test_execute_calls_railway_get_logs(self):
        spec = ACTIONS["FETCH_LOGS"]
        tools = _fake_tools()
        tools.railway.get_logs = AsyncMock(return_value="log line 1\nlog line 2")
        resolved = resolve_args(spec, {"service": "api", "environment": "production", "lines": "50"})
        result = await spec.execute(tools, resolved)
        tools.railway.get_logs.assert_called_once_with(service="api", environment="production", lines=50)
        assert result == "log line 1\nlog line 2"

    @pytest.mark.asyncio
    async def test_execute_propagates_tool_error(self):
        spec = ACTIONS["FETCH_LOGS"]
        tools = _fake_tools()
        tools.railway.get_logs = AsyncMock(side_effect=ToolError("railway", ["railway", "logs"], "service not found", 1))
        with pytest.raises(ToolError):
            await spec.execute(tools, {"service": "api", "environment": "production"})


class TestFetchErrorLogs:
    def test_describe(self):
        spec = ACTIONS["FETCH_ERROR_LOGS"]
        resolved = resolve_args(spec, {"service": "api", "lines": "25"})
        assert spec.describe(resolved) == "Fetch 25 error logs for api (default environment)"

    @pytest.mark.asyncio
    async def test_execute_calls_railway_get_error_logs(self):
        spec = ACTIONS["FETCH_ERROR_LOGS"]
        tools = _fake_tools()
        tools.railway.get_error_logs = AsyncMock(return_value="ERROR something failed\nTraceback ...")
        resolved = resolve_args(spec, {"service": "api", "environment": "production", "lines": "25"})
        result = await spec.execute(tools, resolved)
        tools.railway.get_error_logs.assert_called_once_with(service="api", environment="production", lines=25)
        assert result == "ERROR something failed\nTraceback ..."

    @pytest.mark.asyncio
    async def test_execute_propagates_tool_error(self):
        spec = ACTIONS["FETCH_ERROR_LOGS"]
        tools = _fake_tools()
        tools.railway.get_error_logs = AsyncMock(side_effect=ToolError("railway", ["railway", "logs"], "service not found", 1))
        with pytest.raises(ToolError):
            await spec.execute(tools, {"service": "api", "environment": "production"})


class TestListDeployments:
    def test_describe(self):
        spec = ACTIONS["LIST_DEPLOYMENTS"]
        resolved = resolve_args(spec, {"service": "api", "limit": "5"})
        assert spec.describe(resolved) == "List deployments for api (default environment)"

    @pytest.mark.asyncio
    async def test_execute_calls_railway_list_deployments(self):
        spec = ACTIONS["LIST_DEPLOYMENTS"]
        tools = _fake_tools()
        tools.railway.list_deployments = AsyncMock(return_value=[
            {"id": "dep-1", "status": "SUCCESS", "created_at": "2026-01-01 12:00"},
            {"id": "dep-2", "status": "FAILED", "created_at": "2026-01-01 12:05"},
        ])
        resolved = resolve_args(spec, {"service": "api", "environment": "production", "limit": "5"})
        result = await spec.execute(tools, resolved)
        tools.railway.list_deployments.assert_called_once_with(service="api", environment="production", limit=5)
        assert "dep-1" in result
        assert "dep-2" in result

    @pytest.mark.asyncio
    async def test_execute_handles_error_dict(self):
        spec = ACTIONS["LIST_DEPLOYMENTS"]
        tools = _fake_tools()
        tools.railway.list_deployments = AsyncMock(return_value=[{"error": "CLI not installed"}])
        resolved = resolve_args(spec, {"service": "api", "environment": "production"})
        result = await spec.execute(tools, resolved)
        assert "Could not list deployments" in result
        assert "CLI not installed" in result


class TestListEnvVars:
    def test_describe(self):
        spec = ACTIONS["LIST_ENV_VARS"]
        resolved = resolve_args(spec, {"service": "api", "environment": "production"})
        assert spec.describe(resolved) == "List env vars for api (production)"

    @pytest.mark.asyncio
    async def test_execute_calls_railway_list_env_vars(self):
        spec = ACTIONS["LIST_ENV_VARS"]
        tools = _fake_tools()
        tools.railway.list_env_vars = AsyncMock(return_value={"DATABASE_URL": "[set]", "API_KEY": "[set]"})
        resolved = resolve_args(spec, {"service": "api", "environment": "production"})
        result = await spec.execute(tools, resolved)
        tools.railway.list_env_vars.assert_called_once_with(service="api", environment="production")
        assert result == "✅ Environment variables (2 keys): DATABASE_URL, API_KEY"

    @pytest.mark.asyncio
    async def test_execute_propagates_tool_error(self):
        spec = ACTIONS["LIST_ENV_VARS"]
        tools = _fake_tools()
        tools.railway.list_env_vars = AsyncMock(side_effect=ToolError("railway", ["railway", "variables"], "not authenticated", 1))
        with pytest.raises(ToolError):
            await spec.execute(tools, {"service": "api", "environment": "production"})


class TestReadLocalFile:
    def test_describe(self):
        spec = ACTIONS["READ_LOCAL_FILE"]
        resolved = resolve_args(spec, {"path": "logs/app.log"})
        assert spec.describe(resolved) == "Read local file logs/app.log"

    @pytest.mark.asyncio
    async def test_execute_calls_local_file_read(self):
        spec = ACTIONS["READ_LOCAL_FILE"]
        tools = _fake_tools()
        tools.local_file.read_file = AsyncMock(
            return_value={"path": "/logs/app.log", "content": "log data"}
        )
        resolved = resolve_args(spec, {"path": "logs/app.log"})
        result = await spec.execute(tools, resolved)

        tools.local_file.read_file.assert_called_once_with("logs/app.log")
        assert "log data" in result

    @pytest.mark.asyncio
    async def test_execute_reports_error(self):
        spec = ACTIONS["READ_LOCAL_FILE"]
        tools = _fake_tools()
        tools.local_file.read_file = AsyncMock(
            return_value={"path": "logs/app.log", "error": "Access denied"}
        )
        resolved = resolve_args(spec, {"path": "logs/app.log"})
        result = await spec.execute(tools, resolved)

        assert "Could not read" in result
        assert "Access denied" in result


class TestWriteLocalFile:
    def test_describe(self):
        spec = ACTIONS["WRITE_LOCAL_FILE"]
        resolved = resolve_args(spec, {"path": "configs/app.json", "content": "{}"})
        assert spec.describe(resolved) == "Write local file configs/app.json"

    @pytest.mark.asyncio
    async def test_execute_calls_local_file_write(self):
        spec = ACTIONS["WRITE_LOCAL_FILE"]
        tools = _fake_tools()
        tools.local_file.write_file = AsyncMock(
            return_value={"path": "/configs/app.json", "bytes_written": 2}
        )
        resolved = resolve_args(spec, {"path": "configs/app.json", "content": "{}"})
        result = await spec.execute(tools, resolved)

        tools.local_file.write_file.assert_called_once_with("configs/app.json", "{}")
        assert "Wrote" in result
        assert "2 bytes" in result

    @pytest.mark.asyncio
    async def test_execute_reports_error(self):
        spec = ACTIONS["WRITE_LOCAL_FILE"]
        tools = _fake_tools()
        tools.local_file.write_file = AsyncMock(
            return_value={"path": "configs/app.json", "error": "Permission denied"}
        )
        resolved = resolve_args(spec, {"path": "configs/app.json", "content": "{}"})
        result = await spec.execute(tools, resolved)

        assert "Could not write" in result
        assert "Permission denied" in result


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