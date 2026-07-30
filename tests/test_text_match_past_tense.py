# test_text_match_past_tense.py
"""Tests for -ied past-tense stemming in core/text_match.py (bug #31)."""
from __future__ import annotations

from core.text_match import tokenize


def test_past_tense_ied_folds_to_base():
    assert tokenize("study") & tokenize("studied"), "study/studied should overlap"
    assert tokenize("reply") & tokenize("replied"), "reply/replied should overlap"
    assert tokenize("apply") & tokenize("applied"), "apply/applied should overlap"
    assert tokenize("carry") & tokenize("carried"), "carry/carried should overlap"


def test_short_ied_words_escape():
    """Short -ied words (<=5 chars) should escape stemming via length check."""
    assert tokenize("died") == {"died"}
    assert tokenize("lied") == {"lied"}
    assert tokenize("tied") == {"tied"}
