"""
core/logger.py
--------------
Structured JSON logging. Every log entry includes:
  - timestamp (ISO 8601)
  - level
  - agent (which agent emitted this)
  - event (what kind of event)
  - message
  - duration_ms (when provided)
  - extra fields passed as kwargs

Usage:
    from core.logger import get_logger
    log = get_logger("business")
    log.info("Handled message", event="user_message", duration_ms=142)
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any


# ──────────────────────────────────────────────
# JSON formatter
# ──────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "agent": getattr(record, "agent", "core"),
            "event": getattr(record, "event", ""),
            "msg": record.getMessage(),
        }
        if duration := getattr(record, "duration_ms", None):
            entry["duration_ms"] = duration
        # Include any extra fields attached to the record
        for key, val in record.__dict__.items():
            if key not in (
                "msg", "args", "levelname", "levelno", "pathname", "filename",
                "module", "exc_info", "exc_text", "stack_info", "lineno",
                "funcName", "created", "msecs", "relativeCreated", "thread",
                "threadName", "processName", "process", "name", "message",
                "agent", "event", "duration_ms",
            ):
                entry[key] = val
        return json.dumps(entry)


class PrettyFormatter(logging.Formatter):
    COLORS = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[35m",   # magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        agent = getattr(record, "agent", "core")
        event = getattr(record, "event", "")
        duration = getattr(record, "duration_ms", None)

        parts = [
            f"{color}[{record.levelname}]{self.RESET}",
            f"[{agent}]",
            f"({event})" if event else "",
            record.getMessage(),
            f"  {duration}ms" if duration else "",
        ]
        return " ".join(p for p in parts if p)


# ──────────────────────────────────────────────
# AgentLogger — thin wrapper that binds agent name
# ──────────────────────────────────────────────

class AgentLogger:
    def __init__(self, agent: str, logger: logging.Logger):
        self._agent = agent
        self._logger = logger

    def _log(self, level: int, msg: str, **kwargs: Any) -> None:
        extra = {"agent": self._agent, **kwargs}
        self._logger.log(level, msg, extra=extra)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.CRITICAL, msg, **kwargs)

    def timer(self) -> "_Timer":
        """Context manager that measures elapsed time and returns ms."""
        return _Timer()


class _Timer:
    def __enter__(self) -> "_Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self.ms = round((time.perf_counter() - self._start) * 1000, 1)


# ──────────────────────────────────────────────
# Setup & factory
# ──────────────────────────────────────────────

_configured = False

def _configure(level: str = "INFO", fmt: str = "json") -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter() if fmt == "json" else PrettyFormatter())
    root.addHandler(handler)


def get_logger(agent: str) -> AgentLogger:
    """
    Returns an AgentLogger bound to the given agent name.
    Call this once per module: log = get_logger("business")
    """
    # Lazy configure with defaults if not already done
    _configure()
    return AgentLogger(agent=agent, logger=logging.getLogger(agent))


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Call this early in main.py with values from settings."""
    _configure(level=level, fmt=fmt)
