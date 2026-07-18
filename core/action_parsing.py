"""
core/action_parsing.py
-----------------------
Shared parser for ACTION: lines emitted by agent LLM responses.

Format:
    ACTION: <TYPE> | key=value key2="quoted value" ...
    ACTION: <TYPE> | free text description   (legacy — no args)

Used by agents/devops/actions.py and agents/business/actions.py.
"""

from __future__ import annotations

import re

_KV_TOKEN = re.compile(
    r'(?P<key>[A-Za-z_][A-Za-z0-9_]*)='
    r'(?:"(?P<dquoted>[^"]*)"|\'(?P<squoted>[^\']*)\'|(?P<bare>[^\s]+))'
)


def parse_action_line(action_line: str) -> tuple[str, dict[str, str]]:
    """
    Parse an "ACTION: <TYPE> | ..." line into (action_type, args).

    args is {} when the segment after "|" contains no valid key=value
    tokens (i.e. it's a legacy free-text description).
    """
    line = action_line.strip()
    line = re.sub(r"^ACTION:\s*", "", line, flags=re.IGNORECASE)

    parts = line.split("|", 1)
    action_type = parts[0].strip().upper()
    remainder = parts[1].strip() if len(parts) > 1 else ""

    args: dict[str, str] = {}
    for match in _KV_TOKEN.finditer(remainder):
        key = match.group("key")
        value = match.group("dquoted")
        if value is None:
            value = match.group("squoted")
        if value is None:
            value = match.group("bare")
        
        # Skip tokens with unbalanced quotes (bare value starts with quote but has no closing quote)
        bare = match.group("bare")
        if bare is not None and (bare.startswith('"') or bare.startswith("'")):
            continue
        
        args[key] = value

    return action_type, args