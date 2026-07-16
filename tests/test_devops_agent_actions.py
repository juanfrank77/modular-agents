"""
test_devops_agent_actions.py
-------------------------------
Tests for DevOpsAgent._handle_action_proposal — verifies that approved
ACTION: lines execute the real tool call, denied ones show the blocked
message, and unmapped types show the "not wired" note instead of
silently discarding (agents/devops/agent.py).

Run:
    python -m pytest tests/test_devops_agent_actions.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.devops.agent import DevOpsAgent
from agents.devops.tools import DevOpsTools
from agents.devops.tools.cli_runner import ToolError


def _make_agent(check_action_return=True) -> DevOpsAgent:
    settings = MagicMock()
    settings.devops_agent_autonomy = "autonomous"
    agent = DevOpsAgent(
        settings=settings,
        storage=MagicMock(),
        notifier=MagicMock(),
        llm=MagicMock(),
        memory=MagicMock(),
        safety=MagicMock(),
    )
    agent.safety.check_action = AsyncMock(return_value=check_action_return)
    agent._tools = DevOpsTools(github=AsyncMock(), railway=AsyncMock())
    return agent


class TestWiredActionExecutesOnApproval:
    @pytest.mark.asyncio
    async def test_merge_pr_executes_and_replaces_line(self):
        agent = _make_agent(check_action_return=True)
        agent.tools.github.merge_pr = AsyncMock(
            return_value={"repo": "org/x", "number": 42, "merged": True, "output": ""}
        )
        response = "Sure, here's the plan.\nACTION: MERGE_PR | number=42 repo=org/x\nDone."
        result = await agent._handle_action_proposal("chat1", response)

        assert "✅ Auto-merge enabled for PR #42 in org/x (rebase)" in result
        assert "ACTION:" not in result
        agent.tools.github.merge_pr.assert_called_once_with(number=42, repo="org/x", method="rebase")

        # Approval description reflects the resolved default, not a separate free-text string
        call_kwargs = agent.safety.check_action.call_args.kwargs
        assert call_kwargs["description"] == "Merge PR #42 in org/x (rebase)"

    @pytest.mark.asyncio
    async def test_merge_pr_override_method(self):
        agent = _make_agent(check_action_return=True)
        agent.tools.github.merge_pr = AsyncMock(
            return_value={"repo": "org/x", "number": 42, "merged": True, "output": ""}
        )
        response = "ACTION: MERGE_PR | number=42 repo=org/x method=squash"
        await agent._handle_action_proposal("chat1", response)
        agent.tools.github.merge_pr.assert_called_once_with(number=42, repo="org/x", method="squash")


class TestActionDeniedShowsBlockedMessage:
    @pytest.mark.asyncio
    async def test_denied_action_not_executed(self):
        agent = _make_agent(check_action_return=False)
        agent.tools.railway.deploy = AsyncMock()
        response = "ACTION: DEPLOY_PROD | service=api"
        result = await agent._handle_action_proposal("chat1", response)

        assert "⚠️ Action blocked" in result
        agent.tools.railway.deploy.assert_not_called()


class TestToolErrorSurfacesAsFailure:
    @pytest.mark.asyncio
    async def test_tool_error_becomes_failure_message(self):
        agent = _make_agent(check_action_return=True)
        agent.tools.github.merge_pr = AsyncMock(
            side_effect=ToolError("github", ["gh", "pr", "merge"], "not mergeable", 1)
        )
        response = "ACTION: MERGE_PR | number=42 repo=org/x"
        result = await agent._handle_action_proposal("chat1", response)

        assert "❌ Action failed" in result
        assert "not mergeable" in result


class TestMissingRequiredArg:
    @pytest.mark.asyncio
    async def test_missing_required_arg_fails_before_approval(self):
        agent = _make_agent(check_action_return=True)
        response = "ACTION: MERGE_PR | repo=org/x"
        result = await agent._handle_action_proposal("chat1", response)

        assert "❌ Action failed: missing required argument 'number'" in result
        agent.safety.check_action.assert_not_called()


class TestMalformedArgDoesNotCrash:
    @pytest.mark.asyncio
    async def test_non_numeric_pr_number_becomes_failure_message(self):
        agent = _make_agent(check_action_return=True)
        response = "ACTION: MERGE_PR | number=abc repo=org/x"
        result = await agent._handle_action_proposal("chat1", response)

        assert "❌ Action failed" in result
        agent.tools.github.merge_pr.assert_not_called()


class TestUnmappedActionShowsNotWiredNote:
    @pytest.mark.asyncio
    async def test_unwired_type_approved_shows_note(self):
        agent = _make_agent(check_action_return=True)
        response = "ACTION: RESTART_SERVICE | Restart the api service"
        result = await agent._handle_action_proposal("chat1", response)

        assert "✅ Approved, but no execution handler wired for RESTART_SERVICE yet." in result

    @pytest.mark.asyncio
    async def test_unwired_type_denied_shows_existing_blocked_message(self):
        agent = _make_agent(check_action_return=False)
        response = "ACTION: RESTART_SERVICE | Restart the api service"
        result = await agent._handle_action_proposal("chat1", response)

        assert "⚠️ Action blocked" in result