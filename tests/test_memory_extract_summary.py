# test_memory_extract_summary.py
"""Tests for _extract_summary skipping HTML comments (bug #37).

Context files declare topic-keywords/topic-always-load via HTML comments
(see memory/context/README.md). _extract_summary must skip those lines
when picking a one-line summary for MEMORY.md's index, the same way it
already skips '#' headings and '_' metadata lines.
"""
from __future__ import annotations

from core.memory import _extract_summary


class TestExtractSummary:
    def test_skips_html_comment_line(self):
        # Note: "## Active Projects" still starts with "#" so it is skipped
        # by the pre-existing heading rule too (unrelated to this bug); the
        # HTML-comment line is what must now also be skipped.
        content = (
            "# Projects\n\n"
            "<!-- topic-keywords: deploy, k8s -->\n\n"
            "Example project details here.\n"
        )

        assert _extract_summary(content) == "Example project details here."

    def test_skips_always_load_comment(self):
        content = (
            "# Preferences\n"
            "<!-- topic-always-load -->\n"
            "Timezone: UTC\n"
        )

        assert _extract_summary(content) == "Timezone: UTC"

    def test_falls_back_when_only_comments_and_headings(self):
        content = "# Title\n<!-- topic-keywords: a, b -->\n"

        # No qualifying line found — falls back to the truncated raw content.
        assert _extract_summary(content) == content[:150].replace("\n", " ")
