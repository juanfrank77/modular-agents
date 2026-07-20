"""
test_config_hardcoded_values.py
-----------------------------------
Tests for Settings fields that replaced previously-hardcoded module
constants: pairing_max_failed_attempts, approval_default_timeout,
skill_min_score.

Run:
    python -m pytest tests/test_config_hardcoded_values.py -x -q
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


class TestPairingMaxFailedAttempts:
    def test_defaults_to_five(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PAIRING_MAX_FAILED_ATTEMPTS", raising=False)
        settings = load_settings(env_path=_write_env(tmp_path))
        assert settings.pairing_max_failed_attempts == 5

    def test_overridden_via_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PAIRING_MAX_FAILED_ATTEMPTS", raising=False)
        env_file = _write_env(tmp_path, "PAIRING_MAX_FAILED_ATTEMPTS=3\n")
        settings = load_settings(env_path=env_file)
        assert settings.pairing_max_failed_attempts == 3


class TestApprovalDefaultTimeout:
    def test_defaults_to_300(self, tmp_path, monkeypatch):
        monkeypatch.delenv("APPROVAL_DEFAULT_TIMEOUT", raising=False)
        settings = load_settings(env_path=_write_env(tmp_path))
        assert settings.approval_default_timeout == 300

    def test_overridden_via_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("APPROVAL_DEFAULT_TIMEOUT", raising=False)
        env_file = _write_env(tmp_path, "APPROVAL_DEFAULT_TIMEOUT=60\n")
        settings = load_settings(env_path=env_file)
        assert settings.approval_default_timeout == 60


class TestSkillMinScore:
    def test_defaults_to_point_zero_five(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SKILL_MIN_SCORE", raising=False)
        settings = load_settings(env_path=_write_env(tmp_path))
        assert settings.skill_min_score == 0.05

    def test_overridden_via_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SKILL_MIN_SCORE", raising=False)
        env_file = _write_env(tmp_path, "SKILL_MIN_SCORE=0.2\n")
        settings = load_settings(env_path=env_file)
        assert settings.skill_min_score == 0.2
