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

    # LLM providers
    kilo_api_key: str = ""
    kilo_base_url: str = "https://api.kilo.ai/api/gateway"
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    default_model: str = "claude-sonnet-4.6"
    default_max_tokens: int = 2048
    classifier_model: str = "claude-haiku-4.6"

    # Storage
    db_path: Path = Path("memory/sessions.db")
    db_encryption_key: str = ""
    memory_context_dir: Path = Path("memory/context")
    memory_solutions_dir: Path = Path("memory/solutions")

    # Scheduler / heartbeat
    heartbeat_interval_minutes: int = 30
    scheduler_db_path: Path = Path("memory/scheduler.db")

    # Agent autonomy overrides (can be set per-agent in .env)
    business_agent_autonomy: str = "supervised"
    devops_agent_autonomy: str = "autonomous"
    wellbeing_agent_autonomy: str = "autonomous"
    projects_agent_autonomy: str = "supervised"

    # LLM retry / backoff
    llm_max_retries: int = 3
    llm_retry_min_wait: int = 2  # seconds
    llm_retry_max_wait: int = 60  # seconds

    # Approval timeouts per ActionType name (seconds)
    # Keys match ActionType enum names: WRITE_HIGH, EXECUTE, DESTRUCTIVE
    approval_timeouts: dict[str, int] = field(
        default_factory=lambda: {
            "WRITE_HIGH": 120,
            "EXECUTE": 300,
            "DESTRUCTIVE": 600,
        }
    )

    # Composio (optional — for Gmail/Calendar tools)
    composio_api_key: str = ""
    composio_user_id: str = "default"

    # Command blocklist — additional patterns beyond core defaults
    extra_blocked_patterns: list[str] = field(default_factory=list)

    # Quiet hours gating
    quiet_hours_enabled: bool = True
    quiet_hours_morning_start: str = "07:00"
    quiet_hours_morning_end: str = "09:30"
    quiet_hours_morning_allowed: list[str] = field(
        default_factory=lambda: ["wellbeing-nudge"]
    )
    quiet_hours_evening_start: str = "19:30"
    quiet_hours_evening_end: str = "07:00"
    quiet_hours_evening_allowed: list[str] = field(
        default_factory=lambda: ["wellbeing-nudge", "emergency"]
    )
    emergency_keywords: list[str] = field(
        default_factory=lambda: ["server_down", "security", "data_loss", "payment_failure"]
    )

    # Wellbeing nudge
    wellbeing_location: str = ""
    wellbeing_wake_time: str = "07:00"
    wellbeing_bedtime: str = "23:00"

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # "json" | "pretty"
    log_redact_content: bool = True
    log_content_max_length: int = 200

    # HTTP interface
    http_host: str = "127.0.0.1"
    http_port: int = 8080
    session_ttl_hours: int = 24

    # Rate limiting
    rate_limit_rpm: int = 20  # messages per minute per chat_id

    # Local file access
    local_file_paths: list[Path] = field(default_factory=list)

    # Web tools
    tavily_api_key: str = ""


# ──────────────────────────────────────────────
# Loader
# ──────────────────────────────────────────────


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        print(
            f"[config] FATAL: required env var '{key}' is missing. Check your .env file."
        )
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
        print(
            f"[config] WARNING: {env_path} is readable by others. Run: chmod 600 {env_path}"
        )


def load_settings(env_path: Path = Path(".env")) -> Settings:
    _check_env_permissions(env_path)
    load_dotenv(env_path)

    # Parse allowed chat IDs (comma-separated in .env)
    raw_ids = _optional("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed_ids = [i.strip() for i in raw_ids.split(",") if i.strip()]

    # Parse approval timeouts: "WRITE_HIGH=120,EXECUTE=300,DESTRUCTIVE=600"
    approval_timeouts: dict[str, int] = {
        "WRITE_HIGH": 120,
        "EXECUTE": 300,
        "DESTRUCTIVE": 600,
    }
    raw_timeouts = _optional("APPROVAL_TIMEOUTS", "")
    if raw_timeouts:
        for pair in raw_timeouts.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                try:
                    approval_timeouts[k.strip().upper()] = int(v.strip())
                except ValueError:
                    pass  # ignore malformed entries

    # Parse extra blocked patterns (comma-separated in .env)
    raw_patterns = _optional("EXTRA_BLOCKED_PATTERNS", "")
    extra_patterns = [p.strip() for p in raw_patterns.split(",") if p.strip()]

    return Settings(
        # Required
        telegram_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_allowed_chat_ids=allowed_ids,
        # LLM providers (at least one must be configured, validated in main.py)
        kilo_api_key=_optional("KILO_API_KEY", ""),
        anthropic_api_key=_optional("ANTHROPIC_API_KEY", ""),
        openrouter_api_key=_optional("OPENROUTER_API_KEY", ""),
        ollama_base_url=_optional("OLLAMA_BASE_URL", "http://localhost:11434"),
        # Optional with defaults
        default_model=_optional("DEFAULT_MODEL", "claude-sonnet-4.6"),
        default_max_tokens=int(_optional("DEFAULT_MAX_TOKENS", "2048")),
        classifier_model=_optional("CLASSIFIER_MODEL", "claude-haiku-4.6"),
        db_path=Path(_optional("DB_PATH", "memory/sessions.db")),
        db_encryption_key=_optional("DB_ENCRYPTION_KEY", ""),
        memory_context_dir=Path(_optional("MEMORY_CONTEXT_DIR", "memory/context")),
        memory_solutions_dir=Path(
            _optional("MEMORY_SOLUTIONS_DIR", "memory/solutions")
        ),
        heartbeat_interval_minutes=int(_optional("HEARTBEAT_INTERVAL_MINUTES", "30")),
        scheduler_db_path=Path(_optional("SCHEDULER_DB_PATH", "memory/scheduler.db")),
        business_agent_autonomy=_optional("BUSINESS_AGENT_AUTONOMY", "supervised"),
        devops_agent_autonomy=_optional("DEVOPS_AGENT_AUTONOMY", "autonomous"),
        wellbeing_agent_autonomy=_optional("WELLBEING_AGENT_AUTONOMY", "autonomous"),
        projects_agent_autonomy=_optional("PROJECTS_AGENT_AUTONOMY", "supervised"),
        llm_max_retries=int(_optional("LLM_MAX_RETRIES", "3")),
        llm_retry_min_wait=int(_optional("LLM_RETRY_MIN_WAIT", "2")),
        llm_retry_max_wait=int(_optional("LLM_RETRY_MAX_WAIT", "60")),
        log_level=_optional("LOG_LEVEL", "INFO"),
        log_format=_optional("LOG_FORMAT", "json"),
        approval_timeouts=approval_timeouts,
        local_file_paths=[
            Path(p.strip())
            for p in _optional("LOCAL_FILE_PATHS", "").split(",")
            if p.strip()
        ],
        quiet_hours_enabled=_optional("QUIET_HOURS_ENABLED", "true").lower() == "true",
        quiet_hours_morning_start=_optional("QUIET_HOURS_MORNING_START", "07:00"),
        quiet_hours_morning_end=_optional("QUIET_HOURS_MORNING_END", "09:30"),
        quiet_hours_morning_allowed=[
            x.strip()
            for x in _optional("QUIET_HOURS_MORNING_ALLOWED", "wellbeing-nudge").split(",")
            if x.strip()
        ],
        quiet_hours_evening_start=_optional("QUIET_HOURS_EVENING_START", "19:30"),
        quiet_hours_evening_end=_optional("QUIET_HOURS_EVENING_END", "07:00"),
        quiet_hours_evening_allowed=[
            x.strip()
            for x in _optional("QUIET_HOURS_EVENING_ALLOWED", "wellbeing-nudge,emergency").split(",")
            if x.strip()
        ],
        emergency_keywords=[
            x.strip()
            for x in _optional(
                "EMERGENCY_KEYWORDS", "server_down,security,data_loss,payment_failure"
            ).split(",")
            if x.strip()
        ],
        wellbeing_location=_optional("WELLBEING_LOCATION", ""),
        wellbeing_wake_time=_optional("WELLBEING_WAKE_TIME", "07:00"),
        wellbeing_bedtime=_optional("WELLBEING_BEDTIME", "23:00"),
        tavily_api_key=_optional("TAVILY_API_KEY", ""),
        composio_api_key=_optional("COMPOSIO_API_KEY", ""),
        composio_user_id=_optional("COMPOSIO_USER_ID", ""),
        extra_blocked_patterns=extra_patterns,
        log_redact_content=_optional("LOG_REDACT_CONTENT", "true").lower() == "true",
        log_content_max_length=int(_optional("LOG_CONTENT_MAX_LENGTH", "200")),
        http_host=_optional("HTTP_HOST", "127.0.0.1"),
        http_port=int(_optional("HTTP_PORT", "8080")),
        session_ttl_hours=int(_optional("SESSION_TTL_HOURS", "24")),
        rate_limit_rpm=int(_optional("RATE_LIMIT_RPM", "20")),
    )


# Module-level singleton — import this everywhere
settings: Settings = load_settings()
