"""
core/bus.py
-----------
The message bus. Agents subscribe to event types — no direct coupling.
The bus routes; agents handle.

Flow:
  Telegram update → bus.publish(AgentEvent) → subscribed agent.handle(event)

Adding a new agent = register it. Zero changes to existing agents.

Usage:
    from core.bus import MessageBus
    bus = MessageBus()
    bus.register(my_agent)
    await bus.publish(event)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from core.logger import get_logger
from core.protocols import AgentEvent, AgentResponse, EventType
from core.budget import ActionType, BudgetManager

if TYPE_CHECKING:
    from agents.base import BaseAgent

log = get_logger("bus")

# Mapping from EventType to ActionType for budget classification
_EVENT_TO_ACTION: dict[EventType, ActionType] = {
    EventType.USER_MESSAGE: ActionType.REACTIVE,
    EventType.SCHEDULED_TASK: ActionType.PROACTIVE,
    EventType.HEARTBEAT_TICK: ActionType.PROACTIVE,
    EventType.WEBHOOK_EVENT: ActionType.PROACTIVE,
    EventType.APPROVAL_RESPONSE: ActionType.REACTIVE,
    EventType.AGENT_MESSAGE: ActionType.PROACTIVE,
}


class MessageBus:
    def __init__(self) -> None:
        # agent_name → agent instance
        self._agents: dict[str, "BaseAgent"] = {}
        # Maps chat_id → last active agent name (for conversation continuity)
        self._chat_agent_map: dict[str, str] = {}
        # Budget manager for proactive action limiting
        self._budget: BudgetManager | None = None

    # ── Registration ──────────────────────────

    def register(self, agent: "BaseAgent") -> None:
        self._agents[agent.name] = agent
        log.info("Agent registered", event="agent_registered", agent=agent.name)

    def set_budget(self, budget: BudgetManager) -> None:
        """Set budget manager for proactive action limiting."""
        self._budget = budget

    def _get_action_type(self, event: AgentEvent) -> ActionType:
        """Determine if an event is proactive or reactive for budget purposes."""
        return _EVENT_TO_ACTION.get(event.type, ActionType.REACTIVE)

    # ── Publishing ────────────────────────────

    async def publish(self, event: AgentEvent) -> AgentResponse | None:
        """
        Route an event to the appropriate agent and return its response.

        Routing priority:
          1. event.agent_name if explicitly set (scheduled tasks, heartbeats)
          2. Last agent that handled this chat_id (conversation continuity)
          3. First registered agent as fallback
        """
        agent = self._resolve_agent(event)
        if not agent:
            log.warning(
                "No agent found for event",
                event="routing_failed",
                agent_hint=event.agent_name,
            )
            return None

        # Track which agent is handling this chat
        if event.chat_id:
            self._chat_agent_map[event.chat_id] = agent.name

        log.info(
            "Routing event",
            event="bus_route",
            agent=agent.name,
            event_type=event.type.name,
            chat_id=event.chat_id,
        )

        try:
            with log.timer() as t:
                response = await agent.handle(event)
            log.info(
                "Event handled",
                event="bus_handled",
                agent=agent.name,
                duration_ms=t.ms,
                success=response.success,
            )
            return response
        except Exception as e:
            log.error(
                "Agent raised exception",
                event="bus_error",
                agent=agent.name,
                error=str(e),
            )
            return AgentResponse(
                text="Something went wrong. Please try again.",
                agent_name=agent.name,
                success=False,
            )

    async def publish_all(self, event: AgentEvent) -> list[AgentResponse]:
        """Broadcast an event to ALL registered agents (used for heartbeats)."""
        tasks = [agent.handle(event) for agent in self._agents.values()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        responses = []

        for r in results:
            if isinstance(r, Exception):
                log.error(
                    "Agent error during broadcast",
                    event="bus_broadcast_error",
                    error=str(r),
                )
            else:
                responses.append(r)

        return responses

    # ── Health ────────────────────────────────

    async def health_check_all(self) -> dict[str, bool]:
        results = {}
        for name, agent in self._agents.items():
            try:
                results[name] = await agent.health_check()
            except Exception as e:
                log.error(
                    "Health check failed",
                    event="health_check_error",
                    agent=name,
                    error=str(e),
                )
                results[name] = False
        return results

    @property
    def registered_agents(self) -> list[str]:
        return list(self._agents.keys())

    async def send_thinking(self, chat_id: str) -> int | None:
        """Send a 'Thinking...' placeholder and return its message ID."""
        if not self._agents:
            return None
        agent = next(iter(self._agents.values()))
        return await agent.notifier.send_and_get_id(chat_id, "⏳ Thinking...")

    async def clear_thinking(self, chat_id: str, message_id: int) -> None:
        """Delete the thinking placeholder message."""
        if not self._agents:
            return
        agent = next(iter(self._agents.values()))
        await agent.notifier.delete_message(chat_id, message_id)

    async def send_notification(self, chat_id: str, text: str) -> None:
        """
        Send a plain message to a chat via the first available agent's notifier.
        Used by main.py for system messages (pairing, startup notices) without
        needing to access bus._agents directly.
        """
        if not self._agents:
            log.warning(
                "send_notification called but no agents registered",
                event="notify_no_agents",
            )
            return
        agent = next(iter(self._agents.values()))
        await agent.notifier.send(chat_id, text)

    # ── Internal ─────────────────────────────

    def _resolve_agent(self, event: AgentEvent) -> "BaseAgent | None":
        # Explicit routing (scheduled tasks always set agent_name)
        if event.agent_name and event.agent_name in self._agents:
            return self._agents[event.agent_name]

        # Continuity: same chat → same agent
        if event.chat_id and event.chat_id in self._chat_agent_map:
            last_agent = self._chat_agent_map[event.chat_id]
            if last_agent in self._agents:
                return self._agents[last_agent]

        # Fallback: first registered agent
        if self._agents:
            return next(iter(self._agents.values()))

        return None
