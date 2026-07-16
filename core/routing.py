"""
core/routing.py
-----------------
Shared '@agent' tag parser. Lets a user prefix a message with '@name' to
route it to a specific registered agent, bypassing whatever routing the
bus would otherwise choose (stickiness or the intent classifier). Used by
every interface (telegram, cli, http) so tag behavior is identical
everywhere.
"""

from __future__ import annotations


def parse_agent_tag(text: str, registered_agents: list[str]) -> tuple[str, str]:
    """
    Strip a leading '@name' from `text` if `name` matches a registered
    agent (case-insensitive) and non-whitespace text remains after it.

    Returns (agent_name, remaining_text). agent_name is "" when no valid
    tag is found (unknown name, or nothing follows the tag) — in that
    case remaining_text is the original text, unchanged.
    """
    if not text.startswith("@"):
        return "", text

    first, _, rest = text.partition(" ")
    candidate = first[1:].lower().strip()
    rest = rest.strip()

    if candidate in registered_agents and rest:
        return candidate, rest

    return "", text