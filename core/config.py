"""
core/config.py
--------------
Loads and validates .env. Exposes a single typed Settings object.
Fails fast on startup if required keys are missing — no silent failures.

Usage:
    from core.config import settings
    print(settings.telegram_token)
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


# ──────────────────────────────────────────────
# Settings dataclass
# ──────────────────────────────────────────────

@dataclass
class Settings:
    # Telegram
    telegram_token: str
    telegram_allowed_chat_ids: list[str]

    # LLM
    anthropic_api_key: str
    default_model: str = "claude-opus-4-6"
    default_max_tokens: int = 2048

    # Storage
    db_path: Path = Path("memory/sessions.db")
    memory_context_dir: Path = Path("memory/context")
    memory_solutions_dir: Path = Path("memory/solutions")

    # Scheduler / heartbeat
    heartbeat_interval_minutes: int = 30

    # Agent autonomy overrides (can be set per-agent in .env)
    business_agent_autonomy: str = "supervised"
    devops_agent_autonomy: str = "autonomous"

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"   # "json" | "pretty"


# ──────────────────────────────────────────────
# Loader
# ──────────────────────────────────────────────

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        print(f"[config] FATAL: required env var '{key}' is missing. Check your .env file.")
        sys.exit(1)
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _check_env_permissions(env_path: Path) -> None:
    """Warn if .env is world-readable (should be chmod 600)."""
    if not env_path.exists():
        return
    mode = env_path.stat().st_mode
    if mode & stat.S_IROTH or mode & stat.S_IWOTH:
        print(f"[config] WARNING: {env_path} is readable by others. Run: chmod 600 {env_path}")


def load_settings(env_path: Path = Path(".env")) -> Settings:
    _check_env_permissions(env_path)
    load_dotenv(env_path)

    # Parse allowed chat IDs (comma-separated in .env)
    raw_ids = _optional("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed_ids = [i.strip() for i in raw_ids.split(",") if i.strip()]

    return Settings(
        # Required
        telegram_token=_require("TELEGRAM_BOT_TOKEN"),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        telegram_allowed_chat_ids=allowed_ids,

        # Optional with defaults
        default_model=_optional("DEFAULT_MODEL", "claude-opus-4-6"),
        default_max_tokens=int(_optional("DEFAULT_MAX_TOKENS", "2048")),

        db_path=Path(_optional("DB_PATH", "memory/sessions.db")),
        memory_context_dir=Path(_optional("MEMORY_CONTEXT_DIR", "memory/context")),
        memory_solutions_dir=Path(_optional("MEMORY_SOLUTIONS_DIR", "memory/solutions")),

        heartbeat_interval_minutes=int(_optional("HEARTBEAT_INTERVAL_MINUTES", "30")),

        business_agent_autonomy=_optional("BUSINESS_AGENT_AUTONOMY", "supervised"),
        devops_agent_autonomy=_optional("DEVOPS_AGENT_AUTONOMY", "autonomous"),

        log_level=_optional("LOG_LEVEL", "INFO"),
        log_format=_optional("LOG_FORMAT", "json"),
    )


# Module-level singleton — import this everywhere
settings: Settings = load_settings()
