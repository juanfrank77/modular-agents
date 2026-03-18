"""
agents/base.py
--------------
The BaseAgent abstract base class. Every domain agent inherits this.
The bus only ever calls methods defined here — no direct coupling to
concrete agent implementations.

To add a new agent:
  1. Create agents/myagent/agent.py
  2. Subclass BaseAgent
  3. Implement handle(), register_schedules(), health_check()
  4. Register with the bus in main.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from core.protocols import AgentEvent, AgentResponse

if TYPE_CHECKING:
    from core.bus import MessageBus
    from core.config import Settings
    from core.protocols import LLMProvider
    from core.memory import Memory
    from core.notifier import TelegramNotifier
    from core.safety import Safety
    from core.skill_loader import SkillLoader
    from core.storage import Storage


class BaseAgent(ABC):
    # Every subclass must declare these at class level
    name: str           # unique identifier, e.g. "business"
    description: str    # used by bus for routing decisions
    autonomy_level: str # "read_only" | "supervised" | "autonomous"

    def __init__(
        self,
        settings: "Settings",
        storage: "Storage",
        notifier: "TelegramNotifier",
        llm: "LLMProvider | None" = None,
        memory: "Memory | None" = None,
        safety: "Safety | None" = None,
        skill_loader: "SkillLoader | None" = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.notifier = notifier
        self.llm = llm
        self.memory = memory
        self.safety = safety
        self.skill_loader = skill_loader

    @abstractmethod
    async def handle(self, event: AgentEvent) -> AgentResponse:
        """
        Process an incoming event and return a response.
        The bus calls this for every event routed to this agent.
        """

    @abstractmethod
    async def register_schedules(self, bus: "MessageBus") -> None:
        """
        Register cron jobs and heartbeat handlers at startup.
        Called once by main.py during initialisation.
        Use the scheduler from core.scheduler inside your implementation.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Return True if the agent and all its dependencies are healthy.
        Called periodically by the bus and on startup.
        """

    # ── Helpers available to all agents ───────

    async def reply(self, event: AgentEvent, text: str) -> AgentResponse:
        """Send a message back to the user and return a response object."""
        await self.notifier.send(event.chat_id, text)
        return AgentResponse(text=text, agent_name=self.name)

    def _is_authorized(self, chat_id: str) -> bool:
        """Check if a chat_id is in the allowlist."""
        allowed = self.settings.telegram_allowed_chat_ids
        # Empty allowlist means no restriction (useful for development)
        if not allowed:
            return True
        return chat_id in allowed
