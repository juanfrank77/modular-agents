"""
test_action_parsing.py
-----------------------
Tests for core/action_parsing.py — the shared key=value ACTION-line parser
used by both the DevOps and Business agents.

Run:
    python -m pytest tests/test_action_parsing.py -x -q
"""

from __future__ import annotations

from core.action_parsing import parse_action_line


class TestParseActionLine:
    def test_parses_type_and_simple_args(self):
        line = "ACTION: MERGE_PR | number=42 repo=org/x method=squash"
        action_type, args = parse_action_line(line)
        assert action_type == "MERGE_PR"
        assert args == {"number": "42", "repo": "org/x", "method": "squash"}

    def test_parses_quoted_values_with_spaces(self):
        line = 'ACTION: CREATE_ISSUE | repo=org/x title="Flaky CI on main" body="Steps to reproduce here"'
        action_type, args = parse_action_line(line)
        assert action_type == "CREATE_ISSUE"
        assert args == {
            "repo": "org/x",
            "title": "Flaky CI on main",
            "body": "Steps to reproduce here",
        }

    def test_parses_single_quoted_values(self):
        line = "ACTION: CREATE_ISSUE | repo=org/x title='Flaky CI on main'"
        action_type, args = parse_action_line(line)
        assert args["title"] == "Flaky CI on main"

    def test_no_args_returns_empty_dict(self):
        line = "ACTION: DEPLOY_PROD | Deploy v1.4.2 to production via Fly.io"
        action_type, args = parse_action_line(line)
        assert action_type == "DEPLOY_PROD"
        assert args == {}

    def test_type_is_uppercased(self):
        line = "ACTION: merge_pr | number=1 repo=org/x"
        action_type, _ = parse_action_line(line)
        assert action_type == "MERGE_PR"

    def test_malformed_token_without_equals_is_skipped(self):
        line = "ACTION: MERGE_PR | number=42 garbage repo=org/x"
        action_type, args = parse_action_line(line)
        assert args == {"number": "42", "repo": "org/x"}

    def test_unbalanced_quote_is_skipped(self):
        line = 'ACTION: CREATE_ISSUE | repo=org/x title="unterminated'
        action_type, args = parse_action_line(line)
        assert args == {"repo": "org/x"}

    def test_handles_leading_whitespace_in_action_line(self):
        line = "  ACTION: MERGE_PR | number=42 repo=org/x  "
        action_type, args = parse_action_line(line)
        assert action_type == "MERGE_PR"
        assert args == {"number": "42", "repo": "org/x"}