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

from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType

if TYPE_CHECKING:
    from core.bus import MessageBus
    from core.config import Settings
    from core.protocols import LLMProvider
    from core.memory import Memory
    from core.notifier import TelegramNotifier
    from core.safety import Safety
    from core.skill_loader import SkillLoader
    from core.storage import Storage

log = get_logger("base")


class BaseAgent(ABC):
    # Every subclass must declare these at class level
    name: str  # unique identifier, e.g. "business"
    description: str  # used by bus for routing decisions
    autonomy_level: str  # "read_only" | "supervised" | "autonomous"

    def __init__(
        self,
        settings: "Settings",
        storage: "Storage",
        notifier: "TelegramNotifier",
        llm: "LLMProvider | None" = None,
        memory: "Memory | None" = None,
        safety: "Safety | None" = None,
        skill_loader: "SkillLoader | None" = None,
        bus: "MessageBus | None" = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.notifier = notifier
        self.llm = llm
        self.memory = memory
        self.safety = safety
        self.skill_loader = skill_loader
        self.bus = bus

    @abstractmethod
    async def handle(self, event: AgentEvent) -> AgentResponse:
        """
        Process an incoming event and return a response.
        The bus calls this for every event routed to this agent.
        """

    async def register_schedules(self, bus: "MessageBus") -> None:
        """
        Register cron jobs and heartbeat handlers at startup.
        Called once by main.py during initialisation.
        Stores the bus reference — subclasses should call
        await super().register_schedules(bus) then register their own cron jobs.
        """
        self.bus = bus

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Return True if the agent and all its dependencies are healthy.
        Called periodically by the bus and on startup.
        """

    # ── Cross-agent notifications ─────────────

    async def emit(
        self, agent_name: str, event: str, data: dict | None = None, context: str = ""
    ) -> "AgentResponse | None":
        """
        Send a notification to another agent.

        Exanple:
            await self.emit(
                agent_name="business",
                event="deploy_failure",
                data={"service": "api", "env": "production"},
                context="Production API returned 503 three times in the last 15 minutes."
            )
        """
        if not self.bus:
            log.warning(
                "emit called before bus was stored — call register_schedules first",
                event="notify_no_bus",
                from_agent=self.name,
                to_agent=agent_name,
            )
            return

        message = AgentEvent(
            type=EventType.AGENT_MESSAGE,
            origin_agent=self.name,
            agent_name=agent_name,
            chat_id="",
            text=context,
            data={
                "from_agent": self.name,
                "event": event,
                **(data or {}),
            },
        )

        response = await self.bus.publish(message)

        log.info(
            "Agent message sent",
            event="agent_emit",
            from_agent=self.name,
            to_agent=agent_name,
            message_event=event,
            success=response.success if response else None,
        )

        return response

    async def _handle_agent_message(self, event: AgentEvent) -> AgentResponse:
        """
        Default handler for AGENT_MESSAGE events.

        Logs the notification and stores it in memory as context so the
        agent can reference it in future responses.

        Override this in subclasses to take specific action.
        """
        if event.origin_agent == self.name:
            return AgentResponse(text="", agent_name=self.name)

        from_agent = event.data.get("from_agent", "unknown")
        message_event = event.data.get("event", "unknown")
        context = event.text

        log.info(
            "Agent message received",
            event="agent_message_received",
            agent=self.name,
            from_agent=from_agent,
            message_event=message_event,
        )

        # Store in memory so future responses can use it
        if self.memory and context:
            try:
                session_id = f"{self.name}_agent_messages"
                await self.memory.save_message(
                    session_id=session_id,
                    role="user",
                    content=(
                        f"[Agent notification from {from_agent}] "
                        f"Event: {message_event}. {context}"
                    ),
                    agent=self.name,
                )
            except Exception as e:
                log.warning(
                    "Failed to store agent message in memory",
                    event="message_memory_error",
                    error=str(e),
                )

        return AgentResponse(
            text="", agent_name=self.name, data={"notification_received": True}
        )

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
