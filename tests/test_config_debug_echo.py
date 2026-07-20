"""
test_config_debug_echo.py
-----------------------------
Tests for Settings.debug_echo_agent — gates whether EchoAgent is
constructed/registered at all (main.py), so it can't receive stray
@echo-tagged messages or hallucinated classifier picks in production.

Run:
    python -m pytest tests/test_config_debug_echo.py -x -q
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


class TestDebugEchoAgentSetting:
    """load_dotenv() doesn't override an already-set os.environ entry, so
    every test here must delenv first — otherwise a prior test's value
    leaks across via the process-wide environment regardless of what
    this test's own .env file says."""

    def test_defaults_to_false(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEBUG_ECHO_AGENT", raising=False)
        env_file = _write_env(tmp_path)
        settings = load_settings(env_path=env_file)
        assert settings.debug_echo_agent is False

    def test_true_when_set(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEBUG_ECHO_AGENT", raising=False)
        env_file = _write_env(tmp_path, "DEBUG_ECHO_AGENT=true\n")
        settings = load_settings(env_path=env_file)
        assert settings.debug_echo_agent is True

    def test_false_when_explicitly_set_false(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEBUG_ECHO_AGENT", raising=False)
        env_file = _write_env(tmp_path, "DEBUG_ECHO_AGENT=false\n")
        settings = load_settings(env_path=env_file)
        assert settings.debug_echo_agent is False

    def test_case_insensitive(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEBUG_ECHO_AGENT", raising=False)
        env_file = _write_env(tmp_path, "DEBUG_ECHO_AGENT=True\n")
        settings = load_settings(env_path=env_file)
        assert settings.debug_echo_agent is True
