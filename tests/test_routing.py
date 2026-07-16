"""
test_routing.py
-----------------
Tests for core/routing.py — the shared '@agent' tag parser used by every
interface (telegram, cli, http) to route a message to a specific agent.

Run:
    python -m pytest tests/test_routing.py -x -q
"""

from __future__ import annotations

from core.routing import parse_agent_tag


class TestParseAgentTag:
    def test_valid_tag_strips_prefix(self):
        agent_name, text = parse_agent_tag(
            "@devops restart the server", ["business", "devops"]
        )
        assert agent_name == "devops"
        assert text == "restart the server"

    def test_case_insensitive_tag(self):
        agent_name, text = parse_agent_tag(
            "@DevOps restart it", ["business", "devops"]
        )
        assert agent_name == "devops"
        assert text == "restart it"

    def test_unknown_agent_name_not_stripped(self):
        text_in = "@unknown do something"
        agent_name, text = parse_agent_tag(text_in, ["business", "devops"])
        assert agent_name == ""
        assert text == text_in

    def test_tag_with_no_trailing_text_not_stripped(self):
        text_in = "@devops"
        agent_name, text = parse_agent_tag(text_in, ["business", "devops"])
        assert agent_name == ""
        assert text == text_in

    def test_tag_with_only_whitespace_after_not_stripped(self):
        text_in = "@devops   "
        agent_name, text = parse_agent_tag(text_in, ["business", "devops"])
        assert agent_name == ""
        assert text == text_in

    def test_no_tag_passes_through(self):
        text_in = "just a normal message"
        agent_name, text = parse_agent_tag(text_in, ["business", "devops"])
        assert agent_name == ""
        assert text == text_in

    def test_empty_registered_agents_never_matches(self):
        text_in = "@devops restart it"
        agent_name, text = parse_agent_tag(text_in, [])
        assert agent_name == ""
        assert text == text_in