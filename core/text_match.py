"""
core/text_match.py
-------------------
Shared lightweight text-matching utilities: stopword filtering and a
suffix-stripping stemmer, so simple word-overlap scoring (skills, solutions)
treats "meeting"/"meetings"/"meet" as the same token without pulling in a
heavy NLP dependency.
"""

from __future__ import annotations

import re

# Small, deliberately conservative stopword list — just the high-frequency
# function words that would otherwise dilute overlap scoring.
STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "of", "at", "by", "for",
    "with", "about", "against", "between", "into", "through", "during",
    "to", "from", "in", "on", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "should", "could", "can", "this", "that", "these", "those", "it",
    "its", "i", "you", "he", "she", "we", "they", "them", "my", "your",
    "his", "her", "our", "their", "me", "us", "as", "not", "no", "so",
    "than", "then", "there", "here", "what", "which", "who", "whom",
    "how", "when", "where", "why",
})

_SUFFIXES = ("ing", "edly", "ied", "ed", "ies", "es", "ly", "s")

_VOWELS = frozenset("aeiou")


def _collapse_doubled_consonant(word: str) -> str:
    """Collapse a trailing doubled consonant (e.g. "runn" -> "run"),
    used after stripping a suffix like "ing" so CVC gerunds ("running")
    fold back to their base form ("run")."""
    if (
        len(word) > 2
        and word[-1] == word[-2]
        and word[-1] not in _VOWELS
        and word[-1] not in ("l", "s", "z")
    ):
        return word[:-1]
    return word


def _stem(word: str) -> str:
    """Strip a small set of common suffixes. Not linguistically exact —
    just enough to fold plurals/gerunds together for overlap scoring.
    Runs twice so plural-of-gerund forms ("meetings") fold down as far as
    the singular gerund ("meeting" -> "meet")."""
    for _ in range(2):
        for suffix in _SUFFIXES:
            if len(word) > len(suffix) + 2 and word.endswith(suffix):
                if suffix in ("ies", "ied"):
                    word = word[: -len(suffix)] + "y"
                else:
                    word = word[: -len(suffix)]
                    if suffix == "ing":
                        word = _collapse_doubled_consonant(word)
                break
        else:
            break
    return word


def tokenize(text: str) -> set[str]:
    """Lowercase, alpha-only (2+ chars) tokens with stopwords removed and
    a light stem applied, for word-overlap relevance scoring."""
    words = re.findall(r"[a-z]{2,}", text.lower())
    return {_stem(w) for w in words if w not in STOPWORDS}
