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

    def test_doubled_consonant_gerunds_fold_to_base(self):
        assert tokenize("running") & tokenize("run"), "running/run should overlap"
        assert tokenize("stopping") & tokenize("stop"), "stopping/stop should overlap"
        assert tokenize("planning") & tokenize("plan"), "planning/plan should overlap"
        assert tokenize("getting") & tokenize("get"), "getting/get should overlap"

    def test_non_doubled_gerunds_still_work(self):
        assert tokenize("working") & tokenize("work"), "working/work should overlap"

    def test_plural_of_gerund_still_folds_to_singular_gerund(self):
        assert tokenize("meeting") == tokenize("meetings")
        assert tokenize("meeting") & tokenize("meet") == tokenize("meeting")

    def test_ies_suffix_still_folds_to_y(self):
        assert tokenize("stories") & tokenize("story"), "stories/story should overlap"

    def test_base_words_ending_in_double_consonant_not_collapsed(self):
        assert tokenize("falling") & tokenize("fall"), "falling/fall should overlap"
        assert tokenize("calling") & tokenize("call"), "calling/call should overlap"
        assert tokenize("selling") & tokenize("sell"), "selling/sell should overlap"
        assert tokenize("missing") & tokenize("miss"), "missing/miss should overlap"
        assert tokenize("polling") & tokenize("poll"), "polling/poll should overlap"
