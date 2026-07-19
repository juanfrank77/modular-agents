"""
test_business_agent_actions.py
----------------------------------
Tests for BusinessAgent.tools, _handle_action_proposal (the Ollama-fallback,
ACTION:-text-parsing path), and _handle_tool_call (the native tool-calling
path) — verifies approved SEND_EMAIL/CALENDAR_WRITE/DRAFT actions execute
for real, unavailable Composio config surfaces a clear error, and unmapped
types show the "not wired" note (agents/business/agent.py).

Run:
    python -m pytest tests/test_business_agent_actions.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.business.agent import BusinessAgent
from agents.business.tools import BusinessTools, BusinessToolsUnavailable
from core.protocols import LLMResult, Message, ToolCall


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


def _tool_result(agent, chat_id, name, args, tool_id="call_1"):
    result = LLMResult(tool_calls=[ToolCall(id=tool_id, name=name, args=args)], raw_assistant={"raw": True})
    return agent._handle_tool_call(chat_id, [Message(role="user", content="hi")], "system prompt", result)


class TestNativeToolCallExecutesOnApproval:
    @pytest.mark.asyncio
    async def test_send_email_executes_and_returns_follow_up_text(self):
        agent = _make_agent(check_action_return=True)
        fake_tools = BusinessTools(gmail=AsyncMock(), calendar=AsyncMock())
        fake_tools.gmail.send_email = AsyncMock(return_value={"messageId": "msg_1"})
        agent._tools = fake_tools
        agent.llm.complete = AsyncMock(return_value=LLMResult(text="Sent it!"))

        result = await _tool_result(
            agent, "chat1", "SEND_EMAIL",
            {"to": "bob@example.com", "subject": "Hi", "body": "Hello there"},
        )

        assert result == "Sent it!"
        fake_tools.gmail.send_email.assert_called_once_with(
            to="bob@example.com", subject="Hi", body="Hello there"
        )
        call_kwargs = agent.safety.check_action.call_args.kwargs
        assert call_kwargs["description"] == "Send email to bob@example.com: Hi"

        # Follow-up call carries the tool_result + raw_assistant for continuation
        follow_up_kwargs = agent.llm.complete.call_args.kwargs
        assert follow_up_kwargs["tool_result"].tool_call_id == "call_1"
        assert "✅ Email sent to bob@example.com" in follow_up_kwargs["tool_result"].content
        assert follow_up_kwargs["raw_assistant"] == {"raw": True}
        assert follow_up_kwargs.get("tools") is None


class TestNativeToolCallBusinessToolsUnavailable:
    @pytest.mark.asyncio
    async def test_no_api_key_surfaces_in_tool_result_content(self):
        agent = _make_agent(check_action_return=True, composio_api_key="")
        agent.llm.complete = AsyncMock(return_value=LLMResult(text="Couldn't send it."))

        await _tool_result(
            agent, "chat1", "SEND_EMAIL",
            {"to": "bob@example.com", "subject": "Hi", "body": "Hello there"},
        )

        follow_up_kwargs = agent.llm.complete.call_args.kwargs
        assert "Google account not connected" in follow_up_kwargs["tool_result"].content


class TestNativeToolCallDenied:
    @pytest.mark.asyncio
    async def test_denied_action_not_executed(self):
        agent = _make_agent(check_action_return=False)
        fake_tools = BusinessTools(gmail=AsyncMock(), calendar=AsyncMock())
        agent._tools = fake_tools
        agent.llm.complete = AsyncMock(return_value=LLMResult(text="Not sent."))

        await _tool_result(
            agent, "chat1", "SEND_EMAIL",
            {"to": "bob@example.com", "subject": "Hi", "body": "Hello there"},
        )

        fake_tools.gmail.send_email.assert_not_called()
        follow_up_kwargs = agent.llm.complete.call_args.kwargs
        assert "approval required" in follow_up_kwargs["tool_result"].content.lower()


class TestNativeToolCallMissingRequiredArg:
    @pytest.mark.asyncio
    async def test_missing_required_arg_skips_approval(self):
        agent = _make_agent(check_action_return=True)
        agent.llm.complete = AsyncMock(return_value=LLMResult(text="Missing info."))

        await _tool_result(agent, "chat1", "SEND_EMAIL", {"to": "bob@example.com"})

        agent.safety.check_action.assert_not_called()
        follow_up_kwargs = agent.llm.complete.call_args.kwargs
        assert "missing required argument 'subject'" in follow_up_kwargs["tool_result"].content


class TestNativeToolCallUnwiredType:
    @pytest.mark.asyncio
    async def test_unwired_type_reports_not_wired_in_tool_result(self):
        agent = _make_agent(check_action_return=True)
        agent.llm.complete = AsyncMock(return_value=LLMResult(text="No handler."))

        await _tool_result(agent, "chat1", "CALENDAR_DELETE", {})

        follow_up_kwargs = agent.llm.complete.call_args.kwargs
        assert "no execution handler wired for calendar_delete" in follow_up_kwargs["tool_result"].content.lower()