"""
core/tool_schema.py
--------------------
Builds LLM-facing ToolDef entries from an agent's ActionSpec registry
(agents/business/actions.py, agents/devops/actions.py), so the tool schema
shown to the model and the args used to execute it never drift apart.
"""

from __future__ import annotations

from typing import Any

from core.protocols import ToolDef


def build_tool_defs(actions: dict[str, Any]) -> list[ToolDef]:
    """One ToolDef per ActionSpec entry. `actions` values must expose
    `.description: str`, `.schema: dict[str, dict]`, `.required: list[str]`."""
    tool_defs = []
    for name, spec in actions.items():
        tool_defs.append(
            ToolDef(
                name=name,
                description=spec.description,
                parameters={
                    "type": "object",
                    "properties": dict(spec.schema),
                    "required": list(spec.required),
                },
            )
        )
    return tool_defs
