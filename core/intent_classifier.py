"""
core/intent_classifier.py
---------------------------
Cheap LLM-based classifier that picks which registered agent should
handle a user message, based on each agent's `description`. Used by
MessageBus when a message has no explicit '@tag' routing (core/routing.py).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from core.logger import get_logger
from core.protocols import Message

if TYPE_CHECKING:
    from core.protocols import LLMProvider

log = get_logger("intent_classifier")

_SYSTEM_TEMPLATE = (
    "You route user messages to the agent best equipped to handle them. "
    "Reply with ONLY the agent name from this list, nothing else:\n\n"
    "{agent_list}\n\n"
    "If none fit perfectly, reply with the closest match anyway."
)


async def classify_agent(
    text: str,
    agents: dict[str, str],
    llm: "LLMProvider",
    model: str,
) -> str | None:
    """
    Return the name of the agent in `agents` best matching `text`, or
    None if classification fails or the model's answer doesn't resolve
    to a candidate. `agents` maps agent name -> description. Never raises.
    """
    if not agents:
        return None

    agent_list = "\n".join(f"- {name}: {desc}" for name, desc in agents.items())
    system = _SYSTEM_TEMPLATE.format(agent_list=agent_list)

    try:
        reply = await llm.complete(
            messages=[Message(role="user", content=text)],
            system=system,
            model=model,
            max_tokens=20,
        )
    except Exception as e:
        log.warning(
            "Intent classification failed", event="classify_error", error=str(e)
        )
        return None

    candidate = reply.strip().lower().strip(".")
    if candidate in agents:
        return candidate

    # Model sometimes wraps the answer ("Agent: devops.") — accept it if a
    # candidate name appears as a whole word in the reply.
    for name in agents:
        if re.search(rf"\b{re.escape(name)}\b", candidate):
            return name

    log.warning(
        "Classifier returned unmatched agent", event="classify_no_match", reply=reply
    )
    return None