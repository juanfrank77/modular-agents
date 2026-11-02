"""
test_business_tools.py
----------------------
Tests for agents/business/tools/__init__.py — build_tools must build the
local calendar and local-file tools unconditionally and degrade to
gmail=None (with a warning log) when the Google/Composio connection is
unavailable, never raising.

Run:
    python -m pytest tests/test_business_tools.py -x -q
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agents.business.tools import BusinessTools, build_tools
from agents.business.tools.calendar import LocalCalendarTool


def _settings(calendar_db_path, api_key: str | None = "key123"):
    settings = MagicMock()
    settings.composio_api_key = api_key
    settings.composio_user_id = "alice"
    settings.calendar_db_path = calendar_db_path
    settings.local_file_paths = [Path("/tmp")]
    return settings


class TestBuildToolsDegradesGracefully:
    def test_no_api_key_keeps_calendar(self, tmp_path):
        tools = build_tools(_settings(tmp_path / "cal.db", api_key=""))

        assert tools.gmail is None
        assert isinstance(tools.calendar, LocalCalendarTool)
        assert tools.local_file is not None

    def test_composio_init_failure_keeps_calendar(self, tmp_path):
        with patch(
            "agents.business.tools.ComposioTool",
            side_effect=RuntimeError("composio-anthropic is not installed"),
        ):
            tools = build_tools(_settings(tmp_path / "cal.db"))

        assert tools.gmail is None
        assert isinstance(tools.calendar, LocalCalendarTool)

    def test_composio_available_builds_gmail(self, tmp_path):
        composio = MagicMock()
        with patch("agents.business.tools.ComposioTool", return_value=composio) as mock_ct:
            tools = build_tools(_settings(tmp_path / "cal.db"))

        mock_ct.assert_called_once_with(api_key="key123", user_id="alice")
        assert tools.gmail is not None
        assert tools.gmail._composio is composio
        assert isinstance(tools.calendar, LocalCalendarTool)

    def test_never_raises(self, tmp_path):
        # Even with everything Composio-side broken, build_tools must return.
        with patch(
            "agents.business.tools.ComposioTool",
            side_effect=Exception("boom"),
        ):
            tools = build_tools(_settings(tmp_path / "cal.db", api_key=None))

        assert tools.gmail is None
        assert isinstance(tools.calendar, LocalCalendarTool)
