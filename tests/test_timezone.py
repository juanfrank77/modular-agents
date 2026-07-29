# test_timezone.py
"""Tests for core/timezone.py — user timezone helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from zoneinfo import ZoneInfo

from core.timezone import as_user_timezone, load_timezone, now_in_user_timezone


def test_load_timezone_returns_zoneinfo():
    tz = load_timezone("America/Denver")
    assert isinstance(tz, ZoneInfo)
    assert str(tz) == "America/Denver"


def test_load_timezone_falls_back_to_utc_for_unknown_name():
    tz = load_timezone("NotAReal/Timezone")
    assert str(tz) == "UTC"


def test_load_timezone_defaults_to_utc_when_none():
    tz = load_timezone(None)
    assert str(tz) == "UTC"


def test_now_in_user_timezone_uses_settings_attribute():
    settings = MagicMock()
    settings.user_timezone = "America/Denver"
    now = now_in_user_timezone(settings)
    assert now.tzinfo == ZoneInfo("America/Denver")


def test_now_in_user_timezone_accepts_string():
    now = now_in_user_timezone("Asia/Tokyo")
    assert now.tzinfo == ZoneInfo("Asia/Tokyo")


def test_as_user_timezone_treats_naive_as_user_time():
    settings = MagicMock()
    settings.user_timezone = "America/Denver"
    naive = datetime(2024, 1, 1, 14, 0, 0)
    result = as_user_timezone(naive, settings)
    assert result.tzinfo == ZoneInfo("America/Denver")
    assert result.hour == 14
    assert result.minute == 0


def test_as_user_timezone_converts_aware_to_user_time():
    settings = MagicMock()
    settings.user_timezone = "America/Denver"
    utc = datetime(2024, 1, 1, 20, 0, 0, tzinfo=timezone.utc)
    result = as_user_timezone(utc, settings)
    assert result.tzinfo == ZoneInfo("America/Denver")
    # 20:00 UTC == 13:00 MST (Denver in January)
    assert result.hour == 13
