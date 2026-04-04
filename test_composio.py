"""
test_composio.py
----------------
Tests for the Composio integration layer:
  - core/composio_tool.py
  - agents/business/tools/gmail.py
  - agents/business/tools/calendar.py

All tests mock the SDK — composio-anthropic does NOT need to be installed.

Run:
    python -m pytest test_composio.py -x -q
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to inject a fake ComposioToolSet before the module is imported
# ---------------------------------------------------------------------------

def _make_fake_toolset(execute_return=None, get_tools_return=None, search_return=None):
    """Return a MagicMock that mimics ComposioToolSet."""
    toolset = MagicMock()
    toolset.execute_action.return_value = execute_return or {"success": True}
    toolset.get_tools.return_value = get_tools_return or [{"name": "GMAIL_FETCH_EMAILS"}]
    toolset.find_actions_by_use_case.return_value = search_return or [{"name": "GMAIL_SEND_EMAIL"}]
    return toolset


# ---------------------------------------------------------------------------
# core/composio_tool.py
# ---------------------------------------------------------------------------

class TestComposioToolInit:
    def test_raises_when_sdk_not_installed(self, monkeypatch):
        """ComposioTool.__init__ must raise RuntimeError if SDK is absent."""
        # Simulate the import having failed
        import core.composio_tool as mod
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", False)
        with pytest.raises(RuntimeError, match="composio-anthropic is not installed"):
            mod.ComposioTool(api_key="key123")

    def test_init_success(self, monkeypatch):
        """ComposioTool initialises correctly when SDK is available."""
        import core.composio_tool as mod
        fake_toolset = _make_fake_toolset()
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(mod, "ComposioToolSet", MagicMock(return_value=fake_toolset))

        tool = mod.ComposioTool(api_key="key123", user_id="alice")
        assert tool._api_key == "key123"
        assert tool._user_id == "alice"


class TestComposioToolExecute:
    @pytest.fixture()
    def composio(self, monkeypatch):
        import core.composio_tool as mod
        fake_toolset = _make_fake_toolset(execute_return={"messageId": "abc123"})
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(mod, "ComposioToolSet", MagicMock(return_value=fake_toolset))
        return mod.ComposioTool(api_key="key", user_id="u1"), fake_toolset

    @pytest.mark.asyncio
    async def test_execute_returns_result(self, composio):
        tool, fake_toolset = composio
        result = await tool.execute("GMAIL_FETCH_EMAILS", max_results=5)
        assert result == {"messageId": "abc123"}
        fake_toolset.execute_action.assert_called_once_with(
            action="GMAIL_FETCH_EMAILS",
            params={"max_results": 5},
            entity_id="u1",
        )

    @pytest.mark.asyncio
    async def test_execute_returns_error_dict_on_exception(self, composio):
        tool, fake_toolset = composio
        fake_toolset.execute_action.side_effect = RuntimeError("SDK boom")
        result = await tool.execute("GMAIL_FETCH_EMAILS")
        assert "error" in result
        assert "SDK boom" in result["error"]


class TestComposioToolGetTools:
    @pytest.fixture()
    def composio(self, monkeypatch):
        import core.composio_tool as mod
        schemas = [{"name": "GMAIL_FETCH_EMAILS"}, {"name": "GMAIL_SEND_EMAIL"}]
        fake_toolset = _make_fake_toolset(get_tools_return=schemas)
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(mod, "ComposioToolSet", MagicMock(return_value=fake_toolset))
        return mod.ComposioTool(api_key="key", user_id="u1"), fake_toolset

    @pytest.mark.asyncio
    async def test_get_tools_returns_schemas(self, composio):
        tool, fake_toolset = composio
        result = await tool.get_tools(["gmail"])
        assert len(result) == 2
        assert result[0]["name"] == "GMAIL_FETCH_EMAILS"
        fake_toolset.get_tools.assert_called_once_with(apps=["gmail"])

    @pytest.mark.asyncio
    async def test_get_tools_returns_empty_on_error(self, composio):
        tool, fake_toolset = composio
        fake_toolset.get_tools.side_effect = RuntimeError("network error")
        result = await tool.get_tools(["gmail"])
        assert result == []


class TestComposioToolSearchTools:
    @pytest.fixture()
    def composio(self, monkeypatch):
        import core.composio_tool as mod
        fake_toolset = _make_fake_toolset(search_return=[{"name": "GMAIL_SEND_EMAIL"}])
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(mod, "ComposioToolSet", MagicMock(return_value=fake_toolset))
        return mod.ComposioTool(api_key="key", user_id="u1"), fake_toolset

    @pytest.mark.asyncio
    async def test_search_tools_returns_results(self, composio):
        tool, fake_toolset = composio
        result = await tool.search_tools("send an email")
        assert len(result) == 1
        assert result[0]["name"] == "GMAIL_SEND_EMAIL"
        fake_toolset.find_actions_by_use_case.assert_called_once_with(use_case="send an email")

    @pytest.mark.asyncio
    async def test_search_tools_empty_query_returns_empty(self, composio):
        tool, _ = composio
        result = await tool.search_tools("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_tools_returns_empty_on_error(self, composio):
        tool, fake_toolset = composio
        fake_toolset.find_actions_by_use_case.side_effect = RuntimeError("API down")
        result = await tool.search_tools("send email")
        assert result == []


# ---------------------------------------------------------------------------
# agents/business/tools/gmail.py
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_composio():
    """Return an AsyncMock that behaves like ComposioTool."""
    composio = MagicMock()
    composio.execute = AsyncMock(return_value={"success": True})
    return composio


class TestGmailToolListEmails:
    @pytest.mark.asyncio
    async def test_list_emails_returns_messages(self, mock_composio):
        from agents.business.tools.gmail import GmailTool
        mock_composio.execute = AsyncMock(return_value={
            "messages": [{"id": "1", "subject": "Hello"}]
        })
        gmail = GmailTool(composio=mock_composio)
        result = await gmail.list_emails(max_results=5)
        assert len(result) == 1
        assert result[0]["id"] == "1"
        mock_composio.execute.assert_called_once_with(
            "GMAIL_FETCH_EMAILS", max_results=5
        )

    @pytest.mark.asyncio
    async def test_list_emails_default_max_results(self, mock_composio):
        from agents.business.tools.gmail import GmailTool
        mock_composio.execute = AsyncMock(return_value={"messages": []})
        gmail = GmailTool(composio=mock_composio)
        await gmail.list_emails()
        mock_composio.execute.assert_called_once_with(
            "GMAIL_FETCH_EMAILS", max_results=10
        )

    @pytest.mark.asyncio
    async def test_list_emails_returns_error_list_on_failure(self, mock_composio):
        from agents.business.tools.gmail import GmailTool
        mock_composio.execute = AsyncMock(return_value={"error": "auth failed"})
        gmail = GmailTool(composio=mock_composio)
        result = await gmail.list_emails()
        assert result == [{"error": "auth failed"}]


class TestGmailToolSendEmail:
    @pytest.mark.asyncio
    async def test_send_email_calls_correct_slug(self, mock_composio):
        from agents.business.tools.gmail import GmailTool
        mock_composio.execute = AsyncMock(return_value={"messageId": "msg_xyz"})
        gmail = GmailTool(composio=mock_composio)
        result = await gmail.send_email(
            to="bob@example.com", subject="Hi", body="Hello!"
        )
        assert result["messageId"] == "msg_xyz"
        mock_composio.execute.assert_called_once_with(
            "GMAIL_SEND_EMAIL",
            to="bob@example.com",
            subject="Hi",
            body="Hello!",
        )

    @pytest.mark.asyncio
    async def test_send_email_propagates_error(self, mock_composio):
        from agents.business.tools.gmail import GmailTool
        mock_composio.execute = AsyncMock(return_value={"error": "quota exceeded"})
        gmail = GmailTool(composio=mock_composio)
        result = await gmail.send_email(to="x@x.com", subject="s", body="b")
        assert "error" in result


class TestGmailToolDraftReply:
    @pytest.mark.asyncio
    async def test_draft_reply_calls_correct_slug(self, mock_composio):
        from agents.business.tools.gmail import GmailTool
        mock_composio.execute = AsyncMock(return_value={"draftId": "draft_001"})
        gmail = GmailTool(composio=mock_composio)
        result = await gmail.draft_reply(email_id="msg_123", body="Thanks!")
        assert result["draftId"] == "draft_001"
        mock_composio.execute.assert_called_once_with(
            "GMAIL_CREATE_EMAIL_DRAFT",
            message_id="msg_123",
            body="Thanks!",
        )

    @pytest.mark.asyncio
    async def test_draft_reply_propagates_error(self, mock_composio):
        from agents.business.tools.gmail import GmailTool
        mock_composio.execute = AsyncMock(return_value={"error": "thread not found"})
        gmail = GmailTool(composio=mock_composio)
        result = await gmail.draft_reply(email_id="bad_id", body="x")
        assert "error" in result


# ---------------------------------------------------------------------------
# agents/business/tools/calendar.py
# ---------------------------------------------------------------------------

class TestCalendarToolListEvents:
    @pytest.mark.asyncio
    async def test_list_events_returns_items(self, mock_composio):
        from agents.business.tools.calendar import CalendarTool
        mock_composio.execute = AsyncMock(return_value={
            "items": [{"id": "evt1", "summary": "Team Sync"}]
        })
        cal = CalendarTool(composio=mock_composio)
        result = await cal.list_events(max_results=3)
        assert len(result) == 1
        assert result[0]["id"] == "evt1"
        mock_composio.execute.assert_called_once_with(
            "GOOGLECALENDAR_LIST_EVENTS", max_results=3
        )

    @pytest.mark.asyncio
    async def test_list_events_default_max_results(self, mock_composio):
        from agents.business.tools.calendar import CalendarTool
        mock_composio.execute = AsyncMock(return_value={"items": []})
        cal = CalendarTool(composio=mock_composio)
        await cal.list_events()
        mock_composio.execute.assert_called_once_with(
            "GOOGLECALENDAR_LIST_EVENTS", max_results=10
        )

    @pytest.mark.asyncio
    async def test_list_events_returns_error_list_on_failure(self, mock_composio):
        from agents.business.tools.calendar import CalendarTool
        mock_composio.execute = AsyncMock(return_value={"error": "calendar not found"})
        cal = CalendarTool(composio=mock_composio)
        result = await cal.list_events()
        assert result == [{"error": "calendar not found"}]


class TestCalendarToolCreateEvent:
    @pytest.mark.asyncio
    async def test_create_event_calls_correct_slug(self, mock_composio):
        from agents.business.tools.calendar import CalendarTool
        mock_composio.execute = AsyncMock(return_value={"id": "evt_new"})
        cal = CalendarTool(composio=mock_composio)
        result = await cal.create_event(
            title="Sprint Planning",
            start="2026-04-05T10:00:00Z",
            end="2026-04-05T11:00:00Z",
            description="Q2 sprint",
        )
        assert result["id"] == "evt_new"
        mock_composio.execute.assert_called_once_with(
            "GOOGLECALENDAR_CREATE_EVENT",
            summary="Sprint Planning",
            start="2026-04-05T10:00:00Z",
            end="2026-04-05T11:00:00Z",
            description="Q2 sprint",
        )

    @pytest.mark.asyncio
    async def test_create_event_default_description(self, mock_composio):
        from agents.business.tools.calendar import CalendarTool
        mock_composio.execute = AsyncMock(return_value={"id": "evt_2"})
        cal = CalendarTool(composio=mock_composio)
        await cal.create_event(
            title="Standup",
            start="2026-04-05T09:00:00Z",
            end="2026-04-05T09:15:00Z",
        )
        mock_composio.execute.assert_called_once_with(
            "GOOGLECALENDAR_CREATE_EVENT",
            summary="Standup",
            start="2026-04-05T09:00:00Z",
            end="2026-04-05T09:15:00Z",
            description="",
        )

    @pytest.mark.asyncio
    async def test_create_event_propagates_error(self, mock_composio):
        from agents.business.tools.calendar import CalendarTool
        mock_composio.execute = AsyncMock(return_value={"error": "permission denied"})
        cal = CalendarTool(composio=mock_composio)
        result = await cal.create_event(
            title="X", start="2026-04-05T09:00:00Z", end="2026-04-05T09:15:00Z"
        )
        assert "error" in result


class TestCalendarToolBlockTime:
    @pytest.mark.asyncio
    async def test_block_time_prefixes_title(self, mock_composio):
        from agents.business.tools.calendar import CalendarTool
        mock_composio.execute = AsyncMock(return_value={"id": "evt_block"})
        cal = CalendarTool(composio=mock_composio)
        result = await cal.block_time(
            title="Deep Work",
            start="2026-04-05T14:00:00Z",
            end="2026-04-05T16:00:00Z",
        )
        assert result["id"] == "evt_block"
        mock_composio.execute.assert_called_once_with(
            "GOOGLECALENDAR_CREATE_EVENT",
            summary="Blocked: Deep Work",
            start="2026-04-05T14:00:00Z",
            end="2026-04-05T16:00:00Z",
            description="",
        )

    @pytest.mark.asyncio
    async def test_block_time_propagates_error(self, mock_composio):
        from agents.business.tools.calendar import CalendarTool
        mock_composio.execute = AsyncMock(return_value={"error": "conflict"})
        cal = CalendarTool(composio=mock_composio)
        result = await cal.block_time(
            title="Focus", start="2026-04-05T14:00:00Z", end="2026-04-05T16:00:00Z"
        )
        assert "error" in result
