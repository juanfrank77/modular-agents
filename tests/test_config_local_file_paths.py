"""
test_config_local_file_paths.py
--------------------------------
Tests for Settings.local_file_paths — the memory context dir is always part
of the allowlist (F1 in docs/specs/known-failure-modes.md): agents read their
own context assets (projects.md, preferences.md, mission-state.md) from
memory/context/, so the directory must be readable even when LOCAL_FILE_PATHS
is unset.

Run:
    python -m pytest tests/test_config_local_file_paths.py -x -q
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.config import load_settings


@pytest.fixture(autouse=True)
def _clear_local_file_env(monkeypatch):
    monkeypatch.delenv("LOCAL_FILE_PATHS", raising=False)


def _write_env(tmp_path: Path, extra: str = "") -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=test-token\n"
        "KILO_API_KEY=test-key\n"
        f"{extra}"
    )
    return env_file


def test_memory_context_dir_allowed_by_default(tmp_path, monkeypatch):
    """LOCAL_FILE_PATHS unset → allowlist contains memory/context only."""
    env_file = _write_env(tmp_path)
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file)
    assert settings.local_file_paths == [Path("memory/context")]


def test_env_roots_and_context_dir_combined(tmp_path, monkeypatch):
    """LOCAL_FILE_PATHS set → extra roots are appended, no duplicates."""
    env_file = _write_env(tmp_path, extra="LOCAL_FILE_PATHS=/tmp,/var/notes\n")
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file)
    assert settings.local_file_paths == [
        Path("/tmp"),
        Path("/var/notes"),
        Path("memory/context"),
    ]


def test_no_duplicate_context_dir(tmp_path, monkeypatch):
    """LOCAL_FILE_PATHS already naming memory/context → listed once."""
    env_file = _write_env(tmp_path, extra="LOCAL_FILE_PATHS=memory/context\n")
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file)
    assert settings.local_file_paths == [Path("memory/context")]


def test_memory_context_dir_env_override(tmp_path, monkeypatch):
    """MEMORY_CONTEXT_DIR override → the overridden dir is the allowed one."""
    env_file = _write_env(
        tmp_path, extra="MEMORY_CONTEXT_DIR=custom/context\n"
    )
    monkeypatch.chdir(tmp_path)
    settings = load_settings(env_file)
    assert Path("custom/context") in settings.local_file_paths
    assert Path("memory/context") not in settings.local_file_paths
