"""
test_business_actions.py
------------------------
Tests for agents/business/actions.py — the ActionSpec registry that maps
approved ACTION: lines to real BusinessTools calls.

Run:
    python -m pytest tests/test_business_actions.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agents.business.actions import ACTIONS, BusinessToolError, MissingRequiredArg, resolve_args
from agents.business.tools import BusinessTools


def _fake_tools() -> BusinessTools:
    return BusinessTools(gmail=AsyncMock(), calendar=AsyncMock())


class TestResolveArgs:
    def test_missing_required_arg_raises(self):
        spec = ACTIONS["SEND_EMAIL"]
        with pytest.raises(MissingRequiredArg) as exc_info:
            resolve_args(spec, {"to": "bob@example.com"})
        assert "subject" in str(exc_info.value)

    def test_calendar_write_merges_default_description(self):
        spec = ACTIONS["CALENDAR_WRITE"]
        resolved = resolve_args(
            spec, {"title": "Sync", "start": "2026-04-05T10:00:00Z", "end": "2026-04-05T11:00:00Z"}
        )
        assert resolved["description"] == ""


class TestSendEmail:
    def test_describe(self):
        spec = ACTIONS["SEND_EMAIL"]
        resolved = resolve_args(
            spec, {"to": "bob@example.com", "subject": "Hi", "body": "Hello!"}
        )
        assert spec.describe(resolved) == "Send email to bob@example.com: Hi"

    @pytest.mark.asyncio
    async def test_execute_calls_gmail_send_email(self):
        spec = ACTIONS["SEND_EMAIL"]
        tools = _fake_tools()
        tools.gmail.send_email = AsyncMock(return_value={"messageId": "msg_1"})
        resolved = resolve_args(
            spec, {"to": "bob@example.com", "subject": "Hi", "body": "Hello!"}
        )
        result = await spec.execute(tools, resolved)
        tools.gmail.send_email.assert_called_once_with(
            to="bob@example.com", subject="Hi", body="Hello!"
        )
        assert result == "✅ Email sent to bob@example.com"

    @pytest.mark.asyncio
    async def test_execute_raises_business_tool_error_on_composio_error(self):
        spec = ACTIONS["SEND_EMAIL"]
        tools = _fake_tools()
        tools.gmail.send_email = AsyncMock(return_value={"error": "quota exceeded"})
        resolved = resolve_args(
            spec, {"to": "bob@example.com", "subject": "Hi", "body": "Hello!"}
        )
        with pytest.raises(BusinessToolError, match="quota exceeded"):
            await spec.execute(tools, resolved)


class TestCalendarWrite:
    def test_describe(self):
        spec = ACTIONS["CALENDAR_WRITE"]
        resolved = resolve_args(
            spec, {"title": "Sync", "start": "2026-04-05T10:00:00Z", "end": "2026-04-05T11:00:00Z"}
        )
        assert spec.describe(resolved) == "Create calendar event 'Sync' (2026-04-05T10:00:00Z → 2026-04-05T11:00:00Z)"

    @pytest.mark.asyncio
    async def test_execute_calls_calendar_create_event(self):
        spec = ACTIONS["CALENDAR_WRITE"]
        tools = _fake_tools()
        tools.calendar.create_event = AsyncMock(return_value={"id": "evt_1"})
        resolved = resolve_args(
            spec, {"title": "Sync", "start": "2026-04-05T10:00:00Z", "end": "2026-04-05T11:00:00Z"}
        )
        result = await spec.execute(tools, resolved)
        tools.calendar.create_event.assert_called_once_with(
            title="Sync", start="2026-04-05T10:00:00Z", end="2026-04-05T11:00:00Z", description=""
        )
        assert result == "✅ Calendar event created: Sync"

    @pytest.mark.asyncio
    async def test_execute_raises_business_tool_error_on_composio_error(self):
        spec = ACTIONS["CALENDAR_WRITE"]
        tools = _fake_tools()
        tools.calendar.create_event = AsyncMock(return_value={"error": "permission denied"})
        resolved = resolve_args(
            spec, {"title": "Sync", "start": "2026-04-05T10:00:00Z", "end": "2026-04-05T11:00:00Z"}
        )
        with pytest.raises(BusinessToolError, match="permission denied"):
            await spec.execute(tools, resolved)


class TestDraft:
    def test_describe(self):
        spec = ACTIONS["DRAFT"]
        resolved = resolve_args(spec, {"email_id": "msg_123", "body": "Thanks!"})
        assert spec.describe(resolved) == "Draft reply to message msg_123"

    @pytest.mark.asyncio
    async def test_execute_calls_gmail_draft_reply(self):
        spec = ACTIONS["DRAFT"]
        tools = _fake_tools()
        tools.gmail.draft_reply = AsyncMock(return_value={"draftId": "draft_1"})
        resolved = resolve_args(spec, {"email_id": "msg_123", "body": "Thanks!"})
        result = await spec.execute(tools, resolved)
        tools.gmail.draft_reply.assert_called_once_with(email_id="msg_123", body="Thanks!")
        assert result == "✅ Draft reply created for msg_123"