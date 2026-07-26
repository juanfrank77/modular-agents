"""Tests for Settings.message_retention_days — controls how many days of
message history Memory.get_session_context keeps before pruning old rows."""
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


class TestMessageRetentionDays:
    """load_dotenv() doesn't override an already-set os.environ entry, so
    every test here must delenv first."""

    def test_defaults_to_90(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MESSAGE_RETENTION_DAYS", raising=False)
        env_file = _write_env(tmp_path)
        settings = load_settings(env_path=env_file)
        assert settings.message_retention_days == 90

    def test_reads_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MESSAGE_RETENTION_DAYS", raising=False)
        env_file = _write_env(tmp_path, "MESSAGE_RETENTION_DAYS=30\n")
        settings = load_settings(env_path=env_file)
        assert settings.message_retention_days == 30

    def test_zero_disables(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MESSAGE_RETENTION_DAYS", raising=False)
        env_file = _write_env(tmp_path, "MESSAGE_RETENTION_DAYS=0\n")
        settings = load_settings(env_path=env_file)
        assert settings.message_retention_days == 0
