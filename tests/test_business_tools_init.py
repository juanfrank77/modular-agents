"""
test_business_tools_init.py
-------------------------
Tests for agents/business/tools/__init__.py:
  - BusinessTools dataclass
  - build_tools(settings) factory

build_tools degrades gracefully: a missing Google/Composio connection yields
gmail=None (with a warning log) while the local calendar and local-file tools
are still built. Full degradation coverage also lives in
test_business_tools.py — these tests use the FakeSettings pattern.

All tests use _fake_tools pattern to mock ComposioTool.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


def _fake_tools(user_id="default"):
    """Return a fake ComposioTool that can be used to instantiate GmailTool/CalendarTool."""
    from core.composio_tool import ComposioTool

    fake = MagicMock(spec=ComposioTool)
    fake._user_id = user_id
    fake.execute = MagicMock()
    return fake


def _fake_settings(api_key="test_key", user_id="default", calendar_db_path="cal.db"):
    """Return a Settings-like object with composio, local_file, and calendar fields."""
    from dataclasses import dataclass, field

    @dataclass
    class FakeSettings:
        composio_api_key: str = api_key
        composio_user_id: str = user_id
        calendar_db_path: str = ""
        local_file_paths: list[Path] = field(default_factory=list)

    return FakeSettings(calendar_db_path=calendar_db_path)


class TestBusinessToolsInit:
    def test_returns_business_tools_with_gmail_and_calendar(self, monkeypatch, tmp_path):
        """build_tools returns BusinessTools with gmail and calendar attributes."""
        from agents.business.tools import build_tools

        monkeypatch.setattr(
            "agents.business.tools._COMPOSIO_AVAILABLE", True, raising=False
        )

        captured = {}

        def capture_init(*args, **kwargs):
            captured["user_id"] = kwargs.get("user_id", "default")
            captured["composio_instance"] = MagicMock()
            return captured["composio_instance"]

        monkeypatch.setattr(
            "agents.business.tools.ComposioTool", MagicMock(side_effect=capture_init)
        )

        settings = _fake_settings(calendar_db_path=str(tmp_path / "cal.db"))
        tools = build_tools(settings)  # type: ignore[arg-type]

        assert hasattr(tools, "gmail")
        assert hasattr(tools, "calendar")
        assert hasattr(tools, "local_file")
        assert tools.gmail._composio is captured["composio_instance"]
        assert captured["user_id"] == "default"


class TestGmailDegradesGracefully:
    def test_missing_api_key_yields_gmail_none_but_keeps_calendar(
        self, monkeypatch, tmp_path
    ):
        """No COMPOSIO_API_KEY → gmail=None; the local calendar is still built."""
        from agents.business.tools import build_tools
        from agents.business.tools.calendar import LocalCalendarTool

        monkeypatch.setattr(
            "agents.business.tools._COMPOSIO_AVAILABLE", True, raising=False
        )

        settings = _fake_settings(api_key="", calendar_db_path=str(tmp_path / "cal.db"))
        tools = build_tools(settings)  # type: ignore[arg-type]

        assert tools.gmail is None
        assert isinstance(tools.calendar, LocalCalendarTool)

    def test_composio_runtime_error_yields_gmail_none_but_keeps_calendar(
        self, monkeypatch, tmp_path
    ):
        """ComposioTool init failure → gmail=None; the local calendar is still built."""
        from agents.business.tools import build_tools
        from agents.business.tools.calendar import LocalCalendarTool

        monkeypatch.setattr(
            "agents.business.tools._COMPOSIO_AVAILABLE", True, raising=False
        )

        settings = _fake_settings(
            api_key="test_key", calendar_db_path=str(tmp_path / "cal.db")
        )

        def raise_runtime(*args, **kwargs):
            raise RuntimeError("SDK failed")

        monkeypatch.setattr(
            "agents.business.tools.ComposioTool", MagicMock(side_effect=raise_runtime)
        )

        tools = build_tools(settings)  # type: ignore[arg-type]

        assert tools.gmail is None
        assert isinstance(tools.calendar, LocalCalendarTool)


class TestBuildToolsDefaultUserId:
    def test_defaults_user_id_when_settings_value_empty(self, monkeypatch, tmp_path):
        """build_tools defaults user_id to 'default' when settings.composio_user_id is empty."""
        from agents.business.tools import build_tools

        captured = {}

        def capture_init(*args, **kwargs):
            captured["user_id"] = kwargs.get("user_id", "default")
            return MagicMock()

        monkeypatch.setattr(
            "agents.business.tools.ComposioTool", MagicMock(side_effect=capture_init)
        )
        monkeypatch.setattr(
            "agents.business.tools._COMPOSIO_AVAILABLE", True, raising=False
        )

        settings = _fake_settings(
            api_key="test_key", user_id="", calendar_db_path=str(tmp_path / "cal.db")
        )
        build_tools(settings)  # type: ignore[arg-type]

        assert captured["user_id"] == "default"
