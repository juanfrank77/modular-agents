"""
agents/echo/agent.py
--------------------
The hello-world agent. Validates the full Phase 1 stack:
  config → logger → storage → notifier → bus → agent → notifier

What it does:
  - Echoes any message back with a prefix
  - Logs the interaction via core/logger
  - Saves the message to SQLite via core/storage
  - Responds via core/notifier (Telegram)

Replace or remove this once Phase 2 agents are in place.
"""

from __future__ import annotations

from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType
from agents.base import BaseAgent

log = get_logger("echo")


class EchoAgent(BaseAgent):
    name = "echo"
    description = "Echoes messages back. Used to validate the Phase 1 stack."
    autonomy_level = "read_only"

    async def handle(self, event: AgentEvent) -> AgentResponse:
        if not self._is_authorized(event.chat_id):
            log.warning("Unauthorised access attempt", event="auth_denied",
                        chat_id=event.chat_id)
            return AgentResponse(
                text="Unauthorized.", agent_name=self.name, success=False
            )

        if event.type == EventType.HEARTBEAT_TICK:
            log.info("Heartbeat tick received", event="heartbeat")
            return AgentResponse(text="HEARTBEAT_OK", agent_name=self.name)

        # Save inbound message to SQLite
        session_id = await self.storage.get_or_create_session(
            event.chat_id, self.name
        )
        await self.storage.save_message(
            session_id, "user", event.text, self.name
        )

        # Build reply
        reply_text = f"🤖 *Echo*: {event.text}"

        # Save outbound message to SQLite
        await self.storage.save_message(
            session_id, "assistant", reply_text, self.name
        )

        log.info("Message echoed", event="echo", chat_id=event.chat_id)
        return await self.reply(event, reply_text)

    async def register_schedules(self, bus) -> None:
        # Echo agent has no scheduled tasks
        pass

    async def health_check(self) -> bool:
        try:
            # Verify storage is reachable
            await self.storage.search_history("_health_check_", limit=1)
            return True
        except Exception as e:
            log.error("Health check failed", event="health_check_error", error=str(e))
            return False
