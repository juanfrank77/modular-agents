"""
test_config_quiet_hours_windows.py
-----------------------------------
Tests for Settings.quiet_hours_windows — replaced the hardcoded two-window
(morning_routine/evening) schema with an arbitrary named-window list
configurable via QUIET_HOURS_WINDOWS + QUIET_HOURS_<NAME>_{START,END,ALLOWED}.

Run:
    python -m pytest tests/test_config_quiet_hours_windows.py -x -q
"""

from __future__ import annotations

from pathlib import Path

from core.config import load_settings


def _write_env(tmp_path: Path, extra: str = "") -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "KILO_API_KEY=test-key\n"
        f"{extra}"
    )
    return env_file


def _clear_quiet_hours_env(monkeypatch):
    for key in (
        "QUIET_HOURS_WINDOWS",
        "QUIET_HOURS_MORNING_ROUTINE_START",
        "QUIET_HOURS_MORNING_ROUTINE_END",
        "QUIET_HOURS_MORNING_ROUTINE_ALLOWED",
        "QUIET_HOURS_EVENING_START",
        "QUIET_HOURS_EVENING_END",
        "QUIET_HOURS_EVENING_ALLOWED",
        "QUIET_HOURS_FOCUS_BLOCK_START",
        "QUIET_HOURS_FOCUS_BLOCK_END",
        "QUIET_HOURS_FOCUS_BLOCK_ALLOWED",
    ):
        monkeypatch.delenv(key, raising=False)


class TestDefaultWindows:
    def test_defaults_to_morning_and_evening(self, tmp_path, monkeypatch):
        _clear_quiet_hours_env(monkeypatch)
        settings = load_settings(env_path=_write_env(tmp_path))
        assert settings.quiet_hours_windows == [
            {
                "name": "morning_routine",
                "start": "07:00",
                "end": "09:30",
                "allowed": ["wellbeing-nudge"],
            },
            {
                "name": "evening",
                "start": "19:30",
                "end": "07:00",
                "allowed": ["wellbeing-nudge", "emergency"],
            },
        ]


class TestOverriddenWindows:
    def test_per_window_fields_overridden_via_env(self, tmp_path, monkeypatch):
        _clear_quiet_hours_env(monkeypatch)
        env_file = _write_env(
            tmp_path,
            "QUIET_HOURS_MORNING_ROUTINE_START=06:00\n"
            "QUIET_HOURS_MORNING_ROUTINE_ALLOWED=wellbeing-nudge,standup\n",
        )
        settings = load_settings(env_path=env_file)
        morning = next(
            w for w in settings.quiet_hours_windows if w["name"] == "morning_routine"
        )
        assert morning["start"] == "06:00"
        assert morning["end"] == "09:30"  # untouched default
        assert morning["allowed"] == ["wellbeing-nudge", "standup"]

    def test_custom_named_window_added_via_env(self, tmp_path, monkeypatch):
        _clear_quiet_hours_env(monkeypatch)
        env_file = _write_env(
            tmp_path,
            "QUIET_HOURS_WINDOWS=morning_routine,evening,focus_block\n"
            "QUIET_HOURS_FOCUS_BLOCK_START=13:00\n"
            "QUIET_HOURS_FOCUS_BLOCK_END=15:00\n"
            "QUIET_HOURS_FOCUS_BLOCK_ALLOWED=urgent\n",
        )
        settings = load_settings(env_path=env_file)
        names = [w["name"] for w in settings.quiet_hours_windows]
        assert names == ["morning_routine", "evening", "focus_block"]
        focus = next(
            w for w in settings.quiet_hours_windows if w["name"] == "focus_block"
        )
        assert focus == {
            "name": "focus_block",
            "start": "13:00",
            "end": "15:00",
            "allowed": ["urgent"],
        }

    def test_windows_list_reduced_to_single_window(self, tmp_path, monkeypatch):
        _clear_quiet_hours_env(monkeypatch)
        env_file = _write_env(tmp_path, "QUIET_HOURS_WINDOWS=evening\n")
        settings = load_settings(env_path=env_file)
        assert [w["name"] for w in settings.quiet_hours_windows] == ["evening"]
