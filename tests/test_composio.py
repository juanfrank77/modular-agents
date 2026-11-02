"""
test_composio.py
----------------
Tests for the Composio integration layer:
  - core/composio_tool.py
  - agents/business/tools/gmail.py

The calendar tool no longer uses Composio (feature/local-calendar moved it to
SQLite — its tests live in test_local_calendar.py). core/composio_tool.py was
migrated to the current-generation SDK (composio>=0.30, Composio +
AnthropicProvider session API) — the legacy ComposioToolSet no longer exists.

All tests mock the SDK — composio does NOT need to be installed.

Run:
    python -m pytest tests/test_composio.py -x -q
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers to inject a fake Composio SDK before the module is imported
# ---------------------------------------------------------------------------

def _exec_response(data, error=None, successful=True):
    """Return a ToolExecutionResponse-shaped dict (TypedDict: plain dict)."""
    return {"data": data, "error": error, "successful": successful}


def _make_fake_tools(execute_return=None, get_return=None):
    """Return a MagicMock that mimics composio.tools (Tools manager)."""
    tools = MagicMock()
    tools.execute.return_value = execute_return or _exec_response({"ok": True})
    tools.get.return_value = get_return or [{"name": "GMAIL_FETCH_EMAILS"}]
    return tools


def _make_fake_sdk(tools=None):
    """Return a MagicMock that mimics a Composio instance."""
    sdk = MagicMock()
    sdk.tools = tools or _make_fake_tools()
    return sdk


# ---------------------------------------------------------------------------
# core/composio_tool.py
# ---------------------------------------------------------------------------

class TestComposioToolInit:
    def test_raises_when_sdk_not_installed(self, monkeypatch):
        """ComposioTool.__init__ must raise RuntimeError if SDK is absent."""
        # Simulate the import having failed
        import core.composio_tool as mod
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", False)
        with pytest.raises(
            RuntimeError, match="composio / composio-anthropic is not installed"
        ):
            mod.ComposioTool(api_key="key123")

    def test_init_success(self, monkeypatch):
        """ComposioTool initialises correctly when SDK is available."""
        import core.composio_tool as mod
        fake_sdk = _make_fake_sdk()
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(mod, "Composio", MagicMock(return_value=fake_sdk))

        tool = mod.ComposioTool(api_key="key123", user_id="alice")
        assert tool._user_id == "alice"
        mod.Composio.assert_called_once()
        kwargs = mod.Composio.call_args.kwargs
        assert kwargs["api_key"] == "key123"
        assert isinstance(kwargs["provider"], mod.AnthropicProvider)
        assert "toolkit_versions" not in kwargs

    def test_init_passes_toolkit_versions_when_given(self, monkeypatch):
        """toolkit_versions is forwarded into the Composio config when set."""
        import core.composio_tool as mod
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(mod, "Composio", MagicMock(return_value=_make_fake_sdk()))

        mod.ComposioTool(
            api_key="key123",
            user_id="alice",
            toolkit_versions={"gmail": "20251103_01"},
        )
        kwargs = mod.Composio.call_args.kwargs
        assert kwargs["toolkit_versions"] == {"gmail": "20251103_01"}


class TestComposioToolExecute:
    @pytest.fixture()
    def composio(self, monkeypatch):
        import core.composio_tool as mod
        fake_tools = _make_fake_tools(
            execute_return=_exec_response({"messageId": "abc123"})
        )
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(
            mod, "Composio", MagicMock(return_value=_make_fake_sdk(fake_tools))
        )
        return mod.ComposioTool(api_key="key", user_id="u1"), fake_tools

    @pytest.mark.asyncio
    async def test_execute_returns_data_on_success(self, composio):
        tool, fake_tools = composio
        result = await tool.execute("GMAIL_FETCH_EMAILS", max_results=5)
        assert result == {"messageId": "abc123"}
        fake_tools.execute.assert_called_once_with(
            "GMAIL_FETCH_EMAILS",
            {"max_results": 5},
            user_id="u1",
            dangerously_skip_version_check=True,
        )

    @pytest.mark.asyncio
    async def test_execute_maps_unsuccessful_response_to_error(self, composio):
        tool, fake_tools = composio
        fake_tools.execute.return_value = _exec_response(
            {}, error="rate limited", successful=False
        )
        result = await tool.execute("GMAIL_FETCH_EMAILS")
        assert result == {"error": "rate limited"}

    @pytest.mark.asyncio
    async def test_execute_maps_error_field_to_error(self, composio):
        tool, fake_tools = composio
        fake_tools.execute.return_value = _exec_response(
            {}, error="account not connected", successful=True
        )
        result = await tool.execute("GMAIL_FETCH_EMAILS")
        assert result == {"error": "account not connected"}

    @pytest.mark.asyncio
    async def test_execute_returns_error_dict_on_exception(self, composio):
        tool, fake_tools = composio
        fake_tools.execute.side_effect = RuntimeError("SDK boom")
        result = await tool.execute("GMAIL_FETCH_EMAILS")
        assert "error" in result
        assert "SDK boom" in result["error"]


class TestComposioToolGetTools:
    @pytest.fixture()
    def composio(self, monkeypatch):
        import core.composio_tool as mod
        schemas = [{"name": "GMAIL_FETCH_EMAILS"}, {"name": "GMAIL_SEND_EMAIL"}]
        fake_tools = _make_fake_tools(get_return=schemas)
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(
            mod, "Composio", MagicMock(return_value=_make_fake_sdk(fake_tools))
        )
        return mod.ComposioTool(api_key="key", user_id="u1"), fake_tools

    @pytest.mark.asyncio
    async def test_get_tools_returns_schemas(self, composio):
        tool, fake_tools = composio
        result = await tool.get_tools(["gmail"])
        assert len(result) == 2
        assert result[0]["name"] == "GMAIL_FETCH_EMAILS"
        fake_tools.get.assert_called_once_with("u1", toolkits=["gmail"])

    @pytest.mark.asyncio
    async def test_get_tools_returns_error_sentinel_on_error(self, composio):
        tool, fake_tools = composio
        fake_tools.get.side_effect = RuntimeError("network error")
        result = await tool.get_tools(["gmail"])
        assert len(result) == 1
        assert "error" in result[0]
        assert "network error" in result[0]["error"]


class TestComposioToolSearchTools:
    @pytest.fixture()
    def composio(self, monkeypatch):
        import core.composio_tool as mod
        fake_tools = _make_fake_tools(get_return=[{"name": "GMAIL_SEND_EMAIL"}])
        monkeypatch.setattr(mod, "_COMPOSIO_AVAILABLE", True)
        monkeypatch.setattr(
            mod, "Composio", MagicMock(return_value=_make_fake_sdk(fake_tools))
        )
        return mod.ComposioTool(api_key="key", user_id="u1"), fake_tools

    @pytest.mark.asyncio
    async def test_search_tools_returns_results(self, composio):
        tool, fake_tools = composio
        result = await tool.search_tools("send an email")
        assert len(result) == 1
        assert result[0]["name"] == "GMAIL_SEND_EMAIL"
        fake_tools.get.assert_called_once_with("u1", search="send an email")

    @pytest.mark.asyncio
    async def test_search_tools_empty_query_returns_empty(self, composio):
        tool, _ = composio
        result = await tool.search_tools("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_tools_returns_error_sentinel_on_error(self, composio):
        tool, fake_tools = composio
        fake_tools.get.side_effect = RuntimeError("API down")
        result = await tool.search_tools("send email")
        assert len(result) == 1
        assert "error" in result[0]
        assert "API down" in result[0]["error"]


# ---------------------------------------------------------------------------
# agents/business/tools/gmail.py
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_composio():
    """Return an AsyncMock that behaves like ComposioTool."""
    from core.composio_tool import ComposioTool
    composio = MagicMock(spec=ComposioTool)
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
