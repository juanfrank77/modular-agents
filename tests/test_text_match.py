# test_text_match.py
"""Tests for core/text_match.py — stopwords + light stemming for overlap scoring."""
from __future__ import annotations

from core.text_match import tokenize


class TestTokenize:
    def test_strips_stopwords(self):
        assert "the" not in tokenize("schedule the meeting")
        assert "and" not in tokenize("bread and butter")

    def test_stems_plurals_and_gerunds_to_same_token(self):
        assert tokenize("meeting") == tokenize("meetings")
        assert tokenize("meeting") & tokenize("meet") == tokenize("meeting")

    def test_short_words_untouched(self):
        # "is" stopword removed, "ai" too short to stem incorrectly
        tokens = tokenize("ai is fun")
        assert "ai" in tokens
        assert "is" not in tokens

    def test_empty_text(self):
        assert tokenize("") == set()
