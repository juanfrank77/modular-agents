"""
test_business_tools_init.py
-------------------------
Tests for agents/business/tools/__init__.py:
  - BusinessTools dataclass
  - BusinessToolsUnavailable exception
  - build_tools(settings) factory

All tests use _fake_tools pattern to mock ComposioTool.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _fake_tools(user_id="default"):
    """Return a fake ComposioTool that can be used to instantiate GmailTool/CalendarTool."""
    from core.composio_tool import ComposioTool

    fake = MagicMock(spec=ComposioTool)
    fake._user_id = user_id
    fake.execute = MagicMock()
    return fake


def _fake_settings(api_key="test_key", user_id="default"):
    """Return a Settings-like object with composio fields."""
    from dataclasses import dataclass

    @dataclass
    class FakeSettings:
        composio_api_key: str = api_key
        composio_user_id: str = user_id

    return FakeSettings()


class TestBusinessToolsInit:
    def test_returns_business_tools_with_gmail_and_calendar(self, monkeypatch):
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

        settings = _fake_settings()
        tools = build_tools(settings)  # type: ignore[arg-type]

        assert hasattr(tools, "gmail")
        assert hasattr(tools, "calendar")
        assert tools.gmail._composio is captured["composio_instance"]
        assert captured["user_id"] == "default"


class TestBusinessToolsUnavailable:
    def test_raised_when_api_key_missing(self, monkeypatch):
        """BusinessToolsUnavailable raised when api_key is missing."""
        from agents.business.tools import BusinessToolsUnavailable, build_tools

        monkeypatch.setattr(
            "agents.business.tools._COMPOSIO_AVAILABLE", True, raising=False
        )

        settings = _fake_settings(api_key="")

        with pytest.raises(BusinessToolsUnavailable, match="COMPOSIO_API_KEY"):
            build_tools(settings)  # type: ignore[arg-type]

    def test_raised_when_composio_raises_runtime_error(self, monkeypatch):
        """BusinessToolsUnavailable raised when ComposioTool raises RuntimeError."""
        from agents.business.tools import BusinessToolsUnavailable, build_tools

        monkeypatch.setattr(
            "agents.business.tools._COMPOSIO_AVAILABLE", True, raising=False
        )

        settings = _fake_settings(api_key="test_key")

        def raise_runtime(*args, **kwargs):
            raise RuntimeError("SDK failed")

        monkeypatch.setattr(
            "agents.business.tools.ComposioTool", MagicMock(side_effect=raise_runtime)
        )

        with pytest.raises(BusinessToolsUnavailable, match="SDK failed"):
            build_tools(settings)  # type: ignore[arg-type]


class TestBuildToolsDefaultUserId:
    def test_defaults_user_id_when_settings_value_empty(self, monkeypatch):
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

        settings = _fake_settings(api_key="test_key", user_id="")
        build_tools(settings)  # type: ignore[arg-type]

        assert captured["user_id"] == "default"