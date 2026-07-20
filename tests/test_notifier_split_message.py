"""
test_notifier_split_message.py
--------------------------------
Tests for core.notifier._split_message — chunks long messages to respect
Telegram's 4096-char limit, preferring to split on newlines.

Run:
    python -m pytest tests/test_notifier_split_message.py -x -q
"""

from __future__ import annotations

from core.notifier import _split_message


class TestSplitMessageUnderLimit:
    def test_short_text_returns_single_chunk(self):
        assert _split_message("hello") == ["hello"]

    def test_text_exactly_at_limit_returns_single_chunk(self):
        text = "a" * 10
        assert _split_message(text, limit=10) == [text]


class TestSplitMessageOverLimit:
    def test_splits_into_multiple_chunks(self):
        text = "a" * 25
        chunks = _split_message(text, limit=10)
        assert len(chunks) > 1
        assert "".join(chunks) == text

    def test_no_chunk_exceeds_limit(self):
        text = "a" * 25
        chunks = _split_message(text, limit=10)
        assert all(len(c) <= 10 for c in chunks)

    def test_prefers_splitting_at_newline_within_limit(self):
        text = "first line\n" + "b" * 15
        chunks = _split_message(text, limit=15)
        assert chunks[0] == "first line"
        assert chunks[1] == "b" * 15

    def test_strips_leading_newline_from_continuation_chunk(self):
        text = "1234567890\nabc"
        chunks = _split_message(text, limit=10)
        # Split lands exactly at the newline (index 10) — the newline itself
        # is neither in chunk 0 nor kept as a leading char on chunk 1.
        assert chunks[0] == "1234567890"
        assert chunks[1] == "abc"

    def test_hard_splits_when_no_newline_within_limit(self):
        text = "a" * 30  # no newlines at all
        chunks = _split_message(text, limit=10)
        assert chunks == ["a" * 10, "a" * 10, "a" * 10]

    def test_reassembling_chunks_with_newlines_preserves_original_text(self):
        text = "line one\nline two\nline three\nline four\nline five"
        chunks = _split_message(text, limit=20)
        rejoined = chunks[0]
        for c in chunks[1:]:
            rejoined += "\n" + c
        assert rejoined == text


class TestSplitMessageDefaultLimit:
    def test_default_limit_is_telegram_max(self):
        from core.notifier import _MAX_MSG_LENGTH
        assert _MAX_MSG_LENGTH == 4096
        text = "x" * 4096
        assert _split_message(text) == [text]

    def test_over_default_limit_splits(self):
        text = "x" * 5000
        chunks = _split_message(text)
        assert len(chunks) == 2
        assert all(len(c) <= 4096 for c in chunks)
