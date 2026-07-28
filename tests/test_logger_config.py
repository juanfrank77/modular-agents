
"""Tests for core.logger's configure-ordering guard and redaction field
list (improvement-ideas.md §4: a module logging before main.py calls
configure_logging() silently locks in default settings forever; only the
literal field name 'content' was redacted, letting 'text'/similar leak)."""
from __future__ import annotations

import json
import logging

import pytest

import core.logger as logger_module
from core.logger import JSONFormatter, configure_logging, get_logger


@pytest.fixture(autouse=True)
def _reset_logger_state():
    """core.logger's configuration is process-global; isolate each test."""
    # Save original state before any test modifications
    original_configured = logger_module._configured
    original_redact = logger_module._redact_content
    original_max_len = logger_module._max_content_len
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level

    # Reset to clean state before each test
    logger_module._configured = False
    root.handlers.clear()
    root.setLevel(logging.WARNING)

    yield

    # Restore original state after each test
    logger_module._configured = original_configured
    logger_module._redact_content = original_redact
    logger_module._max_content_len = original_max_len
    root.handlers.clear()
    for h in original_handlers:
        root.addHandler(h)
    root.setLevel(original_level)


class TestConfigureOrdering:
    def test_configure_logging_overrides_a_prior_lazy_default_configure(self):
        # Simulate a module calling get_logger() before main.py configures
        # real settings — this must NOT lock in the defaults permanently.
        get_logger("some_early_module")
        assert logger_module._redact_content is True  # the lazy default

        configure_logging(level="DEBUG", fmt="json", redact_content=False, content_max_len=50)

        assert logger_module._redact_content is False
        assert logger_module._max_content_len == 50
        assert logging.getLogger().level == logging.DEBUG

    def test_configure_logging_does_not_duplicate_handlers(self):
        get_logger("some_early_module")
        handler_count_after_lazy = len(logging.getLogger().handlers)

        configure_logging(level="INFO", fmt="json")

        assert len(logging.getLogger().handlers) == handler_count_after_lazy


class TestRedactionFieldList:
    def test_content_field_is_redacted(self):
        logger_module._redact_content = True
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        record.content = "sensitive user message"

        entry = json.loads(formatter.format(record))

        assert entry["content"] != "sensitive user message"

    def test_text_field_is_also_redacted(self):
        logger_module._redact_content = True
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        record.text = "sensitive user message"
        
        entry = json.loads(formatter.format(record))
        
        assert entry["text"] != "sensitive user message"

    def test_unrelated_field_is_left_alone(self):
        logger_module._redact_content = True
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="msg", args=(), exc_info=None,
        )
        record.session_id = "sess_123"

        entry = json.loads(formatter.format(record))

        assert entry["session_id"] == "sess_123"
