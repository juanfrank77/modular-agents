"""
test_business_agent_actions.py
----------------------------------
Tests for BusinessAgent.tools and _handle_action_proposal — verifies
approved SEND_EMAIL/CALENDAR_WRITE/DRAFT actions execute for real,
unavailable Composio config surfaces a clear error, and unmapped types
show the "not wired" note (agents/business/agent.py).

Run:
    python -m pytest tests/test_business_agent_actions.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.business.agent import BusinessAgent
from agents.business.tools import BusinessTools, BusinessToolsUnavailable


def _make_agent(check_action_return=True, composio_api_key="key123"):
    settings = MagicMock()
    settings.business_agent_autonomy = "supervised"
    settings.composio_api_key = composio_api_key
    settings.composio_user_id = "alice"
    agent = BusinessAgent(
        settings=settings,
        storage=MagicMock(),
        notifier=MagicMock(),
        llm=MagicMock(),
        memory=MagicMock(),
        safety=MagicMock(),
    )
    agent.safety.check_action = AsyncMock(return_value=check_action_return)
    return agent


class TestToolsProperty:
    def test_raises_business_tools_unavailable_when_no_api_key(self):
        agent = _make_agent(composio_api_key="")
        with pytest.raises(BusinessToolsUnavailable, match="COMPOSIO_API_KEY"):
            _ = agent.tools

    def test_caches_unavailable_reason_without_retrying(self):
        agent = _make_agent(composio_api_key="")
        with pytest.raises(BusinessToolsUnavailable):
            _ = agent.tools
        with patch("agents.business.agent.build_tools") as mock_build:
            with pytest.raises(BusinessToolsUnavailable):
                _ = agent.tools
            mock_build.assert_not_called()

    def test_returns_business_tools_when_available(self):
        agent = _make_agent()
        fake_tools = BusinessTools(gmail=AsyncMock(), calendar=AsyncMock())
        with patch("agents.business.agent.build_tools", return_value=fake_tools):
            assert agent.tools is fake_tools


class TestWiredActionExecutesOnApproval:
    @pytest.mark.asyncio
    async def test_send_email_executes_and_replaces_line(self):
        agent = _make_agent(check_action_return=True)
        fake_tools = BusinessTools(gmail=AsyncMock(), calendar=AsyncMock())
        fake_tools.gmail.send_email = AsyncMock(return_value={"messageId": "msg_1"})
        agent._tools = fake_tools

        response = 'ACTION: SEND_EMAIL | to=bob@example.com subject=Hi body="Hello there"'
        result = await agent._handle_action_proposal("chat1", response)

        assert "✅ Email sent to bob@example.com" in result
        assert "ACTION:" not in result
        fake_tools.gmail.send_email.assert_called_once_with(
            to="bob@example.com", subject="Hi", body="Hello there"
        )
        call_kwargs = agent.safety.check_action.call_args.kwargs
        assert call_kwargs["description"] == "Send email to bob@example.com: Hi"


class TestBusinessToolsUnavailableSurfacesAsFailure:
    @pytest.mark.asyncio
    async def test_no_api_key_becomes_failure_message(self):
        agent = _make_agent(check_action_return=True, composio_api_key="")
        response = 'ACTION: SEND_EMAIL | to=bob@example.com subject=Hi body="Hello there"'
        result = await agent._handle_action_proposal("chat1", response)

        assert "❌ Action failed: Google account not connected" in result


class TestActionDeniedShowsBlockedMessage:
    @pytest.mark.asyncio
    async def test_denied_action_not_executed(self):
        agent = _make_agent(check_action_return=False)
        fake_tools = BusinessTools(gmail=AsyncMock(), calendar=AsyncMock())
        agent._tools = fake_tools

        response = 'ACTION: SEND_EMAIL | to=bob@example.com subject=Hi body="Hello there"'
        result = await agent._handle_action_proposal("chat1", response)

        assert "⚠️ Action cancelled" in result
        fake_tools.gmail.send_email.assert_not_called()


class TestMissingRequiredArg:
    @pytest.mark.asyncio
    async def test_missing_required_arg_fails_before_approval(self):
        agent = _make_agent(check_action_return=True)
        response = "ACTION: SEND_EMAIL | to=bob@example.com"
        result = await agent._handle_action_proposal("chat1", response)

        assert "❌ Action failed: missing required argument 'subject'" in result
        agent.safety.check_action.assert_not_called()


class TestMalformedArgDoesNotCrash:
    @pytest.mark.asyncio
    async def test_whitespace_only_subject_becomes_failure_message(self):
        agent = _make_agent(check_action_return=True)
        fake_tools = BusinessTools(gmail=AsyncMock(), calendar=AsyncMock())
        fake_tools.gmail.send_email = AsyncMock(
            side_effect=ValueError("send_email requires non-empty to, subject, and body")
        )
        agent._tools = fake_tools

        response = 'ACTION: SEND_EMAIL | to=bob@example.com subject=" " body="hi"'
        result = await agent._handle_action_proposal("chat1", response)

        assert "❌ Action failed" in result


class TestUnmappedActionShowsNotWiredNote:
    @pytest.mark.asyncio
    async def test_unwired_type_approved_shows_note(self):
        agent = _make_agent(check_action_return=True)
        response = "ACTION: CALENDAR_DELETE | Delete the standup event"
        result = await agent._handle_action_proposal("chat1", response)

        assert "✅ Approved, but no execution handler wired for CALENDAR_DELETE yet." in result